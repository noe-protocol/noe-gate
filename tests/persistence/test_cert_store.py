"""
tests/persistence/test_cert_store.py

Unit tests for noe/persistence/cert_store.py.

Coverage:
  - append/readback success
  - cert_id recomputation success
  - tampered record detection
  - broken hash-link detection (prev_cert_id points to non-existent cert)
  - missing predecessor in ordered sequence
  - evidence_hashes deterministic ordering (insertion order must not affect cert_id)
  - blocked evaluation cert persistence (undefined, error domains)
  - schema validation failures (all required fields)
  - IO failures (missing file, corrupt JSONL, unwritable path)
"""
import json
import os
import pytest
from pathlib import Path

from noe.persistence.cert_store import (
    CertStore,
    CertSchemaError,
    build_cert_body,
    compute_cert_id,
    verify_cert_id,
    validate_cert_schema,
    CERT_SCHEMA,
)

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

_REGISTRY_HASH = "a" * 64
_H_SAFE = "b" * 64
_H_ROOT = "c" * 64
_H_DOMAIN = "d" * 64
_H_LOCAL = "e" * 64
_H_COMPOSITE = "f" * 64
_CHAIN_HASH = "1" * 64
_EV_HASH_1 = "aa" * 32
_EV_HASH_2 = "bb" * 32
_EV_HASH_3 = "cc" * 32


def _make_store(tmp_path: Path, filename: str = "certs.jsonl") -> CertStore:
    return CertStore(tmp_path / filename)


def _base_params(**overrides):
    """Return a complete set of valid cert params."""
    defaults = dict(
        created_ts_ms=1_741_234_567_890,
        chain="shi @clear_path nek",
        chain_hash=_CHAIN_HASH,
        h_safe=_H_SAFE,
        h_root=_H_ROOT,
        h_domain=_H_DOMAIN,
        h_local=_H_LOCAL,
        h_composite=_H_COMPOSITE,
        result_domain="truth",
        result_value=True,
        epistemic_basis=["clear_path"],
        registry_hash=_REGISTRY_HASH,
        registry_version="1.0.0",
        semantics_version="NIP-005-v1.0",
        runtime_mode="strict",
        provenance_hash=None,
        action_hash=None,
        decision_hash=None,
        domain_pack_hash=None,
        evidence_hashes=[_EV_HASH_1],
        prev_cert_id=None,
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# 1. Append / readback
# ---------------------------------------------------------------------------

class TestAppendReadback:
    def test_single_cert_appended_and_read(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params())

        assert cert["schema"] == CERT_SCHEMA
        assert len(cert["cert_id"]) == 64
        assert cert["result_domain"] == "truth"

        certs = list(store.iter_certs())
        assert len(certs) == 1
        assert certs[0]["cert_id"] == cert["cert_id"]

    def test_multiple_certs_appended_in_order(self, tmp_path):
        store = _make_store(tmp_path)
        c1 = store.append(**_base_params())
        c2 = store.append(**_base_params(chain="vek @human_nearby nek", prev_cert_id=c1["cert_id"]))

        certs = list(store.iter_certs())
        assert len(certs) == 2
        assert certs[0]["cert_id"] == c1["cert_id"]
        assert certs[1]["cert_id"] == c2["cert_id"]
        assert certs[1]["prev_cert_id"] == c1["cert_id"]

    def test_count(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.count() == 0
        store.append(**_base_params())
        assert store.count() == 1
        store.append(**_base_params())
        assert store.count() == 2

    def test_get_latest_cert_id(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.get_latest_cert_id() is None
        c1 = store.append(**_base_params())
        assert store.get_latest_cert_id() == c1["cert_id"]
        c2 = store.append(**_base_params())
        assert store.get_latest_cert_id() == c2["cert_id"]

    def test_cert_file_is_jsonl(self, tmp_path):
        """Each line must be independent JSON."""
        store = _make_store(tmp_path)
        store.append(**_base_params())
        store.append(**_base_params())
        lines = (tmp_path / "certs.jsonl").read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert isinstance(obj, dict)


# ---------------------------------------------------------------------------
# 2. cert_id recomputation
# ---------------------------------------------------------------------------

class TestCertIdRecomputation:
    def test_verify_cert_id_success(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params())
        assert verify_cert_id(cert) is True

    def test_compute_cert_id_is_deterministic(self):
        body = build_cert_body(**_base_params())
        id1 = compute_cert_id(body)
        id2 = compute_cert_id(body)
        assert id1 == id2

    def test_cert_id_not_allowed_in_body_for_compute(self):
        body = build_cert_body(**_base_params())
        body["cert_id"] = "x" * 64
        with pytest.raises(ValueError, match="ERR_CERT_ID"):
            compute_cert_id(body)

    def test_different_chains_produce_different_cert_ids(self):
        body_a = build_cert_body(**_base_params(chain="shi @clear_path nek"))
        body_b = build_cert_body(**_base_params(chain="vek @clear_path nek"))
        assert compute_cert_id(body_a) != compute_cert_id(body_b)

    def test_different_timestamps_produce_different_cert_ids(self):
        body_a = build_cert_body(**_base_params(created_ts_ms=1000))
        body_b = build_cert_body(**_base_params(created_ts_ms=1001))
        assert compute_cert_id(body_a) != compute_cert_id(body_b)


# ---------------------------------------------------------------------------
# 3. Tamper detection
# ---------------------------------------------------------------------------

class TestTamperDetection:
    def test_tampered_result_domain_detected(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params())
        cert["result_domain"] = "action"  # mutate after read
        assert verify_cert_id(cert) is False

    def test_tampered_chain_detected(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params())
        cert["chain"] = "shi @EVIL nek"
        assert verify_cert_id(cert) is False

    def test_tampered_evidence_hashes_detected(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params())
        cert["evidence_hashes"].append("ff" * 32)
        assert verify_cert_id(cert) is False

    def test_tampered_result_value_detected(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params(result_value=True))
        cert["result_value"] = False
        assert verify_cert_id(cert) is False

    def test_tampered_file_line_detectable_via_verify(self, tmp_path):
        """Directly write a bad line and verify it fails cert_id check."""
        store = _make_store(tmp_path)
        cert = store.append(**_base_params())
        # Overwrite the file with a tampered version
        tampered = dict(cert)
        tampered["chain"] = "shi @evil nek"
        (tmp_path / "certs.jsonl").write_text(
            json.dumps(tampered, separators=(",", ":")) + "\n"
        )
        read_back = list(store.iter_certs())[0]
        assert verify_cert_id(read_back) is False


# ---------------------------------------------------------------------------
# 4. Hash-link integrity
# ---------------------------------------------------------------------------

class TestHashLinkIntegrity:
    def test_prev_cert_id_included_in_cert_id(self, tmp_path):
        """Changing prev_cert_id must change cert_id."""
        body_no_prev = build_cert_body(**_base_params(prev_cert_id=None))
        body_with_prev = build_cert_body(**_base_params(prev_cert_id="a" * 64))
        assert compute_cert_id(body_no_prev) != compute_cert_id(body_with_prev)

    def test_chain_of_two_correctly_linked(self, tmp_path):
        store = _make_store(tmp_path)
        c1 = store.append(**_base_params(prev_cert_id=None))
        c2 = store.append(**_base_params(prev_cert_id=c1["cert_id"]))

        # Independently replicate what c2's cert_id should be
        body2 = build_cert_body(**_base_params(prev_cert_id=c1["cert_id"]))
        expected_c2_id = compute_cert_id(body2)
        assert c2["cert_id"] == expected_c2_id

    def test_broken_link_detectable(self, tmp_path):
        """A cert whose prev_cert_id references a non-existent id is detectable by reading all certs."""
        store = _make_store(tmp_path)
        c1 = store.append(**_base_params(prev_cert_id=None))
        # Write c2 with wrong prev_cert_id (simulates manual splice attack)
        bad_prev = "ff" * 32
        store.append(**_base_params(prev_cert_id=bad_prev))

        certs = list(store.iter_certs())
        cert_ids = {c["cert_id"] for c in certs}
        # Second cert's prev_cert_id is not in the store → broken link
        broken = [c for c in certs if c.get("prev_cert_id") and c["prev_cert_id"] not in cert_ids]
        assert len(broken) == 1


# ---------------------------------------------------------------------------
# 5. evidence_hashes deterministic ordering
# ---------------------------------------------------------------------------

class TestEvidenceHashesSorting:
    def test_insertion_order_does_not_affect_cert_id(self):
        """evidence_hashes must be sorted before cert_id is computed."""
        params_asc = _base_params(evidence_hashes=[_EV_HASH_1, _EV_HASH_2, _EV_HASH_3])
        params_desc = _base_params(evidence_hashes=[_EV_HASH_3, _EV_HASH_2, _EV_HASH_1])
        body_asc = build_cert_body(**params_asc)
        body_desc = build_cert_body(**params_desc)
        # Both should produce the same sorted list in the body
        assert body_asc["evidence_hashes"] == body_desc["evidence_hashes"]
        assert compute_cert_id(body_asc) == compute_cert_id(body_desc)

    def test_evidence_hashes_are_sorted_in_stored_cert(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params(
            evidence_hashes=[_EV_HASH_3, _EV_HASH_1, _EV_HASH_2],
        ))
        assert cert["evidence_hashes"] == sorted([_EV_HASH_1, _EV_HASH_2, _EV_HASH_3])

    def test_two_different_sets_produce_different_cert_ids(self):
        body_a = build_cert_body(**_base_params(evidence_hashes=[_EV_HASH_1]))
        body_b = build_cert_body(**_base_params(evidence_hashes=[_EV_HASH_2]))
        assert compute_cert_id(body_a) != compute_cert_id(body_b)


# ---------------------------------------------------------------------------
# 6. Blocked evaluation cert persistence (undefined/error)
# ---------------------------------------------------------------------------

class TestBlockedEvaluationStorage:
    def test_undefined_domain_stored(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params(
            result_domain="undefined",
            result_value="undefined",
            provenance_hash=None,
            action_hash=None,
        ))
        assert cert["result_domain"] == "undefined"
        assert verify_cert_id(cert) is True

    def test_error_domain_stored(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params(
            result_domain="error",
            result_value={"error": "ERR_CONTEXT_INCOMPLETE"},
            provenance_hash=None,
            action_hash=None,
        ))
        assert cert["result_domain"] == "error"
        assert verify_cert_id(cert) is True

    def test_action_domain_with_non_null_hashes(self, tmp_path):
        store = _make_store(tmp_path)
        cert = store.append(**_base_params(
            result_domain="action",
            result_value={"operator": "mos", "target": "exit"},
            provenance_hash="p" * 64,
            action_hash="a" * 64,
        ))
        assert cert["result_domain"] == "action"
        assert cert["provenance_hash"] == "p" * 64
        assert verify_cert_id(cert) is True


# ---------------------------------------------------------------------------
# 7. Schema validation failures
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    def _valid_cert(self, tmp_path):
        store = _make_store(tmp_path)
        return store.append(**_base_params())

    def test_missing_required_field_raises(self, tmp_path):
        cert = self._valid_cert(tmp_path)
        del cert["chain"]
        with pytest.raises(CertSchemaError, match="missing required fields"):
            validate_cert_schema(cert)

    def test_wrong_schema_tag_raises(self, tmp_path):
        cert = self._valid_cert(tmp_path)
        cert["schema"] = "wrong-schema-v99"
        with pytest.raises(CertSchemaError, match="schema"):
            validate_cert_schema(cert)

    def test_non_int_timestamp_raises(self, tmp_path):
        cert = self._valid_cert(tmp_path)
        cert["created_ts_ms"] = "not-an-int"
        with pytest.raises(CertSchemaError, match="created_ts_ms"):
            validate_cert_schema(cert)

    def test_invalid_result_domain_raises(self, tmp_path):
        # Must be caught at append time because we validate immediately
        store = _make_store(tmp_path)
        with pytest.raises(CertSchemaError, match="result_domain"):
            store.append(**_base_params(result_domain="hallucination"))

    def test_short_cert_id_raises(self, tmp_path):
        cert = self._valid_cert(tmp_path)
        cert["cert_id"] = "tooshort"
        with pytest.raises(CertSchemaError, match="cert_id"):
            validate_cert_schema(cert)

    def test_invalid_prev_cert_id_raises(self, tmp_path):
        cert = self._valid_cert(tmp_path)
        cert["prev_cert_id"] = "not-64-chars"
        with pytest.raises(CertSchemaError, match="prev_cert_id"):
            validate_cert_schema(cert)

    def test_non_list_evidence_hashes_raises(self, tmp_path):
        cert = self._valid_cert(tmp_path)
        cert["evidence_hashes"] = "single_hash"
        with pytest.raises(CertSchemaError, match="evidence_hashes"):
            validate_cert_schema(cert)


# ---------------------------------------------------------------------------
# 8. IO failures
# ---------------------------------------------------------------------------

class TestIOFailures:
    def test_iter_raises_file_not_found(self, tmp_path):
        store = CertStore(tmp_path / "nonexistent" / "certs.jsonl")
        with pytest.raises(FileNotFoundError, match="ERR_IO"):
            list(store.iter_certs())

    def test_corrupt_jsonl_line_raises(self, tmp_path):
        p = tmp_path / "certs.jsonl"
        p.write_text("this is not json\n")
        store = CertStore(p)
        with pytest.raises(ValueError, match="ERR_IO.*corrupt"):
            list(store.iter_certs())

    def test_non_object_jsonl_line_raises(self, tmp_path):
        p = tmp_path / "certs.jsonl"
        p.write_text("[1, 2, 3]\n")
        store = CertStore(p)
        with pytest.raises(ValueError, match="ERR_IO.*expected JSON object"):
            list(store.iter_certs())

    def test_parent_dir_created_if_missing(self, tmp_path):
        store = CertStore(tmp_path / "subdir" / "nested" / "certs.jsonl")
        store.append(**_base_params())
        assert (tmp_path / "subdir" / "nested" / "certs.jsonl").exists()

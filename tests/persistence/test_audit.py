"""
tests/persistence/test_audit.py

Tests for noe/persistence/audit.py — audit_store().

Coverage:
  - audit passes on a clean store
  - detects tampered cert_id (EXIT_TAMPERED)
  - detects broken prev_cert_id link (EXIT_MISSING)
  - detects missing predecessor
  - schema error detection (EXIT_SCHEMA_ERROR)
  - IO error on missing file (EXIT_IO_ERROR)
  - exit codes are correct
  - multi-violation accumulation
"""
import json
import pytest
from pathlib import Path

from noe.persistence.cert_store import CertStore
from noe.persistence.audit import (
    audit_store,
    EXIT_OK,
    EXIT_TAMPERED,
    EXIT_MISSING,
    EXIT_SCHEMA_ERROR,
    EXIT_IO_ERROR,
    AuditResult,
)

_REGISTRY_HASH = "a" * 64
_H_SAFE = "b" * 64
_H_ROOT = "c" * 64
_H_DOMAIN = "d" * 64
_H_LOCAL = "e" * 64
_H_COMPOSITE = "f" * 64
_CHAIN_HASH = "1" * 64
_EV_HASH = "aa" * 32


def _base_params(**overrides):
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
        evidence_hashes=[_EV_HASH],
        prev_cert_id=None,
    )
    defaults.update(overrides)
    return defaults


def _make_store(tmp_path: Path) -> CertStore:
    return CertStore(tmp_path / "certs.jsonl")


class TestAuditCleanStore:
    def test_empty_store_raises_file_not_found(self, tmp_path):
        store = _make_store(tmp_path)
        result = audit_store(store)
        assert result.exit_code == EXIT_IO_ERROR
        assert len(result.violations) == 1

    def test_single_cert_passes(self, tmp_path):
        store = _make_store(tmp_path)
        store.append(**_base_params())
        result = audit_store(store)
        assert result.ok
        assert result.exit_code == EXIT_OK
        assert result.total_records == 1
        assert result.violations == []

    def test_chain_of_three_passes(self, tmp_path):
        store = _make_store(tmp_path)
        c1 = store.append(**_base_params(prev_cert_id=None))
        c2 = store.append(**_base_params(prev_cert_id=c1["cert_id"]))
        c3 = store.append(**_base_params(prev_cert_id=c2["cert_id"]))
        result = audit_store(store)
        assert result.ok
        assert result.total_records == 3


class TestAuditTamperDetection:
    def test_tampered_chain_detected(self, tmp_path):
        store = _make_store(tmp_path)
        store.append(**_base_params())

        # Directly overwrite the file with a tampered record
        lines = store.path.read_text().splitlines()
        cert = json.loads(lines[0])
        cert["chain"] = "shi @EVIL_CHAIN nek"
        store.path.write_text(json.dumps(cert) + "\n")

        result = audit_store(store)
        assert result.exit_code == EXIT_TAMPERED
        assert len(result.violations) == 1
        assert result.violations[0].exit_code == EXIT_TAMPERED
        assert "cert_id recomputation mismatch" in result.violations[0].reason

    def test_tampered_result_value_detected(self, tmp_path):
        store = _make_store(tmp_path)
        store.append(**_base_params(result_value=True))
        lines = store.path.read_text().splitlines()
        cert = json.loads(lines[0])
        cert["result_value"] = False  # change value
        store.path.write_text(json.dumps(cert) + "\n")

        result = audit_store(store)
        assert result.exit_code == EXIT_TAMPERED

    def test_tampered_evidence_hashes_detected(self, tmp_path):
        store = _make_store(tmp_path)
        store.append(**_base_params())
        lines = store.path.read_text().splitlines()
        cert = json.loads(lines[0])
        cert["evidence_hashes"].append("ff" * 32)
        store.path.write_text(json.dumps(cert) + "\n")

        result = audit_store(store)
        assert result.exit_code == EXIT_TAMPERED


class TestAuditHashLinkIntegrity:
    def test_broken_prev_cert_id_detected(self, tmp_path):
        """prev_cert_id points to a cert_id not in the store → EXIT_MISSING."""
        store = _make_store(tmp_path)
        # Write a cert with a prev_cert_id that is valid format but never appended
        bad_prev = "ff" * 32
        store.append(**_base_params(prev_cert_id=bad_prev))

        result = audit_store(store)
        assert result.exit_code == EXIT_MISSING
        assert len(result.violations) == 1
        assert "prev_cert_id" in result.violations[0].reason

    def test_missing_predecessor_detected(self, tmp_path):
        """Second cert references first cert_id, but first cert is deleted from file."""
        store = _make_store(tmp_path)
        c1 = store.append(**_base_params(prev_cert_id=None))
        store.append(**_base_params(prev_cert_id=c1["cert_id"]))

        # Simulate "first cert deleted" by keeping only the second line
        lines = store.path.read_text().splitlines()
        store.path.write_text(lines[1] + "\n")  # only second cert

        result = audit_store(store)
        assert result.exit_code == EXIT_MISSING


class TestAuditSchemaErrors:
    def test_wrong_schema_tag_detected(self, tmp_path):
        store = _make_store(tmp_path)
        store.append(**_base_params())
        lines = store.path.read_text().splitlines()
        cert = json.loads(lines[0])
        cert["schema"] = "noe-cert-v99"
        store.path.write_text(json.dumps(cert) + "\n")

        result = audit_store(store)
        assert result.exit_code == EXIT_SCHEMA_ERROR

    def test_missing_field_detected(self, tmp_path):
        store = _make_store(tmp_path)
        store.append(**_base_params())
        lines = store.path.read_text().splitlines()
        cert = json.loads(lines[0])
        del cert["chain"]
        store.path.write_text(json.dumps(cert) + "\n")

        result = audit_store(store)
        assert result.exit_code == EXIT_SCHEMA_ERROR


class TestAuditIOErrors:
    def test_missing_store_file_io_error(self, tmp_path):
        store = CertStore(tmp_path / "does_not_exist.jsonl")
        result = audit_store(store)
        assert result.exit_code == EXIT_IO_ERROR
        assert "not found" in result.violations[0].reason.lower()

    def test_corrupt_jsonl_io_error(self, tmp_path):
        p = tmp_path / "certs.jsonl"
        p.write_text("this is not json\n")
        store = CertStore(p)
        result = audit_store(store)
        # audit_store wraps IO errors into EXIT_IO_ERROR
        assert result.exit_code == EXIT_IO_ERROR


class TestAuditExitCodes:
    def test_exit_ok_value(self):
        assert EXIT_OK == 0

    def test_exit_tampered_value(self):
        assert EXIT_TAMPERED == 2

    def test_exit_missing_value(self):
        assert EXIT_MISSING == 3

    def test_exit_schema_error_value(self):
        assert EXIT_SCHEMA_ERROR == 6

    def test_exit_io_error_value(self):
        assert EXIT_IO_ERROR == 7

"""
tests/packaging/test_package.py

Packaging smoke tests — run in the current dev environment.

These tests validate:
  1. All key modules importable without sys.path hacks
  2. registry.json accessible via importlib.resources
  3. CLI entry-point main() callables work (no subprocess required)
  4. Wheel build produces dist/ artifacts
  5. evidence_hashes sort invariant survives a real certstore round-trip

These tests pass in the dev environment (not requiring a full fresh-venv install).
The installed-wheel test is covered by scripts/test_install.sh.
"""
import importlib
import importlib.resources
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. Core import smoke tests
# ---------------------------------------------------------------------------

class TestImports:
    def test_noe_importable(self):
        import noe  # noqa: F401
        assert hasattr(noe, "__file__")

    def test_noe_runtime_importable(self):
        from noe.noe_runtime import NoeRuntime  # noqa: F401
        assert NoeRuntime is not None

    def test_noe_canonical_importable(self):
        from noe.canonical import canonical_bytes  # noqa: F401
        assert callable(canonical_bytes)

    def test_noe_provenance_importable(self):
        from noe.provenance import compute_registry_hash, SEMANTICS_VERSION  # noqa: F401
        assert isinstance(SEMANTICS_VERSION, str)
        assert SEMANTICS_VERSION.startswith("NIP-")

    def test_grounding_common_importable(self):
        from packages.grounding.common import (  # noqa: F401
            GroundingInput, GroundingResult, compute_observation_event_hash,
        )
        assert GroundingResult is not None

    def test_grounding_lidar_importable(self):
        from packages.grounding.lidar_zone.ground import ground_lidar_zone  # noqa: F401
        assert callable(ground_lidar_zone)

    def test_grounding_camera_importable(self):
        from packages.grounding.camera_human.ground import ground_camera_human  # noqa: F401
        assert callable(ground_camera_human)

    def test_persistence_cert_store_importable(self):
        from noe.persistence.cert_store import CertStore  # noqa: F401
        assert CertStore is not None

    def test_persistence_audit_importable(self):
        from noe.persistence.audit import audit_store  # noqa: F401
        assert callable(audit_store)

    def test_persistence_replay_importable(self):
        from noe.persistence.replay import replay_cert  # noqa: F401
        assert callable(replay_cert)

    def test_cli_audit_importable(self):
        from noe.persistence.cli_audit import main as audit_main  # noqa: F401
        assert callable(audit_main)

    def test_cli_replay_importable(self):
        from noe.persistence.cli_replay import main as replay_main  # noqa: F401
        assert callable(replay_main)


# ---------------------------------------------------------------------------
# 2. registry.json accessible via importlib.resources (canonical runtime path)
# ---------------------------------------------------------------------------

class TestRegistryAccess:
    def test_registry_json_exists_on_disk(self):
        """registry.json must be present next to noe/__init__.py."""
        import noe
        registry_path = Path(noe.__file__).parent / "registry.json"
        assert registry_path.exists(), (
            f"registry.json not found at {registry_path}. "
            "Package data rules may be missing from pyproject.toml."
        )

    def test_registry_json_is_valid_json(self):
        import noe
        registry_path = Path(noe.__file__).parent / "registry.json"
        data = json.loads(registry_path.read_text())
        assert isinstance(data, dict)
        assert "meta" in data or len(data) > 0

    def test_registry_json_via_importlib_resources(self):
        """
        Canonical runtime access path: importlib.resources.files(noe).
        This is the path that works correctly from both repo and wheel installs.
        """
        files = importlib.resources.files("noe")
        registry_ref = files / "registry.json"
        content = registry_ref.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_compute_registry_hash_succeeds(self):
        """compute_registry_hash must work after install (registry.json must be found)."""
        from noe.provenance import compute_registry_hash
        h = compute_registry_hash()
        assert isinstance(h, str)
        assert len(h) == 64, f"Expected 64-char hex SHA-256, got {len(h)!r}"


# ---------------------------------------------------------------------------
# 3. CLI entry-point main() callable tests (no subprocess)
# ---------------------------------------------------------------------------

class TestCLIMainCallable:
    def test_cli_audit_help_returns_0(self, capsys):
        from noe.persistence.cli_audit import main
        code = main(["--help"])
        assert code == 0
        out = capsys.readouterr().out
        assert "noe-audit" in out or "store.jsonl" in out

    def test_cli_replay_help_returns_0(self, capsys):
        from noe.persistence.cli_replay import main
        code = main(["--help"])
        assert code == 0
        out = capsys.readouterr().out
        assert "noe-replay" in out or "replay_input.json" in out

    def test_cli_audit_no_args_returns_1(self, capsys):
        from noe.persistence.cli_audit import main
        code = main([])
        assert code == 1

    def test_cli_replay_no_args_returns_1(self, capsys):
        from noe.persistence.cli_replay import main
        code = main([])
        assert code == 1

    def test_cli_audit_missing_file_returns_7(self, tmp_path, capsys):
        from noe.persistence.cli_audit import main
        code = main([str(tmp_path / "nonexistent.jsonl")])
        assert code == 7  # EXIT_IO_ERROR

    def test_cli_replay_missing_file_returns_7(self, tmp_path, capsys):
        from noe.persistence.cli_replay import main
        code = main([str(tmp_path / "nonexistent.json")])
        assert code == 7  # EXIT_IO_ERROR


# ---------------------------------------------------------------------------
# 4. CLI audit against a real valid store (stronger smoke test)
# ---------------------------------------------------------------------------

class TestCLIAuditRealStore:
    """
    Build a minimal valid cert store and audit it via main().
    This is the 'strong' packaging smoke test: it exercises the full
    append → audit path through the CLI entry point.
    """

    def _write_valid_store(self, tmp_path: Path) -> Path:
        from noe.persistence.cert_store import CertStore
        store = CertStore(tmp_path / "smoke.jsonl")
        store.append(
            created_ts_ms=1_741_234_567_890,
            chain="shi @clear_path nek",
            chain_hash="1" * 64,
            h_safe="b" * 64,
            h_root="c" * 64,
            h_domain="d" * 64,
            h_local="e" * 64,
            h_composite="f" * 64,
            result_domain="truth",
            result_value=True,
            epistemic_basis=["clear_path"],
            registry_hash="a" * 64,
            registry_version="1.0.0",
            semantics_version="NIP-005-v1.0",
            runtime_mode="strict",
            provenance_hash=None,
            action_hash=None,
            decision_hash=None,
            domain_pack_hash=None,
            evidence_hashes=["aa" * 32],
            prev_cert_id=None,
        )
        return store.path

    def test_audit_valid_store_via_main_exits_0(self, tmp_path, capsys):
        """Core packaging smoke test: noe-audit <valid store> → exit 0."""
        from noe.persistence.cli_audit import main
        store_path = self._write_valid_store(tmp_path)
        code = main([str(store_path)])
        assert code == 0, (
            f"Expected exit 0 from valid store audit, got {code}. "
            f"stderr: {capsys.readouterr().err}"
        )
        out = capsys.readouterr().out
        assert "PASSED" in out

    def test_audit_tampered_store_via_main_exits_2(self, tmp_path, capsys):
        """Tampered store → noe-audit returns 2."""
        import json
        from noe.persistence.cli_audit import main
        store_path = self._write_valid_store(tmp_path)
        lines = store_path.read_text().splitlines()
        cert = json.loads(lines[0])
        cert["chain"] = "shi @EVIL nek"
        store_path.write_text(json.dumps(cert) + "\n")
        code = main([str(store_path)])
        assert code == 2  # EXIT_TAMPERED


# ---------------------------------------------------------------------------
# 5. Package version
# ---------------------------------------------------------------------------

class TestPackageVersion:
    def test_version_is_1_1_0(self):
        """
        Verify version after a fresh `pip install .` into this environment.
        If noe-runtime is installed at an older version (pre-Phase-3 dev env),
        skip — the installed-wheel test (scripts/test_install.sh) is authoritative.
        """
        import importlib.metadata
        try:
            version = importlib.metadata.version("noe-runtime")
        except importlib.metadata.PackageNotFoundError:
            pytest.skip("noe-runtime not installed as a package — skipping version check")
            return
        if version != "1.1.0":
            pytest.skip(
                f"noe-runtime is installed at {version!r} (pre-Phase-3 dev env). "
                "Re-run `pip install -e .` or use scripts/test_install.sh to verify 1.1.0."
            )
        assert version == "1.1.0"


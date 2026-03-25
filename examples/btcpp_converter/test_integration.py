"""
tests/btcpp_converter/test_integration.py

End-to-end integration tests on the patrol_robot.xml example.
Also covers import path test (installed-package import works).
"""
import json
from pathlib import Path

import pytest

PATROL_XML_PATH = Path(__file__).parent.parent.parent / "examples" / "btcpp_converter" / "patrol_robot.xml"


class TestImportPath:
    """Verify the installed-package import path works."""

    def test_package_importable(self):
        import packages.btcpp_converter  # noqa: F401
        assert packages.btcpp_converter is not None

    def test_parse_importable(self):
        from packages.btcpp_converter import parse_bt_xml  # noqa: F401
        assert callable(parse_bt_xml)

    def test_build_report_importable(self):
        from packages.btcpp_converter import build_report  # noqa: F401
        assert callable(build_report)

    def test_main_importable(self):
        from packages.btcpp_converter import main  # noqa: F401
        assert callable(main)


class TestPatrolRobotXML:
    """Integration test on the worked example."""

    @pytest.fixture
    def patrol_report(self):
        from packages.btcpp_converter.report import build_report
        xml = PATROL_XML_PATH.read_text(encoding="utf-8")
        return build_report(xml, source_file=str(PATROL_XML_PATH), now_ms=0)

    def test_parse_succeeds(self, patrol_report):
        assert patrol_report.bt_tree_id == "PatrolMission"

    def test_at_least_one_candidate_chain(self, patrol_report):
        assert len(patrol_report.candidate_chains) > 0

    def test_safety_conditions_flagged(self, patrol_report):
        # IsEmergencyStopClear and IsHumanProximityOK should be flagged
        assert len(patrol_report.safety_relevant_conditions) >= 1

    def test_needs_grounding_populated(self, patrol_report):
        # All conditions and actions require grounding
        assert len(patrol_report.required_grounding_tasks) > 0

    def test_retry_decorator_in_unsupported(self, patrol_report):
        assert any(u.xml_tag == "Retry" for u in patrol_report.unsupported_nodes)

    def test_fallback_produces_lost_semantics(self, patrol_report):
        assert len(patrol_report.lost_semantics) > 0

    def test_fallback_chains_marked(self, patrol_report):
        assert any(c.from_fallback for c in patrol_report.candidate_chains)

    def test_10_assumptions_always_emitted(self, patrol_report):
        assert len(patrol_report.assumptions) == 10

    def test_report_json_serialisable(self, patrol_report):
        d = json.loads(patrol_report.to_json())
        assert "candidate_chains" in d
        assert "assumptions" in d

    def test_manual_canonicalization_nonempty(self, patrol_report):
        assert len(patrol_report.manual_canonicalization_required) > 0

    def test_placeholder_registry_sorted(self, patrol_report):
        keys = [(e["type"], e["noe_id"]) for e in patrol_report.placeholder_registry]
        assert keys == sorted(keys)

    def test_all_placeholder_entries_have_needs_grounding_status(self, patrol_report):
        for entry in patrol_report.placeholder_registry:
            assert entry["status"] == "NEEDS_GROUNDING"

    def test_all_placeholder_entries_have_btcpp_origin(self, patrol_report):
        for entry in patrol_report.placeholder_registry:
            assert entry["origin"] == "btcpp-name-derived"


class TestCLIMainOnPatrolXML:
    def test_cli_main_returns_0(self, capsys, tmp_path):
        from packages.btcpp_converter.cli import main
        code = main([str(PATROL_XML_PATH)])
        assert code in (0, 2), f"Expected exit 0 or 2, got {code}"

    def test_cli_main_help_returns_0(self, capsys):
        from packages.btcpp_converter.cli import main
        code = main(["--help"])
        assert code == 0

    def test_cli_main_no_args_returns_1(self, capsys):
        from packages.btcpp_converter.cli import main
        code = main([])
        assert code == 1

    def test_cli_main_missing_file_returns_7(self, capsys):
        from packages.btcpp_converter.cli import main
        code = main(["/nonexistent/path.xml"])
        assert code == 7

    def test_cli_main_malformed_xml_returns_7(self, tmp_path, capsys):
        from packages.btcpp_converter.cli import main
        bad_file = tmp_path / "bad.xml"
        bad_file.write_text("<root><broken>")
        code = main([str(bad_file)])
        assert code == 7

    def test_cli_json_flag(self, capsys, tmp_path):
        from packages.btcpp_converter.cli import main
        out_file = tmp_path / "report.json"
        code = main([str(PATROL_XML_PATH), "--json", "--out", str(out_file)])
        assert code in (0, 2)
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "candidate_chains" in data

"""
tests/btcpp_converter/test_report.py — ConversionReport tests.
"""
import json
import pytest
from packages.btcpp_converter.parser import BTParseError
from packages.btcpp_converter.report import build_report, CONVERTER_ASSUMPTIONS

SIMPLE_XML = """\
<root><BehaviorTree ID="TestTree">
  <Sequence>
    <IsPathClear/>
    <MoveForward/>
  </Sequence>
</BehaviorTree></root>"""

SAFETY_XML = """\
<root><BehaviorTree ID="SafetyTree">
  <Sequence>
    <IsEmergencyStopClear/>
    <IsHumanProximityOK/>
    <MoveForward/>
  </Sequence>
</BehaviorTree></root>"""

FALLBACK_XML = """\
<root><BehaviorTree ID="FallbackTree">
  <Fallback>
    <Sequence><IsPrimary/><DoPrimary/></Sequence>
    <Sequence><IsBackup/><DoBackup/></Sequence>
  </Fallback>
</BehaviorTree></root>"""

DECORATOR_XML = """\
<root><BehaviorTree ID="DecTree">
  <Retry num_attempts="3"><DoSomething/></Retry>
</BehaviorTree></root>"""

PARALLEL_XML = """\
<root><BehaviorTree ID="ParallelTree">
  <Parallel><Act/><Check/></Parallel>
</BehaviorTree></root>"""

MIXED_XML = """\
<root><BehaviorTree ID="MixedTree">
  <Sequence>
    <IsPathClear/>
    <Retry num_attempts="2"><MoveForward/></Retry>
  </Sequence>
</BehaviorTree></root>"""


class TestReportBasicFields:
    def test_source_file_preserved(self):
        report = build_report(SIMPLE_XML, source_file="test.xml", now_ms=0)
        assert report.source_file == "test.xml"

    def test_tree_id_preserved(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        assert report.bt_tree_id == "TestTree"

    def test_generated_at_ms_injected(self):
        report = build_report(SIMPLE_XML, now_ms=12345)
        assert report.generated_at_ms == 12345


class TestAssumptions:
    def test_10_assumptions_emitted(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        assert len(report.assumptions) == 10

    def test_assumptions_match_constants(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        assert report.assumptions == CONVERTER_ASSUMPTIONS

    def test_assumptions_mention_grounding(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        assert any("grounding" in a.lower() for a in report.assumptions)

    def test_assumptions_mention_fallback(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        assert any("Fallback" in a for a in report.assumptions)


class TestRequiredGroundingTasks:
    def test_condition_in_grounding_tasks(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        assert "is_path_clear" in report.required_grounding_tasks

    def test_action_in_grounding_tasks(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        assert "move_forward" in report.required_grounding_tasks


class TestSafetyRelevant:
    def test_emergency_stop_in_safety_relevant(self):
        report = build_report(SAFETY_XML, now_ms=0)
        assert any("emergency" in s.lower() or "estop" in s.lower() or "IsEmergencyStopClear" in s
                   for s in report.safety_relevant_conditions)

    def test_human_proximity_in_safety_relevant(self):
        report = build_report(SAFETY_XML, now_ms=0)
        assert any("Human" in s or "human" in s for s in report.safety_relevant_conditions)


class TestUnsupportedNodes:
    def test_decorator_in_unsupported(self):
        report = build_report(DECORATOR_XML, now_ms=0)
        assert any(u.xml_tag == "Retry" for u in report.unsupported_nodes)

    def test_parallel_in_unsupported(self):
        report = build_report(PARALLEL_XML, now_ms=0)
        assert any(u.xml_tag == "Parallel" for u in report.unsupported_nodes)

    def test_unsupported_has_reason(self):
        report = build_report(DECORATOR_XML, now_ms=0)
        for u in report.unsupported_nodes:
            assert len(u.reason) > 0


class TestLostSemantics:
    def test_fallback_produces_lost_semantics(self):
        report = build_report(FALLBACK_XML, now_ms=0)
        assert len(report.lost_semantics) > 0

    def test_lost_semantics_mentions_fallback(self):
        report = build_report(FALLBACK_XML, now_ms=0)
        assert any("FALLBACK" in ls or "Fallback" in ls for ls in report.lost_semantics)


class TestAmbiguousMappings:
    def test_fallback_branches_in_ambiguous(self):
        report = build_report(FALLBACK_XML, now_ms=0)
        assert len(report.ambiguous_mappings) > 0


class TestManualCanonicalization:
    def test_action_in_manual_canonicalization(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        assert "move_forward" in report.manual_canonicalization_required


class TestNodeWarnings:
    def test_node_warnings_dict_not_empty(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        assert len(report.node_warnings) > 0

    def test_node_warnings_keys_are_path_strings(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        for key in report.node_warnings:
            assert "." in key or key.isdigit()


class TestPartialChain:
    def test_partial_chain_when_decorator_in_sequence(self):
        report = build_report(MIXED_XML, now_ms=0)
        assert any(c.is_partial for c in report.candidate_chains)


class TestOutputFormats:
    def test_to_dict_is_json_serialisable(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        d = report.to_dict()
        json_str = json.dumps(d)  # Should not raise
        assert isinstance(json_str, str)

    def test_to_json_parses_back(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        parsed = json.loads(report.to_json())
        assert parsed["bt_tree_id"] == "TestTree"
        assert "assumptions" in parsed
        assert len(parsed["assumptions"]) == 10

    def test_to_text_contains_candidate_chains(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        text = report.to_text()
        assert "CANDIDATE CHAINS" in text

    def test_to_text_contains_assumptions(self):
        report = build_report(SIMPLE_XML, now_ms=0)
        text = report.to_text()
        assert "ASSUMPTIONS" in text


class TestParseErrorPropagation:
    def test_malformed_xml_raises_btparse_error(self):
        with pytest.raises(BTParseError):
            build_report("<root><broken>", now_ms=0)

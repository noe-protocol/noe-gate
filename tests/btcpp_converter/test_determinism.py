"""
tests/btcpp_converter/test_determinism.py

Determinism tests: identical input must always produce identical output.

This covers the contract requirement: "Generated outputs must be deterministic."
"""
import json
import pytest
from packages.btcpp_converter.report import build_report

COMPLEX_XML = """\
<root>
  <BehaviorTree ID="DeterminismTest">
    <Sequence>
      <IsPathClear/>
      <IsHumanClear/>
      <Fallback>
        <Sequence><IsPrimary/><DoPrimary/></Sequence>
        <Sequence><IsBackup/><DoBackup/></Sequence>
      </Fallback>
      <MoveForward/>
    </Sequence>
  </BehaviorTree>
</root>"""


class TestDeterministicOutput:
    """Run the same conversion twice and assert byte-identical outputs."""

    def test_candidate_chains_identical(self):
        r1 = build_report(COMPLEX_XML, now_ms=0)
        r2 = build_report(COMPLEX_XML, now_ms=0)
        chains1 = [c.chain for c in r1.candidate_chains]
        chains2 = [c.chain for c in r2.candidate_chains]
        assert chains1 == chains2

    def test_placeholder_registry_identical(self):
        r1 = build_report(COMPLEX_XML, now_ms=0)
        r2 = build_report(COMPLEX_XML, now_ms=0)
        assert r1.placeholder_registry == r2.placeholder_registry

    def test_report_json_identical(self):
        r1 = build_report(COMPLEX_XML, now_ms=0)
        r2 = build_report(COMPLEX_XML, now_ms=0)
        assert r1.to_json() == r2.to_json()

    def test_required_grounding_tasks_stable_order(self):
        r1 = build_report(COMPLEX_XML, now_ms=0)
        r2 = build_report(COMPLEX_XML, now_ms=0)
        assert r1.required_grounding_tasks == r2.required_grounding_tasks

    def test_unsupported_nodes_stable_order(self):
        r1 = build_report(COMPLEX_XML, now_ms=0)
        r2 = build_report(COMPLEX_XML, now_ms=0)
        paths1 = [u.node_path for u in r1.unsupported_nodes]
        paths2 = [u.node_path for u in r2.unsupported_nodes]
        assert paths1 == paths2

    def test_placeholder_registry_sorted_by_type_then_id(self):
        """Registry must be sorted by (type, noe_id) for determinism."""
        report = build_report(COMPLEX_XML, now_ms=0)
        entries = report.placeholder_registry
        keys = [(e["type"], e["noe_id"]) for e in entries]
        assert keys == sorted(keys)

    def test_node_warnings_keys_stable(self):
        r1 = build_report(COMPLEX_XML, now_ms=0)
        r2 = build_report(COMPLEX_XML, now_ms=0)
        assert list(r1.node_warnings.keys()) == list(r2.node_warnings.keys())

    def test_chain_string_stable_across_ten_runs(self):
        """Run 10 times to catch any accidental set/dict ordering dependencies."""
        results = [
            [c.chain for c in build_report(COMPLEX_XML, now_ms=0).candidate_chains]
            for _ in range(10)
        ]
        for run in results[1:]:
            assert run == results[0]

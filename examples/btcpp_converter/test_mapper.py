"""
tests/btcpp_converter/test_mapper.py — Mapper unit tests.
"""
import pytest
from packages.btcpp_converter.parser import parse_bt_xml
from packages.btcpp_converter.mapper import (
    map_tree, MappedNode, MappedTree,
    STATUS_MAPPED, STATUS_UNSUPPORTED, STATUS_NEEDS_GROUNDING, STATUS_NEEDS_EXPANSION,
    normalise_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_mapped_root(xml: str) -> MappedNode:
    trees = parse_bt_xml(xml)
    return map_tree(trees[0]).root


# ---------------------------------------------------------------------------
# normalise_id
# ---------------------------------------------------------------------------

class TestNormaliseId:
    def test_pascal_to_snake(self):
        assert normalise_id("IsPathClear") == "is_path_clear"

    def test_camel_to_snake(self):
        assert normalise_id("moveToGoal") == "move_to_goal"

    def test_already_snake(self):
        assert normalise_id("clear_path") == "clear_path"

    def test_all_caps_acronym(self):
        assert normalise_id("IsBatteryOK") == "is_battery_ok"

    def test_strips_special_chars(self):
        result = normalise_id("Is-Path/Clear")
        assert "-" not in result
        assert "/" not in result

    def test_empty_string_returns_unknown(self):
        assert normalise_id("") == "unknown"


# ---------------------------------------------------------------------------
# Sequence → MAPPED
# ---------------------------------------------------------------------------

SEQ_XML = """\
<root><BehaviorTree ID="T">
  <Sequence>
    <IsPathClear/>
    <MoveForward/>
  </Sequence>
</BehaviorTree></root>"""


class TestSequenceMapping:
    def test_sequence_status_mapped(self):
        root = get_mapped_root(SEQ_XML)
        assert root.mapping_status == STATUS_MAPPED

    def test_sequence_children_mapped(self):
        root = get_mapped_root(SEQ_XML)
        assert len(root.children) == 2

    def test_condition_child_needs_grounding(self):
        root = get_mapped_root(SEQ_XML)
        cond = root.children[0]
        assert cond.mapping_status == STATUS_NEEDS_GROUNDING

    def test_condition_uses_shi_operator(self):
        root = get_mapped_root(SEQ_XML)
        assert root.children[0].noe_operator == "shi"

    def test_condition_no_automatic_vek(self):
        """v1 policy: no vek ever emitted automatically."""
        root = get_mapped_root(SEQ_XML)
        assert root.children[0].noe_operator != "vek"

    def test_action_child_needs_grounding(self):
        root = get_mapped_root(SEQ_XML)
        action = root.children[1]
        assert action.mapping_status == STATUS_NEEDS_GROUNDING

    def test_action_uses_mos_operator(self):
        root = get_mapped_root(SEQ_XML)
        assert root.children[1].noe_operator == "mos"

    def test_action_canonicalization_required(self):
        root = get_mapped_root(SEQ_XML)
        assert root.children[1].manual_canonicalization_required is True


# ---------------------------------------------------------------------------
# Fallback → MAPPED with priority-loss warning
# ---------------------------------------------------------------------------

FALLBACK_XML = """\
<root><BehaviorTree ID="T">
  <Fallback>
    <IsPrimary/>
    <IsBackup/>
  </Fallback>
</BehaviorTree></root>"""


class TestFallbackMapping:
    def test_fallback_status_mapped(self):
        root = get_mapped_root(FALLBACK_XML)
        assert root.mapping_status == STATUS_MAPPED

    def test_fallback_has_priority_loss_warning(self):
        root = get_mapped_root(FALLBACK_XML)
        assert any("FALLBACK_PRIORITY_LOSS" in w for w in root.warnings)

    def test_fallback_warning_contains_lost_semantics(self):
        root = get_mapped_root(FALLBACK_XML)
        warning_text = " ".join(root.warnings)
        assert "branch priority" in warning_text
        assert "short-circuit" in warning_text


# ---------------------------------------------------------------------------
# Decorator → UNSUPPORTED
# ---------------------------------------------------------------------------

DECORATOR_XML = """\
<root><BehaviorTree ID="T">
  <Retry num_attempts="3"><DoSomething/></Retry>
</BehaviorTree></root>"""


class TestDecoratorMapping:
    def test_decorator_unsupported(self):
        root = get_mapped_root(DECORATOR_XML)
        assert root.mapping_status == STATUS_UNSUPPORTED

    def test_decorator_has_reason_in_warnings(self):
        root = get_mapped_root(DECORATOR_XML)
        assert any("UNSUPPORTED" in w for w in root.warnings)

    def test_decorator_reason_mentions_decorator_tag(self):
        root = get_mapped_root(DECORATOR_XML)
        assert any("Retry" in w for w in root.warnings)


# ---------------------------------------------------------------------------
# Parallel → UNSUPPORTED
# ---------------------------------------------------------------------------

PARALLEL_XML = """\
<root><BehaviorTree ID="T">
  <Parallel failure_threshold="1" success_threshold="1">
    <TaskA/><TaskB/>
  </Parallel>
</BehaviorTree></root>"""


class TestParallelMapping:
    def test_parallel_unsupported(self):
        root = get_mapped_root(PARALLEL_XML)
        assert root.mapping_status == STATUS_UNSUPPORTED

    def test_parallel_warning_mentions_tick(self):
        root = get_mapped_root(PARALLEL_XML)
        assert any("tick" in w.lower() or "Parallel" in w for w in root.warnings)


# ---------------------------------------------------------------------------
# ReactiveSequence → UNSUPPORTED
# ---------------------------------------------------------------------------

REACTIVE_XML = """\
<root><BehaviorTree ID="T">
  <ReactiveSequence><Guard/><Act/></ReactiveSequence>
</BehaviorTree></root>"""


class TestReactiveMapping:
    def test_reactive_sequence_unsupported(self):
        root = get_mapped_root(REACTIVE_XML)
        assert root.mapping_status == STATUS_UNSUPPORTED


# ---------------------------------------------------------------------------
# Safety-relevant heuristic (report-only, never influences chain)
# ---------------------------------------------------------------------------

SAFETY_XML = """\
<root><BehaviorTree ID="T">
  <Sequence>
    <IsEmergencyStopClear/>
    <IsHumanProximityOK/>
    <MoveForward/>
  </Sequence>
</BehaviorTree></root>"""


class TestSafetyRelevant:
    def test_emergency_stop_flagged(self):
        root = get_mapped_root(SAFETY_XML)
        child = root.children[0]
        assert child.is_safety_relevant is True

    def test_human_proximity_flagged(self):
        root = get_mapped_root(SAFETY_XML)
        child = root.children[1]
        assert child.is_safety_relevant is True

    def test_move_forward_not_flagged(self):
        root = get_mapped_root(SAFETY_XML)
        child = root.children[2]
        assert child.is_safety_relevant is False


# ---------------------------------------------------------------------------
# Uncertainty hint → warning only, no vek
# ---------------------------------------------------------------------------

UNCERTAINTY_XML = """\
<root><BehaviorTree ID="T">
  <Sequence><IsEstimatedPathClear/><MoveForward/></Sequence>
</BehaviorTree></root>"""


class TestUncertaintyHint:
    def test_condition_with_estimate_still_uses_shi(self):
        root = get_mapped_root(UNCERTAINTY_XML)
        cond = root.children[0]
        assert cond.noe_operator == "shi"

    def test_condition_with_estimate_generates_review_warning(self):
        root = get_mapped_root(UNCERTAINTY_XML)
        cond = root.children[0]
        assert any("REVIEW" in w for w in cond.warnings)

    def test_review_warning_mentions_vek(self):
        root = get_mapped_root(UNCERTAINTY_XML)
        cond = root.children[0]
        assert any("vek" in w for w in cond.warnings)


# ---------------------------------------------------------------------------
# NEEDS_GROUNDING wording in condition/action warnings
# ---------------------------------------------------------------------------

class TestNeedsGroundingWarnings:
    def test_condition_warning_contains_needs_grounding(self):
        root = get_mapped_root(SEQ_XML)
        cond = root.children[0]
        assert any("NEEDS_GROUNDING" in w for w in cond.warnings)

    def test_action_warning_contains_needs_grounding(self):
        root = get_mapped_root(SEQ_XML)
        action = root.children[1]
        assert any("NEEDS_GROUNDING" in w for w in action.warnings)

    def test_action_warning_contains_canonicalization(self):
        root = get_mapped_root(SEQ_XML)
        action = root.children[1]
        assert any("CANONICALIZATION_REQUIRED" in w for w in action.warnings)

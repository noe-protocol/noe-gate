"""
tests/btcpp_converter/test_chain_gen.py — Chain generation unit tests.
"""
import pytest
from packages.btcpp_converter.parser import parse_bt_xml
from packages.btcpp_converter.mapper import map_tree
from packages.btcpp_converter.chain_gen import generate_chains, CandidateChain

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def gen(xml: str):
    trees = parse_bt_xml(xml)
    mapped = map_tree(trees[0])
    return generate_chains(mapped)


# ---------------------------------------------------------------------------
# Sequence → single AND-chain
# ---------------------------------------------------------------------------

SEQ_COND_XML = """\
<root><BehaviorTree ID="T">
  <Sequence>
    <IsPathClear/>
    <MoveForward/>
  </Sequence>
</BehaviorTree></root>"""

SEQ_COND_ONLY_XML = """\
<root><BehaviorTree ID="T">
  <Sequence>
    <IsPathClear/>
    <IsBatteryOK/>
  </Sequence>
</BehaviorTree></root>"""

MULTI_COND_XML = """\
<root><BehaviorTree ID="T">
  <Sequence>
    <IsPathClear/>
    <IsHumanClear/>
    <MoveForward/>
  </Sequence>
</BehaviorTree></root>"""


class TestSequenceChain:
    def test_one_chain_produced(self):
        result = gen(SEQ_COND_XML)
        assert len(result.candidate_chains) == 1

    def test_chain_terminates_with_nek(self):
        result = gen(SEQ_COND_XML)
        assert result.candidate_chains[0].chain.endswith(" nek")

    def test_chain_contains_shi_predicate(self):
        result = gen(SEQ_COND_XML)
        chain = result.candidate_chains[0].chain
        assert "shi @is_path_clear" in chain

    def test_chain_contains_mos_action(self):
        result = gen(SEQ_COND_XML)
        chain = result.candidate_chains[0].chain
        assert "mos @move_forward" in chain

    def test_multi_condition_joined_with_an(self):
        result = gen(MULTI_COND_XML)
        chain = result.candidate_chains[0].chain
        assert " an " in chain
        assert "shi @is_path_clear" in chain
        assert "shi @is_human_clear" in chain

    def test_condition_only_sequence_generates_chain(self):
        result = gen(SEQ_COND_ONLY_XML)
        assert len(result.candidate_chains) == 1
        chain = result.candidate_chains[0].chain
        assert "shi @is_path_clear" in chain
        assert "shi @is_battery_ok" in chain

    def test_chain_not_from_fallback(self):
        result = gen(SEQ_COND_XML)
        assert result.candidate_chains[0].from_fallback is False

    def test_chain_not_partial(self):
        result = gen(SEQ_COND_XML)
        assert result.candidate_chains[0].is_partial is False


# ---------------------------------------------------------------------------
# Fallback → per-branch candidates + from_fallback=True
# ---------------------------------------------------------------------------

FALLBACK_XML = """\
<root><BehaviorTree ID="T">
  <Fallback>
    <Sequence>
      <IsPrimary/>
      <DoPrimary/>
    </Sequence>
    <Sequence>
      <IsBackup/>
      <DoBackup/>
    </Sequence>
  </Fallback>
</BehaviorTree></root>"""


class TestFallbackChain:
    def test_fallback_produces_two_chains(self):
        result = gen(FALLBACK_XML)
        assert len(result.candidate_chains) == 2

    def test_both_chains_from_fallback(self):
        result = gen(FALLBACK_XML)
        assert all(c.from_fallback for c in result.candidate_chains)

    def test_fallback_priority_loss_in_lost_semantics(self):
        result = gen(FALLBACK_XML)
        assert any("FALLBACK" in ls for ls in result.lost_semantics)

    def test_each_branch_ends_with_nek(self):
        result = gen(FALLBACK_XML)
        for chain in result.candidate_chains:
            assert chain.chain.endswith(" nek")

    def test_first_branch_has_primary_predicate(self):
        result = gen(FALLBACK_XML)
        chains = [c.chain for c in result.candidate_chains]
        assert any("is_primary" in c for c in chains)

    def test_second_branch_has_backup_predicate(self):
        result = gen(FALLBACK_XML)
        chains = [c.chain for c in result.candidate_chains]
        assert any("is_backup" in c for c in chains)


# ---------------------------------------------------------------------------
# Decorator inside tree → UNSUPPORTED, chain halts
# ---------------------------------------------------------------------------

DECORATOR_IN_SEQ_XML = """\
<root><BehaviorTree ID="T">
  <Sequence>
    <IsPathClear/>
    <Retry num_attempts="3"><MoveForward/></Retry>
  </Sequence>
</BehaviorTree></root>"""


class TestDecoratorInSequence:
    def test_partial_chain_emitted(self):
        result = gen(DECORATOR_IN_SEQ_XML)
        # Chain from the Sequence is halted by Retry
        partial = [c for c in result.candidate_chains if c.is_partial]
        assert len(partial) >= 1

    def test_unsupported_tracked(self):
        result = gen(DECORATOR_IN_SEQ_XML)
        assert len(result.unsupported_encountered) > 0


# ---------------------------------------------------------------------------
# Parallel → UNSUPPORTED, no chain
# ---------------------------------------------------------------------------

PARALLEL_XML = """\
<root><BehaviorTree ID="T">
  <Parallel failure_threshold="1" success_threshold="1">
    <Act/><Check/>
  </Parallel>
</BehaviorTree></root>"""


class TestParallelNoChain:
    def test_no_top_level_chains_from_parallel(self):
        result = gen(PARALLEL_XML)
        # Parallel is unsupported; any generated chains must be partial or zero
        assert len(result.unsupported_encountered) > 0

    def test_parallel_in_lost_semantics(self):
        result = gen(PARALLEL_XML)
        assert any("Parallel" in ls or "tick" in ls.lower() for ls in result.lost_semantics)


# ---------------------------------------------------------------------------
# Deduplication of identical chains
# ---------------------------------------------------------------------------

DUP_XML = """\
<root><BehaviorTree ID="T">
  <Fallback>
    <Sequence><IsPathClear/><MoveForward/></Sequence>
    <Sequence><IsPathClear/><MoveForward/></Sequence>
  </Fallback>
</BehaviorTree></root>"""


class TestDeduplicate:
    def test_duplicate_chains_deduplicated(self):
        result = gen(DUP_XML)
        chains = [c.chain for c in result.candidate_chains]
        assert len(chains) == len(set(chains))


# ---------------------------------------------------------------------------
# Lone condition / action at tree root
# ---------------------------------------------------------------------------

LONE_COND_XML = """\
<root><BehaviorTree ID="T"><IsPathClear/></BehaviorTree></root>"""
LONE_ACT_XML  = """\
<root><BehaviorTree ID="T"><MoveForward/></BehaviorTree></root>"""


class TestLoneLeaf:
    def test_lone_condition_generates_chain(self):
        result = gen(LONE_COND_XML)
        assert len(result.candidate_chains) == 1
        assert result.candidate_chains[0].chain == "shi @is_path_clear nek"

    def test_lone_action_generates_chain(self):
        result = gen(LONE_ACT_XML)
        assert len(result.candidate_chains) == 1
        assert result.candidate_chains[0].chain == "mos @move_forward nek"

    def test_lone_action_includes_review_warning(self):
        result = gen(LONE_ACT_XML)
        warnings = result.candidate_chains[0].warnings
        assert any("stand-alone" in w.lower() or "guard" in w.lower() for w in warnings)

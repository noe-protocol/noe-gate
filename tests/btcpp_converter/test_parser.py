"""
tests/btcpp_converter/test_parser.py — Parser unit tests.
"""
import pytest
from packages.btcpp_converter.parser import (
    BTNode, BTTree, BTParseError, parse_bt_xml,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_XML = """\
<root>
  <BehaviorTree ID="TestTree">
    <Sequence>
      <IsPathClear name="check_path"/>
      <MoveForward name="move"/>
    </Sequence>
  </BehaviorTree>
</root>
"""

FALLBACK_XML = """\
<root>
  <BehaviorTree ID="FallbackTree">
    <Fallback>
      <IsPathClear/>
      <Recover/>
    </Fallback>
  </BehaviorTree>
</root>
"""

DECORATOR_XML = """\
<root>
  <BehaviorTree ID="DecoratorTree">
    <Retry num_attempts="3">
      <DoSomething/>
    </Retry>
  </BehaviorTree>
</root>
"""

PARALLEL_XML = """\
<root>
  <BehaviorTree ID="ParallelTree">
    <Parallel failure_threshold="1" success_threshold="1">
      <TaskA/>
      <TaskB/>
    </Parallel>
  </BehaviorTree>
</root>
"""

REACTIVE_XML = """\
<root>
  <BehaviorTree ID="ReactiveTree">
    <ReactiveSequence>
      <Guard/>
      <Act/>
    </ReactiveSequence>
  </BehaviorTree>
</root>
"""

MULTI_TREE_XML = """\
<root>
  <BehaviorTree ID="TreeA">
    <Sequence><Act/></Sequence>
  </BehaviorTree>
  <BehaviorTree ID="TreeB">
    <Fallback><Check/></Fallback>
  </BehaviorTree>
</root>
"""

PORT_XML = """\
<root>
  <BehaviorTree ID="PortTree">
    <Sequence>
      <CheckDistance distance="{target_dist}" threshold="2.0" name="check"/>
      <MoveArm speed="0.5"/>
    </Sequence>
  </BehaviorTree>
</root>
"""


class TestParseSuccess:
    def test_minimal_parse_returns_list(self):
        trees = parse_bt_xml(MINIMAL_XML)
        assert isinstance(trees, list)
        assert len(trees) == 1

    def test_tree_id(self):
        trees = parse_bt_xml(MINIMAL_XML)
        assert trees[0].tree_id == "TestTree"

    def test_root_is_sequence(self):
        trees = parse_bt_xml(MINIMAL_XML)
        root = trees[0].root
        assert root.node_kind == "Control"
        assert root.control_type == "Sequence"

    def test_sequence_has_two_children(self):
        trees = parse_bt_xml(MINIMAL_XML)
        root = trees[0].root
        assert len(root.children) == 2

    def test_first_child_xml_tag(self):
        trees = parse_bt_xml(MINIMAL_XML)
        child = trees[0].root.children[0]
        assert child.xml_tag == "IsPathClear"

    def test_instance_name_captured(self):
        trees = parse_bt_xml(MINIMAL_XML)
        child = trees[0].root.children[0]
        assert child.instance_name == "check_path"

    def test_node_id_distinct_from_xml_tag(self):
        """node_id is from ID attr, not xml_tag. For leaf nodes without ID attr, it should be None."""
        trees = parse_bt_xml(MINIMAL_XML)
        child = trees[0].root.children[0]
        # IsPathClear has no ID attribute in this XML
        assert child.node_id is None
        assert child.xml_tag == "IsPathClear"

    def test_node_path_root_is_zero_tuple(self):
        trees = parse_bt_xml(MINIMAL_XML)
        root = trees[0].root
        assert root.node_path == (0,)

    def test_node_path_first_child(self):
        trees = parse_bt_xml(MINIMAL_XML)
        first_child = trees[0].root.children[0]
        assert first_child.node_path == (0, 0)

    def test_node_path_second_child(self):
        trees = parse_bt_xml(MINIMAL_XML)
        second_child = trees[0].root.children[1]
        assert second_child.node_path == (0, 1)

    def test_source_line_is_int_or_none(self):
        """source_line is best-effort. Must be int or None, never fabricated."""
        trees = parse_bt_xml(MINIMAL_XML)
        for node in _all_nodes(trees[0].root):
            assert node.source_line is None or isinstance(node.source_line, int)

    def test_fallback_classified(self):
        trees = parse_bt_xml(FALLBACK_XML)
        root = trees[0].root
        assert root.node_kind == "Control"
        assert root.control_type == "Fallback"

    def test_decorator_classified(self):
        trees = parse_bt_xml(DECORATOR_XML)
        root = trees[0].root
        assert root.node_kind == "Decorator"

    def test_parallel_classified(self):
        trees = parse_bt_xml(PARALLEL_XML)
        root = trees[0].root
        assert root.node_kind == "Control"
        assert root.control_type == "Parallel"

    def test_reactive_sequence_classified(self):
        trees = parse_bt_xml(REACTIVE_XML)
        root = trees[0].root
        assert root.node_kind == "Control"
        assert root.control_type == "ReactiveSequence"

    def test_multi_tree_returns_two_trees(self):
        trees = parse_bt_xml(MULTI_TREE_XML)
        assert len(trees) == 2
        assert trees[0].tree_id == "TreeA"
        assert trees[1].tree_id == "TreeB"

    def test_port_bindings_in_ports_dict(self):
        trees = parse_bt_xml(PORT_XML)
        root = trees[0].root
        check_node = root.children[0]
        # distance and threshold are port bindings (not ID or name)
        assert "distance" in check_node.ports or "threshold" in check_node.ports

    def test_bare_bt_tree_element_accepted(self):
        """A naked <BehaviorTree> without <root> wrapper should also parse."""
        bare = "<BehaviorTree ID=\"X\"><Sequence><Act/></Sequence></BehaviorTree>"
        trees = parse_bt_xml(bare)
        assert trees[0].tree_id == "X"


class TestParseErrors:
    def test_malformed_xml_raises_parse_error(self):
        with pytest.raises(BTParseError, match="ERR_PARSE"):
            parse_bt_xml("<root><BehaviorTree ID='T'><Sequence></root>")

    def test_empty_string_raises_parse_error(self):
        with pytest.raises(BTParseError, match="ERR_PARSE"):
            parse_bt_xml("")

    def test_whitespace_only_raises_parse_error(self):
        with pytest.raises(BTParseError, match="ERR_PARSE"):
            parse_bt_xml("   \n\t  ")

    def test_wrong_root_tag_raises_parse_error(self):
        with pytest.raises(BTParseError, match="ERR_PARSE"):
            parse_bt_xml("<NotABTree><Foo/></NotABTree>")

    def test_empty_bt_element_raises_parse_error(self):
        with pytest.raises(BTParseError, match="ERR_PARSE"):
            parse_bt_xml("<root><BehaviorTree ID='T'/></root>")

    def test_no_bt_elements_raises_parse_error(self):
        with pytest.raises(BTParseError, match="ERR_PARSE"):
            parse_bt_xml("<root></root>")


class TestDisplayName:
    def test_display_name_simple(self):
        trees = parse_bt_xml(MINIMAL_XML)
        child = trees[0].root.children[0]
        name = child.display_name
        assert "IsPathClear" in name

    def test_path_str(self):
        trees = parse_bt_xml(MINIMAL_XML)
        child = trees[0].root.children[1]
        assert child.path_str == "0.1"


# Helpers

def _all_nodes(node: BTNode):
    yield node
    for c in node.children:
        yield from _all_nodes(c)

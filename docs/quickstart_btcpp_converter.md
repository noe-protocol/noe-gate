# Quickstart: BT.CPP-to-Noe Converter

This converter estimates migration effort for ROS2 / BehaviorTree.CPP projects.
It exports candidate Noe chains, placeholder registry entries, and a structured
conversion report from an existing BT.CPP XML tree.

> **This is a migration-effort estimator, not a semantic translator.**
> All generated chains are candidates. All grounding remains manual.

## Installation

```bash
pip install noe-runtime
```

## Running the converter

### CLI

```bash
# Plain-text report (default)
noe-btcpp-convert examples/btcpp_converter/patrol_robot.xml

# JSON report
noe-btcpp-convert examples/btcpp_converter/patrol_robot.xml --json

# Write to file
noe-btcpp-convert patrol.xml --json --out patrol_report.json
```

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | At least one complete candidate chain produced |
| 2 | All chains are partial/incomplete |
| 3 | No chains generated (all nodes unsupported) |
| 7 | Parse error (malformed XML or missing file) |

### Python API

```python
from packages.btcpp_converter.report import build_report

xml = open("patrol_robot.xml").read()
report = build_report(xml, source_file="patrol_robot.xml")

# Candidate chains
for chain in report.candidate_chains:
    print(chain.chain)
    if chain.from_fallback:
        print("  [FALLBACK BRANCH — priority not preserved]")

# Placeholder registry
for entry in report.placeholder_registry:
    print(entry["type"], entry["noe_id"], "←", entry["bt_sources"])

# Report as JSON
print(report.to_json())
```

## What the converter does

Given a BT.CPP XML tree:

```xml
<root>
  <BehaviorTree ID="PatrolMission">
    <Sequence>
      <IsPathClear/>
      <IsHumanProximityOK/>
      <MoveAlongRoute/>
    </Sequence>
  </BehaviorTree>
</root>
```

The converter produces a **candidate Noe chain**:

```
shi @is_path_clear an shi @is_human_proximity_ok an mos @move_along_route nek
```

…and **placeholder registry entries** for each predicate and action:

```json
[
  {"noe_id": "is_human_proximity_ok", "type": "condition", "status": "NEEDS_GROUNDING", ...},
  {"noe_id": "is_path_clear",         "type": "condition", "status": "NEEDS_GROUNDING", ...},
  {"noe_id": "move_along_route",      "type": "action",    "status": "NEEDS_GROUNDING",
   "manual_canonicalization_required": true, ...}
]
```

## Mapping rules (v1)

| BT.CPP construct | Noe output |
|---|---|
| `Sequence` | `shi @cond_1 an … mos @action nek` |
| `Fallback` | One candidate chain per branch + **priority-loss warning** |
| Condition leaf | `shi @<predicate>` placeholder |
| Action leaf | `mos @<action>` placeholder (canonicalization required) |
| Decorator (Inverter, Retry…) | **UNSUPPORTED** — explicit report entry |
| Parallel, ReactiveSequence | **UNSUPPORTED** in v1 |
| SubTree (locally defined) | Expanded inline with provenance record |
| SubTree (missing) | NEEDS_GROUNDING |

## Leaf node classification

BT.CPP XML does not encode whether a leaf is a Condition or Action —
that information lives in the C++ class hierarchy. The converter uses a
**name-prefix heuristic**:

- Tags starting with `Is`, `Has`, `Can`, `Check`, `Are`, `Was`, `Will`, `Should` → **Condition**
- All other leaf tags → **Action**

Every heuristically-classified node includes an explicit `HEURISTIC: …` warning
in the report. Verify before deployment.

## What requires manual work

After conversion, you must:

1. **Implement grounding adapters** for every `NEEDS_GROUNDING` predicate and action.
   See `packages/grounding/` for reference implementations.

2. **Rename action identifiers** — all action names are snake_case-normalised BT node
   IDs and are NOT canonical Noe action names. Rename after semantic review.

3. **Compose Fallback alternatives** — Fallback branches are generated as separate
   candidate chains. Noe has no OR-composition operator. You must decide the
   composition policy.

4. **Rewrite UNSUPPORTED constructs** — Decorators and Parallel nodes must be
   manually decomposed into equivalent Noe policy logic.

## Report fields

| Field | Description |
|---|---|
| `candidate_chains` | Generated Noe chains (complete or partial) |
| `placeholder_registry` | All NEEDS_GROUNDING predicates/actions |
| `unsupported_nodes` | Nodes that could not be converted |
| `lost_semantics` | Semantics explicitly lost (Fallback priority, etc.) |
| `safety_relevant_conditions` | Keyword-flagged conditions (report-only) |
| `manual_canonicalization_required` | Action identifiers requiring renaming |
| `node_warnings` | Per-node warnings keyed by `node_path` |
| `assumptions` | 10 always-emitted converter assumptions |

## See also

- [`examples/btcpp_converter/patrol_robot.xml`](../examples/btcpp_converter/patrol_robot.xml) — worked example
- [`examples/btcpp_converter/run_convert.py`](../examples/btcpp_converter/run_convert.py) — example runner
- [`docs/implementation_contracts.md`](implementation_contracts.md) — normative contracts
- [`docs/quickstart_llm_governance.md`](quickstart_llm_governance.md) — Noe runtime usage

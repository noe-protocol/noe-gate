# Quickstart: LLM Action Gating with Noe

## What this shows

An LLM proposes an action. Noe evaluates whether the current grounded context
permits it. If the grounded sensor state contradicts the policy chain, the
action is vetoed — deterministically, replayably, with no ambiguity.

## Installation

```bash
pip install noe-runtime
```

## The scenario

A warehouse robot's LLM planner proposes: **move to exit A**.

Policy chain (written once by the system designer):

```
shi @clear_path an nai nel @human mos @exit_a nek
```

In plain English:
- `shi @clear_path` — Is path-clear in the knowledge modal?
- `an` — AND
- `nai nel @human` — NOT near a human?
- `mos @exit_a` — If yes, execute: move to exit A

## Minimal working example

```python
from noe import NoeRuntime, ContextManager

# ------------------------------------------------------------------
# Build context from grounded sensor readings.
# In production, populate with outputs from packages.grounding.*.
# Here we use a static snapshot for illustration.
# ------------------------------------------------------------------
C_local = {
    "literals": {
        "clear_path": True,
        "human":       False,
    },
    "modal": {
        "knowledge": {
            "clear_path": True,
            "human":       False,
        },
        "belief": {},
    },
    "temporal": {
        "now":        1_741_234_567_890,
        "max_skew_ms": 500,
    },
    "spatial": {
        "thresholds": {
            "near": 2_000_000,  # 2 m in micrometres
        }
    },
}

# ------------------------------------------------------------------
# Build the runtime with this context
# ------------------------------------------------------------------
cm = ContextManager(root={}, domain={}, local=C_local, staleness_ms=60_000)
runtime = NoeRuntime(context_manager=cm, strict_mode=False)

# ------------------------------------------------------------------
# Evaluate the LLM-proposed action chain
# ------------------------------------------------------------------
chain = "shi @clear_path an nai nel @human mos @exit_a nek"
result = runtime.evaluate(chain)

# ------------------------------------------------------------------
# Gate: only execute if domain is "action"
# ------------------------------------------------------------------
if result.domain == "action":
    print(f"PERMIT — executing: {result.value}")
    # → PERMIT — executing: {'operator': 'mos', 'target': 'exit_a'}
elif result.domain == "undefined":
    print("VETO — context knowledge is insufficient (non-execution)")
elif result.domain == "truth":
    print(f"TRUTH — no action, result={result.value}")
elif result.domain == "error":
    print(f"ERROR — {result.error}")
else:
    print(f"VETO — unexpected domain: {result.domain}")
```

## Veto example — human detected nearby

Change the context so a human is nearby:

```python
C_local["literals"]["human"] = True
C_local["modal"]["knowledge"]["human"] = True

# Re-build context and runtime
cm = ContextManager(root={}, domain={}, local=C_local, staleness_ms=60_000)
runtime = NoeRuntime(context_manager=cm, strict_mode=False)
result = runtime.evaluate(chain)

print(result.domain)  # → "truth" (False), because the AND clause fails
# The mos @exit_a action is not reached — movement is vetoed
```

## Where grounded sensor readings come from

```python
from packages.grounding.lidar_zone.ground import ground_lidar_zone, LidarZoneInput
from packages.grounding.common.context_entry import make_context_entry, insert_into_c_local

inp = LidarZoneInput(
    raw_decimal_m="2.5",         # 2.5 m reading from LiDAR (decimal string — no floats)
    zone_threshold_um=1_500_000, # 1.5 m threshold
    predicate="clear_path",
    confidence=0.95,             # consumed internally; never stored as float
    timestamp_ms=1_741_234_567_890,
    now_ms=1_741_234_567_940,
    debounce_state="stable",
)
grounding_result = ground_lidar_zone(inp)

# If admitted, insert into C_local
if grounding_result.admission_status == "emitted":
    entry = make_context_entry(grounding_result)
    c_local = {}
    insert_into_c_local(entry, c_local)
    # c_local now has literals, modal, and evidence populated
```

## Why this is deterministic

- Sensor readings are quantised to `int64` (no floats in canonical context)
- The same grounded context always produces the same evaluation
- Every decision is replayable — see `docs/quickstart_replay_audit.md`

## Entry points reference

| Symbol | What it does |
|---|---|
| `NoeRuntime.evaluate(chain)` | Evaluate a Noe policy chain |
| `ContextManager` | Merge `C_root`, `C_domain`, `C_local` into `C_safe` |
| `ground_lidar_zone(inp)` | Full 7-stage LiDAR grounding pipeline |
| `ground_camera_human(inp)` | Full 7-stage camera grounding pipeline |
| `make_context_entry(result)` | Convert `GroundingResult` → `context_entry` |
| `insert_into_c_local(entry, c_local)` | Write entry into `C_local` dict |

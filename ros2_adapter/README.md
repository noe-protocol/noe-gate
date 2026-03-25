# noe_ros2_adapter

Thin ROS2 lifecycle node adapter for the Noe core runtime.

Evaluates Noe truth-query chains via the Rust C FFI boundary (`noe::evaluate()`), emits permit/block decisions over ROS2 topics, and writes an append-only JSONL decision log.

> **Scope**: v1 covers one scenario — mobile robot zone entry — using the chain `shi @human_present nek`. The adapter calls Noe for the truth query; the policy decision (PERMITTED/BLOCKED) is made by the adapter based on the result.

<br />

## Validated on target (2026-03-25)

**Environment:** Ubuntu 22.04.5 LTS · ARM64 · ROS2 Humble

**Validated flow:**
```bash
colcon build --packages-select noe_ros2_adapter --cmake-args \
    -DNOE_CORE_LIB_DIR=$HOME/noe_reference/rust/noe_core/target/debug \
    -DNOE_CORE_INCLUDE_DIR=$HOME/noe_reference/rust/noe_core/include \
    -DNOE_CPP_INCLUDE_DIR=$HOME/noe_reference/rust/noe_core/cpp

ros2 launch noe_ros2_adapter mobile_robot_zone_entry.launch.py
# (separate terminal — after node reaches ACTIVE ~4s)
python3 examples/mobile_robot_zone_entry/publish_scenario.py
```

**Results:**
| Scenario | human_present | /noe/permitted | Decision | Result |
|----------|--------------|----------------|----------|--------|
| 1 | `true` | `false` | BLOCKED | **PASS** |
| 2 | `false` | `true` | PERMITTED | **PASS** |

**Honest scope:**
- Validated by manual run in VM — not yet integrated into CI
- Long-running load robustness is future work
- Build fixes applied on target: `now_ms() const` (Humble clock), `on_deactivate` teardown ordering, launch YAML path resolution

<br />

## Prerequisites

| Requirement | Version / source |
|-------------|------------------|
| ROS2 | Humble (validated on Ubuntu 22.04.5 ARM64) |
| Rust / cargo | stable (1.75+) |
| `nlohmann-json3-dev` | Ubuntu package (`sudo apt install nlohmann-json3-dev`) |
| colcon | `sudo apt install python3-colcon-common-extensions` |

<br />

## Building and verifying with Docker (optional convenience path)

The adapter targets Ubuntu 22.04 + ROS2 Humble. On macOS or any non-Ubuntu
machine, use Docker. A `Dockerfile` and `docker_verify.sh` are included.

The primary validated path is native Ubuntu 22.04.5 ARM64 / ROS2 Humble.

**One-shot build + verify:**

```bash
# From repo root:
docker build -t noe_ros2_build -f ros2_adapter/Dockerfile .
docker run --rm noe_ros2_build
```

**Expected output:**

```
✅ PASS: BLOCKED (expected)          ← human_present=true
✅ PASS: PERMITTED (expected)        ← human_present=false
✅ PASS: cert log has expected records
Results: 3 passed, 0 failed
✅ ALL CHECKS PASSED — ROS2 adapter validated
```

**What the verification does:**
1. Starts `noe_gate_node` unconfigured
2. Drives `configure → active` via `ros2 lifecycle set`
3. Publishes `human_present=true` → asserts `/noe/permitted = false`
4. Publishes `human_present=false` → asserts `/noe/permitted = true`
5. Checks `decisions.jsonl` has ≥2 records

> **Note**: The build layer is cached after the first run. Subsequent runs skip
> cargo build and colcon build unless source files change.

<br />

## Build Order (native Ubuntu 22.04 + ROS2 Humble)

**Step 1: Build the Rust library first.**
```bash
cd <repo_root>/rust/noe_core
cargo build
```

This produces `target/debug/libnoe_core.a`. CMakeLists.txt will fail loudly
with a clear message if this file is not present.

<br />

**Step 2: Build the ROS2 package.**
```bash
cd <repo_root>/ros2_adapter
source /opt/ros/humble/setup.bash
colcon build --packages-select noe_ros2_adapter --cmake-args \
    -DNOE_CORE_LIB_DIR=$HOME/noe_reference/rust/noe_core/target/debug \
    -DNOE_CORE_INCLUDE_DIR=$HOME/noe_reference/rust/noe_core/include \
    -DNOE_CPP_INCLUDE_DIR=$HOME/noe_reference/rust/noe_core/cpp
source install/setup.bash
```

To use a non-default Rust build path, override the same variables explicitly:
```bash
colcon build --packages-select noe_ros2_adapter --cmake-args \
    -DNOE_CORE_LIB_DIR=/path/to/rust/target/release \
    -DNOE_CORE_INCLUDE_DIR=/path/to/rust/noe_core/include \
    -DNOE_CPP_INCLUDE_DIR=/path/to/rust/noe_core/cpp
```

<br />

## Run

<br />

**Terminal 1: Launch the node**
```bash
source <repo_root>/ros2_adapter/install/setup.bash
ros2 launch noe_ros2_adapter mobile_robot_zone_entry.launch.py
```

The launch file automatically configures and activates the lifecycle node.

You should see:
```
[noe_gate_node]: chain            : shi @human_present nek
[noe_gate_node]: mode             : strict
[noe_gate_node]: cert_store_path  : /tmp/noe_certs
[noe_gate_node]: max_sensor_age_ms: 5000
[noe_gate_node]: noe_core version : 0.1.0
[noe_gate_node]: cert log         : /tmp/noe_certs/decisions.jsonl
[noe_gate_node]: NoeGateNode configured.
[noe_gate_node]: NoeGateNode active. Listening on /noe/proposed_action.
```

<br />

**Terminal 2: Run the scenario**
```bash
python3 <repo_root>/ros2_adapter/examples/mobile_robot_zone_entry/publish_scenario.py
```

Expected output:
```
--- Scenario 1: human present  → expect BLOCKED (human_present=True) ---
[SENT] /noe/human_present = True
[SENT] /noe/proposed_action = 'enter_zone_alpha'
[RECEIVED] /noe/permitted = false  →  BLOCKED
[RECEIVED] /noe/decision: domain=truth value=True
[PASS] permitted=False (expected=False)

--- Scenario 2: human absent   → expect PERMITTED (human_present=False) ---
[SENT] /noe/human_present = False
[SENT] /noe/proposed_action = 'enter_zone_alpha'
[RECEIVED] /noe/permitted = true   →  PERMITTED
[RECEIVED] /noe/decision: domain=truth value=False
[PASS] permitted=True (expected=True)

=== Scenario run complete. Check /tmp/noe_certs/decisions.jsonl ===
```

<br />

**Terminal 2 (alternate): Manual topic commands**
```bash
# Set human present
ros2 topic pub --once /noe/human_present std_msgs/msg/Bool "data: true"

# Trigger evaluation
ros2 topic pub --once /noe/proposed_action std_msgs/msg/String "data: 'enter_zone_alpha'"

# Check result
ros2 topic echo /noe/permitted --once
ros2 topic echo /noe/decision --once
```

<br />

## Topics

| Topic | Type | Direction | Description |
|-------|------|-----------|-------------|
| `/noe/human_present` | `std_msgs/Bool` | Input | Grounded sensor: is a human known present? |
| `/noe/proposed_action` | `std_msgs/String` | Input | Trigger: proposed robot action (triggers evaluation) |
| `/noe/permitted` | `std_msgs/Bool` | Output | `true` = PERMITTED (zone clear), `false` = BLOCKED |
| `/noe/decision` | `std_msgs/String` | Output | Full Noe result envelope (JSON string) |

<br />

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `chain` | string | `"shi @human_present nek"` | Noe chain to evaluate |
| `mode` | string | `"strict"` | `"strict"` or `"partial"` |
| `cert_store_path` | string | `"/tmp/noe_certs"` | Directory for `decisions.jsonl` |
| `max_sensor_age_ms` | int64 | `5000` | Max sensor age before considered stale (ms) |

Override at launch:
```bash
ros2 launch noe_ros2_adapter mobile_robot_zone_entry.launch.py \
    cert_store_path:=/var/log/noe max_sensor_age_ms:=2000
```

<br />

## Adapter Decision Log

The adapter decision log is **not** the same format as the Python `cert_store.py` JSONL. It is a separate adapter log format and does not implement the Python persistence layer’s chained cert_id / prev_cert_id semantics.

Each evaluation appends one JSONL record to `{cert_store_path}/decisions.jsonl`.

Records are written for **all** evaluations — including stale-input and error cases. This is intentional: those are the cases auditors care about most.

Example record (pretty-printed for readability; stored as one compact line):
```json
{
  "format": "noe_decision_v1",
  "timestamp_ms": 1742214412000,
  "chain": "shi @human_present nek",
  "mode": "strict",
  "proposed_action": "enter_zone_alpha",
  "decision": "BLOCKED",
  "result": {
    "domain": "truth",
    "value": true,
    "meta": { "context_hash": "cf11c36...", "mode": "strict", ... }
  },
  "context_summary": {
    "human_present": true,
    "timestamp_ms": 1742214412000
  }
}
```

**v1 limitations**: The `format=noe_decision_v1` records are readable with standard JSON tools but are **not compatible with `noe.persistence.cli_audit`**, which expects chained `cert_id`/`prev_cert_id` fields. A schema bridge or native chained-cert writer is future work.

Inspect the log:
```bash
cat /tmp/noe_certs/decisions.jsonl | python3 -m json.tool --no-ensure-ascii
```

<br />

## Stale Sensor Handling

Adapter policy on stale sensor input:

- does not call Noe
- publishes `permitted=false` (BLOCKED, fail-safe)
- writes an auditable JSONL record with `ERR_STALE_SENSOR`

This is an adapter-layer policy choice, not a Noe core semantic.

<br />

## Architecture Position

```
/noe/human_present  ──┐
                       ├─→  NoeGateNode (lifecycle)
/noe/proposed_action ─┘       │ context builder (nlohmann/json)
                               │ noe::evaluate() ← FFI boundary
                               │ decision policy
                               │
                      /noe/permitted  (Bool)
                      /noe/decision   (String — full result JSON)
                      /tmp/noe_certs/decisions.jsonl  (append-only JSONL)
```

**What the node does not do:**
- Modify Noe semantics
- Bypass the Rust FFI boundary
- Make policy decisions other than `truth=false → PERMITTED, everything else → BLOCKED`

**What the node does not yet do:**
- Emit Python-compatible chained cert records (`cert_id`/`prev_cert_id` — future work)

<br />

## Regression Checks

After any change, verify:

```bash
# Rust conformance: must remain 93/93
cd <repo_root>/rust/noe_core && cargo test --test conformance -- --nocapture

# C smoke test
cd <repo_root> && make run-c-smoketest

# C++ smoke test
make run-cpp-smoketest

# Zone-entry C example
make run-zone-entry
```

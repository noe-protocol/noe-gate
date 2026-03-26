# noe_ros2_adapter

Thin ROS2 lifecycle node adapter for the Noe core runtime.

Evaluates Noe chains via the Rust C FFI boundary (`noe::evaluate()`), emits permit/block decisions over ROS2 topics, and writes an append-only JSONL certificate log.

> **Scope**: v1 covers one scenario - mobile robot zone entry - using the action chain `shi @zone_clear khi sek mek @enter_zone_alpha sek nek`. The adapter calls Noe to evaluate conditional action admissibility; the policy decision (PERMITTED/BLOCKED) is extracted directly from the emitted domain (action vs undefined).

<br />

## ROS2 adapter quick start

### Validated target

Validated on Ubuntu 22.04.5 ARM64 + ROS2 Humble.

These commands are intended to be pasted exactly as written on a fresh Ubuntu 22.04 + ROS2 Humble machine. They assume the repository is cloned to `~/noe-gate`.

This path assumes a fresh clone into `~/noe-gate`. If you clone elsewhere, replace that path consistently.

**Terminal 1: build and launch**


> If `~/noe-gate` already exists, either `cd ~/noe-gate && git pull` to update,
> or remove it manually before cloning fresh.

```bash
if [ -d ~/noe-gate/.git ]; then
  cd ~/noe-gate && git pull
else
  git clone https://github.com/noe-protocol/noe-gate.git ~/noe-gate
fi
export REPO_ROOT="$HOME/noe-gate"

cd "$REPO_ROOT/rust/noe_core"
cargo build
cd ../..

cd "$REPO_ROOT/ros2_adapter"
source /opt/ros/humble/setup.bash
colcon build --base-paths . --packages-select noe_ros2_adapter --cmake-args \
  -DNOE_CORE_LIB_DIR="$REPO_ROOT/rust/noe_core/target/debug" \
  -DNOE_CORE_INCLUDE_DIR="$REPO_ROOT/rust/noe_core/include" \
  -DNOE_CPP_INCLUDE_DIR="$REPO_ROOT/rust/noe_core/cpp"

source install/setup.bash
ros2 launch noe_ros2_adapter mobile_robot_zone_entry.launch.py
```

**Terminal 2: publish the worked scenario**


Open a second terminal and run:

```bash
export REPO_ROOT="$HOME/noe-gate"
cd "$REPO_ROOT/ros2_adapter"
source /opt/ros/humble/setup.bash
source install/setup.bash
python3 "$REPO_ROOT/ros2_adapter/examples/mobile_robot_zone_entry/publish_scenario.py"
```

### Expected result

You should see:
- zone blocked (@zone_clear=false) → blocked
- zone clear (@zone_clear=true) → permitted
- final result: ALL PASS

### What this path validates
- Rust core library builds
- ROS2 adapter builds against that library
- lifecycle node launches successfully
- scenario publisher drives both blocked and permitted cases
- decision log is written and replayable

### Common failure cases

If you see `No such file or directory` for `rust/noe_core` or `ros2_adapter`, you are not in the expected clone path.

If you see duplicate package errors from `colcon`, you likely ran it from your home directory or another parent directory containing multiple old checkouts. Run it only from:
```bash
cd "$HOME/noe-gate/ros2_adapter"
```

If `source install/setup.bash` fails, the ROS2 package did not build successfully yet.

<br />

## Prerequisites

| Requirement | Version |
|-------------|---------|
| ROS2 | Humble or Iron (Ubuntu 22.04 recommended) |
| Rust / cargo | stable (1.75+) |
| `nlohmann-json3-dev` | Ubuntu package |
| `libnlohmann-json3-dev` | `sudo apt install nlohmann-json3-dev` |
| colcon | `sudo apt install python3-colcon-common-extensions` |

<br />

## Building and verifying with Docker (recommended for macOS / CI)

The adapter targets Ubuntu 22.04 + ROS2 Humble. On macOS or any non-Ubuntu
machine, use Docker. A `Dockerfile` and `docker_verify.sh` are included.

**One-shot build + verify:**

```bash
# From repo root:
docker build -t noe_ros2_build -f ros2_adapter/Dockerfile .
docker run --rm noe_ros2_build
```

**Expected output:**

```
✅ PASS: BLOCKED (expected)          ← zone_clear=false
✅ PASS: PERMITTED (expected)        ← zone_clear=true
✅ PASS: cert log has expected records
Results: 3 passed, 0 failed
✅ ALL CHECKS PASSED — ROS2 adapter validated
```

**What the verification does:**
1. Starts `noe_gate_node` unconfigured
2. Drives `configure → active` via `ros2 lifecycle set`
3. Publishes `zone_clear=false` → asserts `/noe/permitted = false`
4. Publishes `zone_clear=true` → asserts `/noe/permitted = true`
5. Checks `decisions.jsonl` has ≥2 records

> **Note**: The build layer is cached after the first run. Subsequent runs skip
> cargo build and colcon build unless source files change.

<br />

## Build Order (native Ubuntu 22.04 + ROS2 Humble)

**Step 1: Build the Rust library first.**

```bash
export REPO_ROOT="$HOME/noe-gate"

cd "$REPO_ROOT/rust/noe_core"
cargo build

cd "$REPO_ROOT/ros2_adapter"
source /opt/ros/humble/setup.bash
colcon build --base-paths . --packages-select noe_ros2_adapter --cmake-args \
    -DNOE_CORE_LIB_DIR="$REPO_ROOT/rust/noe_core/target/debug" \
    -DNOE_CORE_INCLUDE_DIR="$REPO_ROOT/rust/noe_core/include" \
    -DNOE_CPP_INCLUDE_DIR="$REPO_ROOT/rust/noe_core/cpp"
source install/setup.bash
```

This produces `$REPO_ROOT/rust/noe_core/target/debug/libnoe_core.a`. CMakeLists.txt will fail loudly with a clear message if this file is not present.

To use a release build:
```bash
cargo build --release
colcon build --base-paths . --packages-select noe_ros2_adapter \
    --cmake-args -DNOE_CORE_LIB_DIR="$REPO_ROOT/rust/noe_core/target/release" \
    -DNOE_CORE_INCLUDE_DIR="$REPO_ROOT/rust/noe_core/include" \
    -DNOE_CPP_INCLUDE_DIR="$REPO_ROOT/rust/noe_core/cpp"
```

<br />

## Run

### Terminal 1: Launch the node

```bash
export REPO_ROOT="$HOME/noe-gate"

cd "$REPO_ROOT/ros2_adapter"
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch noe_ros2_adapter mobile_robot_zone_entry.launch.py
```

The launch file automatically configures and activates the lifecycle node.

You should see:
```
[noe_gate_node]: chain            : shi @zone_clear khi sek mek @enter_zone_alpha sek nek
[noe_gate_node]: mode             : strict
[noe_gate_node]: cert_store_path  : /tmp/noe_certs
[noe_gate_node]: max_sensor_age_ms: 5000
[noe_gate_node]: noe_core version : 0.1.0
[noe_gate_node]: cert log         : /tmp/noe_certs/decisions.jsonl
[noe_gate_node]: NoeGateNode configured.
[noe_gate_node]: NoeGateNode active. Listening on /noe/proposed_action.
```

### Terminal 2: Run the scenario

```bash
export REPO_ROOT="$HOME/noe-gate"

cd "$REPO_ROOT/ros2_adapter"
source /opt/ros/humble/setup.bash
source install/setup.bash
python3 "$REPO_ROOT/ros2_adapter/examples/mobile_robot_zone_entry/publish_scenario.py"
```

Expected output:
```
--- Scenario 1: zone blocked (@zone_clear=False) → expect BLOCKED ---
[SENT] /noe/zone_clear = False
[SENT] /noe/proposed_action = 'enter_zone_alpha'
[RECEIVED] /noe/permitted = false  →  BLOCKED (zone clearance not established)
[RECEIVED] /noe/decision: domain=undefined value=undefined
[PASS] permitted=False (expected=False)

--- Scenario 2: zone clear  (@zone_clear=True)  → expect PERMITTED ---
[SENT] /noe/zone_clear = True
[SENT] /noe/proposed_action = 'enter_zone_alpha'
[RECEIVED] /noe/permitted = true   →  PERMITTED (action emitted)
[RECEIVED] /noe/decision: domain=action value={"action_hash":...
[PASS] permitted=True (expected=True)

=== Scenario run complete. Check /tmp/noe_certs/decisions.jsonl ===
```

### Terminal 2 (alternate): Manual topic commands

```bash
# Zone is NOT clear (blocked)
ros2 topic pub --once /noe/zone_clear std_msgs/msg/Bool "data: false"

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
| `/noe/zone_clear` | `std_msgs/Bool` | Input | Grounded sensor: is the zone positively confirmed clear? |
| `/noe/proposed_action` | `std_msgs/String` | Input | Trigger: proposed robot action (triggers evaluation) |
| `/noe/permitted` | `std_msgs/Bool` | Output | `true` = PERMITTED (zone clear), `false` = BLOCKED |
| `/noe/decision` | `std_msgs/String` | Output | Full Noe result envelope (JSON string) |

---

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `chain` | string | `"shi @zone_clear khi sek mek @enter_zone_alpha sek nek"` | Noe chain to evaluate |
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

The adapter decision log is **not** the same format as the Python `cert_store.py` JSONL. It uses a similar naming convention but does not implement chained `cert_id` / `prev_cert_id` SHA-256 semantics.

Each evaluation appends one JSONL record to `{cert_store_path}/decisions.jsonl`.

Records are written for **all** evaluations — including stale-input and error cases. This is intentional: those are the cases auditors care about most.

Example record (pretty-printed for readability; stored as one compact line):
```json
{
  "format": "noe_decision_v1",
  "timestamp_ms": 1742214412000,
  "chain": "shi @zone_clear khi sek mek @enter_zone_alpha sek nek",
  "mode": "strict",
  "proposed_action": "enter_zone_alpha",
  "decision": "BLOCKED",
  "result": {
    "domain": "undefined",
    "value": "undefined",
    "meta": { "context_hash": "cf11c36...", "mode": "strict", ... }
  },
  "context_summary": {
    "zone_clear": false,
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

If `/noe/zone_clear` has not been received within `max_sensor_age_ms`, the adapter:
1. Does **not** call Noe (no point — the context would be ungrounded)
2. Publishes `permitted=false` (BLOCKED, fail-safe)
3. **Still writes a JSONL record** with `"code": "ERR_STALE_SENSOR"` — dropped-sensor events should be auditable

<br />

## Architecture Position

```
/noe/zone_clear      ──┐
                        ├─→  NoeGateNode (lifecycle)
/noe/proposed_action ──┘       │ context builder (nlohmann/json)
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
- Make policy decisions other than `domain=list/action → PERMITTED, everything else → BLOCKED`

**What the node does not yet do:**
- Emit Python-compatible chained cert records (`cert_id`/`prev_cert_id` — future work)

<br />

## Regression Checks

After any change, verify:

```bash
# Rust conformance: must remain 93/93
cd "$REPO_ROOT/rust/noe_core" && cargo test --test conformance -- --nocapture

# C smoke test
cd "$REPO_ROOT" && make run-c-smoketest

# C++ smoke test
make run-cpp-smoketest

# Zone-entry C example
make run-zone-entry
```

<br />

## Custom Clone Locations (Advanced)

If you clone the repository somewhere other than `~/noe-gate`, you must explicitly configure `REPO_ROOT` before running `cargo` or `colcon` commands.

```bash
export REPO_ROOT="/absolute/path/to/your/noe-gate-checkout"
```

Once exported, all build and launch commands listed in this document will use the custom path automatically.

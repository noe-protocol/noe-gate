#!/usr/bin/env bash
# build_and_verify.sh
#
# Run from repo root after ubuntu_setup.sh completes.
# Builds libnoe_core.a, then the ROS2 adapter, then verifies it.
#
# Usage:
#   cd <repo_root>
#   chmod +x ros2_adapter/build_and_verify.sh
#   ./ros2_adapter/build_and_verify.sh

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source /opt/ros/humble/setup.bash
source "$HOME/.cargo/env" 2>/dev/null || true

echo "=========================================="
echo "  Noe ROS2 adapter — build + verify"
echo "  Repo: $REPO_ROOT"
echo "=========================================="

# ── Step 1: Rust build ────────────────────────────────────────────────────────
echo ""
echo "[1/3] Building Rust library..."
cd "$REPO_ROOT/rust/noe_core"
cargo build
LIB="$REPO_ROOT/rust/noe_core/target/debug/libnoe_core.a"
if [[ ! -f "$LIB" ]]; then
    echo "  ❌ FAIL: libnoe_core.a not found at $LIB"
    exit 1
fi
echo "  ✅ libnoe_core.a built ($(du -sh "$LIB" | cut -f1))"

# ── Step 2: colcon build ──────────────────────────────────────────────────────
echo ""
echo "[2/3] Building ROS2 adapter (colcon)..."
cd "$REPO_ROOT/ros2_adapter"
colcon build \
    --packages-select noe_ros2_adapter \
    --cmake-args \
        -DNOE_CORE_LIB_DIR="$REPO_ROOT/rust/noe_core/target/debug" \
        -DNOE_CORE_INCLUDE_DIR="$REPO_ROOT/rust/noe_core/include" \
        -DNOE_CPP_INCLUDE_DIR="$REPO_ROOT/rust/noe_core/cpp" \
        -DCMAKE_BUILD_TYPE=Debug \
    2>&1

if [[ ! -f "$REPO_ROOT/ros2_adapter/install/noe_ros2_adapter/lib/noe_ros2_adapter/noe_gate_node" ]]; then
    echo "  ❌ FAIL: noe_gate_node binary not found after build"
    exit 1
fi
echo "  ✅ colcon build succeeded"

source "$REPO_ROOT/ros2_adapter/install/setup.bash"

# ── Step 3: Runtime verification ─────────────────────────────────────────────
echo ""
echo "[3/3] Runtime verification..."
CERT_DIR="/tmp/noe_verify_$$"
mkdir -p "$CERT_DIR"
PASS=0; FAIL=0

ros2 run noe_ros2_adapter noe_gate_node \
    --ros-args \
    -p chain:="shi @human_present nek" \
    -p mode:=strict \
    -p cert_store_path:="$CERT_DIR" \
    -p max_sensor_age_ms:=5000 \
    > /tmp/noe_node_$$.log 2>&1 &
NODE_PID=$!

cleanup() { kill "$NODE_PID" 2>/dev/null; rm -rf "$CERT_DIR"; }
trap cleanup EXIT

sleep 1
ros2 lifecycle set /noe_gate_node configure >/dev/null
sleep 0.5
ros2 lifecycle set /noe_gate_node activate >/dev/null
sleep 0.5

# Scenario 1: human present → BLOCKED
ros2 topic pub --once /noe/human_present std_msgs/msg/Bool "data: true" >/dev/null 2>&1
sleep 0.3
ros2 topic pub --once /noe/proposed_action std_msgs/msg/String "data: 'enter_zone_alpha'" >/dev/null 2>&1
sleep 0.5
P1=$(ros2 topic echo /noe/permitted --once --field data 2>/dev/null || echo "TIMEOUT")
if [[ "$P1" == "false" ]]; then
    echo "  ✅ BLOCKED (human_present=true → permitted=false)"; ((PASS++))
else
    echo "  ❌ FAIL BLOCKED: expected false, got $P1"; ((FAIL++))
fi

# Scenario 2: human absent → PERMITTED
ros2 topic pub --once /noe/human_present std_msgs/msg/Bool "data: false" >/dev/null 2>&1
sleep 0.3
ros2 topic pub --once /noe/proposed_action std_msgs/msg/String "data: 'enter_zone_alpha'" >/dev/null 2>&1
sleep 0.5
P2=$(ros2 topic echo /noe/permitted --once --field data 2>/dev/null || echo "TIMEOUT")
if [[ "$P2" == "true" ]]; then
    echo "  ✅ PERMITTED (human_present=false → permitted=true)"; ((PASS++))
else
    echo "  ❌ FAIL PERMITTED: expected true, got $P2"; ((FAIL++))
fi

# Cert log
kill "$NODE_PID" 2>/dev/null; sleep 0.3
RECORDS=$(wc -l < "$CERT_DIR/decisions.jsonl" 2>/dev/null || echo 0)
if [[ "$RECORDS" -ge 2 ]]; then
    echo "  ✅ decisions.jsonl: $RECORDS records written"; ((PASS++))
    echo ""
    echo "  Sample record:"
    head -1 "$CERT_DIR/decisions.jsonl" | python3 -m json.tool 2>/dev/null | head -8
else
    echo "  ❌ decisions.jsonl: $RECORDS records (expected ≥2)"; ((FAIL++))
fi

echo ""
echo "=========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "=========================================="
[[ "$FAIL" -eq 0 ]] && echo "  ✅ ALL CHECKS PASSED" && exit 0
echo "  ❌ FAILURES — node log at /tmp/noe_node_$$.log"
exit 1

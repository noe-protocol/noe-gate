#!/usr/bin/env bash
# docker_verify.sh — runs inside the Docker container after build
#
# Acceptance criteria (Item 3):
#   1. noe_gate_node starts and reaches ACTIVE state
#   2. human_present=true  → /noe/permitted=false (BLOCKED)
#   3. human_present=false → /noe/permitted=true  (PERMITTED)
#   4. decisions.jsonl exists and has 2 records

set -euo pipefail

source /opt/ros/humble/setup.bash
source /noe/ros2_adapter/install/setup.bash

CERT_DIR="/tmp/noe_certs"
CERT_LOG="$CERT_DIR/decisions.jsonl"
NODE_LOG="/tmp/noe_gate_node.log"
PASS=0
FAIL=0

echo "============================================================"
echo "  noe_ros2_adapter — Docker verification"
echo "  $(date -u)"
echo "============================================================"

# ── Start node in background ──────────────────────────────────────────────────
rm -rf "$CERT_DIR"
mkdir -p "$CERT_DIR"

ros2 run noe_ros2_adapter noe_gate_node \
    --ros-args \
    -p chain:="shi @human_present nek" \
    -p mode:=strict \
    -p cert_store_path:="$CERT_DIR" \
    -p max_sensor_age_ms:=5000 \
    > "$NODE_LOG" 2>&1 &
NODE_PID=$!

# Give the node time to initialise (it starts unconfigured)
sleep 1

# Manually trigger configure → activate via lifecycle CLI
ros2 lifecycle set /noe_gate_node configure || {
    echo "FAIL: lifecycle configure failed"
    cat "$NODE_LOG"
    exit 1
}
sleep 0.5
ros2 lifecycle set /noe_gate_node activate || {
    echo "FAIL: lifecycle activate failed"
    cat "$NODE_LOG"
    exit 1
}
sleep 0.5

echo ""
echo "── Scenario 1: human present → expect BLOCKED ───────────────"
ros2 topic pub --once /noe/human_present std_msgs/msg/Bool "data: true"
sleep 0.3
ros2 topic pub --once /noe/proposed_action std_msgs/msg/String "data: 'enter_zone_alpha'"
sleep 0.5

PERMITTED_1=$(ros2 topic echo /noe/permitted --once --field data 2>/dev/null || echo "TIMEOUT")
echo "  /noe/permitted = $PERMITTED_1"
if [[ "$PERMITTED_1" == "false" ]]; then
    echo "  ✅ PASS: BLOCKED (expected)"
    ((PASS++))
else
    echo "  ❌ FAIL: expected false, got $PERMITTED_1"
    ((FAIL++))
fi

echo ""
echo "── Scenario 2: human absent → expect PERMITTED ──────────────"
ros2 topic pub --once /noe/human_present std_msgs/msg/Bool "data: false"
sleep 0.3
ros2 topic pub --once /noe/proposed_action std_msgs/msg/String "data: 'enter_zone_alpha'"
sleep 0.5

PERMITTED_2=$(ros2 topic echo /noe/permitted --once --field data 2>/dev/null || echo "TIMEOUT")
echo "  /noe/permitted = $PERMITTED_2"
if [[ "$PERMITTED_2" == "true" ]]; then
    echo "  ✅ PASS: PERMITTED (expected)"
    ((PASS++))
else
    echo "  ❌ FAIL: expected true, got $PERMITTED_2"
    ((FAIL++))
fi

# ── Check cert log ─────────────────────────────────────────────────────────────
echo ""
echo "── Certificate log ──────────────────────────────────────────"
kill "$NODE_PID" 2>/dev/null; sleep 0.5

if [[ -f "$CERT_LOG" ]]; then
    RECORD_COUNT=$(wc -l < "$CERT_LOG")
    echo "  decisions.jsonl: $RECORD_COUNT records"
    cat "$CERT_LOG" | python3 -m json.tool --compact 2>/dev/null | head -10
    if [[ "$RECORD_COUNT" -ge 2 ]]; then
        echo "  ✅ PASS: cert log has expected records"
        ((PASS++))
    else
        echo "  ❌ FAIL: cert log has $RECORD_COUNT records (expected ≥2)"
        ((FAIL++))
    fi
else
    echo "  ❌ FAIL: decisions.jsonl not found at $CERT_LOG"
    ((FAIL++))
fi

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "============================================================"

if [[ "$FAIL" -eq 0 ]]; then
    echo "  ✅ ALL CHECKS PASSED — ROS2 adapter validated"
    exit 0
else
    echo "  ❌ FAILURES DETECTED"
    echo ""
    echo "  node log:"
    cat "$NODE_LOG"
    exit 1
fi

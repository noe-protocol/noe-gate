#!/usr/bin/env python3
"""
examples/mobile_robot_zone_entry/publish_scenario.py

Publishes two zone-entry scenarios to NoeGateNode and prints decisions.

Design: single-threaded, no executor thread, no locks.
Uses rclpy.spin_once() as the event loop — deterministic and safe to shut down.

Requires: ROS2 Humble/Iron with rclpy installed.
          NoeGateNode must be running and in ACTIVE state.

Usage:
    python3 publish_scenario.py

Expected output:
    [PASS] permitted=False (expected=False)   ← human present  → BLOCKED (guard failed)
    [PASS] permitted=True  (expected=True)    ← human absent   → PERMITTED (action emitted)
"""

import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String


class ZoneEntryScenarioPublisher(Node):
    def __init__(self):
        super().__init__('zone_entry_scenario_publisher')

        self.pub_human_present   = self.create_publisher(Bool,   '/noe/human_present',  10)
        self.pub_proposed_action = self.create_publisher(String, '/noe/proposed_action', 10)

        self.sub_permitted = self.create_subscription(
            Bool, '/noe/permitted', self.on_permitted, 10)
        self.sub_decision = self.create_subscription(
            String, '/noe/decision', self.on_decision, 10)

        # No lock needed — single-threaded via spin_once().
        self.last_permitted    = None
        self.last_decision     = None
        self.last_permitted_at = 0.0
        self.last_decision_at  = 0.0

    def on_permitted(self, msg: Bool):
        self.last_permitted    = msg.data
        self.last_permitted_at = time.monotonic()
        status = "PERMITTED" if msg.data else "BLOCKED"
        self.get_logger().info(f"[RECEIVED] /noe/permitted = {msg.data}  →  {status}")

    def on_decision(self, msg: String):
        self.last_decision    = msg.data
        self.last_decision_at = time.monotonic()
        self.get_logger().info(f"[RECEIVED] /noe/decision = {msg.data[:80]}")

    def reset_response_state(self):
        self.last_permitted    = None
        self.last_decision     = None
        self.last_permitted_at = 0.0
        self.last_decision_at  = 0.0

    def run_scenario(self, human_present: bool, expected_permitted: bool,
                     proposed_action: str = 'enter_zone_alpha') -> bool:
        self.get_logger().info(
            f"\n--- human_present={human_present}  expected_permitted={expected_permitted} ---")

        self.reset_response_state()

        # Publish sensor context first.
        self.pub_human_present.publish(Bool(data=human_present))
        self.get_logger().info(f"[SENT] /noe/human_present = {human_present}")

        # Short pause — let the gate node ingest the new sensor value before
        # the proposed_action triggers evaluation.
        time.sleep(0.1)

        # Record send time — only accept responses timestamped after this.
        sent_at = time.monotonic()

        self.pub_proposed_action.publish(String(data=proposed_action))
        self.get_logger().info(f"[SENT] /noe/proposed_action = '{proposed_action}'")

        # Spin synchronously until both responses arrive (or timeout).
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
            if self.last_permitted_at > sent_at and self.last_decision_at > sent_at:
                break
        else:
            self.get_logger().error("[TIMEOUT] No fresh response received in 3s")
            return False

        ok = self.last_permitted == expected_permitted
        self.get_logger().info(
            f"[{'PASS' if ok else 'FAIL'}] permitted={self.last_permitted} "
            f"(expected={expected_permitted})")
        return ok


def main():
    rclpy.init()
    node = ZoneEntryScenarioPublisher()

    # Allow time for the node to connect to the gate node's topics.
    for _ in range(10):
        rclpy.spin_once(node, timeout_sec=0.1)

    try:
        r1 = node.run_scenario(human_present=True,  expected_permitted=False)
        r2 = node.run_scenario(human_present=False, expected_permitted=True)

        node.get_logger().info(
            "\n=== Scenario run complete. Check /tmp/noe_certs/decisions.jsonl ===")

        all_pass = r1 and r2
        node.get_logger().info(
            f"Result: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}")
    finally:
        node.destroy_node()
        try:
            rclpy.try_shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()

// include/noe_ros2_adapter/noe_gate_node.hpp
//
// NoeGateNode — thin ROS2 lifecycle node wrapping the noe_core Rust FFI.
//
// Lifecycle:
//   configure → read params, validate, init cert log
//   activate  → create subscriptions and publishers
//   deactivate → stop processing
//   cleanup   → release state
//
// Topics consumed:
//   /noe/human_present  (std_msgs/Bool)   — grounded sensor reading
//   /noe/proposed_action (std_msgs/String) — triggers evaluation
//
// Topics published:
//   /noe/permitted  (std_msgs/Bool)   — true if zone entry is clear
//   /noe/decision   (std_msgs/String) — full Noe result JSON
//
// Parameters:
//   chain            (string) — Noe chain, default "shi @human_present nek"
//   mode             (string) — "strict" or "partial", default "strict"
//   cert_store_path  (string) — path to write decisions.jsonl
//   max_sensor_age_ms (int64) — max age of human_present before considered stale

#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <mutex>
#include <string>

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/string.hpp>

namespace noe_adapter {

using CallbackReturn = rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn;

class NoeGateNode : public rclcpp_lifecycle::LifecycleNode {
public:
    explicit NoeGateNode(const rclcpp::NodeOptions& options = rclcpp::NodeOptions());
    ~NoeGateNode() override = default;

    // Lifecycle transitions
    CallbackReturn on_configure(const rclcpp_lifecycle::State& state) override;
    CallbackReturn on_activate(const rclcpp_lifecycle::State& state) override;
    CallbackReturn on_deactivate(const rclcpp_lifecycle::State& state) override;
    CallbackReturn on_cleanup(const rclcpp_lifecycle::State& state) override;
    CallbackReturn on_shutdown(const rclcpp_lifecycle::State& state) override;

private:
    // ── Parameters ────────────────────────────────────────────────────────────
    std::string chain_;
    std::string mode_;
    std::string cert_store_path_;
    int64_t     max_sensor_age_ms_{5000};

    // ── Sensor state (protected by mutex) ─────────────────────────────────────
    struct SensorState {
        bool    human_present{false};
        int64_t received_at_ms{0};   // 0 = never received
    };
    SensorState sensor_{};
    std::mutex sensor_mutex_;

    // ── Processing flag (set false on deactivate) ─────────────────────────────
    std::atomic<bool> active_{false};

    // ── Certificate log ───────────────────────────────────────────────────────
    std::ofstream cert_log_;           // append-only JSONL file
    std::mutex    cert_mutex_;         // serialise writes

    // ── ROS2 handles ──────────────────────────────────────────────────────────
    rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr   sub_human_present_;
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr sub_proposed_action_;
    rclcpp_lifecycle::LifecyclePublisher<std_msgs::msg::Bool>::SharedPtr   pub_permitted_;
    rclcpp_lifecycle::LifecyclePublisher<std_msgs::msg::String>::SharedPtr pub_decision_;

    // ── Internal methods ──────────────────────────────────────────────────────

    void on_human_present(const std_msgs::msg::Bool::SharedPtr msg);
    void on_proposed_action(const std_msgs::msg::String::SharedPtr msg);

    // Run noe::evaluate and publish results. Sensor state is snapshotted under
    // mutex before calling evaluate (which may block on Rust init, but won't
    // hold the mutex).
    void evaluate_and_publish(const std::string& proposed_action, int64_t now_ms);

    // Append one JSONL record to the cert log.
    void append_cert_record(const std::string& json_line);

    // Build a JSONL record from the decision.
    std::string build_cert_record(
        const std::string& proposed_action,
        const std::string& result_json,
        const std::string& decision_str,
        bool               human_present_snapshot,
        int64_t            now_ms) const;

    // Current wall-clock milliseconds (via ROS2 clock).
    int64_t now_ms();
};

} // namespace noe_adapter

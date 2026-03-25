// src/noe_gate_node.cpp
//
// NoeGateNode implementation.
//
// Evaluation flow (on /noe/proposed_action received while ACTIVE):
//   1. Snapshot sensor state under mutex (non-blocking).
//   2. Check sensor freshness. Stale → ERROR decision.
//   3. Build context JSON via ContextBuilder (deterministic, no side effects).
//   4. Call noe::evaluate(chain_, context_json, mode_) through the C FFI boundary.
//   5. Parse the result envelope into a NoeDecision.
//   6. Publish /noe/permitted (Bool) and /noe/decision (String).
//   7. Append JSONL cert record (always — including stale/error cases).
//
// Memory ownership:
//   noe::evaluate() calls noe_eval_json() internally and frees the Rust heap
//   allocation before returning a std::string. No manual memory management here.

#include "noe_ros2_adapter/noe_gate_node.hpp"
#include "noe_ros2_adapter/context_builder.hpp"

// noe_core.h and noe.hpp are found via CMakeLists target_include_directories.
#include "noe_core.h"
#include "noe.hpp"

#include <chrono>
#include <filesystem>
#include <nlohmann/json.hpp>
#include <sstream>
#include <stdexcept>

namespace noe_adapter {

namespace fs = std::filesystem;

NoeGateNode::NoeGateNode(const rclcpp::NodeOptions& options)
    : LifecycleNode("noe_gate_node", options)
{
    // Declare all parameters with defaults so they appear in param dumps.
    declare_parameter<std::string>("chain",           "shi @human_present nek");
    declare_parameter<std::string>("mode",            "strict");
    declare_parameter<std::string>("cert_store_path", "/tmp/noe_certs");
    declare_parameter<int64_t>("max_sensor_age_ms",   5000);
}

// ─── Lifecycle: configure ─────────────────────────────────────────────────────

CallbackReturn NoeGateNode::on_configure(const rclcpp_lifecycle::State& /*state*/) {
    chain_           = get_parameter("chain").as_string();
    mode_            = get_parameter("mode").as_string();
    cert_store_path_ = get_parameter("cert_store_path").as_string();
    max_sensor_age_ms_ = get_parameter("max_sensor_age_ms").as_int();

    RCLCPP_INFO(get_logger(), "chain            : %s", chain_.c_str());
    RCLCPP_INFO(get_logger(), "mode             : %s", mode_.c_str());
    RCLCPP_INFO(get_logger(), "cert_store_path  : %s", cert_store_path_.c_str());
    RCLCPP_INFO(get_logger(), "max_sensor_age_ms: %ld", max_sensor_age_ms_);
    RCLCPP_INFO(get_logger(), "noe_core version : %s", noe_version());

    // Validate mode
    if (mode_ != "strict" && mode_ != "partial") {
        RCLCPP_ERROR(get_logger(), "Invalid mode '%s' — must be 'strict' or 'partial'",
                     mode_.c_str());
        return CallbackReturn::FAILURE;
    }

    // Create cert_store_path directory if needed
    try {
        fs::create_directories(cert_store_path_);
    } catch (const std::exception& e) {
        RCLCPP_ERROR(get_logger(), "Cannot create cert_store_path '%s': %s",
                     cert_store_path_.c_str(), e.what());
        return CallbackReturn::FAILURE;
    }

    // Open the append-only JSONL log
    std::string log_path = cert_store_path_ + "/decisions.jsonl";
    cert_log_.open(log_path, std::ios::app);
    if (!cert_log_.is_open()) {
        RCLCPP_ERROR(get_logger(), "Cannot open cert log '%s' for append",
                     log_path.c_str());
        return CallbackReturn::FAILURE;
    }
    RCLCPP_INFO(get_logger(), "cert log         : %s", log_path.c_str());

    // Reset sensor state
    {
        std::lock_guard<std::mutex> lk(sensor_mutex_);
        sensor_.human_present = false;
        sensor_.received_at_ms = 0;
    }

    RCLCPP_INFO(get_logger(), "NoeGateNode configured.");
    return CallbackReturn::SUCCESS;
}

// ─── Lifecycle: activate ──────────────────────────────────────────────────────

CallbackReturn NoeGateNode::on_activate(const rclcpp_lifecycle::State& state) {
    LifecycleNode::on_activate(state);

    active_.store(true);

    // Subscriptions
    sub_human_present_ = create_subscription<std_msgs::msg::Bool>(
        "/noe/human_present", 10,
        [this](const std_msgs::msg::Bool::SharedPtr msg) {
            on_human_present(msg);
        });

    sub_proposed_action_ = create_subscription<std_msgs::msg::String>(
        "/noe/proposed_action", 10,
        [this](const std_msgs::msg::String::SharedPtr msg) {
            on_proposed_action(msg);
        });

    // Publishers (lifecycle-managed — only deliver when node is active)
    pub_permitted_ = create_publisher<std_msgs::msg::Bool>("/noe/permitted", 10);
    pub_decision_  = create_publisher<std_msgs::msg::String>("/noe/decision", 10);

    pub_permitted_->on_activate();
    pub_decision_->on_activate();

    RCLCPP_INFO(get_logger(), "NoeGateNode active. Listening on /noe/proposed_action.");
    return CallbackReturn::SUCCESS;
}

// ─── Lifecycle: deactivate ────────────────────────────────────────────────────

CallbackReturn NoeGateNode::on_deactivate(const rclcpp_lifecycle::State& state) {
    // Set inactive FIRST — stops callbacks from publishing before we tear down.
    active_.store(false);

    // Reset subscriptions before deactivating publishers so no callback can
    // fire on a deactivated publisher (race between teardown and callback).
    sub_human_present_.reset();
    sub_proposed_action_.reset();

    // Base class must be called after we've stopped accepting callbacks.
    LifecycleNode::on_deactivate(state);

    pub_permitted_->on_deactivate();
    pub_decision_->on_deactivate();

    RCLCPP_INFO(get_logger(), "NoeGateNode deactivated.");
    return CallbackReturn::SUCCESS;
}

// ─── Lifecycle: cleanup ───────────────────────────────────────────────────────

CallbackReturn NoeGateNode::on_cleanup(const rclcpp_lifecycle::State& /*state*/) {
    {
        std::lock_guard<std::mutex> lk(cert_mutex_);
        if (cert_log_.is_open()) {
            cert_log_.close();
        }
    }
    pub_permitted_.reset();
    pub_decision_.reset();
    RCLCPP_INFO(get_logger(), "NoeGateNode cleaned up.");
    return CallbackReturn::SUCCESS;
}

CallbackReturn NoeGateNode::on_shutdown(const rclcpp_lifecycle::State& /*state*/) {
    active_.store(false);
    sub_human_present_.reset();
    sub_proposed_action_.reset();
    pub_permitted_.reset();
    pub_decision_.reset();
    std::lock_guard<std::mutex> lk(cert_mutex_);
    if (cert_log_.is_open()) cert_log_.close();
    return CallbackReturn::SUCCESS;
}

// ─── Subscription callbacks ───────────────────────────────────────────────────

void NoeGateNode::on_human_present(const std_msgs::msg::Bool::SharedPtr msg) {
    if (!active_.load()) return;
    std::lock_guard<std::mutex> lk(sensor_mutex_);
    sensor_.human_present  = msg->data;
    sensor_.received_at_ms = now_ms();
    RCLCPP_DEBUG(get_logger(), "human_present updated: %s",
                 msg->data ? "true" : "false");
}

void NoeGateNode::on_proposed_action(const std_msgs::msg::String::SharedPtr msg) {
    if (!active_.load()) return;
    evaluate_and_publish(msg->data, now_ms());
}

// ─── Evaluation ───────────────────────────────────────────────────────────────

void NoeGateNode::evaluate_and_publish(
    const std::string& proposed_action,
    int64_t current_ms)
{
    // Snapshot sensor state under mutex before calling into Rust.
    SensorState snap;
    {
        std::lock_guard<std::mutex> lk(sensor_mutex_);
        snap = sensor_;
    }

    // Check sensor freshness. A never-received sensor (received_at_ms==0) is
    // always considered stale. Stale input is BLOCKED and still logged.
    bool stale = (snap.received_at_ms == 0) ||
                 ((current_ms - snap.received_at_ms) > max_sensor_age_ms_);

    NoeDecision decision;

    if (stale) {
        // Build a synthetic error result envelope (stale context → blocked).
        int64_t age_ms = (snap.received_at_ms == 0)
                           ? -1
                           : (current_ms - snap.received_at_ms);
        std::string stale_msg = "sensor data stale or never received (age=" +
                                std::to_string(age_ms) + "ms, max=" +
                                std::to_string(max_sensor_age_ms_) + "ms)";
        nlohmann::json err;
        err["domain"]  = "error";
        err["code"]    = "ERR_STALE_SENSOR";
        err["value"]   = stale_msg;
        err["meta"]["context_hash"] = "";
        err["meta"]["mode"] = mode_;
        err["meta"]["context_hashes"] = { {"root",""}, {"domain",""}, {"local",""}, {"total",""} };
        decision.result_json  = err.dump();
        decision.domain       = "error";
        decision.permitted    = false;
        decision.decision_str = "ERROR";
        RCLCPP_WARN(get_logger(), "Stale sensor: %s", stale_msg.c_str());
    } else {
        // Build context and call Noe through the FFI boundary.
        ZoneEntryContextParams params;
        params.human_present = snap.human_present;
        params.now_ms        = current_ms;
        params.max_skew_ms   = max_sensor_age_ms_;

        std::string context_json = build_zone_entry_context(params);

        std::string result_json;
        try {
            result_json = noe::evaluate(chain_, context_json, mode_);
        } catch (const std::runtime_error& e) {
            // Only thrown on OOM (NULL return from noe_eval_json).
            RCLCPP_ERROR(get_logger(), "noe::evaluate threw: %s", e.what());
            nlohmann::json err;
            err["domain"] = "error";
            err["code"]   = "ERR_FFI_OOM";
            err["value"]  = "Rust runtime returned NULL (OOM)";
            err["meta"]["context_hash"] = "";
            err["meta"]["mode"] = mode_;
            err["meta"]["context_hashes"] = { {"root",""}, {"domain",""}, {"local",""}, {"total",""} };
            result_json = err.dump();
        }

        decision = parse_noe_result(result_json);
        RCLCPP_INFO(get_logger(),
                    "Noe decision: %s (domain=%s, proposed='%s')",
                    decision.decision_str.c_str(),
                    decision.domain.c_str(),
                    proposed_action.c_str());
    }

    // Publish results — guard with active_ so a late-arriving SIGTERM during
    // teardown cannot publish on a reset/deactivated publisher.
    if (active_.load()) {
        auto permitted_msg = std_msgs::msg::Bool();
        permitted_msg.data = decision.permitted;
        pub_permitted_->publish(permitted_msg);

        auto decision_msg = std_msgs::msg::String();
        decision_msg.data = decision.result_json;
        pub_decision_->publish(decision_msg);
    }

    // Always append cert record — including stale/error cases.
    std::string cert_line = build_cert_record(
        proposed_action,
        decision.result_json,
        decision.decision_str,
        snap.human_present,
        current_ms);
    append_cert_record(cert_line);
}

// ─── Certificate logging ─────────────────────────────────────────────────────

std::string NoeGateNode::build_cert_record(
    const std::string& proposed_action,
    const std::string& result_json,
    const std::string& decision_str,
    bool               human_present_snapshot,
    int64_t            ts_ms) const
{
    // Parse result JSON back to verify it's valid (it always should be).
    nlohmann::json result;
    try {
        result = nlohmann::json::parse(result_json);
    } catch (...) {
        result = nlohmann::json{{"raw", result_json}};
    }

    nlohmann::json record;
    record["format"]           = "noe_decision_v1";
    record["timestamp_ms"]     = ts_ms;
    record["chain"]            = chain_;
    record["mode"]             = mode_;
    record["proposed_action"]  = proposed_action;
    record["decision"]         = decision_str;
    record["result"]           = result;
    record["context_summary"]["human_present"] = human_present_snapshot;
    record["context_summary"]["timestamp_ms"]  = ts_ms;

    return record.dump();  // one compact JSON line, no trailing newline
}

void NoeGateNode::append_cert_record(const std::string& json_line) {
    std::lock_guard<std::mutex> lk(cert_mutex_);
    if (cert_log_.is_open()) {
        cert_log_ << json_line << "\n";
        cert_log_.flush();
    }
}

// ─── Utility ─────────────────────────────────────────────────────────────────

int64_t NoeGateNode::now_ms() {
    auto now = get_clock()->now();
    return static_cast<int64_t>(now.nanoseconds() / 1'000'000);
}

} // namespace noe_adapter

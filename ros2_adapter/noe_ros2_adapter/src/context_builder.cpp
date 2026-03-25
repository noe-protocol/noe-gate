// src/context_builder.cpp
//
// Builds the Noe layered context JSON for the zone-entry scenario and
// parses the Noe result envelope into a NoeDecision.
//
// The context JSON produced must match the schema expected by the Rust
// strict-mode validator:
//   root.modal.knowledge.@human_present  (bool)
//   root.modal.belief                    (empty object)
//   root.modal.certainty                 (empty object)
//   root.literals.@human_present         (bool, mirrors knowledge shard)
//   root.axioms.value_system             (accepted/rejected lists)
//   root.rel                             (empty object)
//   root.spatial                         (unit, thresholds, orientation)
//   root.temporal.now                    (int64 ms)
//   root.temporal.max_skew_ms            (int64 ms)
//   domain                              (empty object)
//   local.timestamp                      (int64 ms — same as now)

#include "noe_ros2_adapter/context_builder.hpp"

#include <stdexcept>

namespace noe_adapter {

std::string build_zone_entry_context(const ZoneEntryContextParams& p) {
    nlohmann::json ctx;

    // root shard
    ctx["root"]["literals"]["@human_present"] = p.human_present;
    ctx["root"]["modal"]["knowledge"]["@human_present"] = p.human_present;
    ctx["root"]["modal"]["belief"] = nlohmann::json::object();
    ctx["root"]["modal"]["certainty"] = nlohmann::json::object();
    ctx["root"]["axioms"]["value_system"]["accepted"] = nlohmann::json::array();
    ctx["root"]["axioms"]["value_system"]["rejected"] = nlohmann::json::array();
    ctx["root"]["rel"] = nlohmann::json::object();
    ctx["root"]["spatial"]["unit"] = "metric";
    ctx["root"]["spatial"]["thresholds"]["near"] = 2.0;
    ctx["root"]["spatial"]["thresholds"]["far"] = 10.0;
    ctx["root"]["spatial"]["orientation"]["target"] = 0.0;
    ctx["root"]["spatial"]["orientation"]["tolerance"] = 0.5;
    ctx["root"]["temporal"]["now"] = p.now_ms;
    ctx["root"]["temporal"]["max_skew_ms"] = p.max_skew_ms;

    // domain shard (empty for this scenario — no domain-level overrides)
    ctx["domain"] = nlohmann::json::object();

    // local shard
    ctx["local"]["timestamp"] = p.now_ms;

    return ctx.dump();
}

NoeDecision parse_noe_result(const std::string& result_json) {
    NoeDecision d;
    d.result_json = result_json;

    nlohmann::json j;
    try {
        j = nlohmann::json::parse(result_json);
    } catch (const nlohmann::json::parse_error&) {
        d.permitted    = false;
        d.domain       = "parse_error";
        d.decision_str = "ERROR";
        return d;
    }

    d.domain = j.value("domain", "");

    // Policy: chain is "shi @human_present nek"
    //   domain="truth", value=false → zone clear → PERMITTED
    //   domain="truth", value=true  → human present → BLOCKED
    //   anything else               → BLOCKED (fail-safe)
    if (d.domain == "truth") {
        bool value = j.value("value", true); // default true = safe-block on unknown
        d.permitted    = !value;             // permitted only when human NOT present
        d.decision_str = value ? "BLOCKED" : "PERMITTED";
    } else {
        d.permitted    = false;
        d.decision_str = "ERROR";
    }

    return d;
}

} // namespace noe_adapter

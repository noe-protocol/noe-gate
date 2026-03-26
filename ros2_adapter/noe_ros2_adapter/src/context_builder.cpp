// src/context_builder.cpp
//
// Builds the Noe layered context JSON for the zone-entry scenario and
// parses the Noe result envelope into a NoeDecision.
//
// The context JSON produced must match the schema expected by the Rust
// strict-mode validator:
//   root.modal.knowledge.@zone_clear      (bool)
//   root.modal.belief                     (empty object)
//   root.modal.certainty                  (empty object)
//   root.literals.@zone_clear             (bool, mirrors knowledge shard)
//   root.literals.@enter_zone_alpha       (string target)
//   root.axioms.value_system              (accepted/rejected lists)
//   root.rel                              (empty object)
//   root.spatial                          (unit, thresholds, orientation)
//   root.temporal.now                     (int64 ms)
//   root.temporal.max_skew_ms             (int64 ms)
//   domain                               (empty object)
//   local.timestamp                       (int64 ms — same as now)

#include "noe_ros2_adapter/context_builder.hpp"

#include <nlohmann/json.hpp>
#include <stdexcept>

namespace noe_adapter {

std::string build_zone_entry_context(const ZoneEntryContextParams &p) {
  nlohmann::json ctx;

  // root shard
  ctx["root"]["literals"]["@zone_clear"] = p.zone_clear;
  ctx["root"]["literals"]["@enter_zone_alpha"] = "fwd_target";
  ctx["root"]["modal"]["knowledge"]["@zone_clear"] = p.zone_clear;
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
  ctx["root"]["audit"] = nlohmann::json::object();
  ctx["root"]["entities"] = nlohmann::json::object();

  // domain shard (empty for this scenario — no domain-level overrides)
  ctx["domain"] = nlohmann::json::object();

  // local shard
  ctx["local"]["timestamp"] = p.now_ms;

  return ctx.dump();
}

NoeDecision parse_noe_result(const std::string &result_json) {
  NoeDecision d;
  d.result_json = result_json;

  nlohmann::json j;
  try {
    j = nlohmann::json::parse(result_json);
  } catch (const nlohmann::json::parse_error &) {
    d.permitted = false;
    d.domain = "parse_error";
    d.decision_str = "ERROR";
    return d;
  }

  d.domain = j.value("domain", "");

  // Policy: chain is "shi @zone_clear khi sek mek @enter_zone_alpha sek nek"
  //   domain="action" → @zone_clear is true → PERMITTED (action emitted)
  //   domain="undefined" → @zone_clear is false/missing → BLOCKED
  //   anything else      → BLOCKED (fail-safe)
  if (d.domain == "list" || d.domain == "action") {
    d.permitted = true;
    d.decision_str = "PERMITTED";
  } else if (d.domain == "undefined") {
    d.permitted = false;
    d.decision_str = "BLOCKED";
  } else {
    d.permitted = false;
    d.decision_str = "ERROR";
  }

  return d;
}

} // namespace noe_adapter

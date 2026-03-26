// include/noe_ros2_adapter/context_builder.hpp
//
// Assembles the Noe layered context JSON for the mobile robot zone-entry
// scenario from discrete sensor values. This is the ONLY place where
// ROS2 input data is translated into the Noe context schema.
//
// The context produced matches the schema validated by the Rust runtime's
// strict mode:
//   root.modal.knowledge.@zone_clear  = bool
//   root.literals.@zone_clear         = bool
//   root.literals.@enter_zone_alpha   = string
//   root.temporal.now                 = ms since epoch (int)
//   root.temporal.max_skew_ms         = max tolerated staleness
//   local.timestamp                   = same as root.temporal.now
//
// No semantic decisions are made here. This builder is deterministic and
// side-effect free.

#pragma once

#include <cstdint>
#include <string>

namespace noe_adapter {

struct ZoneEntryContextParams {
  bool zone_clear;     // true = zone is known clear
  int64_t now_ms;      // current wall-clock ms (from ROS clock)
  int64_t max_skew_ms; // from parameter max_sensor_age_ms
};

/// Build the full Noe context JSON string for a zone entry evaluation.
std::string build_zone_entry_context(const ZoneEntryContextParams &p);

/// Result of parsing a Noe evaluation envelope.
/// domain="action"    → PERMITTED (chain emitted the action)
/// domain="undefined" → BLOCKED (guard condition did not hold)
struct NoeDecision {
  bool permitted;           // true = PERMITTED, false = BLOCKED/ERROR
  std::string domain;       // raw domain field from result
  std::string decision_str; // "PERMITTED" | "BLOCKED" | "ERROR"
  std::string result_json;  // full raw result JSON for logging
};

NoeDecision parse_noe_result(const std::string &result_json);

} // namespace noe_adapter

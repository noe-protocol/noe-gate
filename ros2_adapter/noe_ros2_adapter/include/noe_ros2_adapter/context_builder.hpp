// include/noe_ros2_adapter/context_builder.hpp
//
// Assembles the Noe layered context JSON for the mobile robot zone-entry
// scenario from discrete sensor values. This is the ONLY place where
// ROS2 input data is translated into the Noe context schema.
//
// The context produced matches the schema validated by the Rust runtime's
// strict mode:
//   root.modal.knowledge.@human_present = bool
//   root.temporal.now                   = ms since epoch (int)
//   root.temporal.max_skew_ms           = max tolerated staleness
//   local.timestamp                     = same as root.temporal.now
//
// No semantic decisions are made here. This builder is deterministic and
// side-effect free.

#pragma once

#include <cstdint>
#include <string>

#include <nlohmann/json.hpp>

namespace noe_adapter {

struct ZoneEntryContextParams {
    bool        human_present;
    int64_t     now_ms;           // current wall-clock ms (from ROS clock)
    int64_t     max_skew_ms;      // from parameter max_sensor_age_ms
};

/// Build the full Noe context JSON string for a zone entry evaluation.
/// Returns a well-formed JSON string that can be passed directly to noe::evaluate().
std::string build_zone_entry_context(const ZoneEntryContextParams& p);

/// Parse the domain and value fields from a Noe result envelope JSON string.
/// Returns true if domain=="truth" and value==false (zone clear, robot may enter).
/// Returns false in all other cases (human present, error, undefined, parse failure).
struct NoeDecision {
    bool    permitted;        // true = PERMITTED, false = BLOCKED/ERROR
    std::string domain;       // raw domain field from result
    std::string decision_str; // "PERMITTED" | "BLOCKED" | "ERROR"
    std::string result_json;  // full raw result JSON for logging
};

NoeDecision parse_noe_result(const std::string& result_json);

} // namespace noe_adapter

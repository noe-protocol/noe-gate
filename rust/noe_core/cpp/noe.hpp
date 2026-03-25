/**
 * noe.hpp — C++20 single-header wrapper for the noe_core C FFI
 *
 * Provides a RAII-safe evaluate() function that handles memory ownership
 * automatically. The caller gets a std::string and never touches
 * noe_free_string() directly.
 *
 * Usage:
 *   #include "noe_core.h"
 *   #include "noe.hpp"
 *
 *   std::string result = noe::evaluate("shi @human_present nek", ctx_json);
 *   // result is a std::string containing the full Noe result JSON.
 */

#pragma once

#include <stdexcept>
#include <string>
#include <string_view>

#include "noe_core.h"

namespace noe {

/**
 * Evaluate a Noe chain against a JSON context string.
 *
 * @param chain        Noe chain (e.g. "shi @human_present nek")
 * @param context_json Full context as JSON string
 * @param mode         "strict" (default) or "partial"
 *
 * @returns The full Noe result envelope as a std::string (JSON).
 *
 * @throws std::runtime_error if noe_eval_json returns NULL (OOM only —
 *         all other failures return error JSON, not NULL).
 */
inline std::string evaluate(
    std::string_view chain,
    std::string_view context_json,
    std::string_view mode = "strict")
{
    // Build null-terminated copies for the C API
    std::string chain_s(chain);
    std::string ctx_s(context_json);
    std::string mode_s(mode);

    char* raw = ::noe_eval_json(chain_s.c_str(), ctx_s.c_str(), mode_s.c_str());
    if (raw == nullptr) {
        throw std::runtime_error(
            "noe_eval_json returned NULL (catastrophic OOM — no result available)");
    }

    // RAII: copy into std::string, then free the Rust allocation immediately.
    std::string result(raw);
    ::noe_free_string(raw);
    return result;
}

/**
 * Return the noe_core version string.
 */
inline std::string_view version() {
    return ::noe_version();
}

} // namespace noe

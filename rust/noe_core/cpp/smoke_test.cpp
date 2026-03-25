/**
 * cpp/smoke_test.cpp — C++ wrapper smoke test for noe_core
 *
 * Build:
 *   cd rust/noe_core && cargo build
 *   c++ -std=c++20 cpp/smoke_test.cpp \
 *       -Iinclude -Ltarget/debug -lnoe_core \
 *       -Wl,-rpath,target/debug \
 *       -o /tmp/noe_cpp_smoke && /tmp/noe_cpp_smoke
 */

#include <cassert>
#include <iostream>
#include <stdexcept>
#include <string>

#include "noe_core.h"
#include "noe.hpp"

static const std::string VALID_CTX = R"({
    "root": {
        "literals": {},
        "modal": {"knowledge": {}, "belief": {}, "certainty": {}},
        "axioms": {"value_system": {"accepted": [], "rejected": []}},
        "rel": {},
        "spatial": {"unit": "generic",
            "thresholds": {"near": 1.0, "far": 10.0},
            "orientation": {"target": 0.0, "tolerance": 0.1}},
        "temporal": {"now": 1000, "max_skew_ms": 5000}
    },
    "domain": {},
    "local": {"timestamp": 1000}
})";

int main() {
    std::cout << "=== noe_core C++ wrapper smoke test ===\n";

    // 1. Version string
    auto ver = noe::version();
    assert(!ver.empty() && "noe_version must not be empty");
    std::cout << "[PASS] version = \"" << ver << "\"\n";

    // 2. Simple truth evaluation
    auto result = noe::evaluate("true nek", VALID_CTX);
    assert(result.find("\"domain\":\"truth\"") != std::string::npos &&
           "expected domain:truth");
    assert(result.find("\"value\":true") != std::string::npos &&
           "expected value:true");
    std::cout << "[PASS] true nek -> domain:truth, value:true\n";

    // 3. Error chain (invalid chain returns ERR_PARSE_FAILED)
    auto err = noe::evaluate("nek", VALID_CTX);
    assert(err.find("\"domain\":\"error\"") != std::string::npos &&
           "nek-only chain must be error domain");
    assert(err.find("ERR_PARSE_FAILED") != std::string::npos &&
           "nek-only chain must be ERR_PARSE_FAILED");
    std::cout << "[PASS] nek-only chain -> ERR_PARSE_FAILED\n";

    // 4. Null input produces error JSON (not exception from nullptr deref)
    auto null_result = noe::evaluate("true nek", "null");
    // null context → ERR_BAD_CONTEXT
    assert(null_result.find("\"domain\":\"error\"") != std::string::npos &&
           "null context must produce error domain");
    std::cout << "[PASS] null context JSON -> error domain\n";

    std::cout << "=== All C++ smoke tests passed ===\n";
    return 0;
}

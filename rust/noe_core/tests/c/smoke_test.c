/*
 * tests/c/smoke_test.c — C FFI smoke test for noe_core
 *
 * Verifies:
 *   1. noe_version() returns a non-null, non-empty string
 *   2. noe_eval_json("true nek", ...) returns a JSON envelope with "truth" domain
 *      and value:true
 *   3. noe_free_string() correctly frees the returned pointer
 *   4. Null chain input returns error JSON (not NULL)
 *   5. Invalid context JSON returns error JSON (not NULL)
 *
 * Build:
 *   cd rust/noe_core && cargo build
 *   cc tests/c/smoke_test.c \
 *       -Iinclude -Ltarget/debug -lnoe_core \
 *       -o /tmp/noe_c_smoke && /tmp/noe_c_smoke
 */

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "noe_core.h"

/* Minimal valid layered context JSON */
static const char* VALID_CTX =
    "{"
    "\"root\":{"
        "\"literals\":{},"
        "\"modal\":{\"knowledge\":{},\"belief\":{},\"certainty\":{}},"
        "\"axioms\":{\"value_system\":{\"accepted\":[],\"rejected\":[]}},"
        "\"rel\":{},"
        "\"spatial\":{\"unit\":\"generic\","
            "\"thresholds\":{\"near\":1.0,\"far\":10.0},"
            "\"orientation\":{\"target\":0.0,\"tolerance\":0.1}},"
        "\"temporal\":{\"now\":1000,\"max_skew_ms\":5000}"
    "},"
    "\"domain\":{},"
    "\"local\":{\"timestamp\":1000}"
    "}";

static void test_version(void) {
    const char* ver = noe_version();
    assert(ver != NULL && "noe_version() must not return NULL");
    assert(strlen(ver) > 0 && "noe_version() must not be empty");
    printf("[PASS] noe_version = \"%s\"\n", ver);
}

static void test_true_nek(void) {
    char* result = noe_eval_json("true nek", VALID_CTX, "strict");
    assert(result != NULL && "noe_eval_json must not return NULL for valid inputs");

    /* Check exact fields present in the JSON string */
    assert(strstr(result, "\"domain\":\"truth\"") != NULL &&
           "expected domain:truth");
    assert(strstr(result, "\"value\":true") != NULL &&
           "expected value:true");
    assert(strstr(result, "\"context_hash\"") != NULL &&
           "expected meta.context_hash present");

    printf("[PASS] true nek -> %s\n", result);
    noe_free_string(result);
}

static void test_null_chain(void) {
    char* result = noe_eval_json(NULL, VALID_CTX, "strict");
    /* Must not crash and must return error JSON (not NULL) */
    assert(result != NULL && "null chain must return error JSON, not NULL");
    assert(strstr(result, "\"domain\":\"error\"") != NULL &&
           "null chain must produce error domain");
    assert(strstr(result, "ERR_FFI_NULL_INPUT") != NULL &&
           "null chain must produce ERR_FFI_NULL_INPUT code");
    printf("[PASS] null chain -> error JSON\n");
    noe_free_string(result);
}

static void test_invalid_context_json(void) {
    char* result = noe_eval_json("true nek", "not valid json {{", "strict");
    assert(result != NULL && "invalid context JSON must return error JSON, not NULL");
    assert(strstr(result, "\"domain\":\"error\"") != NULL &&
           "invalid JSON must produce error domain");
    assert(strstr(result, "ERR_FFI_BAD_CONTEXT_JSON") != NULL &&
           "invalid JSON must produce ERR_FFI_BAD_CONTEXT_JSON");
    printf("[PASS] invalid context JSON -> error JSON\n");
    noe_free_string(result);
}

static void test_free_null(void) {
    /* Must not crash */
    noe_free_string(NULL);
    printf("[PASS] noe_free_string(NULL) is safe\n");
}

int main(void) {
    printf("=== noe_core C FFI smoke test ===\n");
    test_version();
    test_true_nek();
    test_null_chain();
    test_invalid_context_json();
    test_free_null();
    printf("=== All smoke tests passed ===\n");
    return 0;
}

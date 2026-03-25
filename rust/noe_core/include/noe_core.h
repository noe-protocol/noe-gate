/*
 * noe_core.h — C interface for the Noe core runtime (noe_core Rust crate)
 *
 * Provides a minimal JSON-in / JSON-out API for evaluating Noe chains from C
 * or C++ code. The full result envelope (domain, value, code, details, meta)
 * is returned as a heap-allocated JSON string.
 *
 * MEMORY OWNERSHIP
 * ----------------
 * - noe_eval_json() allocates the returned string. The caller MUST free it
 *   with noe_free_string(). Do NOT use free(3) or delete directly.
 * - noe_free_string() is safe to call with NULL.
 * - noe_version() returns a static string. Do NOT free it.
 *
 * THREAD SAFETY
 * -------------
 * Each noe_eval_json() call is fully independent. Concurrent calls from
 * different threads are safe (no shared mutable state).
 *
 * ERROR HANDLING
 * --------------
 * All ordinary failures (null input, bad UTF-8, bad JSON, parse failure,
 * validation failure) return a Noe error JSON envelope — never NULL.
 * NULL is returned only on catastrophic allocation failure (OOM).
 *
 * RESULT SHAPE
 * ------------
 * Successful returns are always a JSON object:
 *   {
 *     "domain": "truth" | "error" | "action" | "undefined" | ...,
 *     "value":  <boolean | string | object | null>,
 *     "code":   <string | null>,          -- present only on error
 *     "details":<string | null>,          -- present only on error
 *     "meta": {
 *       "context_hash": "<hex>",
 *       "mode":         "strict" | "partial",
 *       "context_hashes": { "root": "...", "domain": "...", "local": "...", "total": "..." },
 *       "flags": { ... }                  -- present only in strict mode errors
 *     }
 *   }
 */

#ifndef NOE_CORE_H
#define NOE_CORE_H

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Evaluate a Noe chain against a JSON context.
 *
 * @param chain        Null-terminated UTF-8 Noe chain string (e.g. "shi @human_present nek")
 * @param context_json Null-terminated UTF-8 JSON object string
 * @param mode         Null-terminated UTF-8 mode: "strict" or "partial"
 *
 * @returns A heap-allocated null-terminated UTF-8 JSON string.
 *          The caller MUST call noe_free_string() on this pointer.
 *          Returns NULL only on catastrophic OOM failure.
 */
char* noe_eval_json(const char* chain, const char* context_json, const char* mode);

/**
 * Free a string returned by noe_eval_json().
 *
 * Must be called exactly once per pointer returned by noe_eval_json().
 * Safe to call with NULL.
 *
 * @param ptr Pointer returned by noe_eval_json(), or NULL.
 */
void noe_free_string(char* ptr);

/**
 * Return the noe_core version string.
 *
 * The returned pointer is valid for the lifetime of the process.
 * Do NOT free this pointer.
 *
 * @returns Null-terminated version string (e.g. "0.1.0")
 */
const char* noe_version(void);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* NOE_CORE_H */

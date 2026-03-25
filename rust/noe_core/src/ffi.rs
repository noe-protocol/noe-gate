// src/ffi.rs
//
// C-facing FFI boundary for noe_core.
//
// Exposes a minimal JSON-in / JSON-out API designed to be easy to use correctly
// and hard to misuse. All ordinary failures (bad input, parse error, validation
// error) return a Noe error JSON envelope — not NULL. NULL is reserved for
// catastrophic allocation failure (OOM) only.
//
// Ownership rules:
//   - noe_eval_json() returns a heap-allocated C string.
//     The caller MUST free it with noe_free_string(). Never with free(3) directly.
//   - noe_free_string() is safe to call with NULL.
//   - noe_version() returns a static string. Do NOT free it.
//
// Thread safety:
//   - Each noe_eval_json() call is independent with no shared mutable state.
//   - Concurrent calls from different threads are safe.

use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::panic;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Convert a C string pointer to a Rust &str, or return an error JSON if
/// the pointer is null or the bytes are not valid UTF-8.
fn cstr_to_str<'a>(ptr: *const c_char, param_name: &str) -> Result<&'a str, String> {
    if ptr.is_null() {
        return Err(format!(
            r#"{{"domain":"error","code":"ERR_FFI_NULL_INPUT","value":"null {} pointer","meta":{{"context_hash":"","mode":"","context_hashes":{{"root":"","domain":"","local":"","total":""}}}}}}"#,
            param_name
        ));
    }
    // SAFETY: caller is responsible for passing a valid null-terminated C string.
    // Lifetime is limited to the duration of the FFI call.
    let cstr = unsafe { CStr::from_ptr(ptr) };
    cstr.to_str().map_err(|_| {
        format!(
            r#"{{"domain":"error","code":"ERR_FFI_INVALID_UTF8","value":"invalid UTF-8 in {}","meta":{{"context_hash":"","mode":"","context_hashes":{{"root":"","domain":"","local":"","total":""}}}}}}"#,
            param_name
        )
    })
}

/// Allocate a CString from a Rust String and return the raw pointer.
/// Returns NULL only on CString allocation failure (interior NUL byte —
/// should not happen for well-formed JSON, but handled defensively).
fn to_c_string(s: String) -> *mut c_char {
    match CString::new(s) {
        Ok(cs) => cs.into_raw(),
        Err(_) => std::ptr::null_mut(),
    }
}

/// Build a minimal error JSON envelope when we cannot call run_noe_logic.
fn ffi_error_envelope(code: &str, message: &str, mode: &str) -> String {
    format!(
        r#"{{"domain":"error","code":"{code}","value":"{message}","meta":{{"context_hash":"","mode":"{mode}","context_hashes":{{"root":"","domain":"","local":"","total":""}}}}}}"#
    )
}

// ---------------------------------------------------------------------------
// Public FFI surface
// ---------------------------------------------------------------------------

/// Evaluate a Noe chain against a JSON context.
///
/// # Parameters
/// - `chain`        — null-terminated UTF-8 Noe chain string
/// - `context_json` — null-terminated UTF-8 JSON object string
/// - `mode`         — null-terminated UTF-8: `"strict"` or `"partial"`
///
/// # Returns
/// A heap-allocated null-terminated UTF-8 JSON string containing the full
/// Noe result envelope `{domain, value?, code?, details?, meta}`.
///
/// The caller MUST free this pointer with `noe_free_string()`.
///
/// Returns NULL only on catastrophic failure (OOM when allocating the result
/// CString). All ordinary failures (invalid input, parse errors, validation
/// errors) return an error JSON envelope instead of NULL.
#[no_mangle]
pub extern "C" fn noe_eval_json(
    chain: *const c_char,
    context_json: *const c_char,
    mode: *const c_char,
) -> *mut c_char {
    // Wrap the entire body in catch_unwind so Rust panics cannot unwind into C.
    let result = panic::catch_unwind(|| {
        // 1. Validate and decode inputs
        let chain_str = match cstr_to_str(chain, "chain") {
            Ok(s) => s,
            Err(e) => return e,
        };
        let context_str = match cstr_to_str(context_json, "context_json") {
            Ok(s) => s,
            Err(e) => return e,
        };
        let mode_str = match cstr_to_str(mode, "mode") {
            Ok(s) => s,
            Err(e) => return e,
        };

        // 2. Parse context JSON
        let context_value: serde_json::Value = match serde_json::from_str(context_str) {
            Ok(v) => v,
            Err(e) => {
                return ffi_error_envelope(
                    "ERR_FFI_BAD_CONTEXT_JSON",
                    &format!("context_json is not valid JSON: {e}"),
                    mode_str,
                );
            }
        };

        // 3. Run evaluation
        let eval_result = crate::run_noe_logic(chain_str, &context_value, mode_str);

        // 4. Serialise result to JSON string
        match serde_json::to_string(&eval_result) {
            Ok(json) => json,
            Err(e) => ffi_error_envelope(
                "ERR_FFI_SERIALISE",
                &format!("failed to serialise result: {e}"),
                mode_str,
            ),
        }
    });

    match result {
        Ok(json_str) => to_c_string(json_str),
        Err(_) => {
            // Rust panicked inside catch_unwind — extremely unusual.
            // Return a static-content error as a best effort.
            to_c_string(ffi_error_envelope("ERR_FFI_PANIC", "internal panic", ""))
        }
    }
}

/// Free a string returned by `noe_eval_json`.
///
/// MUST be called exactly once per pointer returned by `noe_eval_json`.
/// Safe to call with NULL.
#[no_mangle]
pub extern "C" fn noe_free_string(ptr: *mut c_char) {
    if !ptr.is_null() {
        // SAFETY: ptr was created by CString::into_raw() in noe_eval_json.
        // Reconstituting and immediately dropping frees it.
        unsafe { drop(CString::from_raw(ptr)) };
    }
}

/// Return the noe_core version string.
///
/// The returned pointer is valid for the lifetime of the process.
/// Do NOT free this pointer.
#[no_mangle]
pub extern "C" fn noe_version() -> *const c_char {
    // SAFETY: This string literal is valid UTF-8 and null-terminated by the
    // compiler (b"...\0"). Casting to *const c_char is correct.
    concat!(env!("CARGO_PKG_VERSION"), "\0").as_ptr() as *const c_char
}

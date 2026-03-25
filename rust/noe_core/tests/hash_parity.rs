// tests/hash_parity.rs
//
// Python-vs-Rust canonical_json / canonical_bytes / composite_hash parity tests.
//
// All expected values source-verified from Python runtime output.
// Run: cargo test --test hash_parity

use noe_core::hash::{canonical_json, canonical_bytes, composite_hash, shard_digest};
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn j(s: &str) -> Value {
    serde_json::from_str(s).unwrap()
}

fn cj(v: &Value) -> String {
    canonical_json(v).expect("canonical_json should not fail")
}

fn cb(v: &Value) -> Result<Vec<u8>, noe_core::hash::HashError> {
    canonical_bytes(v)
}

// ---------------------------------------------------------------------------
// canonical_json — source-verified expected values from Python
// ---------------------------------------------------------------------------

#[test]
fn test_sorted_keys() {
    assert_eq!(cj(&json!({"b": 2, "a": 1})), r#"{"a":1,"b":2}"#);
}

#[test]
fn test_bool_null() {
    assert_eq!(
        cj(&json!({"x": true, "y": false, "z": null})),
        r#"{"x":true,"y":false,"z":null}"#
    );
}

#[test]
fn test_array() {
    assert_eq!(cj(&json!(["hello", "world"])), r#"["hello","world"]"#);
}

#[test]
fn test_nested_dict() {
    assert_eq!(
        cj(&json!({"nested": {"c": 3, "a": 1}})),
        r#"{"nested":{"a":1,"c":3}}"#
    );
}

/// ensure_ascii=True: "café" → {"s":"caf\u00e9"}
#[test]
fn test_non_ascii_ensure_ascii() {
    // Python produces: {"s":"caf\u00e9"}
    // Note: in Rust string, this is r#"{"s":"caf\u00e9"}"# — literal backslash-u
    let v = json!({"s": "caf\u{00e9}"});
    let result = cj(&v);
    assert_eq!(result, r#"{"s":"caf\u00e9"}"#);
}

#[test]
fn test_int_array() {
    assert_eq!(cj(&json!([1, 2, 3])), "[1,2,3]");
}

#[test]
fn test_float_context() {
    // Floats ARE allowed in canonical_json (context hashing path)
    // Python: {"val":1.5}
    let v = json!({"val": 1.5});
    assert_eq!(cj(&v), r#"{"val":1.5}"#);
}

/// CRITICAL PARITY: Python serializes 1e+100 (with '+'), Rust ryu produces 1e100.
/// This test confirms Rust matches Python.
#[test]
fn test_big_number_exponent_sign() {
    // Python: {"v":1e+100}
    let v = json!({"v": 1e100_f64});
    let result = cj(&v);
    assert_eq!(result, r#"{"v":1e+100}"#, "Python adds '+' in exponent; Rust must match");
}

#[test]
fn test_empty_object() {
    assert_eq!(cj(&json!({})), "{}");
}

#[test]
fn test_empty_array() {
    assert_eq!(cj(&json!([])), "[]");
}

#[test]
fn test_null_value() {
    assert_eq!(cj(&Value::Null), "null");
}

#[test]
fn test_negative_int() {
    assert_eq!(cj(&json!({"n": -1})), r#"{"n":-1}"#);
}

// ---------------------------------------------------------------------------
// canonical_bytes — float ban
// ---------------------------------------------------------------------------

#[test]
fn test_canonical_bytes_ok_for_int() {
    assert!(cb(&json!({"a": 1, "b": 2})).is_ok());
}

#[test]
fn test_canonical_bytes_rejects_float() {
    assert!(cb(&json!({"val": 1.5})).is_err(), "canonical_bytes must reject floats");
}

#[test]
fn test_canonical_bytes_rejects_big_float() {
    assert!(cb(&json!({"v": 1e100_f64})).is_err(), "canonical_bytes must reject 1e100");
}

#[test]
fn test_canonical_bytes_ok_for_bool_null_string() {
    assert!(cb(&json!({"x": true, "y": null, "s": "hello"})).is_ok());
}

// ---------------------------------------------------------------------------
// composite_hash — source-verified values from ContextManager
// ---------------------------------------------------------------------------

/// all_empty: root={}, domain={}, local={}
/// Python verified:
///   root_hash    = 44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a
///   domain_hash  = 44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a
///   local_hash   = 44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a
///   composite    = 18374dd155903ec18abe2b5f095e47f59e2957962e0b005cd13b8fce33a84abb
#[test]
fn test_composite_hash_all_empty() {
    let r = composite_hash(&json!({}), &json!({}), &json!({}))
        .expect("composite_hash should succeed");
    assert_eq!(r.root_hash, "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a");
    assert_eq!(r.domain_hash, "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a");
    assert_eq!(r.local_hash, "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a");
    assert_eq!(r.composite_hash, "18374dd155903ec18abe2b5f095e47f59e2957962e0b005cd13b8fce33a84abb");
    assert_eq!(r.context_hash, r.composite_hash, "context_hash == composite_hash");
}

/// lit_001 context: Python verified composite_hash = b361cf6b...
#[test]
fn test_composite_hash_lit_001() {
    let root = j(r#"{"audit":{},"axioms":{"value_system":{"accepted":[],"rejected":[]}},"delivery":{"status":{}},"demonstratives":{},"literals":{"fact":"some_fact"},"modal":{"belief":{},"certainty":{},"knowledge":{"fact":true}},"rel":{},"spatial":{"orientation":{"target":0,"tolerance":0},"thresholds":{"far":10,"near":1}},"temporal":{"max_skew_ms":5000,"now":1678886400000},"timestamp":1000}"#);
    let domain = json!({});
    let local = j(r#"{"timestamp":1678886400000}"#);

    let r = composite_hash(&root, &domain, &local).expect("should succeed");
    assert_eq!(r.root_hash, "e8203191e7c00494d88859ece943184311a46b8c6bdc725361d35b8777216809");
    assert_eq!(r.domain_hash, "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a");
    assert_eq!(r.local_hash, "818b064d894bcf5c9526b7924d1c8cc4e4cefc1f69cb5dac1dc019a80f717a15");
    assert_eq!(r.composite_hash, "b361cf6b5064203911839660a398223bf4335dbb45dd69f2a1afb727616267cc");
}

/// total == composite_hash (both names exist in Python for the same value)
#[test]
fn test_total_equals_composite() {
    let r = composite_hash(&json!({}), &json!({}), &json!({})).unwrap();
    assert_eq!(r.composite_hash, r.context_hash);
}

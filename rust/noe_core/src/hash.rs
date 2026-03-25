// src/hash.rs
//
// Canonical hashing — must produce byte-identical output to Python's
// canonical_json / canonical_bytes / composite_hash.
//
// All claims source-verified against noe/canonical.py and context_manager.py.

use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

// ---------------------------------------------------------------------------
// Public interface
// ---------------------------------------------------------------------------

/// canonical_json — mirrors Python's `canonical_json(obj)`:
///   json.dumps(obj, sort_keys=True, separators=(",",":"),
///              ensure_ascii=True, allow_nan=False)
///
/// Rules (source-verified):
///   - Dict keys sorted lexicographically (BTreeMap)
///   - No whitespace in separators
///   - Non-ASCII characters escaped as \uXXXX (ensure_ascii=True)
///   - NaN/Infinity rejected (serde_json default)
///   - Floats allowed (for context shard hashing)
///   - Python's float repr: 1.5 → "1.5", 1e+100 → "1e+100" (with '+')
pub fn canonical_json(value: &serde_json::Value) -> Result<String, HashError> {
    let mut out = String::new();
    write_value(value, &mut out)?;
    Ok(out)
}

/// canonical_bytes — mirrors Python's `canonical_bytes(obj)`:
///   Same as canonical_json, but rejects floats (raises ValueError).
///   Used ONLY for action/provenance/decision hashing.
pub fn canonical_bytes(value: &serde_json::Value) -> Result<Vec<u8>, HashError> {
    check_no_floats(value)?;
    Ok(canonical_json(value)?.into_bytes())
}

/// sha256_hex — SHA-256 hex digest of bytes
pub fn sha256_hex(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}

/// sha256_digest — raw 32-byte SHA-256 digest
pub fn sha256_digest(data: &[u8]) -> [u8; 32] {
    Sha256::digest(data).into()
}

/// shard_digest — SHA-256 of canonical_json(obj) encoded as UTF-8
/// Returns (raw_32_bytes, hex_string)
pub fn shard_digest(value: &serde_json::Value) -> Result<([u8; 32], String), HashError> {
    let json_str = canonical_json(value)?;
    let json_bytes = json_str.as_bytes();
    let raw = sha256_digest(json_bytes);
    let hex = hex::encode(raw);
    Ok((raw, hex))
}

/// composite_hash — source-verified formula from context_manager.py lines 413-418:
///   SHA-256(root_raw_32bytes ++ domain_raw_32bytes ++ local_raw_32bytes)
///
/// NOT SHA-256 of a JSON array of hex strings.
pub fn composite_hash(
    root: &serde_json::Value,
    domain: &serde_json::Value,
    local: &serde_json::Value,
) -> Result<CompositeHashResult, HashError> {
    let (root_raw, root_hex) = shard_digest(root)?;
    let (domain_raw, domain_hex) = shard_digest(domain)?;
    let (local_raw, local_hex) = shard_digest(local)?;

    // Concat raw 32-byte digests and hash (source: context_manager.py line 416-418)
    let mut combined = [0u8; 96];
    combined[..32].copy_from_slice(&root_raw);
    combined[32..64].copy_from_slice(&domain_raw);
    combined[64..].copy_from_slice(&local_raw);

    let total_hex = sha256_hex(&combined);

    Ok(CompositeHashResult {
        root_hash: root_hex,
        domain_hash: domain_hex,
        local_hash: local_hex,
        composite_hash: total_hex.clone(),
        // context_hash in meta == composite_hash
        context_hash: total_hex,
    })
}

#[derive(Debug, Clone)]
pub struct CompositeHashResult {
    pub root_hash: String,
    pub domain_hash: String,
    pub local_hash: String,
    pub composite_hash: String,
    pub context_hash: String,
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum HashError {
    FloatInCanonicalBytes(String),
    NanOrInfinity,
    InvalidValue(String),
}

impl std::fmt::Display for HashError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HashError::FloatInCanonicalBytes(path) => write!(
                f,
                "Floats are disallowed in canonical hash-bearing fields (noe-canonical-v1) at {path}"
            ),
            HashError::NanOrInfinity => write!(f, "NaN/Infinity not allowed"),
            HashError::InvalidValue(msg) => write!(f, "Invalid value: {msg}"),
        }
    }
}

// ---------------------------------------------------------------------------
// Internal: write JSON value to string (ensure_ascii, sorted keys, no spaces)
// ---------------------------------------------------------------------------

fn write_value(value: &serde_json::Value, out: &mut String) -> Result<(), HashError> {
    match value {
        serde_json::Value::Null => out.push_str("null"),
        serde_json::Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        serde_json::Value::Number(n) => write_number(n, out)?,
        serde_json::Value::String(s) => write_string(s, out),
        serde_json::Value::Array(arr) => {
            out.push('[');
            for (i, v) in arr.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_value(v, out)?;
            }
            out.push(']');
        }
        serde_json::Value::Object(map) => {
            // Sort keys using a BTreeMap (serde_json::Map preserves insertion order)
            let sorted: BTreeMap<&str, &serde_json::Value> =
                map.iter().map(|(k, v)| (k.as_str(), v)).collect();
            out.push('{');
            for (i, (k, v)) in sorted.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_string(k, out);
                out.push(':');
                write_value(v, out)?;
            }
            out.push('}');
        }
    }
    Ok(())
}

/// Write a JSON number matching Python's repr-like output.
///
/// Critical parity point: Python's json.dumps serializes:
///   1.5     → "1.5"        (f64: same)
///   1e+100  → "1e+100"     (Python adds '+'; Rust serde_json produces "1e100")
///   -1      → "-1"         (integer)
///   1000    → "1000"       (integer)
///
/// Strategy: try to use the integer representation first (no decimal point
/// or exponent). If it is a float, produce Python-compatible output.
fn write_number(n: &serde_json::Number, out: &mut String) -> Result<(), HashError> {
    // If representable as i64 without loss, output as integer
    if let Some(i) = n.as_i64() {
        out.push_str(&i.to_string());
        return Ok(());
    }
    if let Some(u) = n.as_u64() {
        out.push_str(&u.to_string());
        return Ok(());
    }
    // Float path
    if let Some(f) = n.as_f64() {
        if f.is_nan() || f.is_infinite() {
            return Err(HashError::NanOrInfinity);
        }
        out.push_str(&python_float_repr(f));
        return Ok(());
    }
    // Fall back to serde_json's display (last resort)
    out.push_str(&n.to_string());
    Ok(())
}

/// Produce a float string that matches Python's json.dumps output.
///
/// Python uses a repr-like format: it adds '+' in exponents (1e+100),
/// uses 'e-' for negative exponents, and minimises decimal digits.
///
/// Rust's f64 formatting: {} gives "1e100" without '+' and fewer digits.
/// We match Python by:
///   1. Using Rust's {:?} debug repr (which includes more precision), OR
///   2. Parsing Rust's output and inserting '+' where needed.
///
/// This is the confirmed parity blocker from fixtures:
///   Python: 1e+100
///   Rust {:?} or {}: 1e100  ← WRONG without this fix
fn python_float_repr(f: f64) -> String {
    // Use Python-compatible repr: enough digits to round-trip, '+' in exponents
    // Strategy: format with Rust, then fix the exponent separator.
    //
    // serde_json's internal float formatter uses ryu crate which produces
    // "1e100" (no +). We re-format here to match Python.
    let s = format!("{f:e}"); // Rust scientific notation: "1e100"
    
    // Split on 'e'
    if let Some(pos) = s.find('e') {
        let mantissa = &s[..pos];
        let exp_str = &s[pos + 1..];
        
        // Normalise mantissa: remove trailing zeros after decimal but keep at least one digit
        let _mantissa_clean = normalise_mantissa(mantissa);
        
        // exp_str is like "100" or "-5" (Rust doesn't add '+')
        let _exp_signed = if exp_str.starts_with('-') {
            exp_str.to_string()
        } else {
            format!("+{exp_str}")
        };
        
        // Check: if the number can be expressed more naturally without scientific notation
        // Python uses scientific notation only for very large or very small numbers.
        // For normal range, use plain decimal. Use the serde_json serialized form as authority.
        let serde_repr = serde_json::to_string(&f).unwrap_or_else(|_| f.to_string());
        
        // serde_json (ryu) is accurate enough for round-tripping but lacks '+' in exp
        if serde_repr.contains('e') || serde_repr.contains('E') {
            // Needs scientific notation — fix the exponent sign
            let fixed = fix_exponent_sign(&serde_repr);
            return fixed;
        }
        // No scientific notation needed — use serde_json's plain form
        serde_repr
    } else {
        // No exponent in Rust format — use serde_json directly
        serde_json::to_string(&f).unwrap_or_else(|_| f.to_string())
    }
}

/// Fix Rust/ryu exponent notation to match Python: insert '+' if missing.
/// "1e100" → "1e+100", "1e-5" → "1e-5" (already has sign)
fn fix_exponent_sign(s: &str) -> String {
    if let Some(e_pos) = s.find('e').or_else(|| s.find('E')) {
        let after_e = &s[e_pos + 1..];
        if !after_e.starts_with('+') && !after_e.starts_with('-') {
            // Insert '+' after 'e'
            let mut result = s[..e_pos + 1].to_string();
            result.push('+');
            result.push_str(after_e);
            return result;
        }
    }
    s.to_string()
}

fn normalise_mantissa(m: &str) -> &str {
    // Remove trailing zeros after decimal point (keep at least X.0 form)
    // This is a rough approximation; the serde_json path handles the real case
    m
}

/// Write string with ensure_ascii=True: non-ASCII → \uXXXX
fn write_string(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\x00'..='\x1f' => {
                // Control characters as \uXXXX
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c if (c as u32) > 127 => {
                // Non-ASCII: ensure_ascii=True → \uXXXX
                // For basic multilingual plane (BMP, ≤0xFFFF): \uXXXX
                // For supplementary (>0xFFFF): Python uses surrogate pairs \uD800\uDC00
                let code = c as u32;
                if code <= 0xFFFF {
                    out.push_str(&format!("\\u{:04x}", code));
                } else {
                    // Surrogate pair encoding (matching Python's ensure_ascii)
                    let code = code - 0x10000;
                    let high = 0xD800 + (code >> 10);
                    let low = 0xDC00 + (code & 0x3FF);
                    out.push_str(&format!("\\u{:04x}\\u{:04x}", high, low));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Reject floats for canonical_bytes (action/provenance hashing only)
fn check_no_floats(value: &serde_json::Value) -> Result<(), HashError> {
    match value {
        serde_json::Value::Number(n) => {
            // A number is a float if it cannot be represented as i64/u64
            if n.as_i64().is_none() && n.as_u64().is_none() {
                return Err(HashError::FloatInCanonicalBytes("(root)".to_string()));
            }
        }
        serde_json::Value::Array(arr) => {
            for v in arr {
                check_no_floats(v)?;
            }
        }
        serde_json::Value::Object(map) => {
            for v in map.values() {
                check_no_floats(v)?;
            }
        }
        _ => {}
    }
    Ok(())
}

// src/types.rs
//
// Noe runtime types — conformance-first.
//
// External EvalResult mirrors Python's result envelope json shape exactly.
// Internal NoeVal is a richer evaluation-time type; it is never serialized directly.

use serde::{Deserialize, Serialize};
use serde_json::Value;

// ---------------------------------------------------------------------------
// Error codes (match Python strings exactly — never derive snake_case)
// ---------------------------------------------------------------------------

pub const ERR_BAD_CONTEXT: &str = "ERR_BAD_CONTEXT";
pub const ERR_CONTEXT_INCOMPLETE: &str = "ERR_CONTEXT_INCOMPLETE";
pub const ERR_CONTEXT_STALE: &str = "ERR_CONTEXT_STALE";
pub const ERR_STALE_CONTEXT: &str = "ERR_STALE_CONTEXT";
pub const ERR_EPISTEMIC_MISMATCH: &str = "ERR_EPISTEMIC_MISMATCH";
pub const ERR_LITERAL_MISSING: &str = "ERR_LITERAL_MISSING";
pub const ERR_INVALID_LITERAL: &str = "ERR_INVALID_LITERAL";
pub const ERR_UNDEFINED_TARGET: &str = "ERR_UNDEFINED_TARGET";
pub const ERR_INVALID_ACTION: &str = "ERR_INVALID_ACTION";
pub const ERR_ACTION_CYCLE: &str = "ERR_ACTION_CYCLE";
pub const ERR_ACTION_MISUSE: &str = "ERR_ACTION_MISUSE";
pub const ERR_BAD_ACTION_TARGET_REF: &str = "ERR_BAD_ACTION_TARGET_REF";
pub const ERR_SPATIAL_UNGROUNDABLE: &str = "ERR_SPATIAL_UNGROUNDABLE";
pub const ERR_DEMONSTRATIVE_UNGROUNDED: &str = "ERR_DEMONSTRATIVE_UNGROUNDED";
pub const ERR_MORPHOLOGY: &str = "ERR_MORPHOLOGY";
pub const ERR_GUARD_TYPE: &str = "ERR_GUARD_TYPE";
pub const ERR_INTERNAL: &str = "ERR_INTERNAL";
pub const ERR_INVALID_NUMBER: &str = "ERR_INVALID_NUMBER";
pub const ERR_PARSE_FAILED: &str = "ERR_PARSE_FAILED";

// ---------------------------------------------------------------------------
// External (conformance) result envelope — mirrors Python JSON shape
//
// Exact match definition: canonical-JSON-identical full result envelope,
// including domain, value, code, details, and meta.
// Structural match is a debug-only classification; it does not count as pass.
// Any meta mismatch is a conformance failure until resolved.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvalResult {
    pub domain: String,

    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<Value>,

    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,

    /// details: can be a string or structured object depending on the error.
    /// Always mirrors Python's actual serialized shape — not normalized.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<Value>,

    pub meta: EvalMeta,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvalMeta {
    /// SHA-256 hex digest of composite_hash (= SHA-256(root_digest||domain_digest||local_digest))
    /// Matches Python meta.context_hash which equals snap.composite_hash.
    pub context_hash: String,
    pub mode: String,
    pub context_hashes: ContextHashes,

    /// Validator flags — present only on some error envelopes (skip if None)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub flags: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextHashes {
    pub root: String,
    pub domain: String,
    pub local: String,
    /// Equals context_hash (composite_hash)
    pub total: String,
}

// ---------------------------------------------------------------------------
// Internal evaluation type — richer, never serialized directly
//
// Only includes variants needed to cover the 80 conformance vectors.
// Resist adding variants not exercised by conformance vectors.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum NoeVal {
    Truth(bool),
    /// f64 for internal arithmetic. Number formatting for output is handled
    /// separately in eval.rs to match Python's repr-like behaviour (1e+100).
    Numeric(f64),
    Undefined,
    Action(Value),
    Literal { key: String, value: Value },
    Error { code: &'static str, message: String },
}

impl NoeVal {
    pub fn domain_str(&self) -> &'static str {
        match self {
            NoeVal::Truth(_) => "truth",
            NoeVal::Numeric(_) => "numeric",
            NoeVal::Undefined => "undefined",
            NoeVal::Action(_) => "action",
            NoeVal::Literal { .. } => "literal",
            NoeVal::Error { .. } => "error",
        }
    }

    pub fn is_error(&self) -> bool {
        matches!(self, NoeVal::Error { .. })
    }

    pub fn is_undef(&self) -> bool {
        matches!(self, NoeVal::Undefined)
    }
}

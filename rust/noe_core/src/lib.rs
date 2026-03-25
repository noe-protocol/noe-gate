// src/lib.rs
//
// Noe core runtime — public API
//
// run_noe_logic: main entry point matching Python's run_noe_logic output envelope.
//
// Execution order (mirrors run_noe_logic in noe_parser.py):
//   1. Classify context (layered/flat/invalid)
//   2. Compute shard hashes and composite_hash
//   3. Strict-mode: run validation pre-check → may return early with error envelope
//   4. Parse chain → may return early with error envelope
//   5. Evaluate AST
//   6. Convert NoeVal → EvalResult with full EvalMeta
//
// EXACT MATCH REQUIREMENT: canonical-JSON-identical full result envelope
// (domain, value, code, details, meta including context_hash). Not negotiable.

pub mod ast;
pub mod context;
pub mod eval;
pub mod ffi;
pub mod hash;
pub mod parser;
pub mod types;
pub mod validator;

use crate::context::parse_context;
use crate::eval::eval;
use crate::types::*;
use crate::validator::validate_strict;
use serde_json::{json, Value};

/// Run a Noe chain against a context object.
/// mode: "strict" (default) or "partial"
pub fn run_noe_logic(chain: &str, context_object: &Value, mode: &str) -> EvalResult {
    // ----------------------------------------------------------------
    // STEP 1: Classify and parse context
    // Returns Err("ERR_BAD_CONTEXT") for non-dict/null/array inputs.
    // For ERR_BAD_CONTEXT: Python emits empty hashes + {schema_invalid: true}
    // ----------------------------------------------------------------
    let ctx_layers = match parse_context(context_object, mode) {
        Ok(layers) => layers,
        Err(type_name) => {
            // Non-dict context: empty hashes, schema_invalid flag only.
            // details = "Context malformed: {type}" — type_name is Python type string
            let details = format!("Context malformed: {type_name}");
            let empty_hashes = ContextHashes {
                root: String::new(),
                domain: String::new(),
                local: String::new(),
                total: String::new(),
            };
            return EvalResult {
                domain: "error".to_string(),
                value: Some(Value::String("blocked".to_string())),
                code: Some(ERR_BAD_CONTEXT.to_string()),
                details: Some(Value::String(details)),
                meta: EvalMeta {
                    context_hash: String::new(),
                    mode: mode.to_string(),
                    context_hashes: empty_hashes,
                    flags: Some(json!({"schema_invalid": true})),
                },
            };
        }
    };

    // Build the real EvalMeta for all subsequent returns
    let ctx_hashes = ctx_layers.context_hashes();
    let ctx_hash = ctx_layers.hashes.context_hash.clone();

    let make_meta = |flags: Option<Value>| EvalMeta {
        context_hash: ctx_hash.clone(),
        mode: mode.to_string(),
        context_hashes: ctx_hashes.clone(),
        flags,
    };

    // ----------------------------------------------------------------
    // STEP 2: Strict-mode validation pre-check
    // Mirrors Python validate_chain() called before evaluation.
    // On failure: return error envelope with full flags dict.
    // ----------------------------------------------------------------
    if mode == "strict" {
        let val = validate_strict(chain, &ctx_layers);
        if !val.ok {
            let flags_json = val.flags.to_json();
            let code = val.error_code.unwrap_or(ERR_BAD_CONTEXT).to_string();
            return EvalResult {
                domain: "error".to_string(),
                value: Some(Value::String("blocked".to_string())),
                code: Some(code),
                details: Some(Value::String(val.details_message)),
                meta: make_meta(Some(flags_json)),
            };
        }
    }

    // ----------------------------------------------------------------
    // STEP 3: Parse chain
    // ----------------------------------------------------------------
    let ast = match crate::parser::parse(chain) {
        Ok(ast) => ast,
        Err(e) => {
            return EvalResult {
                domain: "error".to_string(),
                value: Some(Value::String(e.to_string())),
                code: Some(ERR_PARSE_FAILED.to_string()),
                details: None,
                meta: make_meta(None),
            };
        }
    };

    // ----------------------------------------------------------------
    // STEP 4: Evaluate
    // ----------------------------------------------------------------
    let result = eval(&ast, &ctx_layers, mode);

    // ----------------------------------------------------------------
    // STEP 5: Convert NoeVal → EvalResult with full EvalMeta
    // ----------------------------------------------------------------
    noe_val_to_eval_result(result, make_meta(None))
}

fn noe_val_to_eval_result(val: NoeVal, meta: EvalMeta) -> EvalResult {
    match val {
        NoeVal::Truth(b) => EvalResult {
            domain: "truth".to_string(),
            value: Some(Value::Bool(b)),
            code: None,
            details: None,
            meta,
        },
        NoeVal::Numeric(f) => EvalResult {
            domain: "numeric".to_string(),
            value: Some(format_numeric(f)),
            code: None,
            details: None,
            meta,
        },
        NoeVal::Undefined => EvalResult {
            domain: "undefined".to_string(),
            value: Some(Value::String("undefined".to_string())),
            code: None,
            details: None,
            meta,
        },
        NoeVal::Action(a) => EvalResult {
            domain: "action".to_string(),
            value: Some(a),
            code: None,
            details: None,
            meta,
        },
        NoeVal::Literal { key, value } => EvalResult {
            domain: "literal".to_string(),
            value: Some(json!({"key": key, "value": value})),
            code: None,
            details: None,
            meta,
        },
        NoeVal::Error { code, message } => {
            // Epistemic errors (ERR_EPISTEMIC_MISMATCH) and action misuse errors use the message as the value field.
            // Other evaluator errors use "blocked". Source-verified from EDET_002 and REQ_003 ground truth.
            // NOTE: Evaluator errors do NOT include a details field — only validator errors do.
            let value_str = if code == ERR_EPISTEMIC_MISMATCH || code == ERR_ACTION_MISUSE {
                message.clone()
            } else {
                "blocked".to_string()
            };
            EvalResult {
                domain: "error".to_string(),
                value: Some(Value::String(value_str)),
                code: Some(code.to_string()),
                details: None, // Evaluator errors don't emit details — only validator errors do
                meta,
            }
        }
    }
}

/// Format f64 for output, matching Python's json serialisation including 1e+100 exponent sign.
/// Uses fix_exponent_sign from hash.rs via serde_json serialization.
fn format_numeric(f: f64) -> Value {
    // For integers stored as f64: emit as integer (avoids 1.0 vs 1 mismatch)
    if f.fract() == 0.0 && f.abs() < 1e15 {
        if let Some(n) = serde_json::Number::from_f64(f) {
            return Value::Number(n);
        }
    }
    // For floats: serde_json Number is the native type; fix_exponent_sign
    // is applied during canonical_json() comparison in the conformance test.
    match serde_json::Number::from_f64(f) {
        Some(n) => Value::Number(n),
        None => Value::Null,
    }
}

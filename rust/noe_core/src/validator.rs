// src/validator.rs
//
// Strict-mode validation pre-check — mirrors Python's validate_chain() behavior.
//
// Python runs validation BEFORE evaluation in strict mode (run_noe_logic lines 2759-2893).
// The validator computes flags and maps them to error codes.
//
// Source-verified flag names from ground truth:
//   invalid_literal, literal_mismatch, action_misuse, demonstrative_ungrounded,
//   spatial_mismatch, epistemic_mismatch, sensor_mismatch, delivery_mismatch,
//   audit_mismatch, schema_invalid, context_stale, demonstrative_mismatch,
//   value_system_mismatch

use crate::context::ContextLayers;
use serde_json::{json, Value};

/// Full flags dict as emitted by Python validator.
/// All 13 keys present when flags is non-null (error path only).
/// On success path: flags is None/null (not emitted).
#[derive(Debug, Clone)]
pub struct ValidationFlags {
    pub invalid_literal: bool,
    pub literal_mismatch: bool,
    pub action_misuse: bool,
    pub demonstrative_ungrounded: bool,
    pub spatial_mismatch: bool,
    pub epistemic_mismatch: bool,
    pub sensor_mismatch: bool,
    pub delivery_mismatch: bool,
    pub audit_mismatch: bool,
    pub schema_invalid: bool,
    pub context_stale: bool,
    pub demonstrative_mismatch: bool,
    pub value_system_mismatch: bool,
}

impl Default for ValidationFlags {
    fn default() -> Self {
        Self {
            invalid_literal: false,
            literal_mismatch: false,
            action_misuse: false,
            demonstrative_ungrounded: false,
            spatial_mismatch: false,
            epistemic_mismatch: false,
            sensor_mismatch: false,
            delivery_mismatch: false,
            audit_mismatch: false,
            schema_invalid: false,
            context_stale: false,
            demonstrative_mismatch: false,
            value_system_mismatch: false,
        }
    }
}

impl ValidationFlags {
    /// Convert to serde_json Value (all 13 bool fields, sorted lexicographically)
    pub fn to_json(&self) -> Value {
        json!({
            "action_misuse": self.action_misuse,
            "audit_mismatch": self.audit_mismatch,
            "context_stale": self.context_stale,
            "delivery_mismatch": self.delivery_mismatch,
            "demonstrative_mismatch": self.demonstrative_mismatch,
            "demonstrative_ungrounded": self.demonstrative_ungrounded,
            "epistemic_mismatch": self.epistemic_mismatch,
            "invalid_literal": self.invalid_literal,
            "literal_mismatch": self.literal_mismatch,
            "schema_invalid": self.schema_invalid,
            "sensor_mismatch": self.sensor_mismatch,
            "spatial_mismatch": self.spatial_mismatch,
            "value_system_mismatch": self.value_system_mismatch,
        })
    }

    pub fn any_set(&self) -> bool {
        self.invalid_literal
            || self.literal_mismatch
            || self.action_misuse
            || self.demonstrative_ungrounded
            || self.spatial_mismatch
            || self.epistemic_mismatch
            || self.sensor_mismatch
            || self.delivery_mismatch
            || self.audit_mismatch
            || self.schema_invalid
            || self.context_stale
            || self.demonstrative_mismatch
            || self.value_system_mismatch
    }
}

/// Validation result
#[derive(Debug)]
pub struct ValidationResult {
    pub ok: bool,
    pub flags: ValidationFlags,
    /// Error code to use if !ok (maps flags following Python priority order)
    pub error_code: Option<&'static str>,
    /// Details string to emit in error envelope
    pub details_message: String,
}

/// Run strict-mode validation on a parsed chain against a context.
pub fn validate_strict(chain: &str, ctx: &ContextLayers) -> ValidationResult {
    let mut flags = ValidationFlags::default();
    let mut missing_literal: Option<String> = None;

    // 1. Schema validation: core shards must be present.
    // Source-verified: Python only requires a minimal core (temporal, modal, literals)
    // for schema_invalid. Vectors with 7/9 shards still get literal_mismatch not schema_invalid.
    // The 3 truly required core shards are:
    let core_shards = &["temporal", "modal", "literals"];
    let merged = &ctx.merged;
    for shard in core_shards {
        if merged.get(*shard).is_none() {
            flags.schema_invalid = true;
            break;
        }
    }

    // Check temporal schema: Python requires both 'now' and 'max_skew_ms' to be non-null integers
    // Source-verified: TEMPORAL_INCOMPLETE_001 has max_skew_ms=null → schema_invalid
    // Python: has_legacy = temp.get("now") is not None and temp.get("max_skew_ms") is not None
    if !flags.schema_invalid {
        if let Some(temporal) = merged.get("temporal") {
            let has_now = temporal.get("now").map(|v| !v.is_null()).unwrap_or(false);
            let has_skew = temporal.get("max_skew_ms").map(|v| !v.is_null()).unwrap_or(false);
            let has_now_us = temporal.get("now_us").map(|v| !v.is_null()).unwrap_or(false);
            if !has_now_us && !(has_now && has_skew) {
                // temporal present but neither (now+max_skew_ms) nor now_us is non-null
                flags.schema_invalid = true;
            }
        }
    }

    // 2. Staleness check (NIP-015)
    // Python's compute_stale_flag: reads temporal.timestamp (from merged temporal shard)
    // NOT local.timestamp. Direction: if (now - ts) > skew -> stale.
    // Source-verified: T-DRIFT uses now=1000, local.timestamp=1678886400000 but
    // temporal has no 'timestamp' key → ts=None → NOT stale → returns truth.
    if !flags.schema_invalid {
        let temporal = ctx.merged.get("temporal").or_else(|| ctx.root.get("temporal"));

        if let Some(temporal) = temporal {
            let now = temporal.get("now").and_then(|v| v.as_f64());
            let max_skew = temporal.get("max_skew_ms").and_then(|v| v.as_f64());
            // Python reads timestamp from temporal shard first, then C_total.get("timestamp")
            let ts = temporal.get("timestamp").and_then(|v| v.as_f64())
                .or_else(|| ctx.merged.get("timestamp").and_then(|v| v.as_f64()));

            if let (Some(now), Some(max_skew), Some(ts)) = (now, max_skew, ts) {
                // Directional check: (now - ts) > skew, NOT abs_diff
                if (now - ts) > max_skew {
                    flags.context_stale = true;
                }
            }
            // If ts is None → cannot determine staleness → returns False (not stale)
        }
    }

    // 3. Map chain tokens to specific flag checks
    validate_chain_operators(chain, ctx, &mut flags, &mut missing_literal);

    // Determine error code and details message
    let (error_code, details_message) = if flags.any_set() {
        let code = flags_to_error_code(&flags);
        let msg = details_for_code(code, &missing_literal, chain, &flags);
        (Some(code), msg)
    } else {
        (None, String::new())
    };

    ValidationResult {
        ok: !flags.any_set(),
        error_code,
        details_message,
        flags,
    }
}

/// Map flags to error code following Python's ERROR_PRIORITY dict (lower number = higher priority).
/// Python priorities: ERR_CONTEXT_INCOMPLETE=1, ERR_CONTEXT_STALE=2, ERR_ACTION_MISUSE=3,
/// epistemic/spatial/demonstrative=4, ERR_INVALID_LITERAL/ERR_LITERAL_MISSING=5
/// Source-verified: ADV_003 has schema_invalid=true AND invalid_literal=true → ERR_CONTEXT_INCOMPLETE wins
fn flags_to_error_code(f: &ValidationFlags) -> &'static str {
    use crate::types::*;
    // Priority 1: completeness (schema shape or missing subsystem)
    if f.schema_invalid { return ERR_CONTEXT_INCOMPLETE; }
    if f.audit_mismatch || f.delivery_mismatch { return ERR_CONTEXT_INCOMPLETE; }
    // Priority 2: staleness
    if f.context_stale { return ERR_CONTEXT_STALE; }
    // Priority 3: action safety
    if f.action_misuse { return ERR_ACTION_MISUSE; }
    // Priority 4: subsystem grounding
    if f.epistemic_mismatch { return ERR_EPISTEMIC_MISMATCH; }
    if f.spatial_mismatch { return ERR_SPATIAL_UNGROUNDABLE; }
    if f.demonstrative_ungrounded { return ERR_DEMONSTRATIVE_UNGROUNDED; }
    // Priority 5: literal/dependency
    if f.invalid_literal { return ERR_INVALID_LITERAL; }
    if f.literal_mismatch { return ERR_LITERAL_MISSING; }
    ERR_BAD_CONTEXT
}

/// Generate the details string matching Python's exact error messages.
/// Source-verified from ground truth vectors.
fn details_for_code(code: &str, missing_literal: &Option<String>, chain: &str, flags: &ValidationFlags) -> String {
    use crate::types::*;
    match code {
        ERR_LITERAL_MISSING => {
            // Python: "Literal '@key' missing"
            if let Some(key) = missing_literal {
                format!("Literal '@{key}' missing")
            } else {
                // Extract first @literal from chain text
                let key = chain.split_whitespace()
                    .find(|t| t.starts_with('@'))
                    .unwrap_or("@unknown");
                format!("Literal '{key}' missing")
            }
        }
        ERR_INVALID_LITERAL => {
            // Python: "Malformed literal '@bad-char'"
            // Source-verified: LIT_003 expects 'Malformed literal @bad-char'
            let key = chain.split_whitespace()
                .find(|t| t.starts_with('@'))
                .unwrap_or("@unknown");
            format!("Malformed literal '{key}'")
        }
        ERR_CONTEXT_STALE => "Context is stale based on timestamp/skew".to_string(),
        ERR_EPISTEMIC_MISMATCH => "Epistemic knowledge check failed".to_string(),
        ERR_CONTEXT_INCOMPLETE => {
            // Flag-specific details messages (source-verified from ground truth)
            if flags.audit_mismatch {
                "Missing audit subsystem".to_string()
            } else if flags.delivery_mismatch {
                "C.delivery must be an object in strict mode".to_string()
            } else {
                // schema_invalid → default Python message
                format!("Context shape invalid: {ERR_CONTEXT_INCOMPLETE}")
            }
        }
        ERR_SPATIAL_UNGROUNDABLE => "Spatial grounding failed".to_string(),
        ERR_DEMONSTRATIVE_UNGROUNDED => "Missing spatial grounding".to_string(),
        _ => format!("Context shape invalid: {code}"),
    }
}

/// Check chain operators against context to set validation flags.
fn validate_chain_operators(
    chain: &str,
    ctx: &ContextLayers,
    flags: &mut ValidationFlags,
    missing_literal: &mut Option<String>,
) {
    use crate::context::normalize_literal_key;

    let literal_refs: Vec<String> = extract_literal_refs(chain);

    // Invalid literal check: runs BEFORE schema_invalid early return
    // Source-verified: ADV_003 has schema_invalid=true AND invalid_literal=true — Python checks both
    // Python: validates @-references with non-alphanumeric/non-underscore characters REGARDLESS of schema
    for lit_key in &literal_refs {
        let bare = normalize_literal_key(lit_key);
        // Valid literal keys: alphanumeric and underscore only (no hyphens, dots, emoji, etc.)
        if !bare.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
            flags.invalid_literal = true;
        }
    }

    if flags.schema_invalid {
        return;
    }

    let merged = &ctx.merged;

    let has_shi = chain.contains("shi ");
    let has_vek = chain.contains("vek ");
    let has_sha = chain.contains("sha ");
    let has_any_epistemic = has_shi || has_vek || has_sha;

    for lit_key in &literal_refs {
        let key = normalize_literal_key(lit_key);

        if has_any_epistemic && chain_contains_epistemic_for(chain, lit_key) {
            let modal_shard = merged.get("modal");
            let in_k = ctx_shard_has_key(modal_shard.and_then(|m| m.get("knowledge")), &key);
            let in_b = ctx_shard_has_key(modal_shard.and_then(|m| m.get("belief")), &key);
            let in_c = ctx_shard_has_key(modal_shard.and_then(|m| m.get("certainty")), &key);

            if !in_k && !in_b && !in_c {
                // Key not in any modal shard — check literals shard
                let in_lit = ctx_shard_has_key(merged.get("literals"), &key);
                if !in_lit {
                    // Truly missing — flag literal_mismatch (Python: validator short-circuits
                    // before evaluator for shi/vek/sha with missing keys)
                    // Source-verified: UNGROUNDED_001 expects ERR_LITERAL_MISSING for shi @foo
                    // where @foo is in neither knowledge nor literals.
                    flags.literal_mismatch = true;
                    if missing_literal.is_none() {
                        *missing_literal = Some(key.clone());
                    }
                }
                // else: key in literals but not modal — evaluator handles
            }
            // else: key found in modal, evaluator handles the outcome
        } else {
            let in_literals = ctx_shard_has_key(merged.get("literals"), &key);
            if !in_literals {
                flags.literal_mismatch = true;
                if missing_literal.is_none() {
                    *missing_literal = Some(key.clone());
                }
            }
        }
    }

    // For 'mek'/'men' verbs (action/audit): audit shard must exist in context
    // Source-verified: AUD_002 (men) and UNGROUNDED_002 (mek) both expect ERR_CONTEXT_INCOMPLETE with audit_mismatch=true
    let has_action_verb = chain.contains("mek ") || chain.contains(" mek ")
        || chain.contains("men ") || chain.contains(" men ") || chain.ends_with(" mek") || chain.ends_with(" men");
    if has_action_verb {
        if merged.get("audit").is_none() {
            flags.audit_mismatch = true;
        }
    }

    // For 'vus'/'vel' verbs (delivery): delivery shard must exist in context as an object
    // Source-verified: DEL_003 delivery='broken' (string not object) → delivery_mismatch=true
    let has_delivery_verb = chain.contains("vus ") || chain.contains(" vus ")
        || chain.contains("vel ") || chain.contains(" vel ")
        || chain.ends_with(" vus") || chain.ends_with(" vel");
    if has_delivery_verb {
        // Check delivery shard exists AND is an object (not string/null/other)
        let delivery_ok = merged.get("delivery")
            .map(|d| matches!(d, serde_json::Value::Object(_)))
            .unwrap_or(false);
        if !delivery_ok {
            flags.delivery_mismatch = true;
        }
    }

    // For 'dia'/'doq' demonstratives: spatial.thresholds must have 'near'/'far' key
    // Source-verified: DEM_005 expects ERR_DEMONSTRATIVE_UNGROUNDED with demonstrative_ungrounded=true
    // when spatial.thresholds = {} (empty, missing 'near' key)
    let has_dia = chain.contains(" dia ") || chain.ends_with(" dia") || chain.starts_with("dia ");
    let has_doq = chain.contains(" doq ") || chain.ends_with(" doq") || chain.starts_with("doq ");
    if has_dia || has_doq {
        // Check if demonstratives shard has direct binding (if so, skip spatial check)
        let has_direct_binding = if has_dia {
            ctx_shard_has_key(merged.get("demonstratives"), "dia")
                || ctx_shard_has_key(merged.get("demonstratives"), "proximal")
        } else {
            ctx_shard_has_key(merged.get("demonstratives"), "doq")
                || ctx_shard_has_key(merged.get("demonstratives"), "distal")
        };

        if !has_direct_binding {
            let threshold_key = if has_dia { "near" } else { "far" };
            let has_threshold = merged
                .get("spatial")
                .and_then(|s| s.get("thresholds"))
                .map(|t| if let serde_json::Value::Object(m) = t { m.contains_key(threshold_key) } else { false })
                .unwrap_or(false);

            if !has_threshold {
                flags.demonstrative_ungrounded = true;
            }
        }
    }

    // Suppress literal_mismatch when invalid_literal is set — Python never sets both simultaneously
    if flags.invalid_literal {
        flags.literal_mismatch = false;
    }
}



/// Extract @literals from chain text — includes both valid and invalid literal references
/// Source-verified: ADV_003 chain '@🚀 | nek' → @🚀 should be extracted and flagged invalid
fn extract_literal_refs(chain: &str) -> Vec<String> {
    let mut refs = Vec::new();
    for token in chain.split_whitespace() {
        if let Some(rest) = token.strip_prefix('@') {
            // Strip trailing punctuation/brackets that are part of chain syntax not the key.
            // E.g. chain '(shi @alpha)' → split gives '@alpha)' → strip ')' to get 'alpha'.
            // Also preserve unicode emoji keys by not stripping non-ASCII chars.
            let key = rest.trim_end_matches(|c: char| matches!(c, '(' | ')' | '[' | ']' | '{' | '}' | ',' | ';' | '.' | '|' | '&'));
            if !key.is_empty() {
                refs.push(key.to_string());
            }
        }
    }
    refs
}

/// Check if the chain uses an epistemic operator before this literal
fn chain_contains_epistemic_for(chain: &str, literal: &str) -> bool {
    let has_epistemic = chain.contains("shi ") || chain.contains("vek ") || chain.contains("sha ");
    let has_literal = chain.contains(&format!("@{}", literal));
    has_epistemic && has_literal
}

/// Check if a specific operator appears before the given literal in the chain.
/// Scaffolding for future chain-structure validation rules.
#[allow(dead_code)]
fn chain_op_before_literal(chain: &str, op: &str, literal: &str) -> bool {
    let op_pos = chain.find(&format!("{op} ")).or_else(|| chain.find(&format!("{op}\t")));
    let lit_pos = chain.find(&format!("@{literal}"));
    match (op_pos, lit_pos) {
        (Some(o), Some(l)) => o < l,
        // If only op is present (no literal found), return true as fallback
        (Some(_), None) => true,
        _ => false,
    }
}

/// Look up a key in a context shard, trying both '@key' and 'key' forms.
fn ctx_shard_has_key(shard_val: Option<&Value>, key: &str) -> bool {
    if let Some(Value::Object(map)) = shard_val {
        map.contains_key(key) || map.contains_key(&format!("@{key}"))
    } else {
        false
    }
}

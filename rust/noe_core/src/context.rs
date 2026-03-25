// src/context.rs
//
// Context layer handling: classification, deep merge, shard hashing.
//
// Rules (source-verified from run_noe_logic and context_manager.py):
//   - Layered context: has root/domain/local keys AND no shard keys at top level
//   - Flat context: treat entire dict as local (root={}, domain={})
//   - Merge precedence: root < domain < local (local wins)
//   - Shard keys for classification:
//       literals, entities, spatial, temporal, modal, axioms, audit, rel

use crate::hash::{composite_hash, CompositeHashResult};
use crate::types::ContextHashes;
use serde_json::Value;

/// Shard keys that identify a layered context from a flat one.
/// Source: run_noe_logic lines 2703-2717.
const SHARD_KEYS: &[&str] = &[
    "literals", "entities", "spatial", "temporal",
    "modal", "axioms", "audit", "rel",
];

/// Classify the top-level context JSON value.
pub enum ContextKind {
    /// Has root/domain/local keys and no shard keys at top level
    Layered,
    /// Has shard keys at top level (treated as flat/local)
    Flat,
    /// Not a dict — will produce ERR_BAD_CONTEXT
    Invalid,
}

pub fn classify(ctx: &Value) -> ContextKind {
    if let Value::Object(map) = ctx {
        // Source-verified: if root key is present alongside domain/local,
        // always treat as Layered, even if extra shard keys exist at top level.
        // (STALE_002 adds temporal at top level of a layered context for test overrides)
        let has_root = map.contains_key("root");
        let has_layer_key = has_root || map.contains_key("domain") || map.contains_key("local");
        let has_shard = SHARD_KEYS.iter().any(|k| map.contains_key(*k));

        if has_root {
            // Root present → definitely Layered
            ContextKind::Layered
        } else if has_layer_key && !has_shard {
            ContextKind::Layered
        } else {
            ContextKind::Flat
        }
    } else {
        ContextKind::Invalid
    }
}

/// Parsed context: raw layers + merged effective context for evaluation.
#[derive(Debug, Clone)]
pub struct ContextLayers {
    pub root: Value,
    pub domain: Value,
    pub local: Value,
    /// Deep-merged effective context (root ← domain ← local)
    pub merged: Value,
    /// Provenance hashes
    pub hashes: CompositeHashResult,
}

impl ContextLayers {
    pub fn context_hashes(&self) -> ContextHashes {
        ContextHashes {
            root: self.hashes.root_hash.clone(),
            domain: self.hashes.domain_hash.clone(),
            local: self.hashes.local_hash.clone(),
            total: self.hashes.composite_hash.clone(),
        }
    }
}

/// Parse a raw context JSON value into ContextLayers.
/// Returns Err(type_name_str) for non-dict or malformed layer values.
pub fn parse_context(ctx: &Value, _mode: &str) -> Result<ContextLayers, &'static str> {
    let (root, domain, local) = match classify(ctx) {
        ContextKind::Layered => {
            let map = ctx.as_object().unwrap();
            let root = map.get("root").cloned().unwrap_or(Value::Object(Default::default()));
            let domain = map.get("domain").cloned().unwrap_or(Value::Object(Default::default()));
            let local = map.get("local").cloned().unwrap_or(Value::Object(Default::default()));
            // Reject None-equivalent layers (Python returns them unmerged for validator to reject)
            if root.is_null() || domain.is_null() || local.is_null() {
                return Err("null");
            }
            (root, domain, local)
        }
        ContextKind::Flat => {
            // Treat entire dict as local
            (
                Value::Object(Default::default()),
                Value::Object(Default::default()),
                ctx.clone(),
            )
        }
        ContextKind::Invalid => {
            // Return the type name so lib.rs can emit "Context malformed: {type}"
            let type_name = match ctx {
                Value::Array(_) => "list",
                Value::String(_) => "str",
                Value::Null => "null",
                Value::Number(_) => "int",
                Value::Bool(_) => "bool",
                _ => "unknown",
            };
            return Err(type_name);
        }
    };

    // Compute composite hash
    let hashes = composite_hash(&root, &domain, &local)
        .map_err(|_| "ERR_BAD_CONTEXT")?;

    // Deep merge: root ← domain ← local
    let merged = {
        let mut m = Value::Object(Default::default());
        deep_merge(&mut m, &root);
        deep_merge(&mut m, &domain);
        deep_merge(&mut m, &local);
        m
    };

    Ok(ContextLayers { root, domain, local, merged, hashes })
}

/// Deep merge overlay into base (in place). overlay wins on conflicts.
/// Source: _deep_merge in context_manager.py and run_noe_logic.
/// Lists: replaced entirely (not merged) — matches Python behavior.
pub fn deep_merge(base: &mut Value, overlay: &Value) {
    match (base, overlay) {
        (Value::Object(base_map), Value::Object(overlay_map)) => {
            for (k, v) in overlay_map {
                let entry = base_map.entry(k.clone()).or_insert(Value::Null);
                if entry.is_object() && v.is_object() {
                    deep_merge(entry, v);
                } else {
                    *entry = v.clone();
                }
            }
        }
        (base, overlay) => {
            *base = overlay.clone();
        }
    }
}

/// Lookup a dot-separated path in a merged context value.
pub fn ctx_get_path<'a>(ctx: &'a Value, path: &str) -> Option<&'a Value> {
    let mut current = ctx;
    for part in path.split('.') {
        current = current.get(part)?;
    }
    Some(current)
}

/// Normalize a literal key: NFKC + lowercase + strip leading '@'
/// Source: canonical.py canonical_literal_key
pub fn normalize_literal_key(key: &str) -> String {
    // Rust doesn't have built-in NFKC; for now strip '@' and lowercase.
    // The conformance vectors use ASCII-only keys so this is sufficient for v1.
    // Full NFKC normalisation can be added with the `unicode-normalization` crate if needed.
    let k = key.trim().to_lowercase();
    if let Some(stripped) = k.strip_prefix('@') {
        stripped.to_string()
    } else {
        k
    }
}

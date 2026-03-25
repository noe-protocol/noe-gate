// src/eval.rs  —  Stage 1: K3 core + epistemic + literals + numeric + error dispatch
//
// Evaluates a parsed Noe AST against a merged context.
// Output is NoeVal (internal), converted to EvalResult by run_noe_logic.
//
// Operator semantics source: noe_parser.py NoeEvaluator

use crate::ast::*;
use crate::context::{ctx_get_path, normalize_literal_key, ContextLayers};
use crate::types::*;
use serde_json::Value;
use sha2::{Digest, Sha256};

// ---------------------------------------------------------------------------
// Evaluation entry point
// ---------------------------------------------------------------------------

pub fn eval(expr: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    eval_expr(expr, ctx, mode)
}

fn eval_expr(expr: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    match expr {
        Expr::Bool(b) => NoeVal::Truth(*b),
        Expr::Undefined => NoeVal::Undefined,
        Expr::Number(s) => eval_number(s),
        Expr::Glyph(w) => eval_glyph(w, ctx, mode),
        Expr::Literal(name) => eval_literal_lookup(name, ctx, mode),
        Expr::Demonstrative(kind) => eval_demonstrative(kind, ctx, mode),

        Expr::UnaryOp { op, operand } => eval_unary(op, operand, ctx, mode),
        Expr::BinOp { op, left, right } => eval_binop(op, left, right, ctx, mode),
        Expr::Action { verb, target } => eval_action(verb, target, ctx, mode),
        Expr::Question { q_type, body } => eval_question(q_type, body, ctx, mode),
        Expr::Conditional { cond, guard } => eval_conditional(cond, guard, ctx, mode),
    }
}

// ---------------------------------------------------------------------------
// Atom evaluation
// ---------------------------------------------------------------------------

fn eval_number(s: &str) -> NoeVal {
    match s.parse::<f64>() {
        Ok(f) => NoeVal::Numeric(f),
        Err(_) => NoeVal::Error {
            code: ERR_INVALID_NUMBER,
            message: format!("Cannot parse number: {s}"),
        },
    }
}

/// Bare glyph: look up in context as a key, otherwise undefined.
fn eval_glyph(name: &str, ctx: &ContextLayers, _mode: &str) -> NoeVal {
    // Try literals shard first
    if let Some(v) = ctx_get_path(&ctx.merged, &format!("literals.{name}")) {
        return val_from_json(v);
    }
    NoeVal::Undefined
}

/// @literal lookup: context.literals[key]
fn eval_literal_lookup(name: &str, ctx: &ContextLayers, _mode: &str) -> NoeVal {
    let key = normalize_literal_key(name);
    // Look in literals shard
    if let Some(literals) = ctx_get_path(&ctx.merged, "literals") {
        if let Some(v) = literals.get(&key) {
            return NoeVal::Literal { key: key.clone(), value: v.clone() };
        }
    }
    // Not found → domain literal (domain the key itself as a raw literal reference)
    NoeVal::Literal { key, value: Value::Null }
}

fn val_from_json(v: &Value) -> NoeVal {
    match v {
        Value::Bool(b) => NoeVal::Truth(*b),
        Value::Number(n) => {
            if let Some(f) = n.as_f64() { NoeVal::Numeric(f) } else { NoeVal::Undefined }
        }
        Value::Null => NoeVal::Undefined,
        Value::String(_) | Value::Array(_) | Value::Object(_) => {
            // Structural — wrap as literal value
            NoeVal::Literal { key: String::new(), value: v.clone() }
        }
    }
}

// ---------------------------------------------------------------------------
// Demonstrative (Stage 2: dia/doq)
// ---------------------------------------------------------------------------

fn eval_demonstrative(kind: &str, ctx: &ContextLayers, _mode: &str) -> NoeVal {
    // Look up ctx.demonstratives[kind]
    if let Some(demo) = ctx_get_path(&ctx.merged, "demonstratives") {
        if let Some(v) = demo.get(kind) {
            return val_from_json(v);
        }
    }
    NoeVal::Undefined
}

// ---------------------------------------------------------------------------
// Unary operators
// ---------------------------------------------------------------------------

fn eval_unary(op: &UnaryOp, operand: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    match op {
        // Epistemic operators: lookup in modal.{knowledge,belief,certainty}
        UnaryOp::Shi => eval_epistemic("knowledge", operand, ctx, mode),
        UnaryOp::Vek => eval_epistemic("belief", operand, ctx, mode),
        UnaryOp::Sha => eval_sha_certainty(operand, ctx, mode),

        // Logic NOTs
        UnaryOp::Nai => {
            let v = eval_expr(operand, ctx, mode);
            k3_not(v)
        }
        UnaryOp::Nex => {
            let v = eval_expr(operand, ctx, mode);
            k3_not(v)
        }

        // Modal (possibility/necessity) — evaluate operand through modal lens
        UnaryOp::Tor | UnaryOp::Da => eval_expr(operand, ctx, mode),

        // Temporal operators: evaluate operand (grounding handled by context)
        UnaryOp::Nau | UnaryOp::Ret | UnaryOp::Tri => eval_expr(operand, ctx, mode),

        // Deontic: qer/eni/sem — evaluate operand
        UnaryOp::Qer | UnaryOp::Eni | UnaryOp::Sem => eval_expr(operand, ctx, mode),

        // Normative
        UnaryOp::Mun | UnaryOp::Fiu => eval_expr(operand, ctx, mode),

        // Delivery verbs: vus/vel produce action envelopes with hashes
        UnaryOp::Vus => eval_action("vus", operand, ctx, mode),
        UnaryOp::Vel => eval_action("vel", operand, ctx, mode),
    }
}

/// shi/vek/sha: look up operand (must be a literal) in ctx.modal.{shard}
fn eval_epistemic(shard: &str, operand: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    // If the operand is a Demonstrative (e.g. 'dia'), check if it exists in context.demonstratives.
    // Python's visit_demonstrative:
    //   1. Check demonstratives dict for direct binding (entity reference)
    //   2. Fall back to spatial resolution: entities.distance <= spatial.thresholds.near (for 'dia')
    //      exactly one candidate → truth; zero or multiple → undefined
    //   3. DEM_005 has thresholds={} → no 'near' key → undefined (validator catches → demonstrative_ungrounded)
    if let Expr::Demonstrative(dem_kind) = operand {
        use serde_json::Value;

        // Step 1: Check demonstratives dict
        if let Some(demo) = ctx.merged.get("demonstratives") {
            // Bind by dem_type or "proximal"/"distal" alias
            let alias = if dem_kind == "dia" { "proximal" } else { "distal" };
            if let Some(_binding) = demo.get(dem_kind.as_str()).or_else(|| demo.get(alias)) {
                // Direct binding exists: entity referenced
                return NoeVal::Truth(true);
            }
            // demonstratives shard found but no binding for this dem_type → fall through to spatial
        }

        // Step 2: Spatial resolution fallback
        let entities = ctx.merged.get("entities");
        let spatial = ctx.merged.get("spatial");

        // Must have spatial.thresholds
        let thresholds = spatial
            .and_then(|s| s.get("thresholds"))
            .and_then(|t| if let Value::Object(m) = t { Some(m) } else { None });

        if thresholds.is_none() {
            return NoeVal::Undefined;
        }

        let threshold_key = if dem_kind == "dia" { "near" } else { "far" };
        let limit = thresholds.unwrap().get(threshold_key).and_then(|v| v.as_f64());
        if limit.is_none() {
            // No threshold value for this demonstrative type → undefined
            return NoeVal::Undefined;
        }
        let limit = limit.unwrap();

        // Count candidate entities within distance threshold
        let mut candidates = 0usize;
        if let Some(Value::Object(ents)) = entities {
            for (_ent_id, ent_data) in ents.iter() {
                if let Value::Object(ent) = ent_data {
                    if let Some(dist_val) = ent.get("distance").and_then(|v| v.as_f64()) {
                        let matches = if dem_kind == "dia" {
                            dist_val <= limit
                        } else {
                            dist_val >= limit
                        };
                        if matches {
                            candidates += 1;
                        }
                    }
                }
            }
        }

        // Exactly one candidate → truth; zero or multiple → undefined
        if candidates == 1 {
            return NoeVal::Truth(true);
        }
        return NoeVal::Undefined;
    }

    // The operand should evaluate to a literal key (or numeric literal)
    let key = match operand {
        Expr::Literal(name) => normalize_literal_key(name),
        Expr::Glyph(w) => w.clone(),
        Expr::Number(_) => {
            // Numeric operand for epistemic (e.g. 'shi 123') — not a valid key; return undefined
            return NoeVal::Undefined;
        }
        _ => {
            let v = eval_expr(operand, ctx, mode);
            match v {
                NoeVal::Literal { key, .. } => key,
                NoeVal::Numeric(_) => return NoeVal::Undefined, // numeric result → undefined for epistemic
                other => return other,
            }
        }
    };

    // Look up in modal.{shard} — context may store keys as 'key' OR '@key'
    // Try both forms (source-verified: EDET contexts use '@fact' as key in modal.knowledge)
    let modal = ctx.merged.get("modal");
    let shard_obj = modal.and_then(|m| m.get(shard));

    let val = if let Some(Value::Object(map)) = shard_obj {
        // Try bare key first, then @key
        map.get(&key).or_else(|| map.get(&format!("@{key}")))
    } else {
        None
    };

    if let Some(v) = val {
        match v {
            Value::Bool(b) => return NoeVal::Truth(*b),
            Value::Number(n) => {
                if let Some(f) = n.as_f64() {
                    // For certainty (sha): apply threshold check
                    if shard == "certainty" {
                        let threshold = 0.5_f64; // NIP-015 default certainty threshold
                        if f < threshold {
                            if mode == "strict" {
                                return NoeVal::Error {
                                    code: ERR_EPISTEMIC_MISMATCH,
                                    message: format!(
                                        "Certainty check failed for '{key}' (threshold {threshold}) in strict mode",
                                        threshold = threshold
                                    ),
                                };
                            }
                            return NoeVal::Undefined;
                        }
                        return NoeVal::Truth(true);
                    }
                    // For knowledge/belief: truth if > 0.0
                    return NoeVal::Truth(f > 0.0);
                }
            }
            Value::Null => return NoeVal::Undefined,
            _ => {}
        }
    }

    // Key not in modal shard → error semantics depend on shard type
    // Only 'knowledge' (shi) errors in strict mode; belief/certainty return Undefined
    // Source-verified: EDET_006 vek @unknown belief-miss → undefined, not error
    if shard == "knowledge" && mode == "strict" {
        NoeVal::Error {
            code: ERR_EPISTEMIC_MISMATCH,
            message: format!("Knowledge check failed for '{key}' in strict mode"),
        }
    } else if shard == "certainty" && mode == "strict" {
        // Certainty key missing → also an error in strict mode
        let threshold = 0.5_f64;
        NoeVal::Error {
            code: ERR_EPISTEMIC_MISMATCH,
            message: format!("Certainty check failed for '{key}' (threshold {threshold}) in strict mode"),
        }
    } else {
        NoeVal::Undefined
    }
}

/// SHA operator: certainty-gated truth check.
/// Mirrors Python sha semantics exactly:
/// 1. certainty_threshold must be in modal or error in strict
/// 2. cert_level from certainty shard (defaults to 0.0 if key absent)
/// 3. cert_level >= threshold → look up truth value in knowledge then belief
/// 4. below threshold → ERR_EPISTEMIC_MISMATCH in strict, undefined in partial
fn eval_sha_certainty(operand: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    use crate::context::normalize_literal_key;
    use serde_json::Value;

    // Handle numeric operand → undefined 
    if matches!(operand, Expr::Number(_)) {
        return NoeVal::Undefined;
    }

    let key = match operand {
        Expr::Literal(name) => normalize_literal_key(name),
        Expr::Glyph(w) => w.clone(),
        Expr::Demonstrative(d) => d.clone(),
        _ => {
            let v = eval_expr(operand, ctx, mode);
            match v {
                NoeVal::Literal { key, .. } => key,
                NoeVal::Numeric(_) => return NoeVal::Undefined,
                other => return other,
            }
        }
    };

    let modal = ctx.merged.get("modal");

    // Step 1: Check if certainty_threshold is in modal
    let threshold = if let Some(Value::Object(modal_map)) = modal {
        if let Some(t) = modal_map.get("certainty_threshold") {
            t.as_f64().unwrap_or(0.5)
        } else if mode == "strict" {
            // strict mode: missing certainty_threshold is an error
            return NoeVal::Error {
                code: ERR_EPISTEMIC_MISMATCH,
                message: format!("Cannot evaluate sha '{key}': certainty_threshold is not defined in modal subsystem (strict mode)"),
            };
        } else {
            0.8 // legacy fallback for non-strict
        }
    } else {
        if mode == "strict" {
            return NoeVal::Error {
                code: ERR_EPISTEMIC_MISMATCH,
                message: format!("Cannot evaluate sha '{key}': certainty_threshold is not defined in modal subsystem (strict mode)"),
            };
        }
        0.8
    };

    // Step 2: Look up cert_level in certainty shard (default 0.0 if not found)
    let certainty_shard = modal.and_then(|m| m.get("certainty"));
    let cert_level = if let Some(Value::Object(cm)) = certainty_shard {
        cm.get(&key)
            .or_else(|| cm.get(&format!("@{key}")))
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0)
    } else {
        0.0
    };

    // Step 3: Compare cert_level >= threshold
    if cert_level >= threshold {
        // Look up truth value in knowledge then belief
        let knowledge = modal.and_then(|m| m.get("knowledge"));
        if let Some(Value::Object(km)) = knowledge {
            if let Some(v) = km.get(&key).or_else(|| km.get(&format!("@{key}"))) {
                return match v {
                    Value::Bool(b) => NoeVal::Truth(*b),
                    Value::Number(n) => NoeVal::Truth(n.as_f64().unwrap_or(0.0) > 0.0),
                    _ => NoeVal::Undefined,
                };
            }
        }
        let belief = modal.and_then(|m| m.get("belief"));
        if let Some(Value::Object(bm)) = belief {
            if let Some(v) = bm.get(&key).or_else(|| bm.get(&format!("@{key}"))) {
                return match v {
                    Value::Bool(b) => NoeVal::Truth(*b),
                    Value::Number(n) => NoeVal::Truth(n.as_f64().unwrap_or(0.0) > 0.0),
                    _ => NoeVal::Undefined,
                };
            }
        }
        // High certainty but no truth value available
        if mode == "strict" {
            return NoeVal::Error {
                code: ERR_EPISTEMIC_MISMATCH,
                message: format!("Certainty check passed for '{key}' but no truth value found in strict mode"),
            };
        }
        return NoeVal::Undefined;
    }

    // Step 4: Below threshold
    if mode == "strict" {
        NoeVal::Error {
            code: ERR_EPISTEMIC_MISMATCH,
            message: format!("Certainty check failed for '{key}' (threshold {threshold}) in strict mode"),
        }
    } else {
        NoeVal::Undefined
    }
}

// ---------------------------------------------------------------------------
// K3 three-valued logic
// ---------------------------------------------------------------------------

fn k3_not(v: NoeVal) -> NoeVal {
    match v {
        NoeVal::Truth(b) => NoeVal::Truth(!b),
        NoeVal::Undefined => NoeVal::Undefined,
        other => other,
    }
}

fn k3_and(left: NoeVal, right_expr: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    // Short-circuit: false AND _ = false
    match &left {
        NoeVal::Truth(false) => return NoeVal::Truth(false),
        NoeVal::Error { .. } => return left,
        _ => {}
    }
    let right = eval_expr(right_expr, ctx, mode);
    match (left, right) {
        (NoeVal::Truth(a), NoeVal::Truth(b)) => NoeVal::Truth(a && b),
        (NoeVal::Truth(false), _) | (_, NoeVal::Truth(false)) => NoeVal::Truth(false),
        (NoeVal::Error { code, message }, _) | (_, NoeVal::Error { code, message }) => {
            NoeVal::Error { code, message }
        }
        // undef AND true = undef; undef AND undef = undef
        _ => NoeVal::Undefined,
    }
}

fn k3_or(left: NoeVal, right_expr: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    // Short-circuit: true OR _ = true
    match &left {
        NoeVal::Truth(true) => return NoeVal::Truth(true),
        NoeVal::Error { .. } => return left,
        _ => {}
    }
    let right = eval_expr(right_expr, ctx, mode);
    match (left, right) {
        (NoeVal::Truth(a), NoeVal::Truth(b)) => NoeVal::Truth(a || b),
        (NoeVal::Truth(true), _) | (_, NoeVal::Truth(true)) => NoeVal::Truth(true),
        (NoeVal::Error { code, message }, _) | (_, NoeVal::Error { code, message }) => {
            NoeVal::Error { code, message }
        }
        _ => NoeVal::Undefined,
    }
}

// ---------------------------------------------------------------------------
// Binary operators
// ---------------------------------------------------------------------------

fn eval_binop(op: &BinOp, left: &Expr, right: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    match op {
        BinOp::An => {
            let lv = eval_expr(left, ctx, mode);
            k3_and(lv, right, ctx, mode)
        }
        BinOp::Ur => {
            let lv = eval_expr(left, ctx, mode);
            k3_or(lv, right, ctx, mode)
        }
        // Numeric comparisons
        BinOp::Lt | BinOp::Gt | BinOp::Le | BinOp::Ge | BinOp::Eq => {
            let lv = eval_expr(left, ctx, mode);
            let rv = eval_expr(right, ctx, mode);
            eval_numeric_cmp(op, lv, rv)
        }
        // noq: request from subject to execute action
        // Pattern: @subject noq @action  left=Literal(subject), right=action_verb_expr
        BinOp::Noq => eval_request(left, right, ctx, mode),
        // Spatial binary operators: nel/tel/xel/en/tra/fra
        BinOp::Nel | BinOp::Tel | BinOp::Xel | BinOp::En | BinOp::Tra | BinOp::Fra => {
            eval_spatial(op, left, right, ctx)
        }
        // Other binary operators — evaluate both sides (stub for stage 2+)
        _ => {
            let lv = eval_expr(left, ctx, mode);
            let rv = eval_expr(right, ctx, mode);
            match (lv, rv) {
                (e @ NoeVal::Error { .. }, _) | (_, e @ NoeVal::Error { .. }) => e,
                _ => NoeVal::Undefined,
            }
        }
    }
}

fn eval_spatial(op: &BinOp, left: &Expr, right: &Expr, ctx: &ContextLayers) -> NoeVal {
    eval_spatial_inner(op, left, right, ctx).unwrap_or(NoeVal::Undefined)
}

fn eval_spatial_inner(op: &BinOp, left: &Expr, right: &Expr, ctx: &ContextLayers) -> Option<NoeVal> {
    use crate::context::normalize_literal_key;
    use serde_json::Value;

    // Extract entity key from literal operand (strip @ prefix)
    let entity_key = |expr: &Expr| -> Option<String> {
        match expr {
            Expr::Literal(name) => Some(normalize_literal_key(name)),
            Expr::Glyph(w) => Some(w.clone()),
            _ => None,
        }
    };

    let key_l = entity_key(left)?;
    let key_r = entity_key(right)?;

    // Get entities from context
    let entities = ctx.merged.get("entities");
    let get_pos = |key: &str| -> Option<[f64; 2]> {
        if let Some(Value::Object(ents)) = entities {
            if let Some(Value::Object(ent)) = ents.get(key) {
                if let Some(Value::Array(pos)) = ent.get("position") {
                    let x = pos.get(0)?.as_f64()?;
                    let y = pos.get(1)?.as_f64()?;
                    return Some([x, y]);
                }
            }
        }
        None
    };

    let get_vel = |key: &str| -> Option<[f64; 2]> {
        if let Some(Value::Object(ents)) = entities {
            if let Some(Value::Object(ent)) = ents.get(key) {
                if let Some(Value::Array(vel)) = ent.get("velocity").or_else(|| ent.get("vel")) {
                    let vx = vel.get(0)?.as_f64()?;
                    let vy = vel.get(1)?.as_f64()?;
                    return Some([vx, vy]);
                }
            }
        }
        None
    };

    let pos_l = get_pos(&key_l)?;
    let pos_r = get_pos(&key_r)?;

    let dx = pos_r[0] - pos_l[0];
    let dy = pos_r[1] - pos_l[1];
    let dist = (dx * dx + dy * dy).sqrt();

    let spatial = ctx.merged.get("spatial");

    match op {
        BinOp::Nel => {
            let limit = spatial
                .and_then(|s| s.get("thresholds"))
                .and_then(|t| t.get("near"))
                .and_then(|v| v.as_f64())?;
            Some(NoeVal::Truth(dist <= limit))
        }
        BinOp::Tel => {
            let limit = spatial
                .and_then(|s| s.get("thresholds"))
                .and_then(|t| t.get("far"))
                .and_then(|v| v.as_f64())?;
            Some(NoeVal::Truth(dist >= limit))
        }
        BinOp::Xel => {
            let orientation = spatial.and_then(|s| s.get("orientation"));
            let target_angle = orientation.and_then(|o| o.get("target")).and_then(|v| v.as_f64())?;
            let tolerance = orientation.and_then(|o| o.get("tolerance")).and_then(|v| v.as_f64())?;
            let angle = dy.atan2(dx).to_degrees();
            let mut diff = (angle - target_angle).abs() % 360.0;
            if diff > 180.0 { diff = 360.0 - diff; }
            Some(NoeVal::Truth(diff <= tolerance))
        }
        BinOp::En => {
            let radius = if let Some(Value::Object(ents)) = entities {
                ents.get(&key_r)
                    .and_then(|e| e.get("radius"))
                    .and_then(|v| v.as_f64())
            } else {
                None
            }?;
            Some(NoeVal::Truth(dist <= radius))
        }
        BinOp::Tra | BinOp::Fra => {
            let cone = spatial.and_then(|s| s.get("cone"));
            let d_min = cone.and_then(|c| c.get("d_min")).and_then(|v| v.as_f64()).unwrap_or(0.0);
            if dist < d_min {
                return Some(NoeVal::Undefined);
            }
            let vel = get_vel(&key_l)?;
            let vx = vel[0]; let vy = vel[1];
            let speed = (vx * vx + vy * vy).sqrt();
            let v_min = cone.and_then(|c| c.get("v_min")).and_then(|v| v.as_f64()).unwrap_or(0.0);
            if speed < v_min {
                return Some(NoeVal::Undefined);
            }
            let nx = vx / speed; let ny = vy / speed;
            let rx = dx / dist; let ry = dy / dist;
            let dot = nx * rx + ny * ry;
            let limit = cone.and_then(|c| c.get("cos_theta")).and_then(|v| v.as_f64()).unwrap_or(0.707);
            if *op == BinOp::Tra {
                Some(NoeVal::Truth(dot >= limit))
            } else {
                Some(NoeVal::Truth(dot <= -limit))
            }
        }
        _ => None,
    }
}

fn eval_numeric_cmp(op: &BinOp, lv: NoeVal, rv: NoeVal) -> NoeVal {
    let (l, r) = match (lv, rv) {
        (NoeVal::Numeric(a), NoeVal::Numeric(b)) => (a, b),
        _ => return NoeVal::Undefined,
    };
    let result = match op {
        BinOp::Lt => l < r,
        BinOp::Gt => l > r,
        BinOp::Le => l <= r,
        BinOp::Ge => l >= r,
        BinOp::Eq => (l - r).abs() < f64::EPSILON,
        _ => unreachable!(),
    };
    NoeVal::Truth(result)
}

// ---------------------------------------------------------------------------
// Actions (vus/mek/men/noq and related)
// ---------------------------------------------------------------------------

/// Determine action kind from verb.
/// Source-verified from ground truth vectors.
fn action_kind(verb: &str) -> &'static str {
    match verb {
        "vus" | "vel" => "delivery",
        "men" => "audit",
        "mek" => "execution",
        "noq" => "request",
        "sek" => "halt",
        _ => "action",
    }
}

fn eval_action(verb: &str, target: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    

    let kind = action_kind(verb);
    let context_hash = ctx.hashes.context_hash.clone();

    match kind {
        "delivery" => {
            // Delivery target is the raw literal reference string (e.g. "@item"), not the value
            let target_key = action_target_key(target);

            // Look up delivery status from context
            let (status, verified) = lookup_delivery_status(&target_key, ctx);

            // action_hash = SHA-256(canonical_json(action_dict without outcome fields))
            // Excludes: status, verified (OUTCOME_FIELDS), action_hash, event_hash, provenance
            let action_dict = serde_json::json!({
                "kind": "delivery",
                "target": target_key,
                "type": "action",
                "verb": verb
            });

            let action_hash = hash_canonical_json(&action_dict);

            // event_hash = SHA-256(canonical_json(action_dict WITH outcome fields))
            // Same structure as action_hash dict but includes status + verified
            let event_dict = serde_json::json!({
                "kind": "delivery",
                "status": status,
                "target": target_key,
                "type": "action",
                "verb": verb,
                "verified": verified
            });
            let event_hash = hash_canonical_json(&event_dict);

            NoeVal::Action(serde_json::json!({
                "type": "action",
                "kind": "delivery",
                "verb": verb,
                "target": target_key,
                "status": status,
                "verified": verified,
                "action_hash": action_hash,
                "event_hash": event_hash,
                "provenance": {
                    "action_hash": action_hash,
                    "event_hash": event_hash,
                    "context_hash": context_hash,
                    "source": format!("{verb} {target_key} nek")
                }
            }))
        }
        "audit" => {
            // audit target is raw literal reference or action key
            let target_key = action_target_key(target);
            let action_dict = serde_json::json!({
                "kind": "audit",
                "target": target_key,
                "type": "action",
                "verb": verb
            });
            let action_hash = hash_canonical_json(&action_dict);
            // For audit: event_hash == action_hash (no outcome fields for audit)
            NoeVal::Action(serde_json::json!({
                "type": "action",
                "verb": verb,
                "target": target_key,
                "kind": "audit",
                "action_hash": action_hash,
                "event_hash": action_hash,
                "provenance": {
                    "action_hash": action_hash,
                    "event_hash": action_hash,
                    "context_hash": context_hash,
                    "source": format!("{verb} {target_key} nek")
                }
            }))
        }
        "execution" => {
            let target_key = action_target_key(target);
            // T-STRICT-ACTION-003: mek output does NOT include 'kind' field (only 'target' is kept)
            // Source-verified: expected {action_hash, event_hash, provenance, target, type:'action', verb:'mek'}
            let action_dict = serde_json::json!({
                "target": target_key,
                "type": "action",
                "verb": verb
            });
            let action_hash = hash_canonical_json(&action_dict);
            NoeVal::Action(serde_json::json!({
                "type": "action",
                "verb": verb,
                "target": target_key,
                "action_hash": action_hash,
                "event_hash": action_hash,
                "provenance": {
                    "action_hash": action_hash,
                    "event_hash": action_hash,
                    "context_hash": context_hash,
                    "source": format!("{verb} {target_key} nek")
                }
            }))
        }
        _ => {
            // Generic action
            let target_val = eval_expr(target, ctx, mode);
            let tj = noe_val_to_json(target_val);
            NoeVal::Action(serde_json::json!({
                "type": "action",
                "verb": verb,
                "target": tj
            }))
        }
    }
}

/// Evaluate the target expression for an action, returning Err(NoeVal::Error) on failures.
/// Scaffolding for future action-chain support; not yet wired to the main evaluator.
#[allow(dead_code)]
fn eval_action_target(target: &Expr, ctx: &ContextLayers, mode: &str) -> Result<Value, NoeVal> {
    let target_val = eval_expr(target, ctx, mode);
    match &target_val {
        NoeVal::Error { code, message } => {
            if mode == "strict" {
                return Err(NoeVal::Error { code, message: message.clone() });
            }
        }
        NoeVal::Undefined => {
            if mode == "strict" {
                return Err(NoeVal::Error {
                    code: ERR_UNDEFINED_TARGET,
                    message: "Action target is undefined".to_string(),
                });
            }
        }
        _ => {}
    }
    Ok(noe_val_to_json(target_val))
}

/// Extract the raw target key string (e.g. "@item") from target Expr for source string.
fn action_target_key(target: &Expr) -> String {
    match target {
        Expr::Literal(name) => format!("@{name}"),
        Expr::Glyph(w) => w.clone(),
        Expr::Demonstrative(k) => k.clone(),
        _ => "?".to_string(),
    }
}

/// eval_request: handles noq (request) operator.
/// Pattern: @subject noq @action_or_verb
/// left=subject expression, right=action expression or verb
fn eval_request(subject: &Expr, target: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    use crate::hash::canonical_bytes;

    // Extract subject identity (e.g. "@robot" → "robot")
    let subject_name = match subject {
        Expr::Literal(name) => normalize_literal_key(name),
        Expr::Glyph(w) => w.clone(),
        _ => {
            let sv = eval_expr(subject, ctx, mode);
            match sv {
                NoeVal::Literal { key, .. } => key,
                _ => "unknown".to_string(),
            }
        }
    };

    // Evaluate the target action expression
    // REQ_001: If target is a Literal, check if the literal value in context is an action object
    // REQ_003: If target is '?' or non-action bool, ERR_ACTION_MISUSE
    let target_val = match target {
        Expr::Literal(name) => {
            // Check context.literals for action object
            let key = normalize_literal_key(name);
            let lit_val = ctx.merged.get("literals")
                .and_then(|l| l.get(&key).or_else(|| l.get(&format!("@{key}"))));
            match lit_val {
                Some(v) if v.get("type").and_then(|t| t.as_str()) == Some("action") => {
                    // literal IS an action object — resolve it as NoeVal::Action
                    // child_action_hash = hash of {verb, type:"action"}
                    let verb_str = v.get("verb").and_then(|vv| vv.as_str()).unwrap_or("");
                    let child_dict = serde_json::json!({
                        "type": "action",
                        "verb": verb_str
                    });
                    let child_hash = hash_canonical_json(&child_dict);
                    let child_action = serde_json::json!({
                        "type": "action",
                        "verb": verb_str,
                        "action_hash": child_hash
                    });
                    NoeVal::Action(child_action)
                }
                _ => eval_expr(target, ctx, mode),
            }
        }
        _ => eval_expr(target, ctx, mode),
    };

    // noq RHS must be an action — per Python: "noq RHS must be an action"
    let (target_action_val, child_action_hash) = match &target_val {
        NoeVal::Action(a) => {
            let child_hash = a.get("action_hash").and_then(|h| h.as_str()).unwrap_or("").to_string();
            let target_json = serde_json::json!({
                "type": "action",
                "verb": a.get("verb").and_then(|v| v.as_str()).unwrap_or(""),
                "action_hash": child_hash
            });
            (target_json, child_hash)
        }
        NoeVal::Undefined | NoeVal::Literal { .. } | NoeVal::Truth(_) | NoeVal::Numeric(_) => {
            if mode == "strict" {
                return NoeVal::Error {
                    code: ERR_ACTION_MISUSE,
                    message: "noq RHS must be an action".to_string(),
                };
            }
            (Value::Null, String::new())
        }
        NoeVal::Error { code, message } => {
            return NoeVal::Error { code, message: message.clone() };
        }
        #[allow(unreachable_patterns)]
        _ => (noe_val_to_json(target_val.clone()), String::new()),
    };

    let context_hash = ctx.hashes.context_hash.clone();

    // Build request action dict for hashing
    // REQ_001: Python _normalize_action excludes 'target' when 'child_action_hash' is present.
    // Source-verified: hash dict = {type, kind, verb, subject, child_action_hash}
    // Python code: if "child_action_hash" in obj: EXCLUDED_KEYS.add("target")
    // Expected hash cf7fcefc computed from {kind:request, subject:robot, child_action_hash:f03520..., type:action, verb:noq}
    let request_dict = if !child_action_hash.is_empty() {
        serde_json::json!({
            "type": "action",
            "kind": "request",
            "verb": "noq",
            "subject": subject_name,
            "child_action_hash": child_action_hash
        })
    } else {
        serde_json::json!({
            "type": "action",
            "kind": "request",
            "verb": "noq",
            "subject": subject_name,
            "target": target_action_val
        })
    };

    let action_hash = if let Ok(cjson_bytes) = canonical_bytes(&request_dict) {
        let mut hasher = Sha256::new();
        hasher.update(&cjson_bytes);
        hex::encode(hasher.finalize())
    } else {
        String::new()
    };

    let result = serde_json::json!({
        "type": "action",
        "kind": "request",
        "verb": "noq",
        "subject": subject_name,
        "target": target_action_val,
        "child_action_hash": child_action_hash,
        "action_hash": action_hash,
        "event_hash": action_hash,
        "provenance": {
            "action_hash": action_hash,
            "event_hash": action_hash,
            "context_hash": context_hash,
            "source": format!("@{} noq {} nek", subject_name, action_target_key(target))
        }
    });

    NoeVal::Action(result)
}

/// Hash a pre-built JSON Value using canonical_json + SHA-256 (NIP-010 formula).
/// The Value should already have outcome fields included/excluded as desired.
fn hash_canonical_json(val: &Value) -> String {
    use crate::hash::canonical_bytes;
    if let Ok(bytes) = canonical_bytes(val) {
        let mut hasher = Sha256::new();
        hasher.update(&bytes);
        hex::encode(hasher.finalize())
    } else {
        String::new()
    }
}

/// Look up delivery status from context.delivery.items[target_key].
/// Returns (status_string, verified_bool) — status is passed through directly from context.
fn lookup_delivery_status(target_key: &str, ctx: &ContextLayers) -> (String, bool) {
    // Try with and without @ prefix
    let bare = target_key.trim_start_matches('@');
    if let Some(delivery) = ctx.merged.get("delivery") {
        if let Some(items) = delivery.get("items") {
            // First try direct bare-key lookup
            if let Some(item) = items.get(bare).or_else(|| items.get(target_key)) {
                let status = item.get("status").and_then(|s| s.as_str()).unwrap_or("pending").to_string();
                let verified = item.get("verified").and_then(|v| v.as_bool()).unwrap_or(false);
                return (status, verified);
            }
            // Fallback: use the first item in the items map
            if let Some(obj) = items.as_object() {
                if let Some((_k, v)) = obj.iter().next() {
                    let status = v.get("status").and_then(|s| s.as_str()).unwrap_or("pending").to_string();
                    let verified = v.get("verified").and_then(|v| v.as_bool()).unwrap_or(false);
                    return (status, verified);
                }
            }
        }
    }
    ("pending".to_string(), false)
}

fn noe_val_to_json(v: NoeVal) -> Value {
    match v {
        NoeVal::Truth(b) => Value::Bool(b),
        NoeVal::Numeric(f) => serde_json::json!(f),
        NoeVal::Undefined => Value::Null,
        NoeVal::Action(a) => a,
        NoeVal::Literal { value, .. } => value,
        NoeVal::Error { .. } => Value::Null,
    }
}

// ---------------------------------------------------------------------------
// Questions
// ---------------------------------------------------------------------------

fn eval_question(q_type: &Option<String>, body: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    let body_val = eval_expr(body, ctx, mode);
    // Questions return a "question" domain object (not the body value directly)
    let q = serde_json::json!({
        "type": q_type.as_deref().unwrap_or("fek"),
        "body": noe_val_to_json(body_val)
    });
    NoeVal::Action(q) // question domain handled in run_noe_logic
}

// ---------------------------------------------------------------------------
// Conditional (khi ... sek)
// ---------------------------------------------------------------------------

fn eval_conditional(cond: &Expr, guard: &Expr, ctx: &ContextLayers, mode: &str) -> NoeVal {
    let guard_val = eval_expr(guard, ctx, mode);
    // Guard must be truth-typed
    match guard_val {
        NoeVal::Truth(true) => eval_expr(cond, ctx, mode),
        NoeVal::Truth(false) => NoeVal::Undefined,
        NoeVal::Undefined => NoeVal::Undefined,
        NoeVal::Error { .. } => guard_val,
        _ => NoeVal::Error {
            code: ERR_GUARD_TYPE,
            message: "khi guard must evaluate to truth or undefined".to_string(),
        },
    }
}

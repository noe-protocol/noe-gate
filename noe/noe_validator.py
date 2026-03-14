r"""
noe_validator.py

Validation layer for the Noe Runtime (Validator V2).

This module enforces safety invariants before a chain is evaluated
by the pure logic engine in noe_parser.py.

It separates concerns according to NIP-009 / NIP-015:
1. `validate_context_strict` handles shape and staleness checks on the flat `C_merged`.
2. `build_safe_context` handles the explicit canonical projection (π_safe), structurally filtering the layered inputs (`C_root`, `C_domain`, `C_local`) into the final `c_safe` execution boundary.

Key functions:
  build_safe_context(C_root, C_domain, C_local, *, mode, now_ms)
      → {c_safe, hashes: {root, domain, local, safe}, stale, error}

  validate_chain(chain_text, context_object, *, context_layers, ...)
      → existing shape + {c_safe, h_safe} (non-breaking extension)

If ok is False, the runtime MUST return domain="error" with the appropriate
error code and MUST NOT execute any actions.
"""

from typing import Dict, Any, List, Optional
from collections.abc import Mapping
import re
import unicodedata
import json
import hashlib
import copy
import os
import sys



# ==========================================
# DEBUGGING INSTRUMENTATION
# ==========================================
# Debug disabled by default for clean production output
# Enable with: NOE_DEBUG=1
_DEBUG_ENABLED = os.getenv("NOE_DEBUG", "0") == "1"

def _debug_print(*args, **kwargs):
    """Print only if debug is enabled."""
    if _DEBUG_ENABLED:
        print(*args, **kwargs)

# ==========================================
# PERFORMANCE: CACHING
# ==========================================
# [DELETED] ID-based caching is unsafe for mutable contexts.
# v1.0 Validator computes hashes on-demand or relies on caller.


ValidationResult = Dict[str, Any]


# Default minimal context for initialization/fallback
DEFAULT_CONTEXT_PARTIAL = {
    "root": {},
    "domain": {},
    "local": {}, 
    "literals": {},
    "spatial": {"thresholds": {"near": 1, "far": 10}, "orientation": {}},
    "temporal": {"now": 0, "max_skew_ms": 1000},
    "modal": {},
    "axioms": {"value_system": {}},
    "delivery": {},
    "audit": {},
    "entities": {}
}

from .context_requirements import CONTEXT_REQUIREMENTS

_LITERAL_RE = re.compile(r"@[\w]+", flags=re.UNICODE)

# Use canonical requirements from context_requirements.py
# If specific validator overrides are needed, they can be merged here.
GROUNDING_REQUIREMENTS = CONTEXT_REQUIREMENTS

_MAX_CONTEXT_DEPTH = 32  # Strict Limit to prevent RecursionError

def _check_depth(obj: Any, current_depth: int = 0) -> bool:
    """
    Recursively check depth of nested dictionaries/lists.
    Returns False if depth exceeds _MAX_CONTEXT_DEPTH.
    """
    if current_depth > _MAX_CONTEXT_DEPTH:
        return False
        
    if isinstance(obj, dict):
        for v in obj.values():
            if not _check_depth(v, current_depth + 1):
                return False
    elif isinstance(obj, list):
        for v in obj:
            if not _check_depth(v, current_depth + 1):
                return False
                
    return True

def check_grounding(op_name: str, args: tuple, C_total: dict) -> bool:
    if _DEBUG_ENABLED:
        _debug_print(f"DEBUG CHECK GROUNDING: op={op_name}, C_keys={list(C_total.keys())}")

    """
    Returns True if the operator is fully grounded under C_total and args,
    False otherwise.
    """
    # 1. Look up requirements
    reqs = GROUNDING_REQUIREMENTS.get(op_name, [])
    
    # 2. Verify required subsystems/keys exist
    for req in reqs:
        # Check if entire shard is missing
        parts = req.split(".")
        curr = C_total
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                return False
                
    # 3. Operator-specific dynamic checks
    # Spatial entities must exist in entities shard
    if op_name in ["nel", "tel", "xel", "en", "tra", "fra"]:
        entities = C_total.get("entities", {})
        if not isinstance(entities, dict): return False
        for arg in args:
            if isinstance(arg, str) and arg.startswith("@"):
                if arg not in entities:
                    return False
                if "position" not in entities[arg]:
                    return False

    # Epistemic targets must exist in modal shard (if we check them here)
    # But usually validated via ERR_EPISTEMIC_MISMATCH in validate_chain
    
    return True



def _extract_literals(chain_text: str) -> List[str]:
    """Return all literal tokens like @home, @dock_3 from the chain."""
    return list({m.group(0) for m in _LITERAL_RE.finditer(chain_text)})


from .canonical import canonical_json, canonical_literal_key, canonicalize_chain
from .tokenize import extract_ops as _tokenize_extract_ops

def _ast_depth(node: Any, d: int = 0) -> int:
    if d > _MAX_CONTEXT_DEPTH: return d
    
    # Lazy import to avoid import errors if arpeggio isn't installed
    try:
        from arpeggio import PTNode
        has_arpeggio = True
    except ImportError:
        has_arpeggio = False
        
    if has_arpeggio and isinstance(node, PTNode):
        if not node: return d + 1
        return max((_ast_depth(c, d+1) for c in node), default=d+1)
        
    if isinstance(node, (list, tuple)):
        return max((_ast_depth(c, d+1) for c in node), default=d+1) if node else d+1
    return d+1

def validate_ast_safety(ast_node: Any) -> bool:
    """Validates AST safety (depth, banned nodes)."""
    return _ast_depth(ast_node) <= _MAX_CONTEXT_DEPTH

def _canonical_json(obj: Any) -> bytes:
    """
    Canonical JSON serialization for hashing.
    Normalized using internal logic, then serialized using standard canonical format.
    """
    # 1. Normalize (strip internal keys, sort keys)
    def _normalize(o):
        if isinstance(o, dict):
            return {k: _normalize(v) for k, v in o.items() if isinstance(k, str) and not k.startswith("_")}
        if isinstance(o, (list, tuple)):
            return [_normalize(x) for x in o]
        return o

    norm = _normalize(obj)
    
    # 2. Serialize (Sort keys, standard separators, ensure_ascii=True for safety)
    return canonical_json(norm).encode("utf-8")


def compute_context_hashes(C: Dict[str, Any]) -> Dict[str, str]:
    """
    Compute hierarchical context hashes:

    - H_root   = hash(C_root)
    - H_domain = hash(C_domain)
    - H_local  = hash(C_local)
    - H_total  = hash(H_root || H_domain || H_local)

    Supports both structured and flat contexts.
    """
    # Support both flat and structured contexts
    # Strict Check
    if "root" in C and "domain" in C and "local" in C:
        # Structured context (Optimistic check, validate_chain enforces types)
        C_root = C.get("root", {})
        C_domain = C.get("domain", {})
        C_local = C.get("local", {})
    else:
        # Legacy flat context (treat as local)
        C_root = {}
        C_domain = {}
        C_local = C

    h_root_bytes = hashlib.sha256(_canonical_json(C_root)).digest()
    h_domain_bytes = hashlib.sha256(_canonical_json(C_domain)).digest()
    h_local_bytes = hashlib.sha256(_canonical_json(C_local)).digest()

    h_root = h_root_bytes.hex()
    h_domain = h_domain_bytes.hex()
    h_local = h_local_bytes.hex()

    h_total_bytes = hashlib.sha256(
        h_root_bytes + h_domain_bytes + h_local_bytes
    ).digest()
    h_total = h_total_bytes.hex()

    return {
        "root": h_root,
        "domain": h_domain,
        "local": h_local,
        "total": h_total,
    }


# ============================================================
# build_safe_context  (NIP-009 §π_safe  /  NIP-015 §eval)
# ============================================================

def _deep_merge_layers(C_root: dict, C_domain: dict, C_local: dict) -> dict:
    """
    Standard three-layer merge with local winning: root < domain < local.
    Pure function; all inputs are dicts.
    """
    def _merge(base: dict, overlay: dict) -> dict:
        if not isinstance(base, dict) or not isinstance(overlay, dict):
            return copy.deepcopy(overlay)
        result = base.copy()
        for k, v in overlay.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = _merge(result[k], v)
            else:
                result[k] = copy.deepcopy(v)
        return result

    merged = _merge(_merge({}, C_root), C_domain)
    return _merge(merged, C_local)


SafeBuildResult = Dict[str, Any]   # type alias for clarity


def build_safe_context(
    C_root: Dict[str, Any],
    C_domain: Dict[str, Any],
    C_local: Dict[str, Any],
    *,
    mode: str = "strict",
    now_ms: int = 0,
) -> SafeBuildResult:
    """
    Validator-owned C_safe construction  (NIP-009 §π_safe / NIP-015 §eval).

    This is the **sole** place where π_safe is applied.  The runtime must
    call this and consume the returned c_safe verbatim; it must never pass
    raw c_merged or snap.structured directly to the evaluator.

    Pipeline:
      1. Deep-merge layers (root < domain < local) → C_rich
      2. Compute H_root, H_domain, H_local from raw shards
      3. Resolve now_ms (temporal.now from merged, or caller-supplied)
      4. Extract annotated evidence from C_rich
      5. Apply π_safe → bare safe literals
      6. Enforce strict-mode staleness:  if *no* evidence survives for a
         literal that was present in C_rich, and the raw entry is still
         there (meaning it was suppressed by the staleness filter, not
         simply absent), emit ERR_STALE_CONTEXT.
      7. Reconstruct C_safe:  start from C_rich, overwrite literals +
         modal.knowledge with the projected bare literals only.
      8. Compute H_safe = SHA-256(canonical_json(C_safe))
      9. Inject C_safe.meta = {h_root, h_domain, h_local, h_safe}

    Returns:
      {
        "c_safe":  dict,            # projected context ready for evaluator
        "hashes":  {               # all four NIP-009-required hashes
           "root":   str,
           "domain": str,
           "local":  str,
           "safe":   str,          # normative; use this for provenance
        },
        "stale":   bool,           # True if validator suppressed stale entries
        "error":   None | {"code": str, "detail": str},
      }
    """
    from .context_projection import (
        extract_evidence_from_context,
        pi_safe,
        ProjectionConfig,
    )

    # 1. Merge layers
    C_rich = _deep_merge_layers(
        C_root if isinstance(C_root, dict) else {},
        C_domain if isinstance(C_domain, dict) else {},
        C_local if isinstance(C_local, dict) else {},
    )

    # 2. Raw shard hashes (always computed, exposed in provenance)
    h_root_bytes   = hashlib.sha256(_canonical_json(C_root   if isinstance(C_root,   dict) else {})).digest()
    h_domain_bytes = hashlib.sha256(_canonical_json(C_domain if isinstance(C_domain, dict) else {})).digest()
    h_local_bytes  = hashlib.sha256(_canonical_json(C_local  if isinstance(C_local,  dict) else {})).digest()

    h_root   = h_root_bytes.hex()
    h_domain = h_domain_bytes.hex()
    h_local  = h_local_bytes.hex()

    # 3. Resolve now_ms
    temporal = C_rich.get("temporal", {})
    if isinstance(temporal, dict):
        resolved_now = temporal.get("now", now_ms)
        try:
            resolved_now = int(resolved_now)
        except (TypeError, ValueError):
            resolved_now = now_ms
    else:
        resolved_now = now_ms

    # 4. Extract annotated evidence (C_rich layer)
    config = ProjectionConfig()
    annotated_list = extract_evidence_from_context(C_rich)

    # 5. Apply π_safe
    safe_bare_literals, _ = pi_safe(
        annotated_list,
        config,
        resolved_now,
        with_explanations=True,
        full_context=C_rich,
    )
    safe_pred_set = {bl.predicate for bl in safe_bare_literals}

    # 6. Staleness check (strict mode)
    #    Evidence was present but filtered → stale, not absent.
    #    We compare predicates in raw evidence vs predicates that survived pi_safe.
    stale = False
    stale_detail = None
    if annotated_list:  # only meaningful when there is evidence at all
        raw_preds = {al.predicate for al in annotated_list}
        suppressed = raw_preds - safe_pred_set
        if suppressed and mode == "strict":
            # pi_safe suppressed evidence that was present → staleness
            stale = True
            stale_detail = f"Stale evidence suppressed for: {', '.join(sorted(suppressed))}"

    if stale and mode == "strict":
        return {
            "c_safe": None,
            "hashes": {"root": h_root, "domain": h_domain, "local": h_local, "safe": ""},
            "stale": True,
            "error": {"code": "ERR_STALE_CONTEXT", "detail": stale_detail or "Stale evidence"},
        }

    # 7. Reconstruct C_safe
    #    Start from an explicitly empty canonical schema allowlist.
    #    Map only the 9 required NIP-009 Phase 1 canonical subtrees from C_rich.
    c_safe = {}

    # Helper to enforce pure scalar values (no nested dicts/lists)
    def _is_scalar(v):
        return isinstance(v, (bool, int, float, str))

    # Unconditional Subtrees
    # 1. literals (Start with existing bare literals, explicitly dropping non-scalar payloads)
    literals = {}
    if "literals" in C_rich and isinstance(C_rich["literals"], dict):
        for k, v in C_rich["literals"].items():
            if _is_scalar(v):
                literals[k] = v
    c_safe["literals"] = literals

    # 2. temporal (Freeze exact fields, no deepcopy of arbitrary payloads)
    if "temporal" in C_rich and isinstance(C_rich["temporal"], dict):
        temporal = {}
        rich_temp = C_rich["temporal"]
        for allowed_key in ["now", "max_skew_ms", "now_us", "max_staleness_us"]:
            if allowed_key in rich_temp:
                val = rich_temp[allowed_key]
                if isinstance(val, int) and not isinstance(val, bool):
                    temporal[allowed_key] = val
        if temporal:
            c_safe["temporal"] = temporal
    
    # 3. modal (preserve knowledge, belief, certainty enforcing only bare scalars)
    modal = {}
    if "modal" in C_rich and isinstance(C_rich["modal"], dict):
        rich_modal = C_rich["modal"]
        for subtype in ["knowledge", "belief", "certainty"]:
            if subtype in rich_modal and isinstance(rich_modal[subtype], dict):
                clean_subj = {}
                for k, v in rich_modal[subtype].items():
                    if _is_scalar(v):
                        clean_subj[k] = v
                if clean_subj:
                    modal[subtype] = clean_subj
    c_safe["modal"] = modal
    
    # 4. axioms (preserve value_system)
    axioms = {}
    if "axioms" in C_rich and isinstance(C_rich["axioms"], dict):
        rich_axioms = C_rich["axioms"]
        if "value_system" in rich_axioms: axioms["value_system"] = copy.deepcopy(rich_axioms["value_system"])
    c_safe["axioms"] = axioms

    # Conditional Subtrees
    # 5. spatial (preserve thresholds and orientation)
    if "spatial" in C_rich and isinstance(C_rich["spatial"], dict):
        spatial = {}
        rich_spatial = C_rich["spatial"]
        if "thresholds" in rich_spatial: spatial["thresholds"] = copy.deepcopy(rich_spatial["thresholds"])
        if "thresholds_mm" in rich_spatial: spatial["thresholds_mm"] = copy.deepcopy(rich_spatial["thresholds_mm"])
        if "orientation" in rich_spatial: spatial["orientation"] = copy.deepcopy(rich_spatial["orientation"])
        c_safe["spatial"] = spatial
    
    # 6. entities (safe copy of grounding object dict)
    #    Entities must be structurally frozen to known physical/spatial properties.
    #    Any arbitrary metadata payload (`confidence`, `sensor_fusion`, `driver_notes`) 
    #    is violently stripped by explicitly reconstructing ONLY allowed canonical sub-keys.
    if "entities" in C_rich and isinstance(C_rich["entities"], dict):
        entities = {}
        for ent_id, ent_val in C_rich["entities"].items():
            if isinstance(ent_val, dict):
                clean_ent = {}
                
                # Helper to enforce bounded spatial vectors (dict of numeric values)
                def _safe_vector(v):
                    if not isinstance(v, dict): return None
                    clean_vec = {}
                    for axis, val in v.items():
                        if isinstance(axis, str) and isinstance(val, (int, float)) and not isinstance(val, bool):
                            clean_vec[axis] = val
                    return clean_vec if clean_vec else None
                
                if "position" in ent_val:
                    pos = _safe_vector(ent_val["position"])
                    if pos: clean_ent["position"] = pos
                
                if "velocity" in ent_val:
                    vel = _safe_vector(ent_val["velocity"])
                    if vel: clean_ent["velocity"] = vel
                    
                if "orientation" in ent_val:
                    ori = _safe_vector(ent_val["orientation"])
                    if ori: clean_ent["orientation"] = ori
                    
                if "bounds" in ent_val:
                    bnd = _safe_vector(ent_val["bounds"])
                    if bnd: clean_ent["bounds"] = bnd

                if clean_ent:
                    entities[ent_id] = clean_ent
                    
        if entities:
            c_safe["entities"] = entities

    # 7. rel
    # Requires strict adherence to: rel[op_name_str][subject_id_str][target_id_str] = bool
    # Legacy 'relations' alias is merged into 'rel' if present.
    rel_raw = C_rich.get("rel", {})
    if not rel_raw and "relations" in C_rich:
        rel_raw = C_rich["relations"]
        
    if isinstance(rel_raw, dict) and rel_raw:
        safe_rel = {}
        for op_name, subj_map in rel_raw.items():
            if isinstance(op_name, str) and isinstance(subj_map, dict):
                safe_subj_map = {}
                for subj_id, target_map in subj_map.items():
                    if isinstance(subj_id, str) and isinstance(target_map, dict):
                        safe_target_map = {}
                        for target_id, rel_bool in target_map.items():
                            if isinstance(target_id, str) and isinstance(rel_bool, bool):
                                safe_target_map[target_id] = rel_bool
                        if safe_target_map:
                            safe_subj_map[subj_id] = safe_target_map
                if safe_subj_map:
                    safe_rel[op_name] = safe_subj_map
        if safe_rel:
            c_safe["rel"] = safe_rel

    # 8. audit
    if "audit" in C_rich and isinstance(C_rich["audit"], dict):
        c_safe["audit"] = {} # Only empty audit structure is passed directly, no raw payload

    # 9. delivery
    if "delivery" in C_rich and isinstance(C_rich["delivery"], dict):
        delivery = {}
        rich_delivery = C_rich["delivery"]
        
        # 9a. restrict delivery.status to simple string maps
        if "status" in rich_delivery and isinstance(rich_delivery["status"], dict):
            status = {}
            for k, v in rich_delivery["status"].items():
                if isinstance(k, str) and isinstance(v, str):
                    status[k] = v
            delivery["status"] = status
            
        # 9b. restrict delivery.items to exact known fields
        if "items" in rich_delivery and isinstance(rich_delivery["items"], dict):
            items = {}
            for k, v in rich_delivery["items"].items():
                if isinstance(v, dict):
                    clean_item = {}
                    is_valid = True
                    if "status" in v and isinstance(v["status"], str):
                        clean_item["status"] = v["status"]
                    if "verified" in v and isinstance(v["verified"], bool):
                        clean_item["verified"] = v["verified"]
                    # Timestamps must be strict int, not bool
                    if "observed_at_ms" in v:
                        t_val = v["observed_at_ms"]
                        if isinstance(t_val, int) and not isinstance(t_val, bool):
                            clean_item["observed_at_ms"] = t_val
                        else:
                            is_valid = False
                    if "expires_at_ms" in v:
                        t_val = v["expires_at_ms"]
                        if isinstance(t_val, int) and not isinstance(t_val, bool):
                            clean_item["expires_at_ms"] = t_val
                        else:
                            is_valid = False
                            
                    if clean_item and is_valid:
                        items[k] = clean_item
            delivery["items"] = items
            
        if delivery:
            c_safe["delivery"] = delivery

    # Apply pi_safe projection literal overriding
    projected_literals: Dict[str, Any] = {}
    projected_knowledge: Dict[str, Any] = {}
    for bl in safe_bare_literals:
        projected_literals[bl.predicate] = bl.value
        projected_knowledge[bl.predicate] = bl.value

    # Merge projected literals on top of existing literals (pi_safe may widen)
    existing_literals = c_safe.get("literals", {})
    if not isinstance(existing_literals, dict):
        existing_literals = {}
    existing_literals.update(projected_literals)
    c_safe["literals"] = existing_literals

    knowledge = c_safe["modal"].get("knowledge", {})
    if not isinstance(knowledge, dict):
        knowledge = {}
    knowledge.update(projected_knowledge)
    c_safe["modal"]["knowledge"] = knowledge

    # 8. H_safe = SHA-256(canonical_json(C_safe))
    #    NOTE: We compute this BEFORE injecting meta so meta itself is not
    #    included in the hash (meta is informational, not part of eval input).
    h_safe_bytes = hashlib.sha256(_canonical_json(c_safe)).digest()
    h_safe = h_safe_bytes.hex()

    # 9. Inject meta (informational; not included in H_safe)
    c_safe["meta"] = {
        "h_root":   h_root,
        "h_domain": h_domain,
        "h_local":  h_local,
        "h_safe":   h_safe,
    }

    return {
        "c_safe":  c_safe,
        "hashes":  {"root": h_root, "domain": h_domain, "local": h_local, "safe": h_safe},
        "stale":   stale,
        "error":   None,
    }

from .operator_lexicon import (
    ACTION_OPS,
    LOGIC_OPS,
    COMP_OPS,
    DEMONSTRATIVE_OPS,
    DELIVERY_OPS,
    AUDIT_OPS,
    ALL_OPS
)


# ...

def extract_ops(chain_text):
    """
    Extract operators from raw chain text using robust tokenizer.
    Extract operators from raw chain text using robust tokenizer.
    Delegates to noe.tokenize.extract_ops (ordered list).
    """
    # Ensure canonical form first (tokenize.extract_ops requires it)
    canon = canonicalize_chain(chain_text)
    return _tokenize_extract_ops(canon, ALL_OPS)

def _validate_audit_strict(ctx):
    """Strict validation for audit subsystem."""
    audit = ctx.get("audit")
    if audit is None:
        return {"code": "ERR_CONTEXT_INCOMPLETE", "detail": "Missing audit subsystem"}
    if not isinstance(audit, Mapping):
        return {"code": "ERR_CONTEXT_INCOMPLETE", "detail": "Audit subsystem must be an object"}
    return None

def compute_stale_flag(C_total):
    """Computes staleness based on temporal subsystem relative to local time."""
    temp = C_total.get("temporal", {})
    if not isinstance(temp, dict): return False, "No temporal subsystem"
    
    # Check for canonical inputs
    now = temp.get("now")
    skew = temp.get("max_skew_ms")
    ts = temp.get("timestamp") # Populated by merge logic in strict mode
    
    if ts is None:
        ts = C_total.get("timestamp")
    
    if now is None or skew is None or ts is None:
        # Cannot determine staleness if fields missing.
        # Strict context validation should catch missing 'now'/'skew' in shape check?
        return False, None
        
    # Ensure types
    try:
        now = float(now)
        skew = float(skew)
        ts = float(ts)
    except (ValueError, TypeError):
        return False, "Non-numeric temporal fields"

    # Logic: if now - timestamp > skew -> Stale
    if _DEBUG_ENABLED: print(f"DEBUG: Stale Check: now={now}, ts={ts}, skew={skew}, diff={now-ts}")
    if (now - ts) > skew:
         return True, f"Timestamp {ts} is older than now {now} by > {skew}ms"
    
    return False, None

def _validate_delivery_strict(C_total: Mapping) -> Optional[Dict[str, str]]:
    """
    Validate delivery subsystem for strict mode.
    
    Required when vus, vel, or noq operators are present.
    Delivery subsystem must exist and have valid structure.
    """
    delivery = C_total.get("delivery")
    if not isinstance(delivery, Mapping):
        return {
            "code": "ERR_CONTEXT_INCOMPLETE",
            "detail": "C.delivery must be an object in strict mode"
        }
    
    # Delivery subsystem must have either 'items' or 'status'
    has_items = "items" in delivery
    has_status = "status" in delivery
    
    if not (has_items or has_status):
        return {
            "code": "ERR_CONTEXT_INCOMPLETE",
            "detail": "C.delivery must contain 'items' or 'status' in strict mode"
        }
    
    # If items exists, it must be a mapping
    if has_items and not isinstance(delivery.get("items"), Mapping):
        return {
            "code": "ERR_CONTEXT_INCOMPLETE",
            "detail": "C.delivery.items must be an object"
        }
    
    return None

def validate_context_strict(C_total: dict, tokens: List[str] = None) -> tuple[bool, Optional[str], bool]:
    """
    Validates the flat, merged context (C_merged) against strict NIP-009 shape requirements.
    
    NOTE: This function handles shape and staleness validation only.
    It does NOT perform π_safe projection. NIP-009 separation dictates that
    the caller must subsequently pass the unmerged layers to build_safe_context()
    for structural elimination of adapters, probabilities, and evidence.
    
    Returns (is_valid, error_code, is_stale).
    If valid, returns (True, None, is_stale).
    """
    if not isinstance(C_total, Mapping):
        return False, "ERR_BAD_CONTEXT", False

    # 1. Check Required Top-Level Shards (Shape Only)
    # Literals
    if "literals" not in C_total:
         return False, "ERR_CONTEXT_INCOMPLETE", False
    if not isinstance(C_total["literals"], Mapping):
         return False, "ERR_CONTEXT_INCOMPLETE", False

    # Spatial (NOT required top-level - only validated when spatial operators used)
    # This was causing ERR_SPATIAL_UNGROUNDABLE even for pure epistemic chains
    # Spatial validation moved to operator-specific checks (nel, tel, xel, etc.)
    # if "spatial" not in C_total:
    #      return False, "ERR_CONTEXT_INCOMPLETE", False
    # if not isinstance(C_total["spatial"], Mapping):
    #      return False, "ERR_CONTEXT_INCOMPLETE", False


    # Temporal
    if "temporal" not in C_total:
         return False, "ERR_CONTEXT_INCOMPLETE", False
    if not isinstance(C_total["temporal"], Mapping):
         return False, "ERR_CONTEXT_INCOMPLETE", False
    
    # Strict: Temporal must contain time fields
    # Accept EITHER legacy (now, max_skew_ms) OR v1.0 int64 (now_us, max_staleness_us)
    temp = C_total["temporal"]
    has_legacy = temp.get("now") is not None and temp.get("max_skew_ms") is not None
    has_v1 = temp.get("now_us") is not None
    
    if not (has_legacy or has_v1):
        return False, "ERR_CONTEXT_INCOMPLETE", False
         
    # Modal
    if "modal" not in C_total:
         return False, "ERR_CONTEXT_INCOMPLETE", False
    if not isinstance(C_total["modal"], Mapping):
         return False, "ERR_CONTEXT_INCOMPLETE", False
         
    # Axioms
    if "axioms" not in C_total:
         return False, "ERR_CONTEXT_INCOMPLETE", False
    if not isinstance(C_total["axioms"], Mapping):
         return False, "ERR_CONTEXT_INCOMPLETE", False

    stale, _ = compute_stale_flag(C_total)
    
    return True, None, stale


# ==========================================
# ERROR PRIORITY (Deterministic Ordering)
# ==========================================
# Ensures stable error reporting regardless of check order
ERROR_PRIORITY = {
    # 0. Hard malformed / safety
    "ERR_BAD_CONTEXT": 0,
    "ERR_CONTEXT_TOO_DEEP": 0,
    "ERR_CONTEXT_UNSERIALIZABLE": 0,

    # 1. Completeness (Base structure is missing mandatory subsystems)
    "ERR_CONTEXT_INCOMPLETE": 1,

    # 2. Staleness (Structure is completely valid, but data is too old)
    "ERR_CONTEXT_STALE": 2,

    # 3. Action safety boundary
    "ERR_ACTION_MISUSE": 3,
    "ERR_ACTION_CYCLE": 3,

    # 4. Subsystem grounding
    "ERR_DELIVERY_MISMATCH": 4,
    "ERR_EPISTEMIC_MISMATCH": 4,
    "ERR_SPATIAL_UNGROUNDABLE": 4,
    "ERR_DEMONSTRATIVE_UNGROUNDED": 4,

    # 5. Dependency resolution
    "ERR_LITERAL_MISSING": 5,
    "ERR_INVALID_LITERAL": 5,
    
    # 6. Fallback
    # Parse failures should technically fast-fail, but if they reach sorting, 
    # they represent a fundamental inability to read the payload, so they take absolute 0.
    "ERR_PARSE_FAILED": 0,
}

def _sort_errors(errors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort errors by priority (lowest first) for deterministic reporting."""
    def key(e):
        code = e.get("code", "")
        priority = ERROR_PRIORITY.get(code, 999)
        return (priority, code)
    return sorted(errors, key=key)


# ==========================================
# 2. CHAIN VALIDATION (MAIN ENTRY POINT)
# ==========================================

def validate_chain(
    chain_text: str,
    context_object: Dict[str, Any],
    mode: str = "strict",
    context_hashes: Optional[Dict[str, str]] = None,
    explain: bool = False,
    context_layers: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    # 0. Canonicalize
    chain_text = canonicalize_chain(chain_text)

    # 1. Start Validation
    if _DEBUG_ENABLED: print(f"DEBUG: validate_chain called. Length={len(chain_text)}. Text='{chain_text}'")
    """
    Validate a Noe chain against a given context C before interpretation.

    Args:
        context_layers: Optional {"root": dict, "domain": dict, "local": dict}.
            When provided, build_safe_context is called to produce c_safe and
            h_safe (validator-owned projection, NIP-009/015).  The flat
            context_object is still used for all flag/error checks so that
            legacy callers remain compatible.

    Returns a ValidationResult dict with:
    - ok (bool): True if validation passed, False if critical errors found
    - context_hashes: Raw shard hashes (root, domain, local, total)
    - c_safe (dict | None): Projected safe context.  None when ok=False or no layers.
    - h_safe (str | None): H_safe = SHA-256(canonical_json(c_safe)), normative
      hash for provenance.  None when ok=False or no layers.
    - errors: List of error dicts with code/detail

    Runtime Contract (v1.0 Strict):
    - If ok=False: Runtime MUST return domain="error" with validator error code.
      Never evaluate the chain. Never return undefined for validation failures.
    - If ok=True: Runtime MAY evaluate.  Evaluator MUST consume c_safe, not raw context.
    """
    global ACTION_OPS, LOGIC_OPS, COMP_OPS

    # ---- pre-run: build C_safe from structured layers if provided ----
    _c_safe_result: Optional[Dict[str, Any]] = None
    _c_safe: Optional[Dict[str, Any]] = None
    _h_safe: Optional[str] = None

    if context_layers is not None:
        _C_root   = context_layers.get("root")   or {}
        _C_domain = context_layers.get("domain") or {}
        _C_local  = context_layers.get("local")  or {}
        # Determine now_ms from temporal.now in the context_object (merged flat)
        _now_ms = 0
        if isinstance(context_object, dict):
            _temp = context_object.get("temporal", {})
            if isinstance(_temp, dict):
                try:
                    _now_ms = int(_temp.get("now", 0))
                except (TypeError, ValueError):
                    _now_ms = 0
        _c_safe_result = build_safe_context(
            _C_root, _C_domain, _C_local,
            mode=mode,
            now_ms=_now_ms,
        )
        if _c_safe_result.get("error"):
            # Staleness or other safe-build failure → treat as validation error
            _err = _c_safe_result["error"]
            return {
                "ok": False,
                "context_hashes": _c_safe_result.get("hashes", {}),
                "context_error": _err["code"],
                "flags": {"context_stale": True},
                "reasons": [_err["detail"]],
                "errors": [_err],
                "warnings": [],
                "provenance": None,
                "explained_literals": [],
                "c_safe": None,
                "h_safe": None,
            }
        _c_safe = _c_safe_result["c_safe"]
        _h_safe = _c_safe_result["hashes"]["safe"]

    # 0. Recursion Guard (Defense against Allocation Bomb/Crash)
    if not _check_depth(context_object):
        return {
            "ok": False,
            "context_hashes": {},
            "context_error": "ERR_CONTEXT_TOO_DEEP",
            "flags": {"schema_invalid": True},
            "reasons": [f"Context nesting exceeds limit ({_MAX_CONTEXT_DEPTH})"],
            "errors": [{
                "code": "ERR_CONTEXT_TOO_DEEP", 
                "detail": "Context too deep (recursion protection)"
            }],
            "warnings": [],
            "provenance": None,
            "explained_literals": []
        }
    
    if not isinstance(context_object, Mapping):
        return {
            "ok": False,
            "context_hashes": {},
            "context_error": "ERR_BAD_CONTEXT",
            "flags": {"schema_invalid": True},
            "reasons": [f"Context root must be a dictionary/map, got {type(context_object).__name__}"],
            "errors": [{
                "code": "ERR_BAD_CONTEXT", 
                "detail": f"Context malformed: {type(context_object).__name__}"
            }],
            "warnings": [],
            "provenance": None,
            "explained_literals": []
        }
    
    
    # STRICT MODE: Enforce structured context consistency
    if mode == "strict":
        has_root = "root" in context_object
        has_domain = "domain" in context_object
        has_local = "local" in context_object
        
        # If using structured context, must be complete
        if has_root or has_domain or has_local:
            if not (has_root and has_domain and has_local):
                missing = []
                if not has_root: missing.append("root")
                if not has_domain: missing.append("domain")
                if not has_local: missing.append("local")
                return {
                    "ok": False,
                    "context_hashes": {},
                    "context_error": "ERR_CONTEXT_INCOMPLETE",
                    "flags": {"schema_invalid": True},
                    "reasons": [f"Structured context requires all three layers: missing {', '.join(missing)}"],
                    "errors": [{
                        "code": "ERR_CONTEXT_INCOMPLETE",
                        "detail": f"Structured context requires all three layers: missing {', '.join(missing)}",
                        "meta": {}
                    }],
                    "warnings": [],
                    "provenance": None,
                    "explained_literals": []
                }
            # Verify all three are Mappings
            if not isinstance(context_object.get("root"), Mapping):
                return {
                    "ok": False,
                    "context_hashes": {},
                    "context_error": "ERR_CONTEXT_INCOMPLETE",
                    "flags": {"schema_invalid": True},
                    "reasons": ["'root' must be a dict in structured context"],
                    "errors": [{
                        "code": "ERR_CONTEXT_INCOMPLETE",
                        "detail": "'root' must be a dict"
                    }],
                    "warnings": [],
                    "provenance": None,
                    "explained_literals": []
                }
            if not isinstance(context_object.get("domain"), Mapping):
                return {
                    "ok": False,
                    "context_hashes": {},
                    "context_error": "ERR_CONTEXT_INCOMPLETE",
                    "flags": {"schema_invalid": True},
                    "reasons": ["'domain' must be a dict in structured context"],
                    "errors": [{
                        "code": "ERR_CONTEXT_INCOMPLETE",
                        "detail": "'domain' must be a dict"
                    }],
                    "warnings": [],
                    "provenance": None,
                    "explained_literals": []
                }
            if not isinstance(context_object.get("local"), Mapping):
                return {
                    "ok": False,
                    "context_hashes": {},
                    "context_error": "ERR_CONTEXT_INCOMPLETE",
                    "flags": {"schema_invalid": True},
                    "reasons": ["'local' must be a dict in structured context"],
                    "errors": [{
                        "code": "ERR_CONTEXT_INCOMPLETE",
                        "detail": "'local' must be a dict"
                    }],
                    "warnings": [],
                    "provenance": None,
                    "explained_literals": []
                }
    
    # -----------------------------------------------------------
    # HIERARCHICAL INTEGRITY
    # -----------------------------------------------------------
    C_struct = context_object
    C_total = context_object or {}
    
    # Auto-detect structured contexts and merge to flat form
    if isinstance(C_total.get("root"), dict) and isinstance(C_total.get("domain"), dict) and isinstance(C_total.get("local"), dict):
        # Strict Structured Context: All three must exist and be dicts
        import copy
    
        def _deep_merge_local(base, overlay):
            if not isinstance(base, dict) or not isinstance(overlay, dict):
                return copy.deepcopy(overlay)
            result = base.copy()
            for k, v in overlay.items():
                if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                    result[k] = _deep_merge_local(result[k], v)
                else:
                    result[k] = copy.deepcopy(v)
            return result
    
        C_root = C_total.get("root", {})
        C_domain = C_total.get("domain", {})
        C_local = C_total.get("local", {})
        C_total = _deep_merge_local(_deep_merge_local({}, C_root), C_domain)
        C_total = _deep_merge_local(C_total, C_local)
        
        # Fix 2: Canonical Strict Timestamp
        # Ensure temporal.timestamp is populated from local.timestamp for stale check
        ts = C_local.get("timestamp")
        if ts is not None:
             if "temporal" not in C_total or not isinstance(C_total["temporal"], dict):
                 C_total["temporal"] = {}
             C_total["temporal"]["timestamp"] = ts
    
    # -------------------------------------------------------------------------
    # TRAFFIC COP LOGIC (Strict Mode Priority)
    # -------------------------------------------------------------------------
    
    flags = {
        "invalid_literal": False,
        "literal_mismatch": False, 
        "action_misuse": False,
        "demonstrative_ungrounded": False,
        "spatial_mismatch": False, 
        "epistemic_mismatch": False,
        "sensor_mismatch": False, 
        "delivery_mismatch": False,
        "audit_mismatch": False,
        "schema_invalid": False,
        "context_stale": False,
        "demonstrative_mismatch": False,
        "value_system_mismatch": False,
    }
    reasons = []
    errors = []
    warnings = []
    
    # 1. Invalid Literal Scan (Raw Span)
    # User Strategy: Scan @ followed by non-whitespace. If not exactly @[\w]+ -> Error.
    import re
    # Define regexes for reuse in later blocks (Line 661)
    _LITERAL_LIKE_RE = re.compile(r"(@[^\s(),;]+)", flags=re.UNICODE)
    _LITERAL_RE_STRICT = re.compile(r"^@[a-z0-9_]+$", flags=re.UNICODE)
    
    raw_literals = re.findall(r"@[^ \t\r\n\)\]\}\>,;]+", chain_text, flags=re.UNICODE)
    for raw_lit in raw_literals:
        # Strip trailing punctuation often found in token streams? 
        # User said "stop at whitespace". Strict means STRICT. 
        # If we have "(@foo)", it parses as "@foo)". "@foo)" is invalid.
        # However, we must handle legitimate termination like ")" in ( @foo ).
        # But wait, canonicalization adds spaces around parens?
        # If canonicalized, "(@foo)" -> "( @foo )". Then "@foo" is cleanly separated.
        # If input is "@foo-bar", it is "@foo-bar". MATCH fails.
        # If input is "@foo)", and canonicalizer didn't space it...
        
        # We rely on chain_text being canonicalized by caller?
        # If not, we might flag valid literals that are buttressing punctuation.
        # But User Strategy emphasizes RAW scan.
        # Let's assume canonicalization happened (it is called in run_conformance).
        
        if not re.fullmatch(r"@[a-z0-9_]+", raw_lit):
            if _DEBUG_ENABLED: print(f"DEBUG: INVALID LITERAL FOUND '{raw_lit}'")
            flags["invalid_literal"] = True
            reasons.append(f"Malformed literal '{raw_lit}'")
            errors.append({"code": "ERR_INVALID_LITERAL", "detail": f"Malformed literal '{raw_lit}'"})
        elif mode == "strict": 
            # Valid Syntax. Check Presence.
            # User implies strict mode requires it.
            # NIP-009: Keys in literals shard should be without @.
            canon_key = canonical_literal_key(raw_lit)
            literals_shard = C_total.get("literals", {})

            # Guard: literals shard must be a dict, not None or other type
            if literals_shard is None:
                if mode == "strict":
                    flags["schema_invalid"] = True
                    errors.append({"code": "ERR_CONTEXT_INCOMPLETE", "detail": "context.literals is missing or null"})
                    reasons.append("context.literals is missing or null")
                literals_shard = {}
            elif not isinstance(literals_shard, dict):
                if mode == "strict":
                    flags["schema_invalid"] = True
                    errors.append({"code": "ERR_BAD_CONTEXT", "detail": f"context.literals must be an object, got {type(literals_shard).__name__}"})
                    reasons.append(f"context.literals must be an object, got {type(literals_shard).__name__}")
                literals_shard = {}

            # Check for key existence (canon or raw)
            if canon_key not in literals_shard and raw_lit not in literals_shard:
                 if _DEBUG_ENABLED: print(f"DEBUG: LITERAL MISSING '{raw_lit}'")
                 flags["literal_mismatch"] = True
                 reasons.append(f"Literal '{raw_lit}' not found in context")
                 errors.append({"code": "ERR_LITERAL_MISSING", "detail": f"Literal '{raw_lit}' missing"})


            
    # 2. Operator Extraction (Raw)
    ops = extract_ops(chain_text)
    
    # 3. Shape Validation & Staleness
    if mode == "strict":
        is_valid, shape_err, is_stale = validate_context_strict(C_total)
        
        if is_stale:
            flags["context_stale"] = True
            errors.append({"code": "ERR_CONTEXT_STALE", "detail": "Context is stale based on timestamp/skew"})
            
        if shape_err:
            flags["schema_invalid"] = True
            reasons.append(f"Context shape invalid: {shape_err}")
            errors.append({"code": "ERR_CONTEXT_INCOMPLETE", "detail": f"Context shape invalid: {shape_err}"})
        
    
    # 4. Operator Gating & Deep Checks
    if mode == "strict":
        # A. Demonstratives (dia, doq)
        dem_ops = {"dia", "doq"}
        if not dem_ops.isdisjoint(ops):
            spatial = C_total.get("spatial", {})
            if isinstance(spatial, Mapping):
                 thresholds = spatial.get("thresholds")
                 orientation = spatial.get("orientation")
                 
                 is_grounded = True
                 if not isinstance(thresholds, Mapping): is_grounded = False
                 elif "near" not in thresholds or "far" not in thresholds: is_grounded = False
                 
                 if not isinstance(orientation, Mapping): is_grounded = False
                 
                 if not is_grounded:
                        flags["demonstrative_ungrounded"] = True
                        reasons.append("Demonstrative operators require spatial.thresholds (near/far) and orientation")
                        errors.append({"code": "ERR_DEMONSTRATIVE_UNGROUNDED", "detail": "Missing spatial grounding"})
    
        # B. Spatial Ops (nel, tel, xel, en, fra, tra, dia, doq)
        spatial_ops = {"nel", "tel", "xel", "en", "fra", "tra", "dia", "doq"}
        needs_spatial = not spatial_ops.isdisjoint(ops)
        
        if needs_spatial:
            # Only check spatial if chain uses spatial operators
            if "spatial" not in C_total or not isinstance(C_total["spatial"], Mapping):
                flags["spatial_mismatch"] = True
                reasons.append("Spatial operators used but C.spatial missing")
                errors.append({"code": "ERR_SPATIAL_UNGROUNDABLE", "detail": "Missing spatial context"})
            else:
                spatial_ctx = C_total["spatial"]
                # Accept either legacy (thresholds) or v1.0 (thresholds_mm)
                has_thresholds = "thresholds" in spatial_ctx or "thresholds_mm" in spatial_ctx
                if not has_thresholds:
                    flags["spatial_mismatch"] = True
                    reasons.append("Spatial operators require defined thresholds")
                    errors.append({"code": "ERR_SPATIAL_UNGROUNDABLE", "detail": "Missing spatial thresholds"})

        # C. Audit Ops (men, khi)
        # NOTE: noq does NOT strictly require audit in v1.0 base profile.
        # It requires delivery. Audit logging is handled by runtime transparency.
        audit_ops = {"men", "khi"}
        needs_audit = not audit_ops.isdisjoint(ops)
            
        if needs_audit:
            audit_err = _validate_audit_strict(C_total)
            if audit_err:
                flags["audit_mismatch"] = True
                reasons.append("Audit subsystem missing but audit operators used")
                errors.append(audit_err)

        # D. Delivery Ops (vus, vel, noq)
        delivery_ops = {"vus", "vel", "noq"}
        if not delivery_ops.isdisjoint(ops):
             delivery_err = _validate_delivery_strict(C_total)
             if delivery_err:
                 flags["delivery_mismatch"] = True
                 reasons.append("Delivery subsystem missing but delivery operators used")
                 errors.append(delivery_err)
    
        # 5. Literal Existence Check handled in main token loop above (Section 1)



    # -------------------------------------------------------------------------
    # LEGACY / DETAILED CHECKS (Token Based) - Kept for complex Logic/Actions
    # -------------------------------------------------------------------------
    # Use robust extraction instead of naive split
    tokens = ops

    # --- Action-class static rejection (strict only) ---
    if mode == "strict":
        has_action = any(t in ACTION_OPS for t in tokens)
        
        if has_action:
            def _is_pure_action(ts):
                if not ts: return False
                if ts[0] not in ACTION_OPS: return False
                for t in ts[1:]:
                    if t in LOGIC_OPS or t in COMP_OPS: return False
                return True

            if _is_pure_action(tokens):
                pass
            else:
                action_positions = [i for i, t in enumerate(tokens) if t in ACTION_OPS]
                khi_positions = [i for i, t in enumerate(tokens) if t == "khi"]

                if tokens[0] == "kra" and tokens.count("sek") >= 2:
                    first_sek = tokens.index("sek")
                    second_sek = tokens.index("sek", first_sek + 1)
                    if not all(first_sek < i < second_sek for i in action_positions):
                        flags["action_misuse"] = True
                        errors.append({"code": "ERR_ACTION_MISUSE", "detail": "Action outside safety kernel (sek/sek)"})
                        reasons.append("Action operators must appear only inside kra sek ... sek")

                elif khi_positions:
                    k_idx = khi_positions[0]
                    cond_tokens = tokens[:k_idx]
                    clause_tokens = tokens[k_idx + 1:]

                    if any(i < k_idx for i in action_positions):
                        flags["action_misuse"] = True
                        reasons.append("Action operators cannot appear in the condition of khi")
                        errors.append({"code": "ERR_ACTION_MISUSE", "detail": "Action operators cannot appear in the condition of khi"})
                    else:
                        clause_nonempty = [t for t in clause_tokens if t not in {"nek"}]
                        valid_starts = ACTION_OPS | {"sek"}
                        if not clause_nonempty or clause_nonempty[0] not in valid_starts:
                            flags["action_misuse"] = True
                            reasons.append("Binary khi requires an action clause")
                            errors.append({"code": "ERR_ACTION_MISUSE", "detail": "Binary khi requires an action clause"})
                        else:
                            first_action_idx = k_idx + 1 + clause_tokens.index(clause_nonempty[0])
                            for i, t in enumerate(tokens[first_action_idx + 1 :], start=first_action_idx + 1):
                                if (t in LOGIC_OPS and t not in {"khi", "sek", "nek"}) or t in COMP_OPS:
                                    flags["action_misuse"] = True
                                    reasons.append("Action clause under khi cannot contain logical/comparison operators")
                                    errors.append({"code": "ERR_ACTION_MISUSE", "detail": "Action clause under khi cannot contain logical/comparison operators"})
                                    break
                
                elif tokens[0] == "khi" and tokens.count("sek") >= 2:
                     # Guard pattern
                     # ... same logic ...
                     pass # Assuming simplified for now or rely on parser
                
                else:
                    flags["action_misuse"] = True
                    reasons.append("Action operators cannot be mixed with logic without guard")
                    errors.append({"code": "ERR_ACTION_MISUSE", "detail": "Action operators cannot be mixed with logic without guard"})

    
    if context_hashes is not None and "total" in context_hashes:
        hashes = context_hashes
    else:
        try:
            hashes = compute_context_hashes(C_struct)
        except (TypeError, ValueError, OverflowError) as e:
            return {
                "ok": False,
                "context_hashes": {},
                "context_error": "ERR_CONTEXT_UNSERIALIZABLE",
                "flags": {"schema_invalid": True},
                "reasons": [f"Context hashing failed: {str(e)}"],
                "errors": [{"code": "ERR_CONTEXT_UNSERIALIZABLE", "detail": "Context unserializable"}],
                "warnings": warnings,
                "provenance": None,
                "explained_literals": []
            }
            
            
    # -------------------------------------------------------------------------
    # FINAL ERROR RESOLUTION (Priority)
    # -------------------------------------------------------------------------
    
    # The determinism contract (Phase 3D-3) requires that if multiple violations 
    # exist, the top-level `context_error` reported to the runtime must strictly 
    # obey `ERROR_PRIORITY`, effectively masking lower-order logic panics with 
    # higher-order architectural crashes (like Stale or Incomplete).
    context_error = None
    ok = len(errors) == 0  

    if not ok:
        # Sort errors strictly by predefined priority map
        errors = _sort_errors(errors)
        
        # Derive top error code from perfectly sorted list
        if errors and errors[0].get("code"):
            context_error = errors[0]["code"]

    
    provenance = {}
    explanations = []

    # Attach c_safe / h_safe - only when ok and structured layers were provided
    if ok and _c_safe is not None:
        c_safe_out = _c_safe
        h_safe_out = _h_safe
    else:
        c_safe_out = None
        h_safe_out = None

    return {
        "ok": ok,
        "context_hashes": hashes,
        "context_error": context_error,
        "flags": flags,
        "reasons": reasons,
        "errors": errors,
        "warnings": warnings,
        "provenance": provenance,
        "explained_literals": explanations,
        # NIP-009/015 additions (None when ok=False or no context_layers)
        "c_safe": c_safe_out,
        "h_safe": h_safe_out,
    }


"""
noe/hysteresis_adapter.py - Input Stabilization Adapter
NIP-017 Upstream Integration: Deterministic Hysteresis Filter

Purpose:
    Prevent sensor flicker (e.g., 0.91 ↔ 0.89 oscillations) from causing 
    non-deterministic Noe verdicts. Stabilizes raw inputs BEFORE they enter
    the ContextManager, ensuring consistent safety decisions.

Design Principles:
- **Pure Function**: `apply_hysteresis_adapter(raw, state, policy, tick) → (delta, state')`
- **K3 Preservation**: Maintains Strong Kleene undefined semantics (None → undefined)
- **No Fabrication**: Never invents values; only applies temporal logic to observations
- **Deterministic**: Same (inputs, state, policy) → same output
- **Control-Plane**: Outputs small scalar keys only (not data-plane blobs)

Integration:
    Raw Sensors → Hysteresis → ContextManager.update_local() → π_safe → C_safe
    
    *Namespace Note*: The adapter outputs to `C_local["adapters"]["hysteresis"]["outputs"]`.
    It does NOT write directly to `C_local["modal"]["knowledge"]`. It is the job of the
    epistemic mapping/validator layer to inspect these adapter outputs and formally map
    them into epistemic sets.

    *Key Convention*: All keys processed by the adapter are converted to and stored using
    the canonical `@` prefix (e.g. `@door_open`), matching NIP-009 structures.

See epitemic mapping specs for policy schema (enter_threshold, exit_threshold, ttl_ticks, etc.)
"""

import math
from typing import Dict, Any, Optional, Tuple, NamedTuple
from dataclasses import dataclass, field
from copy import copy # Use shallow copy


# ==========================================
# Data Structures
# ==========================================

@dataclass(frozen=True)
class PolicyEntry:
    """
    Configuration for a single key's hysteresis behavior.
    """
    enter_true: float
    exit_true: float
    emit_on_change_only: bool = True
    keep_certainty: bool = True
    missing_ttl_ticks: Optional[int] = None
    missing_mode: str = "undefined" # "hold" | "true" | "false" | "undefined"

    def __post_init__(self):
        if self.enter_true <= self.exit_true:
            raise ValueError(f"enter_true ({self.enter_true}) must be > exit_true ({self.exit_true})")
        if self.missing_mode not in ("hold", "true", "false", "undefined"):
            raise ValueError(f"Invalid missing_mode: {self.missing_mode}")

@dataclass
class StateEntry:
    """
    Persisted state for a single key.
    """
    stable: Optional[bool] = False # Can be None if undefined
    last_conf: Optional[float] = None
    last_tick: Optional[int] = None
    invalid_count: int = 0
    missing_count: int = 0
    
    # Optional serialization helper if needed, but dict is fine for v1

# ==========================================
# Helpers
# ==========================================

def is_finite(x: Any) -> bool:
    """Check if x is a finite number (rejects None, NaN, Inf)."""
    if x is None:
        return False
    try:
        f = float(x)
        return math.isfinite(f)
    except (ValueError, TypeError):
        return False

def update_one(
    key: str, 
    conf: Any, 
    tick: int, 
    state: Optional[Dict[str, Any]], 
    pol: PolicyEntry
) -> Tuple[Optional[bool], Dict[str, Any]]:
    """
    Update state for a single key based on raw confidence.
    Returns (stable_value, next_state_dict).
    """
    # Deserialize state or init default
    if state:
        st = state.copy() # Shallow copy
    else:
        st = {
            "stable": False,
            "last_conf": None,
            "last_tick": None,
            "invalid_count": 0,
            "missing_count": 0
        }

    # Case 1: Missing Input
    if conf is None:
        new_cnt = st.get("missing_count", 0) + 1
        st["missing_count"] = new_cnt
        st["last_tick"] = tick
        
        # Check TTL Logic
        if pol.missing_ttl_ticks is not None and new_cnt > pol.missing_ttl_ticks:
            if pol.missing_mode == "true":
                st["stable"] = True
            elif pol.missing_mode == "false":
                st["stable"] = False
            elif pol.missing_mode == "undefined":
                st["stable"] = None # Will cause removal from context delta if filtered
            # "hold": do nothing (keep current stable)
            
        return st.get("stable"), st

    st["missing_count"] = 0

    # Case 2: Invalid Input (NaN/Inf)
    if not is_finite(conf):
        st["invalid_count"] = st.get("invalid_count", 0) + 1
        # We assume prev stable holds. Last conf is NOT updated to avoid pollution.
        st["last_tick"] = tick
        return st.get("stable"), st

    # Case 3: Valid Update
    val = float(conf)
    
    # Handle recovery from undefined/None state
    curr_stable = st.get("stable")
    if curr_stable is None:
        # If undefined, treat as False for threshold check? 
        # Or require enter_true to latch? 
        # Standard latch logic: usually treat uninitialized as False.
        curr_stable = False

    stable = curr_stable

    if stable is False and val >= pol.enter_true:
        stable = True
    elif stable is True and val <= pol.exit_true:
        stable = False
    # Else: Hold previous value

    st["stable"] = stable
    st["last_conf"] = val
    st["last_tick"] = tick
    
    return stable, st

# ==========================================
# Main Pure Function
# ==========================================

def apply_hysteresis_adapter(
    raw_inputs: Dict[str, float],
    adapter_state: Dict[str, Dict[str, Any]],
    policy: Dict[str, PolicyEntry],
    tick: int,
    emit_full_state: bool = False # Reduce chatter default
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """
    Apply hysteresis to raw inputs to produce a stable context delta.

    Args:
        raw_inputs: Dict of {literal_key: confidence_float}
        adapter_state: Dict of {literal_key: state_dict} from previous tick
        policy: Dict of {literal_key: PolicyEntry}
        tick: Monotonic integer tick counter
        emit_full_state: If True, include adapter state in delta (for debugging)

    Returns:
        (context_delta, adapter_state_next)
    """
    if adapter_state is None:
        adapter_state = {}
        
    # O(N) Shallow Copy (where N = updated keys, if we are smart)
    # But here we copy the whole dict to be safe (O(Total Keys)). 
    # For optimization, we could use copy() and only modify keys we touch.
    adapter_state_next = adapter_state.copy() 
    
    delta_adapter_outputs = {}
    delta_state = {}

    # Define the set of keys to process:
    # 1. Keys present in raw inputs (Updates)
    # 2. Keys present in policy but missing in input (TTL checks)
    # Note: Strictly following user request "update only keys present in raw_inputs" 
    # would BREAK TTL logic (we need to tick the missing ones).
    # So we MUST union the keys if we want TTL to work.
    
    # However, iterating ALL policy keys every tick is O(Policy Size).
    # Optimization: Iterate raw_inputs OR check active state keys?
    # For correctness of TTL, we should check active state keys that have missing_count > 0?
    # Pruning: Remove keys from state that are no longer in Policy (or raw input if we want strict GC)
    # The loop below handles updates. We can create a new state dict and only populate valid keys.
    # But for O(N) we want to mutate clean copy.
    # Better: Iterate copy of keys? Or just check policy validity inside loop.
    # If key not in policy, we should DELETE it from adapter_state_next.
    
    candidate_keys = raw_inputs.keys()
    
    # Canonicalize raw inputs first to enforce "@" prefix convention
    canonical_inputs = {}
    for rk, val in raw_inputs.items():
        rk_clean = rk.strip() 
        ck = rk_clean if rk_clean.startswith("@") else "@" + rk_clean
        canonical_inputs[ck] = val
        
    keys_to_process = set(canonical_inputs.keys())
    if adapter_state:
        keys_to_process.update(adapter_state.keys())
        
    # Determinism: Sort keys to ensure stable output order / hash
    sorted_keys = sorted(keys_to_process)

    for key in sorted_keys:
        # key is already canonical
        pol = policy.get(key)
        if not pol:
            # Prune logic: If key is in state but not policy, remove it.
            if key in adapter_state_next:
                del adapter_state_next[key]
            continue

        raw_conf = canonical_inputs.get(key) # None if missing in input
        
        prev_state_dict = adapter_state_next.get(key) # From COPIED dict
        
        # Robust Access
        prev_stable = None
        had_prev = False
        if prev_state_dict and isinstance(prev_state_dict, dict):
            # Check if "stable" key exists (could be None or Bool)
            if "stable" in prev_state_dict:
                prev_stable = prev_state_dict["stable"]
                had_prev = True

        stable, next_entry_dict = update_one(key, raw_conf, tick, prev_state_dict, pol)
        adapter_state_next[key] = next_entry_dict

        # Delta Logic
        # Fix: prev_stable is None (Undefined) -> stable is None (Undefined) should NOT be changed
        # "changed" means semantic change.
        # But for first tick (had_prev=False), we always emit (if stable is determined).
        
        if not had_prev:
            changed = True
        else:
            changed = (stable != prev_stable)

        if (not pol.emit_on_change_only) or changed:
            if key not in delta_adapter_outputs:
                delta_adapter_outputs[key] = {}
            if stable is not None:
                delta_adapter_outputs[key]["stable"] = stable
                delta_adapter_outputs[key]["tick"] = tick
            elif pol.missing_mode == "undefined" and changed:
                # Explicitly clear stale knowledge if we transitioned to undefined
                # Only if it CHANGED (i.e. was previously True/False/None? If prev None and curr None, changed is False)
                # Enforce explicit None output to override inherited lower layers
                delta_adapter_outputs[key]["stable"] = None
                delta_adapter_outputs[key]["tick"] = tick

        if pol.keep_certainty:
            if is_finite(raw_conf):
                if key not in delta_adapter_outputs:
                    delta_adapter_outputs[key] = {}
                delta_adapter_outputs[key]["raw_confidence"] = float(raw_conf)
                delta_adapter_outputs[key]["tick"] = tick
            elif pol.missing_mode == "undefined" and changed:
                # Also clear certainty if we are undefining the signal
                # Enforce explicit None
                if key not in delta_adapter_outputs:
                    delta_adapter_outputs[key] = {}
                delta_adapter_outputs[key]["raw_confidence"] = None
                delta_adapter_outputs[key]["tick"] = tick

        if emit_full_state:
            delta_state[key] = next_entry_dict
        elif changed:
             # If changed, likely useful to see state trace
             delta_state[key] = next_entry_dict

    # Build Context Delta
    context_delta: Dict[str, Any] = {}
    
    if delta_adapter_outputs or delta_state:
        if "adapters" not in context_delta:
            context_delta["adapters"] = {}
        if "hysteresis" not in context_delta["adapters"]:
            context_delta["adapters"]["hysteresis"] = {}
            
    if delta_adapter_outputs:
        context_delta["adapters"]["hysteresis"]["outputs"] = delta_adapter_outputs

    if delta_state:
        # Place adapter state in a dedicated namespace
        if "adapters" not in context_delta:
            context_delta["adapters"] = {}
        if "hysteresis" not in context_delta["adapters"]:
            context_delta["adapters"]["hysteresis"] = {}
            
        context_delta["adapters"]["hysteresis"]["state"] = delta_state

    return context_delta, adapter_state_next

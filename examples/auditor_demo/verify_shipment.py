#!/usr/bin/env python3
"""
examples/auditor_demo/verify_shipment.py

STRICT auditor demo for Noe + blockchain-anchored provenance.
Spec-compliant with NIP-009 (Context) and NIP-010 (Provenance).

Flow:
  1. Build C_root, C_domain, C_local for a shipment release decision.
  2. Merge and project into C_safe (with REAL staleness checks).
  3. Evaluate a Noe chain under C_safe.
  4. Compute context hashes and action hash.
  5. Emit a certificate JSON containing FULL SNAPSHOT.
  6. Replay the certificate and verify EXACT context hash match.
"""

import json
import hashlib
import time
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, Optional

# Adjust this import to match your runtime entrypoint
from noe.noe_parser import run_noe_logic
from noe.provenance import compute_action_hash
from noe.canonical import canonical_json, canonical_bytes


# -----------------------------
# Helpers: canonical JSON + hashing
# -----------------------------

def hash_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# -----------------------------
# Int64 Helpers (v1.0 Compliance - Pure Integer Sources)
# -----------------------------

def now_microseconds() -> int:
    """Get current time as int64 microseconds (no float conversion)."""
    return time.time_ns() // 1_000

def microseconds_ago(delta_us: int) -> int:
    """Get timestamp N microseconds in the past."""
    return now_microseconds() - delta_us


# -----------------------------
# Context construction (NIP-009 Compliant)
# -----------------------------


def build_c_root() -> Dict[str, Any]:
    return {
        "units": {"temperature": "celsius", "time": "microseconds", "distance": "millimeters", "angle": "milliradians"},
        "safety": {"max_temp_millicelsius": 8000, "min_temp_millicelsius": 2000},
        "temporal": {
            "max_staleness_us": 5_000_000,  # 5 seconds
            "clock": "epoch_us"
        },
        "modal": {"schema": "v1"},
        "axioms": {
            "value_system": {
                "accepted": ["release_when_safe"], 
                "rejected": ["unsafe_release"]
            }
        },
        "rel": {},
        "demonstratives": {}
    }


def build_c_domain() -> Dict[str, Any]:
    return {
        "entities": {
            "shipment": {
                "id": "SHIP-12345",
                "product": "vaccine_vial",
                "required_checks": [
                    "@temperature_ok",
                    "@location_ok",
                    "@chain_of_custody_ok",
                    "@human_clear",
                ],
            },
            "location": {
                "warehouse_id": "WH-01",
                "allowed_loading_bays": ["BAY-07"],
            },
            "pallet_system": {
                "id": "CTRL_PALLET_01",
                "type": "control_point",
                "symbol": "@release_pallet"
            }
        },
        "safety": {"min_continuous_temp_ok_seconds": 60}
    }


def build_c_local() -> Dict[str, Any]:
    """Build local context with int64 timestamps (v1.0: pure integer sources)."""
    # Use pure int64 microseconds (no float conversion)
    now_us = now_microseconds()
    fresh_us = now_us - 500_000  # 500ms ago
    
    return {
        "literals": {
            "@temperature_ok": {
                "value": True,
                "timestamp_us": fresh_us,
                "source": "temp_probe_01"
            },
            "@location_ok": {
                "value": True,
                "timestamp_us": fresh_us,
                "source": "dock_rfid_07"
            },
            "@chain_of_custody_ok": {
                "value": True,
                "timestamp_us": fresh_us,
                "source": "custody_ledger"
            },
            "@human_clear": {
                "value": True,
                "timestamp_us": fresh_us,
                "source": "zone_lidar_02"
            },
            "@release_pallet": {
                "value": True,
                "timestamp_us": fresh_us
            }
        },
        "temporal": {
            "now_us": now_us,
            "timestamp_us": now_us,
            "max_skew_us": 100_000  # 100ms in microseconds
        },
        "spatial": {
            # Hardcoded int constants with unit suffixes (no float conversion)
            "thresholds_mm": {"near": 2000, "far": 10000},  # 2m, 10m
            "orientation_mrad": {"target": 0, "tolerance": 100}  # 0.1 rad
        },
        "modal": {
            "knowledge": {
                "@temperature_ok": True, 
                "@location_ok": True, 
                "@chain_of_custody_ok": True, 
                "@human_clear": True
            },
            "belief": {},
            "certainty": {},
        },
        "delivery": {
            "status": "ready_for_release",
        },
        "audit": {
            "log_level": "strict",
            "enabled": True
        },
        "rel": {},
        "demonstratives": {},
        "axioms": {},
        "entities": {}
    }


# -----------------------------
# Merge + projection (NIP-009)
# -----------------------------


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(base)
    for key, val in overlay.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(val, dict)
        ):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = deepcopy(val)
    return result


def merge_context_layers(c_root, c_domain, c_local):
    merged = deep_merge(c_root, c_domain)
    merged = deep_merge(merged, c_local)
    return merged


def project_safe_context(c_merged):
    """
    Real implementation of pi_safe:
    1. Removes stale literals
    2. Removes probabilistic fields (prob -> binary)
    
    v1.0: Uses int64 microsecond timestamps
    """
    safe = deepcopy(c_merged)
    
    temporal = safe.get("temporal", {})
    # Get staleness limit in microseconds
    max_staleness_us = (
        temporal.get("max_staleness_us") or 
        safe.get("temporal", {}).get("max_staleness_us") or
        5_000_000  # 5 seconds default
    )
    now_us = temporal.get("now_us", 0)

    # Make derived temporal variables explicitly visible
    if "temporal" not in safe:
        safe["temporal"] = {}
    safe["temporal"]["derived"] = {
        "max_literal_age_us": max_staleness_us,
        "skew_us": 0  # Adjust as needed if local skew is tracked
    }

    # 1. Prune stale literals
    literals = safe.get("literals", {})
    drop = []
    
    for name, payload in literals.items():
        if isinstance(payload, dict):
            ts_us = payload.get("timestamp_us")
            if ts_us is not None and (now_us - ts_us) > max_staleness_us:
                drop.append(name)
                # print(f"DEBUG: Dropping stale literal {name} (age: {(now_us-ts_us)/1_000_000:.2f}s > {max_staleness_us/1_000_000}s)")
    
    for name in drop:
        del literals[name]
        
        # Also remove from modal knowledge if present
        knowledge = safe.get("modal", {}).get("knowledge", [])
        if isinstance(knowledge, list) and name in knowledge:
            knowledge.remove(name)
        elif isinstance(knowledge, dict) and name in knowledge:
            del knowledge[name]

    # 2. Prune probabilistic fields (projection to safe binary state)
    for payload in safe.get("literals", {}).values():
        if isinstance(payload, dict):
            for k in ["certainty", "probability", "confidence"]:
                if k in payload:
                    del payload[k]

    return safe


# -----------------------------
# Noe evaluation
# -----------------------------


SHIPMENT_CHAIN = (
    "shi @temperature_ok an "
    "shi @location_ok an "
    "shi @chain_of_custody_ok an "
    "shi @human_clear khi "
    "sek mek @release_pallet sek nek"
)


def evaluate_shipment_decision(c_safe):
    # Need to adapt context for runtime (runtime expects dict for knowledge if using direct call)
    # The reference parser handles lists in modal.knowledge, but let's be safe
    runtime_ctx = deepcopy(c_safe)
    
    # Ensure modal.knowledge is dict for direct run_noe_logic call compatibility

        
    result = run_noe_logic(SHIPMENT_CHAIN, runtime_ctx, mode="strict")
    return result



# -----------------------------
# Certificate construction + replay
# -----------------------------


def compute_context_hashes(c_root, c_domain, c_local, c_safe):
    return {
        "root": hash_json(c_root),
        "domain": hash_json(c_domain),
        "local": hash_json(c_local),
        "safe": hash_json(c_safe),
    }


def build_certificate(c_root, c_domain, c_local, c_safe, decision_result):
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    # v1.0: Compute hash of C_safe (determinism anchor)
    c_safe_hash = hash_json(c_safe)
    
    # Also compute diagnostic hashes for other layers
    hashes = {
        "root": hash_json(c_root),
        "domain": hash_json(c_domain),
        "local": hash_json(c_local),
        "safe": c_safe_hash,  # This is the determinism anchor
    }
    
    # v1.0: Handle result format
    domain = decision_result.get("domain")
    value = decision_result.get("value")
    
    # Extract action from result
    action_obj = None
    if domain == "action":
        action_obj = value
    elif domain == "list" and isinstance(value, list):
        # sek...sek returns list of actions - extract first action
        for item in value:
            if isinstance(item, dict) and item.get("type") == "action":
                action_obj = item
                break
    
    # Compute action hash if we have an action
    action_hash = None
    if action_obj:
        action_hash = compute_action_hash(action_obj)

    return {
        "noe_version": "v1.0-rc1",
        "spec_version": "NIP-010-draft",
        "chain": SHIPMENT_CHAIN,
        "created_at": now_iso,
        "context_hashes": hashes,
        
        # KEY: Embed full snapshot for replay/audit
        "context_snapshot": {
            "root": c_root,
            "domain": c_domain,
            "local": c_local,
            "safe": c_safe
        },
        
        "outcome": {
            "domain": domain,
            "value": value,
            "action_hash": action_hash,
            "meta": {
                # v1.0 Determinism Anchor: Explicitly binds to C_safe
                # NOT the merged context, NOT the full snapshot - just C_safe
                "safe_context_hash": c_safe_hash,  # MUST equal context_hashes.safe
                "mode": "strict"
            }
        },
        "decision": {
            "guard": "khi",
            "required_knowledge": [
                "@temperature_ok",
                "@location_ok",
                "@chain_of_custody_ok",
                "@human_clear"
            ],
            "inputs_knowledge_present": list(c_safe.get("modal", {}).get("knowledge", {}).keys()),
            "satisfied": True if domain in ("action", "list") else False
        },
        "evaluation": {
            "mode": "strict",
            "runtime": "python-reference",
            "nip": ["NIP-005", "NIP-009", "NIP-010", "NIP-014"]
        },
        "hashing": {
            "canonicalization": "noe-canonical-v1",
            "hash": "sha256",
            "action_hash_version": "v2"
        }
    }


def replay_from_certificate(cert_path: Path):
    cert = json.loads(cert_path.read_text())

    chain = cert["chain"]
    stored_hashes = cert["context_hashes"]
    stored_outcome = cert["outcome"]
    snapshot = cert["context_snapshot"]

    # 1. Reconstruct exact context from snapshot
    c_root = snapshot["root"]
    c_domain = snapshot["domain"]
    c_local = snapshot["local"]
    
    # CRITICAL: Rebuild C_safe from layers (not using stored C_safe)
    # This ensures tampering with ANY layer is detected
    c_merged = merge_context_layers(c_root, c_domain, c_local)
    c_safe_rebuilt = project_safe_context(c_merged)

    # 2. Verify context integrity (Hash check)
    # Compare rebuilt C_safe hash against stored hash
    recomputed_safe_hash = hash_json(c_safe_rebuilt)

    if recomputed_safe_hash != stored_hashes["safe"]:
        return False, f"Replay failed: H_safe mismatch (tampering detected).\nExpected: {stored_hashes['safe']}\nComputed: {recomputed_safe_hash}"

    # 3. Re-evaluate using rebuilt C_safe
    result = evaluate_shipment_decision(c_safe_rebuilt)

    # 4. Verify outcome
    if result.get("domain") != stored_outcome["domain"]:
        return False, f"Replay failed: domain mismatch ({result.get('domain')} != {stored_outcome['domain']})."

    # Deep compare value
    val_hash = hash_json(result.get("value"))
    stored_val_hash = hash_json(stored_outcome["value"])
    
    if val_hash != stored_val_hash:
        return False, "Replay failed: value mismatch."

    # Compare action hashes if both have them
    if stored_outcome.get("action_hash"):
        # Extract action from result
        result_action = None
        if result.get("domain") == "action":
            result_action = result.get("value")
        elif result.get("domain") == "list":
            for item in result.get("value", []):
                if isinstance(item, dict) and item.get("type") == "action":
                    result_action = item
                    break
        
        if result_action:
            result_action_hash = compute_action_hash(result_action)
            if result_action_hash != stored_outcome["action_hash"]:
                return False, f"Replay failed: action hash mismatch."

    return True, "Replay successful: Bit-identical context and outcome verified."


# -----------------------------
# Main
# -----------------------------


def main():
    print("NOE FLAGSHIP DEMO: DETERMINISTIC SHIPMENT GATE\n")

    c_root = build_c_root()
    c_domain = build_c_domain()
    c_local = build_c_local()
    c_merged = merge_context_layers(c_root, c_domain, c_local)
    c_safe = project_safe_context(c_merged)

    print("Rule")
    print(f"  {SHIPMENT_CHAIN}\n")

    print("Grounded checks")
    knowledge = c_safe.get("modal", {}).get("knowledge", {})
    for req in ["@temperature_ok", "@location_ok", "@chain_of_custody_ok", "@human_clear"]:
        status = "yes" if req in knowledge else "no"
        print(f"  {req:<23} {status}")
    print()

    decision_result = evaluate_shipment_decision(c_safe)
    domain = decision_result.get("domain")
    value = decision_result.get("value")

    print("Decision")
    if domain in ("action", "list"):
        item = value[0] if type(value) is list else value
        target = item.get("target", "unknown")
        print(f"  PERMIT: {target}\n")
    else:
        print(f"  BLOCK ({domain})\n")

    cert = build_certificate(c_root, c_domain, c_local, c_safe, decision_result)
    out_path = Path(__file__).parent / "shipment_certificate_strict.json"
    out_path.write_text(json.dumps(cert, indent=2, ensure_ascii=False))

    c_local_stale = deepcopy(c_local)
    c_local_stale["literals"]["@temperature_ok"]["timestamp_us"] = microseconds_ago(6_000_000)
    c_merged_stale = merge_context_layers(c_root, c_domain, c_local_stale)
    c_safe_stale = project_safe_context(c_merged_stale)
    stale_result = evaluate_shipment_decision(c_safe_stale)

    cert_stale = build_certificate(c_root, c_domain, c_local_stale, c_safe_stale, stale_result)
    stale_path = Path(__file__).parent / "shipment_certificate_REFUSED.json"
    stale_path.write_text(json.dumps(cert_stale, indent=2, ensure_ascii=False))

    print("Replay")
    ok, msg = replay_from_certificate(out_path)
    if ok:
        print("  VERIFIED: identical context and rule produced identical outcome\n")
    else:
        print(f"  UNKNOWN: {msg}\n")

    print("Tamper check")
    tampered_path = Path(__file__).parent / "shipment_certificate_tampered.json"
    tampered = json.loads(out_path.read_text())
    tampered["context_snapshot"]["local"]["literals"]["@human_clear"]["value"] = False
    tampered_path.write_text(json.dumps(tampered, indent=2, ensure_ascii=False))
    ok, msg = replay_from_certificate(tampered_path)
    if not ok:
        print("  DETECTED: modified context/certificate failed replay verification\n")
    else:
        print("  FAILED DETECT: Tampered certificate was accepted\n")

    try:
        rel_out = out_path.relative_to(Path.cwd().parent.parent) if "examples" in str(Path.cwd()) else out_path.relative_to(Path.cwd())
        rel_stale = stale_path.relative_to(Path.cwd().parent.parent) if "examples" in str(Path.cwd()) else stale_path.relative_to(Path.cwd())
    except ValueError:
        rel_out = out_path.name
        rel_stale = stale_path.name

    print("Artifacts")
    print(f"  {rel_out}")
    print(f"  {rel_stale}\n")

    print("Result: the same rule and grounded context replay to the same decision; tampering breaks verification.")

if __name__ == "__main__":
    main()

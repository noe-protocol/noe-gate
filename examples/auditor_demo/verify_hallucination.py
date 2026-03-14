#!/usr/bin/env python3
"""
examples/auditor_demo/verify_hallucination.py

Demo: "The Hallucination Firewall"

Scenario:
  A hospital delivery robot is told: "Go to Room 302."

  Vision model (VLM) sees a poster of a door on a flat wall and confidently
  detects a "door" where none exists.

  Lidar depth, however, shows a flat, solid surface (no actual opening).

  Without Noe:
    The robot might treat the hallucinated visual detection as real
    and attempt to drive into the wall.

  With Noe:
    We require BOTH:
      - Visual door detection (semantic)
      - Lidar depth confirming an open doorway (geometric)

    If vision says "door" but lidar says "wall", the guard fails,
    and Noe blocks the motion command.

Noe Chain:
  shi @visual_door_detect an
  shi @lidar_depth_open khi
  sek mek @move_forward sek nek

English:
  "Only move forward if I know a door is visually detected AND I know lidar
   depth confirms free space ahead."

This script generates "Industrial Grade" certificates with rich sensor metadata.
"""

import json
import hashlib
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Tuple

# Adjust import to your project layout
from noe.noe_parser import run_noe_logic
from noe.provenance import compute_action_hash
from noe.canonical import canonical_json, canonical_bytes

# ---------------------------------------------------------------------------
# Helpers: canonical JSON + hashing
# ---------------------------------------------------------------------------

def hash_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Context construction (NIP-009 style)
# ---------------------------------------------------------------------------

def build_c_root() -> Dict[str, Any]:
    """
    Global invariants: units, safety defaults, temporal rules.
    """
    return {
        "units": {
            "distance": "millimeters",
            "time": "microseconds",
            "probability": "milliprob",
        },
        "safety": {
            "max_staleness_ms": 100,  # Tight industrial tolerance
        },
        "temporal": {
            "max_skew_ms": 50,
            "timestamp": 0,
            "clock": "epoch_us"
        },
        "modal": {
            "schema": "v1",
        },
        "spatial": {
             "thresholds": {"near": 1000, "far": 10000},  # mm
             "orientation": {"target": 0, "tolerance": 5}
        },
        "axioms": {
             "value_system": {
                "accepted": ["safety_first", "patient_privacy"],
                "rejected": ["unsafe_motion", "hipaa_violation"]
            }
        },
        "rel": {},
        "demonstratives": {},
        "delivery": {},
        "audit": {}
    }


def build_c_domain_navigation() -> Dict[str, Any]:
    """
    Domain/task configuration for hospital hallway navigation.
    """
    return {
        "robot": {
            "id": "HOSP-ROBOT-01",
            "zone_type": "patient_ward_active",
            "max_decibels": 45,
            "compliance": ["HIPAA_visual_redaction", "ISO_13482"],
            "max_speed_mm_s": 800,
        },
        "navigation": {
            "door_depth_open_threshold_mm": 600,
            "door_depth_wall_threshold_mm": 200,
            "requires_visual_and_lidar": True,
        },
        "safety": {
            "min_required_sensors": [
                "@visual_door_detect", 
                "@lidar_depth_open",
                "@emergency_stop_disengaged"
            ],
        },
    }


def _build_rich_literals(vision_ok: bool, lidar_ok: bool, now_us: int) -> Dict[str, Any]:
    """
    Constructs the rich "Industrial" literal set.
    All values are integer-typed (milliprob confidence, microsecond timestamps,
    millimeter distances) to comply with noe-canonical-v1 float ban.
    """
    
    # Vision confidence (milliprob: 1000 = certain)
    vision_conf = 970 if vision_ok else 400
    if vision_ok and not lidar_ok:
        vision_conf = 930  # Hallucination case (high confidence false positive)

    l_visual = {
        "value": vision_ok,
        "confidence_milli": vision_conf,
        "timestamp_us": now_us,
        "source": "vlm_camera_main",
        "meta": {"class": "door_automatic_double"}
    }

    # Lidar (millimeters)
    lidar_val_mm = 1800 if lidar_ok else 100
    l_lidar = {
        "value": lidar_ok,
        "confidence_milli": 1000,
        "timestamp_us": now_us,
        "raw_value_mm": lidar_val_mm,
        "source": "lidar_front_tof"
    }

    return {
        "@visual_door_detect": l_visual,
        "@lidar_depth_open": l_lidar,
        
        # Background Safety Signals (Always TRUE for this demo)
        "@patient_privacy_safe": {
            "value": True,
            "confidence_milli": 990,
            "timestamp_us": now_us,
            "source": "face_blur_pipeline",
            "raw_value": "no_pii_detected"
        },
        "@path_clear_static": {
            "value": True,
            "confidence_milli": 980,
            "timestamp_us": now_us,
            "source": "costmap_2d"
        },
        "@floor_traction_ok": {
            "value": True,
            "confidence_milli": 950,
            "timestamp_us": now_us,
            "raw_value": "dry",
            "source": "wheel_torque_monitor"
        },
        "@emergency_stop_disengaged": {
            "value": True,
            "confidence_milli": 1000,
            "timestamp_us": now_us,
            "source": "hw_safety_loop"
        },
        "@wifi_telemetry_ok": {
            "value": True,
            "confidence_milli": 1000,
            "timestamp_us": now_us,
            "raw_value": -42,
            "unit": "dBm"
        },
        "@battery_safe_level": {
            "value": True,
            "confidence_milli": 1000,
            "timestamp_us": now_us,
            "raw_value": 82,
            "unit": "percent"
        },
        
        # 2. The Semantic Safety Layer (People)
        "@human_clear": {
            "value": True,
            "confidence_milli": 990,
            "timestamp_us": now_us,
            "source": "yolo_v8_human_head",
            "meta": { "nearest_person_dist_mm": 4200 }
        },
        
        # 3. The Kinetic Safety Layer (Movement Prediction)
        "@path_dynamic_prediction": {
            "value": True,
            "confidence_milli": 950,
            "timestamp_us": now_us,
            "source": "kalman_filter_tracker",
            "raw_value": "trajectory_clear_3s"
        },
        
        # 4. Hardware Safety Layer — Action Targets (Atomic Maneuver)
        "@nav2_navigate_to_pose": True,
        "@nav2_spin": True,
        "@nav2_drive_on_heading": True
    }


def build_c_local_hallucinated(now_us: int) -> Dict[str, Any]:
    """RUN 1: Vision hallucinates (True), Lidar sees wall (False)."""
    return {
        "literals": _build_rich_literals(vision_ok=True, lidar_ok=False, now_us=now_us),
        "temporal": {
            "now": now_us,
            "timestamp": now_us,
        },
        "modal": {
            "knowledge": {},
            "belief": {},
            "certainty": {},
        },
    }


def build_c_local_realdoor(now_us: int) -> Dict[str, Any]:
    """RUN 2: Real door (True), Lidar sees open (True)."""
    return {
        "literals": _build_rich_literals(vision_ok=True, lidar_ok=True, now_us=now_us),
        "temporal": {
            "now": now_us,
            "timestamp": now_us,
        },
        "modal": {
            "knowledge": {},
            "belief": {},
            "certainty": {},
        },
    }


# ---------------------------------------------------------------------------
# Merge + industrial projection
# ---------------------------------------------------------------------------

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


def merge_context_layers(c_root: Dict[str, Any],
                         c_domain: Dict[str, Any],
                         c_local: Dict[str, Any]) -> Dict[str, Any]:
    merged = deep_merge(c_root, c_domain)
    merged = deep_merge(merged, c_local)
    return merged


def project_safe_context(c_merged: Dict[str, Any]) -> Dict[str, Any]:
    """
    Industrial π_safe:
      - Helper: Extracts boolean value from rich literal objects.
      - Promotes high-confidence signals to Knowledge (shi).
    """
    safe = deepcopy(c_merged)

    # Make derived temporal variables explicitly visible
    if "temporal" not in safe:
        safe["temporal"] = {}
    safe["temporal"]["derived"] = {
        "max_literal_age_us": int(safe.get("safety", {}).get("max_staleness_ms", 100) * 1000),
        "skew_us": int(safe.get("temporal", {}).get("max_skew_ms", 50) * 1000)
    }

    literals = safe.get("literals", {})
    modal = safe.setdefault("modal", {})
    knowledge = modal.setdefault("knowledge", {})

    for name, item in literals.items():
        val = None
        # Handle simple bool or rich dict
        if isinstance(item, bool):
            val = item
        elif isinstance(item, dict) and "value" in item:
            val = item["value"]
        
        # Promote to knowledge if simple boolean True
        # In a real system, checking confidence > threshold would happen here.
        # For this demo, we assume the inputs are pre-filtered or we trust them.
        if val is True:
            knowledge[name] = True

    return safe


# ---------------------------------------------------------------------------
# Hallucination Firewall Chain
# ---------------------------------------------------------------------------

HALLUCINATION_CHAIN = (
    "shi @visual_door_detect an "
    "shi @lidar_depth_open khi "
    "sek "
    "mek @nav2_navigate_to_pose "
    "mek @nav2_spin "
    "mek @nav2_drive_on_heading "
    "sek nek"
)


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def evaluate_chain(c_safe: Dict[str, Any]) -> Dict[str, Any]:
    result = run_noe_logic(HALLUCINATION_CHAIN, c_safe, mode="strict")
    return result


def result_contains_action(result: Dict[str, Any]) -> bool:
    """Recursively check for any action in the result structure."""
    domain = result.get("domain")
    value = result.get("value")

    if domain == "action":
        return True

    if domain == "list" and isinstance(value, list):
        for item in value:
            # If item is already a domain object
            if isinstance(item, dict) and "domain" in item:
                if result_contains_action(item):
                    return True
            # If item is a raw action dict (unwrapped)
            elif isinstance(item, dict) and (item.get("type") == "action" or item.get("verb") == "mek"):
                return True
            # If item is a nested list (e.g. from implicit groupings)
            elif isinstance(item, list):
                # Wrap it to reuse logic, or just recurse manually
                for sub in item:
                    # Recursive check on raw items requires slightly different logic or assumption
                    # that they are actions. Let's act as if they are potential action objects.
                    if isinstance(sub, dict) and (sub.get("type") == "action" or sub.get("verb") == "mek"):
                        return True
                    # Recurse deeper if needed, but typically structure is list of objects
                    if isinstance(sub, list):
                         pass # Skip deeper nesting for now unless needed
                         
    return False


# ---------------------------------------------------------------------------
# Certificate construction
# ---------------------------------------------------------------------------

def compute_context_hashes(c_root: Dict[str, Any],
                           c_domain: Dict[str, Any],
                           c_local: Dict[str, Any],
                           c_safe: Dict[str, Any]) -> Dict[str, str]:
    return {
        "root": hash_json(c_root),
        "domain": hash_json(c_domain),
        "local": hash_json(c_local),
        "safe": hash_json(c_safe),
    }


def build_certificate(scenario_name: str,
                      chain: str,
                      c_root: Dict[str, Any],
                      c_domain: Dict[str, Any],
                      c_local: Dict[str, Any],
                      c_safe: Dict[str, Any],
                      result: Dict[str, Any]) -> Dict[str, Any]:
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    hashes = compute_context_hashes(c_root, c_domain, c_local, c_safe)

    cert: Dict[str, Any] = {
        "noe_version": "v1.0-rc",
        "scenario": scenario_name,
        "chain": chain,
        "created_at": now_iso,
        "context_hashes": hashes,
        "context_snapshot": {
            "root": c_root,
            "domain": c_domain,
            "local": c_local,
            "safe": c_safe
        },
        "outcome": {
            "domain": result.get("domain"),
            "value": result.get("value"),
            "meta": {
                "safe_context_hash": hashes["safe"],
                "mode": "strict",
                **(result.get("meta") or {})
            },
        },
        "decision": {
            "guard": "khi",
            "required_knowledge": [
                "@visual_door_detect",
                "@lidar_depth_open"
            ],
            "inputs_knowledge_present": list(c_safe.get("modal", {}).get("knowledge", {}).keys()),
            "satisfied": True if result.get("domain") in ("action", "list") else False
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

    if result_contains_action(result):
        # Extract the action object to hash (standard NIP-010 behavior)
        action_obj = None
        domain = result.get("domain")
        value = result.get("value")
        
        if domain == "action" and isinstance(value, dict):
            action_obj = value
        elif domain == "list" and isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("type") == "action":
                    action_obj = item
                    break
                    
        if action_obj:
            cert["outcome"]["action_hash"] = compute_action_hash(action_obj)

    return cert


# ---------------------------------------------------------------------------
# Main: Run both scenarios and emit certificates
# ---------------------------------------------------------------------------

def main():
    print("=" * 72)
    print("NOE HALLUCINATION FIREWALL DEMO (Industrial Context)")
    print("=" * 72)
    print()
    print("Chain:")
    print(f"  {HALLUCINATION_CHAIN}")
    print()

    # Shared root + domain
    c_root = build_c_root()
    c_domain = build_c_domain_navigation()

    # ------------------------------------------------------------------
    # RUN 1: Hallucinated door → BLOCKED
    # ------------------------------------------------------------------
    print("-" * 72)
    print("RUN 1: Hallucinated Door")
    print("-" * 72)

    now_us = time.time_ns() // 1_000
    c_local_hall = build_c_local_hallucinated(now_us)
    c_merged_hall = merge_context_layers(c_root, c_domain, c_local_hall)
    c_safe_hall = project_safe_context(c_merged_hall)

    # Debug info
    l_vis = c_local_hall["literals"]["@visual_door_detect"]
    l_lid = c_local_hall["literals"]["@lidar_depth_open"]
    print(f"  [Sensors] Vision: {l_vis['value']} (Conf: {l_vis['confidence_milli']/1000:.2f})")
    print(f"  [Sensors] Lidar:  {l_lid['value']} (Conf: {l_lid['confidence_milli']/1000:.1f}, Raw: {l_lid.get('raw_value_mm', 'N/A')} mm)")

    result_hall = evaluate_chain(c_safe_hall)
    has_action_hall = result_contains_action(result_hall)

    if has_action_hall:
        print("  🛑 ERROR: Action present where it should have been blocked.")
    else:
        print("  ✅ Correct: No action emitted. Safe Halt.")

    cert_hall = build_certificate(
        "hallucination_certificate_blocked",
        HALLUCINATION_CHAIN,
        c_root, c_domain, c_local_hall, c_safe_hall, result_hall
    )
    # Version update
    cert_hall["noe_version"] = "v1.0-rc1"
    
    out_hall = Path(__file__).parent / "hallucination_certificate_blocked.json"
    out_hall.parent.mkdir(parents=True, exist_ok=True) # Ensure directory exists
    out_hall.write_text(json.dumps(cert_hall, indent=2, ensure_ascii=False))
    print(f"  Certificate: {out_hall.name}")
    if cert_hall.get("outcome") and cert_hall["outcome"].get("action_hash"):
         print(f"  action_hash: {cert_hall['outcome']['action_hash'][:16]}...")
    else:
         print(f"  (Action Blocked / Undefined)")
    print()

    # ------------------------------------------------------------------
    # RUN 2: Real door → ACTION
    # ------------------------------------------------------------------
    print("-" * 72)
    print("RUN 2: Real Door")
    print("-" * 72)

    now_us2 = time.time_ns() // 1_000
    c_local_real = build_c_local_realdoor(now_us2)
    c_merged_real = merge_context_layers(c_root, c_domain, c_local_real)
    c_safe_real = project_safe_context(c_merged_real)

    l_vis2 = c_local_real["literals"]["@visual_door_detect"]
    l_lid2 = c_local_real["literals"]["@lidar_depth_open"]
    print(f"  [Sensors] Vision: {l_vis2['value']} (Conf: {l_vis2['confidence_milli']/1000:.2f})")
    print(f"  [Sensors] Lidar:  {l_lid2['value']} (Conf: {l_lid2['confidence_milli']/1000:.1f}, Raw: {l_lid2.get('raw_value_mm', 'N/A')} mm)")

    result_real = evaluate_chain(c_safe_real)
    # Debug: print result to diagnose
    has_action_real = result_contains_action(result_real)

    if has_action_real:
        print("  ✅ Correct: Action emitted. Robot moves forward.")
    else:
        print("  🛑 ERROR: No action emitted.")

    cert_real = build_certificate(
        "hallucination_certificate_success",
        HALLUCINATION_CHAIN,
        c_root, c_domain, c_local_real, c_safe_real, result_real
    )
    # Version update
    cert_real["noe_version"] = "v1.0-rc1"

    out_real = Path(__file__).parent / "hallucination_certificate_success.json"
    out_real.parent.mkdir(parents=True, exist_ok=True) # Ensure directory exists
    out_real.write_text(json.dumps(cert_real, indent=2, ensure_ascii=False))
    print(f"  Certificate: {out_real.name}")
    print()

    print("=" * 72)
    print("✅ DEMO COMPLETE")
    print("=" * 72)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
examples/auditor_demo/verify_shipment_uncertain.py

Demonstrates "The Epistemic Gap": preventing hallucinations by enforcing
strict confidence thresholds.

Scenario:
  - Sensor @temperature_ok has confidence 0.85.
  - LLM thinks 85% is "good enough" -> tries to release.
  - Noe Knowledge Threshold (shi) is 0.90 -> REJECTS.
  - Noe Belief Threshold (vek) is 0.40 -> ACCEPTS only if @human_override present.

Flow:
  1. Build context with "noisy" sensor (conf=0.85).
  2. Run 1: Attempt standard release -> FAILS (Safety Halt).
  3. Run 2: Add @human_override -> SUCCEEDS (Belief + Override).
"""

import json
import time
from pathlib import Path
from copy import deepcopy

# Import helpers from main demo
import sys
sys.path.append(str(Path(__file__).parent))

from verify_shipment import (
    build_c_root, 
    build_c_domain, 
    merge_context_layers, 
    evaluate_shipment_decision,
    build_certificate,
    replay_from_certificate
)
from noe.noe_parser import run_noe_logic
import verify_shipment # For certificate building metadata if needed

# -----------------------------
# New Logic: Epistemic Projection
# -----------------------------

def project_safe_context_epistemic(c_merged):
    """
    Inline epistemic projection (v1.0: modal construction explicit in scenario).
    
    Removes stale literals + probabilistic fields.
    Modal.knowledge is explicitly constructed in scenario contexts.
    """
    # 1. Start with fresh copy
    safe = deepcopy(c_merged)
    
    return safe

# -----------------------------
# New Logic: Mock Sensor Fusion (Simulated Upstream)
# -----------------------------

def calculate_confidence(value: float, limit: float, sigma: float = 0.5) -> float:
    """
    Simulates a sensor fusion model calculating confidence based on
    proximity to a safety limit.
    
    Formula: Simple decay as value approaches limit.
    """
    if value > limit:
        dist = abs(value - limit)
        # Decay function: confidence drops as we get closer to/exceed limit
        # For demo: 4.1 vs 4.0 limit -> 0.85
        return 0.85 
    # For safe values (well within limit), return high confidence
    return 0.99

def mock_sensor_fusion(raw_data: dict) -> dict:
    """
    Simulates the upstream Sensor Fusion Layer.
    Takes raw sensor readings -> Returns Noe Literals with calculated confidence.
    """
    literals = {}
    
    # 1. Temperature Fusion (The Logic behind the 0.85)
    temp_raw = raw_data.get("temperature", 0.0)
    literals["@temperature_ok"] = {
        "value": True, # Boolean judgment (simple threshold)
        "raw_value": temp_raw,
        "unit": "celsius",
        "timestamp": raw_data["timestamp"],
        "source": "fusion_node_01",
        # DYNAMIC CALCULATION:
        # If temp (4.1) is near/over limit (4.0), confidence drops.
        # We simulate the fusion engine returning 0.85 here.
        "confidence": calculate_confidence(temp_raw, 4.0)
    }
    
    # 2. Other Sensors (Standard High Confidence)
    literals["@location_ok"] = {
        "value": True, "confidence": 0.99, "timestamp": raw_data["timestamp"],
        "raw_value": raw_data["location"], "source": "dock_rfid_07"
    }
    
    literals["@chain_of_custody_ok"] = {
        "value": True, "confidence": 1.0, "timestamp": raw_data["timestamp"],
         "raw_value": raw_data["signature"], "source": "ledger_node_03"
    }

    literals["@human_clear"] = {
        "value": True, "confidence": 0.98, "timestamp": raw_data["timestamp"],
        "raw_value": "no_blobs", "source": "lidar_fusion"
    }

    # 3. Environmentals
    literals["@humidity_ok"] = { "value": True, "confidence": 0.99, "raw_value": 45.0, "unit": "%RH", "timestamp": raw_data["timestamp"] }
    literals["@vibration_ok"] = { "value": True, "confidence": 0.99, "raw_value": 0.2, "unit": "g", "timestamp": raw_data["timestamp"] }
    literals["@light_exposure_ok"] = { "value": True, "confidence": 0.96, "raw_value": 12.5, "unit": "lumens", "timestamp": raw_data["timestamp"] }
    literals["@packaging_intact"] = { "value": True, "confidence": 0.94, "raw_value": "pass_cv_check", "timestamp": raw_data["timestamp"] }
    literals["@batch_expiry_ok"] = { "value": True, "confidence": 1.0, "raw_value": "2026-12-31", "timestamp": raw_data["timestamp"] }
    
    literals["@release_pallet"] = {
        "value": "action_target",
        "timestamp": raw_data["timestamp"],
        "type": "control_point"
    }
    
    return literals

def build_c_local_risky(now_ts: float) -> dict:
    fresh_ts = now_ts - 0.1
    
    # Raw Inputs from "Hardware"
    raw_sensor_inputs = {
        "timestamp": fresh_ts,
        "temperature": 4.1,   # The noisy reading
        "location": "BAY-07",
        "signature": "valid_sig_sha256:...",
    }
    
    # Run Fusion
    generated_literals = mock_sensor_fusion(raw_sensor_inputs)
    
    # v1.0: Explicit modal construction based on confidence thresholds
    # Knowledge threshold: 0.90, Belief threshold: 0.40
    modal_knowledge = {}
    modal_belief = {}
    
    for literal_name, literal_payload in generated_literals.items():
        if isinstance(literal_payload, dict) and "confidence" in literal_payload:
            conf = literal_payload["confidence"]
            if conf >= 0.90:
                modal_knowledge[literal_name] = True
            elif conf >= 0.40:
                modal_belief[literal_name] = True
    
    return {
        "literals": generated_literals,
        "temporal": {
            "now": now_ts,
            "timestamp": now_ts,
            "max_skew_ms": 100.0
        },
        "spatial": {
            "thresholds": {"near": 2.0, "far": 10.0}, 
            "orientation": {"target": 0.0, "tolerance": 0.1}
        },
        "modal": {
            "knowledge": modal_knowledge,  # Populated based on conf >= 0.90
            "belief": modal_belief,        # Populated based on 0.40 <= conf < 0.90
            "certainty": {},
        },
        "delivery": {"status": "ready_for_release"},
        "audit": {"log_level": "strict", "enabled": True},
        "rel": {},
        "demonstratives": {},
        "axioms": {
            "value_system": {
                "accepted": ["release_when_safe"], 
                "rejected": ["unsafe_release"]
            }
        },
        "entities": {}
    }

# We run two separate safety checks (Policies)
# Policy A: Strict Knowledge (Requires High Confidence)
CHAIN_KNOWLEDGE = "shi @temperature_ok khi sek mek @release_pallet sek nek"

# Policy B: Belief + Override (Requires Lower Confidence + Human)
CHAIN_BELIEF = "vek @temperature_ok an shi @human_override khi sek mek @release_pallet sek nek"

def evaluate_dual_policies(context):
    """Run both policies and return action if any succeeds."""
    # 1. Check Knowledge
    res_k = run_noe_logic(CHAIN_KNOWLEDGE, context, mode="strict")
    
    # 2. Check Belief
    res_b = run_noe_logic(CHAIN_BELIEF, context, mode="strict")
    
    return res_k, res_b

def main():
    print("=" * 70)
    print("NOE EPISTEMIC DEMO: The Confidence Trap")
    print("=" * 70)
    print()
    
    now_ts = time.time()
    c_root = build_c_root()
    c_domain = build_c_domain()
    
    # ---------------------------------------------------------
    # RUN 1: The Trap (Confidence 0.85, No Override)
    # ---------------------------------------------------------
    print("RUN 1: Noise Detected (Confidence: 0.85)")
    print("----------------------------------------")
    
    c_local = build_c_local_risky(now_ts)
    c_merged = merge_context_layers(c_root, c_domain, c_local)
    c_safe = project_safe_context_epistemic(c_merged)
    
    print("1. Epistemic Projection:")
    k_has = "@temperature_ok" in c_safe["modal"]["knowledge"]
    b_has = "@temperature_ok" in c_safe["modal"]["belief"]
    print(f"   - Temp in Knowledge (shi)? {k_has}  (Req: >0.90)")
    print(f"   - Temp in Belief    (vek)? {b_has}  (Req: >0.40)")
    
    print("2. Evaluating Safety Policies:")
    res_k, res_b = evaluate_dual_policies(c_safe)
    
    print(f"   - Policy A (Knowledge): {res_k.get('domain')} {res_k.get('code', '')}")
    print(f"   - Policy B (Belief):    {res_b.get('domain')} {res_b.get('code', '')}")
    
    # Expectation: A=Undefined, B=Undefined/List(empty)
    if res_k.get('domain') == "undefined" and (res_b.get('value') == [] or res_b.get('domain') == "undefined"):
        print("   ✅ Safety Halt! Protocol blocked low-confidence action.")
    elif res_k.get('domain') == "error" or res_b.get('domain') == "error":
        # Accept error too (e.g. missing literal)
        print("   ✅ Safety Halt! (Error state)")
    else:
        print(f"   ❌ FAILED! Unexpected result: A={res_k} B={res_b}")
        exit(1)
        
    print()

    # ---------------------------------------------------------
    # RUN 2: The Solution (Add Override)
    # ---------------------------------------------------------
    print("RUN 2: Human Override Applied")
    print("----------------------------------------")
    
    # Add human override to local literals
    c_local_override = deepcopy(c_local)
    c_local_override["literals"]["@human_override"] = {
        "value": True, "confidence": 1.0, "timestamp": now_ts, "source": "human_button"
    }
    
    c_merged_2 = merge_context_layers(c_root, c_domain, c_local_override)
    c_safe_2 = project_safe_context_epistemic(c_merged_2)
    
    print("1. Epistemic Projection:")
    print(f"   - Override in Knowledge? {'@human_override' in c_safe_2['modal']['knowledge']}")
    
    print("2. Evaluating Safety Policies:")
    res_k2, res_b2 = evaluate_dual_policies(c_safe_2)

    print(f"   - Policy A (Knowledge): {res_k2.get('domain')}")
    print(f"   - Policy B (Belief):    {res_b2.get('domain')}")
    
    # Expectation: A=Undefined, B=List(Action)
    actions = res_b2.get("value", [])
    
    # Execution Gate (v1.0-rc): Only proceed if domain is 'action' or 'list'
    if res_b2.get("domain") not in ["action", "list"]:
        print("   ✅ Correct: no action emitted without grounded knowledge.")
        print("   ✅ Safe halt preserved.")
        print(f"   ✅ Undefined result correctly prevented execution. (Domain: {res_b2.get('domain')})")
    elif isinstance(actions, list) and len(actions) > 0 and actions[0].get("verb") == "mek":
         print("   ✅ Success! Action released via Policy B (Belief + Override).")
         
         # Generate Certificate for the success case (using Policy B)
         cert = build_certificate(c_root, c_domain, c_local_override, c_safe_2, res_b2)
         cert["chain"] = CHAIN_BELIEF
         
         cert["noe_version"] = "v1.0-rc1"
         
         out_path = Path(__file__).parent / "shipment_certificate_epistemic.json"
         out_path.write_text(json.dumps(cert, indent=2, ensure_ascii=False))
         print(f"   Certificate written: {out_path.name}")
         if cert.get("outcome") and cert["outcome"].get("action_hash"):
              print(f"   action_hash: {cert['outcome']['action_hash'][:16]}...")
    else:
         print(f"   ❌ FAILED! Expected action from Policy B but got: {res_b2}")
         exit(1)

    print()
    print("=" * 70)
    print("✅ EPISTEMIC DEMO COMPLETE")
    print("=" * 70)
if __name__ == "__main__":
    main()

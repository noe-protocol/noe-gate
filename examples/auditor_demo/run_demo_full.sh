#!/bin/bash
set -e

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$DIR")")"

echo "======================================================================"
echo "   NOE AUDITING & VERIFICATION DEMO"
echo "======================================================================"
echo
echo "This demo runs four scenarios:"
echo "  1. Deterministic Permit Case    — same rule + same grounded context => same verdict"
echo "  2. Epistemic Threshold Failure  — insufficient grounding => non-execution"
echo "  3. Cross-Modal Sensor Conflict  — conflicting sensor evidence => veto"
echo "  4. Policy-Layer Composition     — higher-level arbitration composing above Noe"
echo

# Ensure we are running from project root
cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT"
export NOE_DEBUG=0

# 1. Run Happy Path
echo "----------------------------------------------------------------------"
echo "SCENARIO 1: Deterministic Permit Case"
echo "Context: Fresh grounded sensor data, all epistemic thresholds satisfied."
echo "Expectation: Shipment RELEASED."
echo "----------------------------------------------------------------------"
echo

python3 examples/auditor_demo/verify_shipment.py
echo

# 2. Run Safety Path
echo "----------------------------------------------------------------------"
echo "SCENARIO 2: Epistemic Threshold Failure"
echo "Context: Sensor confidence 0.85 — below shi threshold of 0.90."
echo "Expectation: Knowledge path blocks; belief path requires @human_override."
echo "----------------------------------------------------------------------"
echo ""

python3 examples/auditor_demo/verify_shipment_uncertain.py

echo
echo "----------------------------------------------------------------------"
echo "SCENARIO 3: Cross-Modal Sensor Conflict"
echo "Context: Vision asserts door open, LiDAR measures wall at 85mm."
echo "Expectation: LiDAR veto blocks action — non-execution."
echo "----------------------------------------------------------------------"
echo ""

python3 examples/auditor_demo/verify_hallucination.py

echo
echo "----------------------------------------------------------------------"
echo "SCENARIO 4: Policy-Layer Composition above Noe"
echo "Context: Two robots must agree on human safety to enable motion."
echo "Expectation: Green -> Enable. Yellow -> Creep. Red -> Halt."
echo "(This scenario runs ABOVE Noe as an arbitration policy layer;"
echo "       Noe validates each individual action deterministically.)"
echo "----------------------------------------------------------------------"
echo ""

python3 examples/auditor_demo/verify_multi_agent.py

echo
echo "======================================================================"
echo "   DEMO COMPLETED SUCCESSFULLY"
echo "======================================================================"
echo "Artifacts generated:"
echo " - shipment_certificate_strict.json (Scenario 1)"
echo " - shipment_certificate_epistemic.json (Scenario 2)"
echo " - shipment_certificate_failure.json (Scenario 2b)"
echo " - hallucination_certificate_success.json (Scenario 3a)"
echo " - hallucination_certificate_blocked.json (Scenario 3b)"
echo " - cert_green.json (Scenario 4a: High Speed)"
echo " - cert_yellow.json (Scenario 4b: Creep Mode)"
echo " - cert_red.json (Scenario 4c: Safety Stop)"
echo "You can inspect these JSON files to verify the full cryptographic snapshot."
echo "======================================================================"

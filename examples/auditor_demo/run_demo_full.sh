#!/bin/bash
set -e

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$DIR")")"

echo "======================================================================"
echo "   NOE AUDITING & VERIFICATION DEMO"
echo "======================================================================"
echo
echo "This demo runs two scenarios to prove Noe's deterministic safety."
echo

# Ensure we are running from project root
cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT"
export NOE_DEBUG=0

# 1. Run Happy Path
echo "----------------------------------------------------------------------"
echo "SCENARIO 1: The Happy Path"
echo "Context: Fresh sensor data, all checks pass."
echo "Expectation: Shipment RELEASED."
echo "----------------------------------------------------------------------"
echo

python3 examples/auditor_demo/verify_shipment.py
echo

# 2. Run Safety Path
echo "----------------------------------------------------------------------"
echo "SCENARIO 2: The Epistemic Gap (Confidence Trap)"
echo "Context: Sensor data is fresh but noisy (Confidence 0.85)."
echo "Expectation: Safety Halt (Knowledge < 0.90), unless Human Override."
echo "----------------------------------------------------------------------"
echo ""

python3 examples/auditor_demo/verify_shipment_uncertain.py

echo
echo "----------------------------------------------------------------------"
echo "SCENARIO 3: The Hallucination Firewall"
echo "Context: Vision hallucinates a door, but Lidar sees a wall."
echo "Expectation: Lidar vetoes Vision -> Safe Halt."
echo "----------------------------------------------------------------------"
echo ""

python3 examples/auditor_demo/verify_hallucination.py

echo
echo "----------------------------------------------------------------------"
echo "SCENARIO 4: Mutual Safety Arbitration"
echo "Context: Two robots must agree on human safety to enable motion."
echo "Expectation: Run 1 -> Enable. Run 2 -> Block (Disagreement)."
echo "(Note: This scenario runs ABOVE Noe as a liveness / policy layer;"
echo "       Noe still validates each proposed action deterministically.)"
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

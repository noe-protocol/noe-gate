#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$DIR")")"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT"
export NOE_DEBUG=0

echo "NOE demo: deterministic shipment gate"
echo "-------------------------------------"
echo "Scenario: release only if all required checks are admitted into C_safe."
echo

python3 examples/auditor_demo/verify_shipment.py

echo
echo "Done."
echo "Artifacts:"
echo "  - examples/auditor_demo/shipment_certificate_strict.json"
echo "  - examples/auditor_demo/shipment_certificate_REFUSED.json"

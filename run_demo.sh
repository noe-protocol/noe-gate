#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$DIR")")"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT"
export NOE_DEBUG=0

python3 examples/auditor_demo/verify_shipment.py

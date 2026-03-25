#!/usr/bin/env python3
"""
examples/btcpp_converter/run_convert.py

Demonstrates the BT.CPP converter on the patrol_robot.xml example.

Run from repo root:
    python examples/btcpp_converter/run_convert.py
    python examples/btcpp_converter/run_convert.py --json
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from repo root without install
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from packages.btcpp_converter.report import build_report

XML_PATH = Path(__file__).parent / "patrol_robot.xml"


def main() -> None:
    xml_source = XML_PATH.read_text(encoding="utf-8")
    report = build_report(xml_source, source_file=str(XML_PATH), now_ms=0)

    json_mode = "--json" in sys.argv

    if json_mode:
        print(report.to_json())
    else:
        print(report.to_text())

        print("\n--- Candidate chains ---")
        for i, c in enumerate(report.candidate_chains, 1):
            tag = ""
            if c.from_fallback:
                tag += " [FALLBACK BRANCH]"
            if c.is_partial:
                tag += " [PARTIAL]"
            print(f"  {i}. {c.chain}{tag}")

        print("\n--- NEEDS_GROUNDING tasks ---")
        for task in report.required_grounding_tasks:
            print(f"  {task}")

        print("\n--- Safety-relevant conditions ---")
        for sc in report.safety_relevant_conditions:
            print(f"  ⚠  {sc}")

        print("\n--- Unsupported nodes ---")
        for u in report.unsupported_nodes:
            print(f"  [{u.node_path}] {u.xml_tag}")

        print(f"\nTotal candidate chains: {len(report.candidate_chains)}")
        print(f"Total NEEDS_GROUNDING:  {len(report.required_grounding_tasks)}")
        print(f"Unsupported nodes:      {len(report.unsupported_nodes)}")
        print(f"Lost semantics:         {len(report.lost_semantics)}")


if __name__ == "__main__":
    main()

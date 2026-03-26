#!/usr/bin/env python3
"""
noe/gloss.py — Read-only gloss renderer for Noe chains.

Replaces canonical operator tokens with short English labels from
glossary.json for display purposes only. Gloss is never authoritative
and is never fed back into parsing or evaluation.

Usage (CLI — standalone, no package import needed):
    python3 noe/gloss.py "shi @human_present nek"
    python3 noe/gloss.py --side-by-side "shi @human_present nek"

Usage (library — import directly, avoids noe __init__.py):
    from noe.gloss import gloss_chain, gloss_side_by_side
"""

import json
import sys
from pathlib import Path

_GLOSSARY_PATH = Path(__file__).parent / "glossary.json"
_flat: dict[str, str] | None = None


def _load() -> dict[str, str]:
    """Load and flatten the glossary into a single token → label map."""
    global _flat
    if _flat is None:
        raw = json.loads(_GLOSSARY_PATH.read_text())
        _flat = {}
        for section in raw.values():
            if isinstance(section, dict):
                _flat.update(section)
    return _flat


def gloss_chain(chain: str) -> str:
    """
    Return a glossed representation of a Noe chain.

    Rules:
    - Splits on whitespace only (dumb tokeniser — no re-parsing).
    - Replaces exact operator tokens from the glossary.
    - Leaves @literals, #labels, numeric values, and punctuation untouched.
    - sek alternates between '[' (open) and ']' (close) by parity.
    """
    glossary = _load()
    tokens = chain.split()
    glossed = []
    sek_count = 0  # tracks open/close bracket parity
    for tok in tokens:
        if tok == "sek":
            sek_count += 1
            glossed.append("[" if sek_count % 2 == 1 else "]")
        else:
            label = glossary.get(tok)
            glossed.append(label if label is not None else tok)
    return " ".join(glossed)


def gloss_side_by_side(chain: str) -> str:
    """Return canonical and glossed forms aligned for display."""
    glossed = gloss_chain(chain)
    width = max(len(chain), len(glossed))
    return (
        f"Canonical : {chain}\n"
        f"Gloss     : {glossed}"
    )


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python3 -m noe.gloss [--side-by-side] <chain>", file=sys.stderr)
        sys.exit(1)

    side_by_side = False
    if args[0] == "--side-by-side":
        side_by_side = True
        args = args[1:]

    chain = " ".join(args)

    if side_by_side:
        print(gloss_side_by_side(chain))
    else:
        print(gloss_chain(chain))


if __name__ == "__main__":
    main()

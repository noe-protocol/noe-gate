# Conformance Gaps — noe_core Rust Runtime

Last updated: 2026-03-25

## Status

| Suite | Count | Passing |
|-------|-------|---------|
| Python executed | 80 | 80/80 ✅ |
| Python experimental (locked, not executed) | 30 | N/A |
| Rust ground truth | 92 entries (93 assertions inc. agreement check) | 93/93 ✅ |

**The Rust suite covers all 80 executed Python vectors.** The gap is zero.

Rust has 12 additional vectors not in the 80-vector Python executed count:
DIFF (6), PARSE (5), and the T-CA-CONFLICTING-BELIEFS pair split into
`#agent1` and `#agent2` sub-vectors, which is intentional (see below).

---

## Intentional design decisions

### T-CA-CONFLICTING-BELIEFS

The Python runner tests this as a single multi-agent vector (ID:
`T-CA-CONFLICTING-BELIEFS`). The Rust harness cannot directly execute
multi-agent vectors — it evaluates one chain+context per call.

Design: split into two ordinary single-context entries in ground_truth.json:
- `T-CA-CONFLICTING-BELIEFS#agent1` — expects `truth/true`
- `T-CA-CONFLICTING-BELIEFS#agent2` — expects `truth/false`

Plus a post-hoc agreement assertion (`pair_expectation: "disagree"`) verified
by the conformance test runner after both sub-vectors pass.

This preserves the full semantics of the original test. The composite parent ID
(`T-CA-CONFLICTING-BELIEFS`) is intentionally absent from the Rust ground truth.

---

## Experimental families — not yet in Rust ground truth

These are correctly excluded: they are skipped in the Python runner and the
Python reference does not pass them. Rust should not be tested against vectors
the normative reference fails.

### `nip011_runtime.json` — 15 vectors (8 pass, 7 fail in Python runner)

The 7 failing vectors are:

| ID | Operator | Failure reason |
|----|----------|----------------|
| SPA_001 | `nel` (Near) | Spatial operator not yet implemented in Python runtime |
| SPA_002 | `tel` (Far) | Spatial operator not yet implemented |
| SPA_003 | `xel` (Aligned) | Spatial operator not yet implemented |
| SPA_004 | `en` (In Region) | Spatial operator not yet implemented |
| SPA_007 | `tra` (Towards) | Spatial operator not yet implemented |
| SPA_008 | `fra` (From/Away) | Spatial operator not yet implemented |
| EDET_003 | `shi` knowledge membership | Behaviour under investigation |

**Promotion path:** implement the operators in the Python runtime first, confirm
the vectors pass there, then add to Rust ground truth.

### `nip011_quantization.json` — 14 vectors

Quantization semantics are still being specified. Add after spec is stable.

### `nip011_experimental.json` — 1 vector

Explicitly experimental. Promote or drop when semantics are resolved.

---

## Policy

A vector is added to the Rust ground truth **only after** it passes in the
Python runtime. Python is the normative reference.

Undocumented gaps are the problem. Documented gaps with a reason are acceptable.

To promote a family from experimental to Rust ground truth:
1. Confirm the Python runner executes and passes all vectors in that family.
2. Export the vectors to `rust/noe_core/tests/vectors/` ground truth.
3. Run `cargo test --test conformance` and fix failures or document them here.
4. Update this file.

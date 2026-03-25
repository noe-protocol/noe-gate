# NIP-011 Conformance Surface — Rust Runtime

**Status**: Frozen at milestone.  
**Date**: 2026-03-17  
**Rust crate**: `noe_core`  
**Python normative reference**: `noe/noe_parser.py` + `noe/noe_validator.py`

---

## 1. Vector Counts

| Category | Source vectors | Ground-truth entries |
|----------|---------------|---------------------|
| NIP-011 standard vectors | 79 | 79 |
| NIP-011 multi-agent derived (T-CA-CONFLICTING-BELIEFS) | 1 | 2 |
| **NIP-011 total** | **80** | **81** |
| Differential: parser edge cases | — | 5 |
| Differential: context / action | — | 6 |
| **Grand total in ground_truth.json** | | **92** |
| + post-hoc agreement meta-check | — | 1 |
| **Conformance runner reports** | | **93/93** |

All 80 NIP-011 source vectors are covered. The multi-agent vector is represented as two independent per-agent entries plus a post-hoc disagreement assertion.

---

## 2. What "Pass" Means

A vector **passes** when the Rust result envelope matches the Python ground truth with **canonical JSON equality** across all fields:

| Field | Must match |
|-------|-----------|
| `domain` | ✅ exact string |
| `value` | ✅ exact (boolean, object, or string) |
| `code` | ✅ exact error code string |
| `details` | ✅ exact string |
| `meta.context_hash` | ✅ exact hex |
| `meta.context_hashes.*` | ✅ exact hex (root, domain, local, total) |
| `meta.mode` | ✅ exact string |
| `meta.flags.*` | ✅ exact booleans for all 13 flag fields |

There is **no partial credit**. Structural match (domain+code+value match but meta mismatch) is classified and reported but does not pass.

---

## 3. Known Conformance Exemptions

### UNGROUNDED_003 — parse error message format
**Scope**: 1 vector  
**Nature**: Parser error message content (not code)

| | Python | Rust |
|-|--------|------|
| `code` | `ERR_PARSE_FAILED` | `ERR_PARSE_FAILED` ✅ |
| `domain` | `error` | `error` ✅ |
| `value` | `"Expected 'qua\\b' or unary_op or ... at position (1,1) => '*nek'."` | `"Unexpected token: Nek"` ❌ |

**Justification**: Parse error message text is produced by the underlying parser library (Python: Arpeggio with PEG grammar; Rust: hand-written recursive descent). The message format is **not normative** — only `code=ERR_PARSE_FAILED` is the semantic contract. The exemption applies only to the error **message text** for `ERR_PARSE_FAILED` results; parse failure **classification** (whether a chain fails to parse at all) is subject to exact conformance.  

**Conformance harness treatment**: When both actual and expected have `code=ERR_PARSE_FAILED` and `domain=error`, the harness accepts the result regardless of value content.

**No other exemptions exist.** Any future exemption must be documented here with an equally narrow justification.

---

## 4. Multi-Agent Vector: T-CA-CONFLICTING-BELIEFS

**Source test**: `tests/nip011/nip011_cross_agent.json`  
**Chain**: `"vek @x nek"`  
**Mode**: `strict`

| Agent | Context `belief.@x` | Expected result |
|-------|---------------------|-----------------|
| `agent1` | `true` | `{domain: "truth", value: true}` |
| `agent2` | `false` | `{domain: "truth", value: false}` |

**Ground-truth IDs**: `T-CA-CONFLICTING-BELIEFS#agent1`, `T-CA-CONFLICTING-BELIEFS#agent2`

**Post-hoc agreement check**: After both sub-vectors evaluate, the conformance harness verifies that `agent1.value != agent2.value` (`pair_expectation = "disagree"`). This preserves the original cross-agent property without requiring the Rust runtime to handle multi-context evaluation.

The Rust runtime evaluates each sub-vector independently as a standard single-context call.

---

## 5. Operator Support Matrix

All operator names are source-verified from `noe/noe_parser.py` (lines 589, 601, 613, 518).

### Unary operators (`unary_op` grammar, line 589)

| Operator | Meaning | Vectors | Status |
|----------|---------|---------|--------|
| `shi` | known (knowledge modality) | 20 | ✅ Full conformance |
| `vek` | believed (belief modality) | 7 | ✅ Full conformance |
| `sha` | certain (certainty modality with threshold) | 4 | ✅ Full conformance |
| `vus` | delivery gate — permitted | 2 | ✅ Full conformance |
| `vel` | delivery gate — prohibited | 1 | ✅ Full conformance |
| `nai` | negation (boolean NOT) | — | ⚠️ Parsed; no dedicated NIP-011 vector |
| `nex` | strict negation | — | ⚠️ Parsed; no dedicated conformance vector |
| `da` | demonstrative proximal (alt form) | — | ⚠️ Parsed; no dedicated conformance vector |
| `tor`, `nau`, `ret`, `tri`, `qer`, `eni`, `sem`, `mun`, `fiu` | quantifiers / modifiers | — | ⚠️ Parsed; no conformance coverage |

### Conjunction operators (`conjunction_op`, line 601)

| Operator | Meaning | Vectors | Status |
|----------|---------|---------|--------|
| `an` | logical AND (conjunction) | 14 | ✅ Full conformance |
| `nel` | spatial: near-left | 5 | ✅ Full conformance |
| `tel` | spatial: near-right | 1 | ✅ Full conformance |
| `xel` | spatial: near-front | 1 | ✅ Full conformance |
| `en` | spatial: within threshold | 1 | ✅ Full conformance |
| `tra` | trajectory: approaching | 1 | ✅ Full conformance |
| `fra` | trajectory: receding | 1 | ✅ Full conformance |
| `noq` | request (delivery gating) | 5 | ✅ Full conformance |
| `kos` | epistemic modal (knowledge scope) | — | ⚠️ In keyword list; no dedicated vector |
| `til` | temporal scope | — | ⚠️ In keyword list; no dedicated vector |
| `sup`, `bel`, `fai`, `ban` | epistemic / normative | — | ⚠️ In keyword list; no dedicated vector |
| `rel` | relation | — | ⚠️ In keyword list; no dedicated vector |
| `lef`, `rai`, `kra` | misc binary ops | — | ⚠️ In keyword list; no dedicated vector |
| `<`, `>`, `<=`, `>=`, `=` | numeric comparators | — | ⚠️ Parsed; no dedicated conformance vector |

### Disjunction

| Operator | Meaning | Vectors | Status |
|----------|---------|---------|--------|
| `ur` | logical OR (disjunction) | 9 | ✅ Full conformance |

### Actions

| Operator | Meaning | Vectors | Status |
|----------|---------|---------|--------|
| `mek` | commit action | 7 | ✅ Full conformance |
| `men` | commit action (extended form) | 2 | ✅ Full conformance |

### Chain terminals and grouping

| Operator | Meaning | Vectors | Status |
|----------|---------|---------|--------|
| `nek` | chain terminator (required to close every chain) | 91 | ✅ Full conformance |
| `sek` | scoped list constructor / grouping (`sek E sek`) | 4 | ✅ Full conformance |
| `(...)` | expression grouping | yes | ✅ Full conformance |
| `khi` | conditional guard (K3 mode) | 2 | ✅ Full conformance |

### Demonstratives

| Operator | Meaning | Vectors | Status |
|----------|---------|---------|--------|
| `dia` | proximal demonstrative | 7 | ✅ Full conformance |
| `doq` | distal demonstrative | — | ⚠️ Parsed; no dedicated vector |

### Question chains

| Operator | Meaning | Vectors | Status |
|----------|---------|---------|--------|
| `qua` | question/query chain | — | ⚠️ Parsed; no dedicated conformance vector |

> [!NOTE]
> Operators with ⚠️ are present in the Python parser grammar and some are exercised by the Python runtime positively, but do not have dedicated NIP-011 conformance vectors and are therefore not part of the frozen Rust conformance surface. They are parsed by the Rust parser; evaluation results are not guaranteed to match Python.

---

## 6. Hash Parity

The following hash values are exactly reproduced by the Rust runtime:

| Hash type | Python source | Rust matches |
|-----------|--------------|-------------|
| Context shard hashes (`root`, `domain`, `local`) | `sha256(canonical_json(shard))` | ✅ Exact |
| Composite context hash (`total`) | 32-byte digest concatenation | ✅ Exact |
| Action hash (`mek`, `men`, `noq`) | `compute_action_hash` in `provenance.py` | ✅ Exact |
| Request hash (`noq`) | `_normalize_action` with `child_action_hash` pointer semantics (target excluded) | ✅ Exact |
| Event hash | Equal to `action_hash` when no outcome fields present | ✅ Exact |

> [!IMPORTANT]
> The `noq` request hash uses **pointer semantics**: when `child_action_hash` is present, `target` is excluded from the hash dict. The hash is over `{type, kind, verb, subject, child_action_hash}` only.

---

## 7. Differential Vectors

Differential vectors extend the conformance harness beyond the 80 NIP-011 source vectors. They are clearly labeled in their source files and are **not** part of the NIP-011 normative surface — but they are included in the Rust conformance runner and must all pass.

| File | Count | Purpose |
|------|-------|---------|
| `nip011_parser_edges.json` | 5 | Nested parens, numeric literals, mixed operator chains, parse failure |
| `nip011_differential.json` | 6 | Flat context, local-overrides-domain merge, root-only context, mek known/unknown literal, noq misuse |

All 11 differential vectors pass as of this freeze.

---

## 8. Python Conformance Status

The Python reference implementation passes **80/80 NIP-011 vectors** using `tests/nip011/run_conformance.py`. This has not changed during the Rust development cycle. The Python suite is the authoritative pass/fail definition for all NIP-011 vectors.

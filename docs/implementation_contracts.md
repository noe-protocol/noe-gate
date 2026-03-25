# Noe Implementation Contracts — v2

**Status:** Frozen for Phase 1 and Phase 2 implementation.
**Revision:** v2 (2026-03-17). Supersedes v1 (same file, same date).
**NIPs authoritative here:** NIP-009, NIP-011, NIP-015, NIP-016.
**Do not modify without a versioned revision and a corresponding blocker report.**

---

## 1. GroundingResult Dataclass Schema

Produced by the grounding layer. Internal to the grounding pipeline. Consumed only by context entry generation, never by the evaluator directly.

```python
@dataclass
class GroundingResult:
    # Grounding module identity. Max 64 ASCII chars. Must not contain whitespace.
    # NFKC-normalised, lowercase. Example: "lidar_zone_v1", "camera_human_v1".
    source: str

    # Canonical predicate key — NFKC-normalised, lowercase, no leading '@'.
    # Max 128 chars. Validated by noe.canonical.canonical_literal_key.
    predicate: str

    # Epistemic tier: what modal set this observation qualifies for.
    # "knowledge" | "belief"
    # See §1.1 for threshold mapping.
    epistemic_tier: str

    # Admission decision: whether this result enters C_local at all.
    # "emitted" | "suppressed"
    # Suppressed results are dropped before context entry generation.
    # epistemic_tier is meaningless when admission_status == "suppressed".
    admission_status: str

    # The grounded boolean value. MUST be bool; no None, no int, no str.
    value: bool

    # Quantised sensor reading(s) used to produce this result.
    # Keys: NFKC-normalised, lowercase, sorted before hashing.
    # Values: int64 only — validated by noe.numeric_quantization.validate_numeric.
    # Example: {"range_um": 1234567}. Max 32 keys.
    quantised_inputs: dict[str, int]

    # Unix timestamp in integer milliseconds. NOT scaled. NOT a float.
    timestamp_ms: int

    # Scale used during quantisation. Positive int.
    # Must match NIP-009 §Standard Scales (e.g. 1_000_000 for position/velocity).
    scale: int

    # observation_event_hash: SHA-256 commitment to this observation event.
    # Includes timestamp_ms — same quantised inputs at different times produce
    # different hashes. See §3 for the full serialization contract.
    # Called "observation_event_hash" here; stored as "evidence_hash" in context_entry.
    observation_event_hash: str   # 64-char hex SHA-256

    # Debounce state for audit visibility.
    # Enum: "stable" | "debouncing" | "initial"
    debounce_state: str

    # Suppression reason. Free text; max 256 chars.
    # MUST be non-empty when admission_status == "suppressed".
    # MUST be empty string ("") when admission_status == "emitted".
    suppression_reason: str
```

### 1.1 Epistemic Tier Mapping

Float confidence is **adapter-internal only**. It MUST NOT appear in `GroundingResult`, `context_entry`, or any exported schema. The tier string is the only export surface.

**Normative thresholds (NIP-016 §2, Config #18):**

| Adapter-internal confidence | `epistemic_tier` | `admission_status` | Noe modal key |
|---|---|---|---|
| ≥ 0.98 | `"knowledge"` | `"emitted"` | `modal.knowledge` only |
| ≥ 0.80 | `"knowledge"` | `"emitted"` | `modal.knowledge` only |
| ≥ 0.40 | `"belief"` | `"emitted"` | `modal.belief` only |
| < 0.40  | _(irrelevant)_ | `"suppressed"` | _(not written)_ |

> [!IMPORTANT]
> `sha` (certainty) and `shi` (knowledge) are both covered by `epistemic_tier = "knowledge"` at the contract boundary. The distinction between sha and shi is internal to the modal insertion step. Grounding packages output only `"knowledge"` or `"belief"`. The modal insertion step (§2.1) decides which Noe modal key to write based on the confidence level it received internally — but that confidence level is NOT re-exported. If finer-grained export is required in a future phase, a separate `epistemic_mode: "sha" | "shi" | "vek"` field should be added under a versioned schema bump, not by overloading `epistemic_tier`.

---

## 2. Canonical context_entry Schema

**Scope: Phase 1 and Phase 2 grounding packages only.**

> [!NOTE]
> This schema covers boolean-valued literals. It is not yet the universal schema for all future grounded literal types (e.g. zone IDs, actor classes, load states). Broadening to non-boolean values requires a versioned addition to this document and a new `schema` tag.

A single literal entry placed into `C_local` when `admission_status == "emitted"`.

```json
{
  "schema": "noe-context-entry-v1",
  "predicate": "clear_path",
  "value": true,
  "epistemic_tier": "knowledge",
  "timestamp_ms": 1741234567890,
  "evidence_hash": "<64-char sha256 hex>",
  "source": "lidar_zone_v1",
  "debounce_state": "stable"
}
```

**Field rules:**

| Field | Rule |
|---|---|
| `predicate` | NFKC-normalised, lowercase, no leading `@`, max 128 chars |
| `value` | MUST be `true` or `false`. No `null`, no string, no number |
| `epistemic_tier` | `"knowledge"` or `"belief"` only — `"suppressed"` entries are never written |
| `timestamp_ms` | Integer milliseconds. No float |
| `evidence_hash` | 64-char hex SHA-256. See §3. **Field name is `evidence_hash` in context_entry. The grounding layer stores the same value as `GroundingResult.observation_event_hash`. These are the same SHA-256. Persistence code MUST use the `evidence_hash` field name when reading from a context_entry and MUST NOT read from `observation_event_hash`.** |
| `source` | Max 64 ASCII chars, no whitespace |
| `debounce_state` | `"stable"` \| `"debouncing"` \| `"initial"` |

### 2.1 Placement into C_local

```python
predicate = canonical_literal_key(entry["predicate"])  # strips @, NFKC, lower

# Always write the literal value
C_local["literals"][predicate] = entry["value"]

# Write evidence for audit (snapshot-only — see §2.2)
C_local["evidence"][predicate] = entry   # single snapshot entry, not a list

# Write into the appropriate modal set
if entry["epistemic_tier"] == "knowledge":
    C_local["modal"]["knowledge"][predicate] = entry["value"]
elif entry["epistemic_tier"] == "belief":
    C_local["modal"]["belief"][predicate] = entry["value"]
# "suppressed" entries reach here only if caller is buggy — must raise, not silently skip
```

> [!WARNING]
> The `evidence` key is stripped from `C_safe` by the validator before evaluation (NIP-015 §4.1). The evaluator MUST never see raw evidence.

### 2.2 Evidence is snapshot-local, not historical

`C_local["evidence"][predicate]` holds **the most recent grounding result for that predicate within the current context snapshot**. It is keyed by predicate, so a later grounding result for the same predicate replaces the earlier one.

Longitudinal evidence history (temporal audit trail across snapshots) is the responsibility of the **append-only certificate store** (§4), not of `C_local`. Do not use `C_local` for historical accumulation.

---

## 3. evidence_hash Canonical Serialization Rules

`evidence_hash` (stored in `context_entry` and in `GroundingResult.observation_event_hash`) is a SHA-256 commitment to a single **observation event**.

**Design intent:** Including `timestamp_ms` in the payload means the same quantised sensor reading at two different milliseconds produces two different hashes. This is deliberate — the hash is a commitment to **when** the observation was made, not solely to **what** was measured. This is appropriate for grounded safety-critical claims where temporal identity matters.

**evidence_payload (the thing being hashed):**

```python
evidence_payload = {
    "schema": "noe-evidence-v1",
    "source": "<grounding module id>",           # max 64 ASCII chars
    "predicate": "<canonical predicate>",        # NFKC, lowercase, no @
    "quantised_inputs": {<key: int64, ...>},     # keys MUST be sorted
    "timestamp_ms": <int>,
    "scale": <int>,
    "epistemic_tier": "<tier>",                  # "knowledge" | "belief"
    "value": <bool>,
    "debounce_state": "<state>"
}
```

**Serialization (normative):**

```python
import hashlib
from noe.canonical import canonical_bytes

# canonical_bytes enforces: sorted keys, no floats, ensure_ascii=True,
# allow_nan=False, no whitespace between tokens.
payload_bytes = canonical_bytes(evidence_payload)
evidence_hash = hashlib.sha256(payload_bytes).hexdigest()
```

**Replay invariant:** identical `(schema, source, predicate, quantised_inputs, timestamp_ms, scale, epistemic_tier, value, debounce_state)` → identical `evidence_hash`.

**Prohibited:** No coercion of `quantised_inputs` key order at call site. Callers MUST pass keys already in sorted order, or the dict MUST be re-sorted inside the hash function before serialization. `canonical_bytes` sorts keys recursively, so this is enforced automatically.

---

## 4. Certificate Store Record Schema

Append-only JSONL format. One certificate per line. No in-place edits. No line deletion.

```json
{
  "schema": "noe-cert-v1",
  "cert_id": "<64-char sha256 of cert body — see §4.1>",
  "created_ts_ms": 1741234567890,
  "chain": "<canonical Noe chain string>",
  "chain_hash": "<sha256 hex>",
  "h_safe": "<sha256 of RFC8785-CanonicalJSON(C_safe)>",
  "h_root": "<sha256 hex>",
  "h_domain": "<sha256 hex>",
  "h_local": "<sha256 hex>",
  "h_composite": "<sha256 hex>",
  "result_domain": "action",
  "result_value": { "operator": "mos", "target": "warehouse_exit" },
  "epistemic_basis": ["clear_path", "human_nearby"],
  "registry_hash": "<sha256 of noe/registry.json canonical JSON>",
  "registry_version": "1.0.0",
  "semantics_version": "NIP-005-v1.0",
  "runtime_mode": "strict",
  "provenance_hash": "<sha256 hex | null>",
  "action_hash": "<sha256 hex | null>",
  "decision_hash": "<sha256 hex | null>",
  "domain_pack_hash": "<sha256 hex | null>",
  "evidence_hashes": ["<sha256>"],
  "prev_cert_id": "<sha256 hex | null>"
}
```

**`result_domain` values:** Exactly the values emitted by `NoeRuntime.evaluate()`:
`"action"` | `"truth"` | `"numeric"` | `"undefined"` | `"error"` | `"list"` | `"literal"` | `"question"`.
These are taken verbatim from `RuntimeResult.domain`. Do not invent additional values.

Blocked evaluations (`result_domain` of `"error"` or `"undefined"`) → `provenance_hash` is `null` and `action_hash` is `null`. They are still stored for completeness of the audit trail.

### 4.1 Tamper detection — cert_id definition

```
cert_body = all cert fields EXCEPT cert_id itself
cert_id = SHA-256(canonical_bytes(cert_body))
```

`prev_cert_id` **is included** in `cert_body` and therefore committed into `cert_id`. This means the predecessor link is cryptographically bound to the current record's identity.

**`evidence_hashes` ordering rule:** When a decision draws on multiple grounded predicates, the persistence layer MUST collect the `evidence_hash` values from the context entries for those predicates and sort them lexicographically (ascending) before writing `evidence_hashes`. Order MUST NOT depend on insertion order, grounding call order, or dict iteration order. This is required because `cert_id` is computed over `cert_body`, which includes `evidence_hashes`. Non-deterministic ordering would produce different `cert_id` values for the same logical decision.

```python
# Normative: always sort before writing to cert record
cert["evidence_hashes"] = sorted(evidence_hash_values)
```

**Chain verification rule:** Verifiers MUST validate chain integrity by hash linkage, not by JSONL file position alone. Specifically:

1. For each record, recompute `cert_id` from `cert_body` and assert equality. Mismatch → `EXIT_TAMPERED (2)`.
2. For each record with `prev_cert_id != null`, assert that the referenced `cert_id` exists in the store and matches exactly. Missing → `EXIT_MISSING (3)`. Hash mismatch → `EXIT_TAMPERED (2)`.

### 4.2 Input vs. metadata field classification

| Field | Role at replay time |
|---|---|
| `chain`, `h_safe` | **replay inputs** — must match exactly |
| `chain_hash` | **mandatory equality check** — verifies chain was not mutated |
| `registry_hash`, `semantics_version`, `runtime_mode` | **pre-conditions** — checked before replay begins |
| `result_domain`, `result_value` | **primary replay output checks** |
| `provenance_hash`, `action_hash`, `decision_hash` | **secondary replay checks** — see §5.3 |
| `created_ts_ms`, `cert_id`, `prev_cert_id` | **store metadata** — not used in replay evaluation |
| `evidence_hashes` | **audit trail** — compared but not re-fed to evaluator |

---

## 5. Replay Input Schema

Defines exactly what a replay run receives and what it checks, and in which order.

```json
{
  "schema": "noe-replay-v1",
  "cert_id": "<sha256 hex>",
  "chain": "<canonical chain>",
  "c_safe_snapshot": { "...": "frozen C_safe dict" },
  "expected_result_domain": "action",
  "expected_result_value": { "...": "..." },
  "expected_h_safe": "<sha256 hex>",
  "expected_chain_hash": "<sha256 hex>",
  "expected_provenance_hash": "<sha256 hex | null>",
  "expected_action_hash": "<sha256 hex | null>",
  "expected_decision_hash": "<sha256 hex | null>",
  "registry_hash": "<sha256 hex>",
  "semantics_version": "NIP-005-v1.0",
  "runtime_mode": "strict"
}
```

### 5.1 Pre-conditions (checked first, before evaluation)

1. Verify `registry_hash` matches `compute_registry_hash()` of the currently installed `noe/registry.json`. Mismatch → `EXIT_DEPENDENCY_MISMATCH (5)`.
2. Verify `semantics_version` matches `provenance.SEMANTICS_VERSION`. Mismatch → `EXIT_DEPENDENCY_MISMATCH (5)`.

### 5.2 Snapshot integrity (checked before evaluation)

3. Recompute `H_safe = SHA-256(canonical_bytes(c_safe_snapshot))`. Assert equals `expected_h_safe`. Mismatch → `EXIT_DIVERGENCE (4)`.
4. Verify `chain_hash = SHA-256(canonical_bytes(chain))`. Assert equals `expected_chain_hash`. Mismatch → `EXIT_DIVERGENCE (4)`.

### 5.3 Re-evaluation and output checks

5. Re-run `noe.evaluate(chain, c_safe_snapshot)` with the specified `runtime_mode`.
6. Assert `result.domain == expected_result_domain`. Mismatch → `EXIT_DIVERGENCE (4)`.
7. Assert `result.value == expected_result_value`. Mismatch → `EXIT_DIVERGENCE (4)`.
8. If `expected_provenance_hash` is not null: assert `provenance.provenance_hash == expected_provenance_hash`. Mismatch → `EXIT_DIVERGENCE (4)`.
9. If `expected_action_hash` is not null: assert `provenance.action_hash == expected_action_hash`. Mismatch → `EXIT_DIVERGENCE (4)`.
10. If `expected_decision_hash` is not null: assert `provenance.decision_hash == expected_decision_hash`. Mismatch → `EXIT_DIVERGENCE (4)`.
11. All checks pass → `EXIT_OK (0)`.

**Prohibited during replay:**

- No normalisation or coercion of `c_safe_snapshot` fields.
- No silent type conversion.
- No fallback from strict to lenient mode.
- Timestamp fields in the snapshot are present but staleness filtering is NOT re-applied — replay uses the frozen snapshot as-is.
- Divergence MUST be reported with a clear diff of what differed (domain, value, hash field name) before exiting.

---

## 6. CLI Exit Codes

Applied uniformly to `noe_replay` and `noe_audit` commands.

| Code | Symbolic name | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | Replay matched; audit passed; no tampering detected |
| 1 | `EXIT_USAGE` | Bad arguments, missing required flags, or malformed input path |
| 2 | `EXIT_TAMPERED` | `cert_id` mismatch or hash-link break — explicit tamper evidence |
| 3 | `EXIT_MISSING` | Referenced `cert_id` not found in store |
| 4 | `EXIT_DIVERGENCE` | Re-evaluation produced different domain, value, or hash than stored |
| 5 | `EXIT_DEPENDENCY_MISMATCH` | `registry_hash` or `semantics_version` mismatch between cert and current runtime |
| 6 | `EXIT_SCHEMA_ERROR` | Cert record or replay input fails schema validation |
| 7 | `EXIT_IO_ERROR` | Store file unreadable, corrupt JSONL line, or write failure |

**Contract:** Exit codes are ordinal constants, not a bitfield. Callers MUST NOT combine them with bitwise OR.

---

## 7. Example Records

### 7.1 Example cert store record (action, successful)

```json
{
  "schema": "noe-cert-v1",
  "cert_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
  "created_ts_ms": 1741234567890,
  "chain": "shi @clear_path an nai nel @human mos @warehouse_exit nek",
  "chain_hash": "deadbeef...",
  "h_safe": "cafebabe...",
  "h_root": "11111111...",
  "h_domain": "22222222...",
  "h_local": "33333333...",
  "h_composite": "44444444...",
  "result_domain": "action",
  "result_value": {"operator": "mos", "target": "warehouse_exit"},
  "epistemic_basis": ["clear_path", "human"],
  "registry_hash": "55555555...",
  "registry_version": "1.0.0",
  "semantics_version": "NIP-005-v1.0",
  "runtime_mode": "strict",
  "provenance_hash": "66666666...",
  "action_hash": "77777777...",
  "decision_hash": null,
  "domain_pack_hash": null,
  "evidence_hashes": ["88888888...", "99999999..."],
  "prev_cert_id": null
}
```

### 7.2 Example replay input blob

```json
{
  "schema": "noe-replay-v1",
  "cert_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
  "chain": "shi @clear_path an nai nel @human mos @warehouse_exit nek",
  "c_safe_snapshot": {
    "literals": {"clear_path": true, "human": false},
    "modal": {"knowledge": {"clear_path": true, "human": false}},
    "temporal": {"now": 1741234567890, "max_skew_ms": 500},
    "spatial": {"thresholds": {"near": 2000000}}
  },
  "expected_result_domain": "action",
  "expected_result_value": {"operator": "mos", "target": "warehouse_exit"},
  "expected_h_safe": "cafebabe...",
  "expected_chain_hash": "deadbeef...",
  "expected_provenance_hash": "66666666...",
  "expected_action_hash": "77777777...",
  "expected_decision_hash": null,
  "registry_hash": "55555555...",
  "semantics_version": "NIP-005-v1.0",
  "runtime_mode": "strict"
}
```

---

## 8. Cross-Reference: Normative Sources

| Contract section | Authoritative NIP or file |
|---|---|
| Quantisation rules | NIP-009 §3 (Frozen Decimal Arithmetic) |
| Staleness / skew | NIP-009 §2 |
| Float ban in C_safe | NIP-009 §3, `noe/numeric_quantization.py` |
| H_safe definition | NIP-015 §3.1 and §4 |
| Evidence stripping | NIP-015 §4.1 |
| Epistemic tiers and thresholds | NIP-016 §2 (Config #18) |
| Error codes | `docs/error_codes.md` |
| Canonical serialization | `noe/canonical.py::canonical_bytes` |
| Provenance record structure | `noe/provenance.py::ProvenanceRecord` |
| Registry hash | `noe/provenance.py::compute_registry_hash` |
| RuntimeResult.domain values | `noe/noe_runtime.py::RuntimeResult` (line 102) |

---

## 9. What Is Not Permitted in Any Phase

- **No floats in canonical context, evidence_payload, or cert body** — enforced by `canonical_bytes`.
- **No silent normalisation at replay time** — schema drift between stored cert and replay input is an error, not a warning.
- **No float confidence values exported from grounding** — tiers and admission status only.
- **No heuristic fallbacks without explicit documentation** — every decision must be traceable to a NIP or a blocker report.
- **No modification of the normative Python core** (`noe_parser.py`, `noe_validator.py`, `noe_runtime.py`, `context_projection.py`) without producing a blocker report referencing this document.
- **No broadening of the boolean context_entry schema** without a versioned addition to this document and a new `schema:` tag.

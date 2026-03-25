# Quickstart: Cert Store, Audit, and Replay

## What this shows

Every Noe policy decision can be stored as an append-only certificate.
That certificate can be audited (tamper detection + hash-link verification)
and replayed (exact re-evaluation against the frozen context snapshot).

## Installation

```bash
pip install noe-runtime
```

## Step 1 — Append a certificate

```python
from noe.persistence.cert_store import CertStore, build_cert_body, compute_cert_id
from noe.provenance import compute_registry_hash, SEMANTICS_VERSION

store = CertStore("decisions.jsonl")

# Placeholder hashes (in production, derive from real context hashes)
import hashlib, json
h = lambda s: s * 32  # 64-char placeholder

cert = store.append(
    created_ts_ms=1_741_234_567_890,
    chain="shi @clear_path an nai nel @human mos @exit_a nek",
    chain_hash=h("1"),          # SHA-256(canonical_bytes(chain))
    h_safe=h("a"),              # SHA-256(canonical_bytes(C_safe))
    h_root=h("b"),
    h_domain=h("c"),
    h_local=h("d"),
    h_composite=h("e"),
    result_domain="action",
    result_value={"operator": "mos", "target": "exit_a"},
    epistemic_basis=["clear_path", "human"],
    registry_hash=compute_registry_hash(),   # binds operator semantics
    registry_version="1.0.0",
    semantics_version=SEMANTICS_VERSION,
    runtime_mode="strict",
    provenance_hash=None,
    action_hash=None,
    decision_hash=None,
    domain_pack_hash=None,
    evidence_hashes=[h("f"), h("g")],        # auto-sorted before cert_id
    prev_cert_id=None,                        # first cert in chain
)
print(f"cert_id: {cert['cert_id'][:16]}…")
print(f"evidence_hashes (sorted): {cert['evidence_hashes']}")
```

## Step 2 — Append a follow-up decision with chain linkage

```python
cert2 = store.append(
    # ... same fields ...
    prev_cert_id=cert["cert_id"],   # cryptographically binds to previous decision
    chain="vek @human mos @stop nek",
    result_domain="action",
    result_value={"operator": "mos", "target": "stop"},
    # ...
)
```

## Step 3 — Audit the store

```bash
noe-audit decisions.jsonl
# ✅  AUDIT PASSED: 2 record(s) verified, 0 violations.
```

Or via Python:

```python
from noe.persistence.cert_store import CertStore
from noe.persistence.audit import audit_store

store = CertStore("decisions.jsonl")
result = audit_store(store)

if result.ok:
    print(f"Audit passed: {result.total_records} records")
else:
    for v in result.violations:
        print(f"  [{v.exit_code}] {v.reason}")
```

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | All cert_id hashes and prev_cert_id links valid |
| 2 | cert_id recomputation mismatch — tamper evidence |
| 3 | prev_cert_id references a cert_id not in the store |
| 6 | Record fails schema validation |
| 7 | File not found or corrupt JSONL |

## Step 4 — Replay a decision

```python
import hashlib, json
from noe.canonical import canonical_bytes
from noe.persistence.replay import replay_cert, compute_h_safe, compute_chain_hash
from noe.provenance import compute_registry_hash, SEMANTICS_VERSION

chain = "shi @clear_path nek"
c_safe = {
    "literals": {"clear_path": True},
    "modal": {"knowledge": {"clear_path": True}, "belief": {}},
    "temporal": {"now": 0, "max_skew_ms": 500},
}

replay_blob = {
    "schema":                  "noe-replay-v1",
    "cert_id":                 cert["cert_id"],
    "chain":                   chain,
    "c_safe_snapshot":         c_safe,
    "expected_result_domain":  "truth",
    "expected_result_value":   True,
    "expected_h_safe":         compute_h_safe(c_safe),
    "expected_chain_hash":     compute_chain_hash(chain),
    "expected_provenance_hash": None,
    "expected_action_hash":    None,
    "expected_decision_hash":  None,
    "registry_hash":           compute_registry_hash(),
    "semantics_version":       SEMANTICS_VERSION,
    "runtime_mode":            "lenient",
}

result = replay_cert(replay_blob)
if result.ok:
    print("Replay PASSED")
else:
    print(f"Replay FAILED [{result.exit_code}]: {result.reason}")
```

Or via CLI:

```bash
# Write the blob to a file
echo '{ "schema": "noe-replay-v1", ... }' > replay.json
noe-replay replay.json
# ✅  REPLAY PASSED: cert_id=a1b2c3d4…
```

## Replay exit codes

| Code | Meaning |
|---|---|
| 0 | Replay matched stored result |
| 4 | Re-evaluation differed (domain, value, or provenance hash) |
| 5 | registry_hash or semantics_version mismatch |
| 6 | Replay input fails schema validation |
| 7 | File not found or invalid JSON |

## Key contracts

- `cert_id = SHA-256(canonical_bytes(cert_body_without_cert_id))`
- `evidence_hashes` are **sorted lexicographically** before `cert_id` is computed
- `prev_cert_id` is committed into `cert_id` (hash linkage)
- Replay uses a **frozen snapshot** — staleness is not re-checked
- No floats are permitted in any canonical structure

## Full contract reference

See [docs/implementation_contracts.md](implementation_contracts.md) for the
normative specification of all fields, exit codes, and ordering rules.

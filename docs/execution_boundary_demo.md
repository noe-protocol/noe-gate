# Measured Execution Boundary Demo
Noe sits between a proposer and execution. It forwards only permitted commands, and binds each forwarded command to a replayable context commitment.

Run it yourself:

```bash
make integration-demo
```

<br />

## What this demo proves

The demo (`examples/integration_demo/run_integration_demo.py`) exercises four scenarios
against the guard chain:

```
shi @path_clear an shi @controller_ready khi sek mek @move_forward sek nek
```

| Scenario | Admission outcome | Kernel verdict | Controller |
|---------|------------------|---------------|-----------|
| **PERMIT** | `@path_clear` and `@controller_ready` grounded | `domain=list` | GOAL FORWARDED |
| **VETO** | `@path_clear` absent (wall at 100mm) | `domain=undefined` | GOAL SUPPRESSED |
| **STALE** | Timestamp 10 min expired | admission refused | GOAL SUPPRESSED + ALERT |
| **ERROR** | `root=None`, malformed context | admission refused | GOAL SUPPRESSED + ALERT |

Key claim: **a forwarded command is bound to a context hash and replay-verifiable.**
Non-forwarded scenarios leave a suppression certificate; they leave no forwarded envelope.

<br />

## Node model

```
planner_node   →   noe_gate   →   controller_sink
     |                |                  |
proposes chain   admit C_safe        receives ForwardedCommand
(untrusted)      evaluate chain      only when verdict = PERMIT
                 emit verdict
```

The proposer is not trusted. The controller only receives commands that pass through
`noe_gate`. The gate never exposes proposer output to the controller directly.

<br />

## Forwarded command envelope

When verdict is PERMIT, `controller_sink` receives a typed `ForwardedCommand`:

```json
{
  "goal_id":          "goal-F33DD5",
  "action":           "@move_forward",
  "chain":            "shi @path_clear an shi @controller_ready khi sek mek @move_forward sek nek",
  "context_hash":     "f7cadcdd58e94af477c1c133c4b1c6d65dd5936d7db6eafc0924ca5c4e44f532",
  "certificate_path": ".../artifacts/cert_permit.json",
  "timestamp_ms":     1773632622022
}
```

This file is written to disk as `artifacts/forwarded_goal_<goal_id>.json`.
For VETO, STALE, and ERROR outcomes, no `ForwardedCommand` is constructed.
The audit trail for non-execution is the presence of a suppression certificate
and the **absence** of a forwarded envelope.

<br />

## Replay verification

Immediately after the PERMIT forward, the gate replays the same chain against the
same admitted context and checks that:

- the context hash is identical
- the domain outcome is identical

Output:

```
[noe_gate]  REPLAY  →  VERIFIED — same context hash + same domain on re-evaluation
```

This proves that verdict is fully determined by `(chain, C_safe)` — not by any
mutable state, timestamp drift, or non-deterministic path in the kernel.

<br />

## Timing methodology

| Phase | Description |
|-------|------------|
| **Cold** | First call — includes grammar initialisation (not representative of steady-state) |
| **Warm median** | Median over 100 runs, no file I/O, no cert generation |
| **Warm P95** | 95th percentile over same 100 runs |

Results on Python 3.11.8 / Darwin / reference runtime:

| Scenario | Cold | Warm P95 |
|---------|:----:|:-------:|
| PERMIT (two facts) | 1.27 ms | 0.099 ms |
| VETO (short-circuit) | 0.08 ms | 0.070 ms |
| STALE / ERROR | caught at admission | — |

Admission timing (~0.001 ms) reflects the minimal structural guard in this harness,
not a production `π_safe` cost.

<br />

## Limitations

- **Admission** here is a minimal structural proxy — not a production `π_safe`.
  A real admission path would canonicalise, filter, and verify provenance of each
  admitted fact before passing `C_safe` to the kernel.
- **`controller_sink`** is a stub representing a ROS2 action client or downstream
  command interface. No DDS transport is involved.
- **Timings** are reference-runtime (CPython, single-thread).
  Cold = first call including grammar init. Warm = parse cache warm, no I/O.
  These are not hard-real-time certification figures.

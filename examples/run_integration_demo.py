#!/usr/bin/env python3
"""
examples/integration_demo/run_integration_demo.py

NOE INTEGRATION DEMO — EXECUTION BOUNDARY
------------------------------------------
Node model:

    planner_node  →  noe_gate  →  controller_sink
         |               |                |
    proposes chain  evaluates C_safe   receives ForwardedCommand
    (untrusted)     (trusted boundary)  only when verdict = PERMIT

Scenarios
---------
    1. PERMIT — @path_clear AND @controller_ready grounded → GOAL FORWARDED
                + replay check proves same verdict from certificate
    2. VETO   — @path_clear absent (wall at 100mm)         → GOAL SUPPRESSED
    3. STALE  — structurally valid but timestamp expired   → GOAL SUPPRESSED + ALERT
    4. ERROR  — root=None, admission refused               → GOAL SUPPRESSED + ALERT

Timing: cold first-run + N=100 warm-run stats (median, P95, max).

Limitations
-----------
  · Admission here is a minimal structural proxy, not a production π_safe.
  · controller_sink is a stub representing a ROS2 action client / downstream
    command interface.
  · Timings are reference-runtime (CPython, single-thread) — not hard-RT certified.
"""

import sys
import os
import json
import time
import uuid
import hashlib
import platform
import statistics
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from noe.noe_parser import run_noe_logic
from noe.canonical import canonical_json

# ── Config ────────────────────────────────────────────────────────────────────

OUT_DIR = Path(__file__).parent / "artifacts"
OUT_DIR.mkdir(exist_ok=True)
BENCH_N = 100

# ── Guard chain (two-fact precondition) ───────────────────────────────────────

GUARD_CHAIN = (
    "shi @path_clear an shi @controller_ready "
    "khi sek mek @move_forward sek nek"
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000

def _ctx_hash(ctx: dict) -> str:
    return hashlib.sha256(canonical_json(ctx).encode()).hexdigest()

# ── Typed command envelope ────────────────────────────────────────────────────

@dataclass
class ForwardedCommand:
    """
    Typed payload sent from noe_gate to controller_sink.
    Represents a concrete, hash-bound action forwarded for execution.
    Never constructed for VETO or ERROR verdicts.
    """
    goal_id:          str
    action:           str
    chain:            str
    context_hash:     str
    certificate_path: str
    timestamp_ms:     int

    def as_dict(self) -> dict:
        return {
            "goal_id":          self.goal_id,
            "action":           self.action,
            "chain":            self.chain,
            "context_hash":     self.context_hash,
            "certificate_path": self.certificate_path,
            "timestamp_ms":     self.timestamp_ms,
        }

# ── Context builders ──────────────────────────────────────────────────────────

def _base_context() -> dict:
    return {
        "modal":    {"knowledge": {}, "belief": {}, "certainty": {}},
        "spatial":  {"unit": "mm", "thresholds": {"near": 300.0, "far": 2000.0}},
        "temporal": {"now": int(time.time() * 1000), "max_skew_ms": 500.0},
        "literals": {"@move_forward": "fwd_target"},
        "axioms":   {"value_system": {"accepted": [], "rejected": []}},
        "rel":      {},
        "demonstratives": {}
    }

def build_c_safe_permit() -> dict:
    ctx = _base_context()
    ctx["modal"]["knowledge"]["@path_clear"]       = True
    ctx["modal"]["knowledge"]["@controller_ready"] = True
    ctx["literals"]["@path_clear"]       = True
    ctx["literals"]["@controller_ready"] = True
    return ctx

def build_c_safe_veto() -> dict:
    ctx = _base_context()
    ctx["modal"]["knowledge"]["@controller_ready"] = True
    ctx["literals"]["@path_clear"]       = False   # present as literal; NOT in knowledge
    ctx["literals"]["@controller_ready"] = True
    return ctx

def build_c_safe_stale() -> dict:
    """Structurally valid, but timestamp is 10 minutes in the past."""
    ctx = _base_context()
    ctx["modal"]["knowledge"]["@path_clear"]       = True
    ctx["modal"]["knowledge"]["@controller_ready"] = True
    ctx["literals"]["@path_clear"]       = True
    ctx["literals"]["@controller_ready"] = True
    ctx["temporal"]["now"] = int((time.time() - 600) * 1000)  # 10 min stale
    return ctx

# ── Admission exception ───────────────────────────────────────────────────────

class AdmissionError(Exception):
    pass

# ── Nodes ─────────────────────────────────────────────────────────────────────

class PlannerNode:
    name = "planner_node"
    def propose(self, chain: str) -> dict:
        return {"proposed_chain": chain, "source": self.name}


class NoeGate:
    name = "noe_gate"

    def admit(self, raw_root) -> tuple:
        t0 = time.perf_counter()
        if raw_root is None:
            raise AdmissionError("ERR_BAD_CONTEXT: root layer is None — admission refused")
        # Staleness check: compare context timestamp to now
        now_ms = int(time.time() * 1000)
        ctx_now = raw_root.get("temporal", {}).get("now", now_ms)
        max_skew = raw_root.get("temporal", {}).get("max_skew_ms", 500.0)
        age_ms = now_ms - ctx_now
        if age_ms > max_skew:
            raise AdmissionError(
                f"ERR_CONTEXT_STALE: context age {age_ms:.0f}ms exceeds max_skew_ms {max_skew:.0f}ms"
            )
        return raw_root, _ms(t0)

    def evaluate(self, c_safe: dict, chain: str) -> tuple:
        t0 = time.perf_counter()
        result = run_noe_logic(chain, c_safe, mode="partial")
        return result, _ms(t0)

    def decide(self, result: dict) -> tuple:
        domain = result.get("domain", "error")
        if domain in ("action", "list"):
            return "PERMIT", ""
        if domain == "undefined":
            return "VETO", "@path_clear not grounded in admitted knowledge — chain resolves to undefined"
        return "ERROR", result.get("code") or str(result.get("value")) or "unknown error"


class ControllerSink:
    name = "controller_sink"

    def __init__(self):
        self.received: list[ForwardedCommand] = []

    def accept(self, cmd: ForwardedCommand):
        self.received.append(cmd)
        print(f"  [{self.name}]  GOAL ACCEPTED")
        d = cmd.as_dict()
        for k, v in d.items():
            val = str(v)[:64] + "..." if len(str(v)) > 64 else v
            print(f"                   {k:<20}: {val}")
        # Write machine-readable forwarded command envelope to disk
        fwd_path = OUT_DIR / f"forwarded_goal_{cmd.goal_id.lower()}.json"
        fwd_path.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        print(f"                   {'envelope written':<20}: {fwd_path.name}")

    def suppress(self, verdict: str, reason: str):
        tag = "ALERT — supervisory review required" if verdict == "ERROR" else "no actuation command issued"
        print(f"  [{self.name}]  GOAL SUPPRESSED → {tag}")
        if reason:
            print(f"                   reason               : {reason}")

# ── Certificate ───────────────────────────────────────────────────────────────

def write_certificate(name: str, scenario: str, chain: str,
                      c_safe: dict, verdict: str, domain: str,
                      result_value, goal_id: str,
                      admission_ms: float, eval_ms: float) -> tuple:
    t0 = time.perf_counter()
    ctx_hash = _ctx_hash(c_safe)
    cert = {
        "noe_version": "v1.0-rc1",
        "scenario": scenario,
        "goal_id": goal_id,
        "chain": chain,
        "context_hash": ctx_hash,
        "verdict": verdict,
        "domain": domain,
        "result_value": (
            result_value
            if isinstance(result_value, (dict, list, str, bool, type(None)))
            else str(result_value)
        ),
        "forwarded_to_controller": verdict == "PERMIT",
        "timings_ms": {
            "admission_single": round(admission_ms, 4),
            "evaluation_single": round(eval_ms, 4),
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.system(),
        }
    }
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(cert, indent=2, ensure_ascii=False))
    return path, _ms(t0), ctx_hash

# ── Replay verification ───────────────────────────────────────────────────────

def replay_check(c_safe: dict, chain: str, original_hash: str, original_domain: str) -> bool:
    """
    Re-evaluate the same chain against the same admitted context.
    Verify same domain and same context hash — proves deterministic replayability.
    """
    replay_result = run_noe_logic(chain, c_safe, mode="partial")
    replay_hash   = _ctx_hash(c_safe)
    domain_match  = replay_result.get("domain") == original_domain
    hash_match    = replay_hash == original_hash
    return domain_match and hash_match

# ── Statistics ────────────────────────────────────────────────────────────────

def bench(c_safe: dict, chain: str, n: int) -> dict:
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        run_noe_logic(chain, c_safe, mode="partial")
        samples.append(_ms(t0))
    samples.sort()
    return {
        "n":      n,
        "median": round(statistics.median(samples), 3),
        "p95":    round(samples[int(0.95 * n)], 3),
        "max":    round(samples[-1], 3),
    }

# ── Per-scenario runner ───────────────────────────────────────────────────────

def run_scenario(label: str, cert_name: str, raw_root,
                 chain: str, planner: PlannerNode,
                 gate: NoeGate, sink: ControllerSink) -> tuple:
    sep = "─" * 68
    print(f"\n{sep}")
    print(f"  {label}")
    print(sep)

    goal_id     = f"goal-{uuid.uuid4().hex[:6].upper()}"
    proposal    = planner.propose(chain)
    verdict     = "ERROR"
    domain      = "error"
    result_value = None
    reason      = ""
    admission_ms = 0.0
    eval_ms      = 0.0
    c_safe       = {}
    ctx_hash     = "n/a"
    stats        = None

    print(f"  [{planner.name}]  PROPOSE  →  {proposal['proposed_chain']}")
    print(f"  [{planner.name}]  goal_id  →  {goal_id}")

    try:
        c_safe, admission_ms = gate.admit(raw_root)
        # Cold timing (first call, includes parse/grammar init if not yet cached)
        cold_t0 = time.perf_counter()
        result = run_noe_logic(chain, c_safe, mode="partial")
        eval_ms = _ms(cold_t0)

        verdict, reason = gate.decide(result)
        domain      = result.get("domain", "error")
        result_value = result.get("value")

    except AdmissionError as e:
        verdict = "ERROR"
        reason  = str(e)

    print(f"  [{gate.name}]     ADMIT    →  {admission_ms:.3f} ms")
    print(f"  [{gate.name}]     EVAL     →  {eval_ms:.3f} ms  (cold)  domain={domain}")

    green, red, rst = "\033[92m", "\033[91m", "\033[0m"
    col = green if verdict == "PERMIT" else red
    print(f"  [{gate.name}]     VERDICT  →  {col}{verdict}{rst}")

    # Cert
    cert_ctx = c_safe if c_safe else {"error": reason}
    path, cert_ms, ctx_hash = write_certificate(
        cert_name, label, chain, cert_ctx,
        verdict, domain, result_value, goal_id, admission_ms, eval_ms
    )
    print(f"  [{gate.name}]     CERT     →  {path.name}  ({cert_ms:.3f} ms)")

    # Forward or suppress
    if verdict == "PERMIT":
        cmd = ForwardedCommand(
            goal_id=goal_id,
            action="@move_forward",
            chain=chain,
            context_hash=ctx_hash,
            certificate_path=str(path),
            timestamp_ms=int(time.time() * 1000),
        )
        sink.accept(cmd)

        # Replay check
        replayed_ok = replay_check(c_safe, chain, ctx_hash, domain)
        if replayed_ok:
            print(f"  [{gate.name}]     REPLAY   →  \033[92mVERIFIED\033[0m — same context hash + same domain on re-evaluation")
        else:
            print(f"  [{gate.name}]     REPLAY   →  \033[91mFAILED\033[0m — non-determinism detected!")

        # Statistical timing
        stats = bench(c_safe, chain, BENCH_N)
    else:
        sink.suppress(verdict, reason)
        if verdict == "VETO":
            stats = bench(c_safe, chain, BENCH_N)

    # Timing output
    if stats:
        total_cold = admission_ms + eval_ms
        print(f"\n  Timing ({stats['n']} warm runs — no I/O, no cert):")
        print(f"    cold first-run  : {total_cold:.3f} ms  (admit + eval)")
        print(f"    warm median     : {stats['median']} ms")
        print(f"    warm P95        : {stats['p95']} ms")
        print(f"    warm max        : {stats['max']} ms")

    return admission_ms, eval_ms, cert_ms, stats

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    planner = PlannerNode()
    gate    = NoeGate()
    sink    = ControllerSink()

    hw = f"Python {platform.python_version()} / {platform.system()} / reference runtime"

    print()
    print("NOE INTEGRATION DEMO")
    print("Node model:  planner_node  →  noe_gate  →  controller_sink")
    print()
    print(f"  chain     : {GUARD_CHAIN}")
    print(f"  bench N   : {BENCH_N} warm runs")
    print(f"  hardware  : {hw}")
    print(f"  artifacts : {OUT_DIR.relative_to(Path.cwd())}")

    scenarios = [
        (
            "SCENARIO 1: PERMIT — @path_clear AND @controller_ready grounded",
            "cert_permit",
            build_c_safe_permit(),
        ),
        (
            "SCENARIO 2: VETO   — @path_clear absent (wall at 100mm)",
            "cert_veto",
            build_c_safe_veto(),
        ),
        (
            "SCENARIO 3: STALE  — valid context, timestamp 10 min expired",
            "cert_stale",
            build_c_safe_stale(),
        ),
        (
            "SCENARIO 4: ERROR  — malformed context (root=None)",
            "cert_error",
            None,
        ),
    ]

    rows = []
    for label, cert_name, raw_root in scenarios:
        adm, ev, cert, stats = run_scenario(
            label, cert_name, raw_root, GUARD_CHAIN, planner, gate, sink
        )
        rows.append((label, adm, ev, stats))

    # Summary table
    print("\n" + "═" * 68)
    print("  SUMMARY")
    print("═" * 68)
    print(f"  {'Scenario':<40}  {'Cold':>7}  {'P95':>7}")
    print("  " + "─" * 60)
    for label, adm, ev, stats in rows:
        cold = f"{adm + ev:.2f}ms"
        p95  = f"{stats['p95']}ms" if stats else "n/a"
        print(f"  {label[:40]:<40}  {cold:>7}  {p95:>7}")
    print()
    print(f"  hardware: {hw}")
    print()
    print("  Limitations:")
    print("    · Admission is a minimal structural proxy — not a production π_safe.")
    print("    · controller_sink is a stub for a ROS2 action client / command interface.")
    print("    · Timings are reference-runtime (CPython, single-thread).")
    print("      Cold = first call incl. grammar init. Warm = parse cache warm.")
    print("═" * 68)

    print(f"\n  controller_sink forwarded {len(sink.received)} command(s):")
    for cmd in sink.received:
        print(f"    goal_id  : {cmd.goal_id}")
        print(f"    action   : {cmd.action}")
        print(f"    ctx_hash : {cmd.context_hash[:32]}...")
        print(f"    cert     : {Path(cmd.certificate_path).name}")

if __name__ == "__main__":
    main()

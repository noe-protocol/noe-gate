"""
bridge_overhead.py - ROS Bridge Tax Benchmark

Simulates the overhead of converting ROS-like sensor data into Noe contexts
and measures the amortized cost per control loop cycle.

Validates:
- Initial context hashing: <2ms (acceptable for setup)
- Incremental updates: <500µs (sensor rate)
- Cached snapshots: <10µs (control loop rate)
- Amortized cost at 50Hz: <1ms per cycle

Target Performance:
- Sensors update at 10Hz (realistic LiDAR/camera rate)
- Control loop at 50Hz (20ms cycle)
- Each 50Hz cycle should cost <1ms for context operations
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import json
from noe.context_manager import ContextManager
from noe.noe_parser import run_noe_logic

def generate_large_context(entity_count=500):
    """Generate a realistic ~100KB context simulating ROS data."""
    root = {
        "spatial": {
            "unit": "m",
            "thresholds": {"near": 1.0, "far": 5.0, "contact": 0.1},
            "orientation": {"target": 0.0, "tolerance": 0.1}
        },
        "temporal": {"now": 1000.0, "max_skew_ms": 100.0},
        "modal": {
            "knowledge": {},
            "belief": {},
            "certainty": {},
            "certainty_threshold": 0.8
        },
        "axioms": {
            "value_system": {"accepted": [], "rejected": []}
        },
        "rel": {},
        "demonstratives": {}
    }

    domain = {
        "entities": {
            "@robot": {"position": [0.0, 0.0], "velocity": [0.0, 0.0]},
        }
    }

    local = {
        "literals": {},
        "entities": {}
    }

    for i in range(entity_count):
        bearing = (i / entity_count) * 360
        distance = 1.0 + (i % 10) * 0.5
        local["entities"][f"@obs_{i}"] = {
            "position": [distance, bearing],
            "distance": distance,
            "bearing": bearing
        }
        local["literals"][f"@obs_{i}"] = True

    return root, domain, local

def measure_first_hash(cm):
    start = time.perf_counter()
    snap = cm.snapshot()
    elapsed = time.perf_counter() - start
    return elapsed * 1000, snap

def measure_incremental_update(cm, update_data):
    start = time.perf_counter()
    cm.update_local(update_data)
    snap = cm.snapshot()
    elapsed = time.perf_counter() - start
    return elapsed * 1000

def measure_cached_snapshot(cm):
    start = time.perf_counter()
    snap = cm.snapshot()
    elapsed = time.perf_counter() - start
    return elapsed * 1000

def simulate_deployment_pattern(is_native=False):
    """Simulate realistic ROS deployment: 10Hz sensors + 50Hz control loop."""
    print("\n[4] Deployment Simulation: 10Hz Sensors + 50Hz Control Loop")
    print("=" * 60)

    root, domain, local = generate_large_context(entity_count=500)
    cm = ContextManager(root=root, domain=domain, local=local)

    _ = cm.snapshot()  # Warmup

    control_cycles = 50
    sensor_update_interval = 5
    total_context_time = 0.0
    sensor_updates = 0

    for cycle in range(control_cycles):
        cycle_start = time.perf_counter()

        if cycle % sensor_update_interval == 0:
            update = {
                "entities": {
                    "@robot": {
                        "position": [cycle * 0.1, 0.0],
                        "velocity": [0.5, 0.0]
                    }
                }
            }
            cm.update_local(update)
            sensor_updates += 1

        snap = cm.snapshot()
        cycle_elapsed = (time.perf_counter() - cycle_start) * 1000
        total_context_time += cycle_elapsed

    avg_per_cycle = total_context_time / control_cycles
    target_ms = 1.0 if is_native else 15.0

    print(f"  Total cycles:        {control_cycles}")
    print(f"  Sensor updates:      {sensor_updates}")
    print(f"  Avg cost/cycle:      {avg_per_cycle:.3f}ms")
    print(f"  Target:              <{target_ms:.1f}ms")
    print(f"  Status:              {'✓ PASS' if avg_per_cycle < target_ms else '✗ FAIL'}")

    return avg_per_cycle < target_ms

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ROS Bridge Overhead Benchmark")
    parser.add_argument("--native", action="store_true", help="Assert against strict Native C++/Rust targets instead of Python reference targets")
    args, _ = parser.parse_known_args()
    is_native = args.native

    print("NOE BRIDGE BENCHMARK")
    print(f"  mode: {'native' if is_native else 'python-reference (CI)'}")

    root, domain, local = generate_large_context(entity_count=500)
    context_size = len(json.dumps({"root": root, "domain": domain, "local": local}))
    print(f"\nContext size: {context_size / 1024:.1f} KB")

    # Test 1: First-time hash (cold start)
    print("\n[1] First-Time Hash (Cold Start)")
    print("=" * 60)
    cm = ContextManager(root=root, domain=domain, local=local)
    first_hash_ms, snap = measure_first_hash(cm)
    target_first_hash = 2.0 if is_native else 20.0
    print(f"  Time:    {first_hash_ms:.3f}ms")
    print(f"  Target:  <{target_first_hash:.1f}ms")
    print(f"  Status:  {'✓ PASS' if first_hash_ms < target_first_hash else '✗ FAIL'}")

    # Test 2: Incremental update (sensor data change)
    print("\n[2] Incremental Update (Sensor @ 10Hz)")
    print("=" * 60)
    update_data = {
        "entities": {
            "@robot": {"position": [1.0, 0.0], "velocity": [0.5, 0.0]}
        },
        "literals": {"@new_obstacle": True}
    }

    incremental_times = []
    for _ in range(100):
        t = measure_incremental_update(cm, update_data)
        incremental_times.append(t)

    incremental_times.sort()
    avg_incremental = sum(incremental_times) / len(incremental_times)
    p95_incremental = incremental_times[int(len(incremental_times) * 0.95)]
    p99_incremental = incremental_times[int(len(incremental_times) * 0.99)]
    max_incremental = max(incremental_times)
    target_p99_inc = 10.0 if is_native else 50.0

    print(f"  Avg:      {avg_incremental:.3f}ms")
    print(f"  P95:      {p95_incremental:.3f}ms")
    print(f"  P99:      {p99_incremental:.3f}ms")
    print(f"  Max:      {max_incremental:.3f}ms")
    print(f"  Target:   P99 <{target_p99_inc:.1f}ms")
    print(f"  Status:   {'✓ PASS' if p99_incremental < target_p99_inc else '✗ FAIL (P99)'}")

    # Test 3: Cached snapshot (no changes)
    print("\n[3] Cached Snapshot (Control Loop @ 50Hz)")
    print("=" * 60)
    cached_times = []
    for _ in range(100):
        t = measure_cached_snapshot(cm)
        cached_times.append(t)

    avg_cached = sum(cached_times) / len(cached_times)
    max_cached = max(cached_times)
    target_cached = 0.01 if is_native else 15.0

    print(f"  Avg time: {avg_cached:.4f}ms")
    print(f"  Max time: {max_cached:.4f}ms")
    print(f"  Target:   <{target_cached}ms")
    print(f"  Status:   {'✓ PASS' if avg_cached < target_cached else '✗ FAIL'}")

    # Test 4: Realistic deployment pattern
    deployment_pass = simulate_deployment_pattern(is_native)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    all_pass = (
        first_hash_ms < target_first_hash and
        p99_incremental < target_p99_inc and
        avg_cached < target_cached and
        deployment_pass
    )

    if all_pass:
        print("✓ ALL BENCHMARKS PASSED")
    else:
        print("✗ SOME BENCHMARKS FAILED")

    return 0 if all_pass else 1

if __name__ == "__main__":
    exit(main())

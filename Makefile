# Noe Reference Implementation — Makefile
# Usage: make test | make conformance | make demo | make bench | make all | make integration-demo

.PHONY: test conformance demo demo-full guard bench integration-demo all clean \
        rust-parity-fixtures rust-conformance rust-diff rust-bench \
        rust-build-ffi run-c-smoketest run-cpp-smoketest \
        build-zone-entry-example run-zone-entry

# ─── Core Test Suites ────────────────────────────────────────────────

test:                          ## Run all unit tests
	python3 -m unittest discover tests

conformance:                   ## Run NIP-011 conformance suite (80 vectors)
	python3 tests/nip011/run_conformance.py

# ─── Demos ───────────────────────────────────────────────────────────

demo:                          ## Run flagship shipment demo
	python3 examples/auditor_demo/verify_shipment.py

demo-full:                     ## Run full auditor demo set
	bash examples/auditor_demo/run_demo_full.sh

guard:                         ## Run robot guard golden-vector demo (7 ticks)
	python3 examples/robot_guard_demo.py

integration-demo:              ## Run execution-boundary integration demo (permit/veto/error)
	python3 examples/integration_demo/run_integration_demo.py

# ─── Benchmarks ──────────────────────────────────────────────────────

bench:                         ## Run ROS bridge overhead benchmark
	python3 benchmarks/bridge_overhead.py

audit-demo:                    ## Run Phase 2 cert-store + replay demo
	.venv311/bin/python scripts/audit_demo.py

# ─── Aggregate ───────────────────────────────────────────────────────

all: ## Run everything
	@echo ""
	@echo "── UNIT TESTS ────────────────────────────────────────────"
	python3 -m unittest discover tests
	@echo ""
	@echo "── CONFORMANCE ───────────────────────────────────────────"
	python3 tests/nip011/run_conformance.py
	@echo ""
	@echo "── SAFETY DEMO ───────────────────────────────────────────"
	python3 examples/robot_guard_demo.py
	@echo ""
	@echo "── AUDIT DEMO ────────────────────────────────────────────"
	python3 examples/auditor_demo/verify_shipment.py
	@echo ""
	@echo "── PERFORMANCE ───────────────────────────────────────────"
	python3 benchmarks/bridge_overhead.py
	@echo ""
	@echo "════════════════════════════════════════════════════════════"
	@echo "  ✅  ALL SUITES PASSED"
	@echo "  same rule + same grounded context → same verdict"
	@echo "  stale / conflicting / missing context → non-execution"
	@echo "════════════════════════════════════════════════════════════"

# ─── Rust Runtime (Phase 5) ─────────────────────────────────────────

rust-parity-fixtures:          ## Export Python ground truth for Rust conformance
	.venv311/bin/python scripts/export_vectors.py

rust-conformance:              ## Run Rust NIP-011 conformance (exact JSON match)
	cd rust/noe_core && $${HOME}/.cargo/bin/cargo test --test conformance 2>&1

rust-hash-parity:              ## Run Rust canonical hash parity tests
	cd rust/noe_core && $${HOME}/.cargo/bin/cargo test --test hash_parity 2>&1

rust-parser-golden:            ## Run Rust parser precedence/associativity tests
	cd rust/noe_core && $${HOME}/.cargo/bin/cargo test --test parser_golden 2>&1

rust-test:                     ## Run all Rust tests (hash + parser + conformance)
	cd rust/noe_core && $${HOME}/.cargo/bin/cargo test 2>&1

rust-diff:                     ## Compare Rust vs Python outputs (requires ground_truth_rust.json)
	.venv311/bin/python scripts/diff_harness.py rust/noe_core/tests/vectors/ground_truth.json

rust-bench:                    ## Rust benchmarks (only after 80/80 exact pass)
	@echo "ERROR: run 'make rust-conformance' first and verify 80/80" && false

# ─── Rust FFI (Phase 6) ──────────────────────────────────────────────

rust-build-ffi:                ## Build Rust shared+static library for C/C++ consumers
	cd rust/noe_core && $${HOME}/.cargo/bin/cargo build

run-c-smoketest: rust-build-ffi  ## Build and run C FFI smoke test
	cc rust/noe_core/tests/c/smoke_test.c \
	    -Irust/noe_core/include \
	    -Lrust/noe_core/target/debug -lnoe_core \
	    -Wl,-rpath,rust/noe_core/target/debug \
	    -o /tmp/noe_c_smoke
	/tmp/noe_c_smoke

run-cpp-smoketest: rust-build-ffi  ## Build and run C++ wrapper smoke test
	c++ -std=c++20 rust/noe_core/cpp/smoke_test.cpp \
	    -Irust/noe_core/include \
	    -Lrust/noe_core/target/debug -lnoe_core \
	    -Wl,-rpath,rust/noe_core/target/debug \
	    -o /tmp/noe_cpp_smoke
	/tmp/noe_cpp_smoke

build-zone-entry-example: rust-build-ffi  ## Compile the zone-entry C example
	cc examples/ros2_zone_entry/run_example.c \
	    -Irust/noe_core/include \
	    -Lrust/noe_core/target/debug -lnoe_core \
	    -Wl,-rpath,rust/noe_core/target/debug \
	    -o examples/ros2_zone_entry/run_example

run-zone-entry: build-zone-entry-example  ## Run zone-entry demo (human present + absent)
	@echo "=== Zone entry: human present ==="
	examples/ros2_zone_entry/run_example examples/ros2_zone_entry/context_human_present.json
	@echo ""
	@echo "=== Zone entry: human absent ==="
	examples/ros2_zone_entry/run_example examples/ros2_zone_entry/context_human_absent.json

# ─── Housekeeping ────────────────────────────────────────────────────

clean:                         ## Remove generated artifacts and caches
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	rm -f examples/auditor_demo/cert_*.json
	rm -f examples/auditor_demo/hallucination_certificate_*.json
	rm -f examples/auditor_demo/shipment_certificate_*.json
	rm -f examples/auditor_demo/*_clear_certificate.json
	rm -rf guard_logs/

help:                          ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

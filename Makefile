# Noe Gate — Makefile
# Usage: make test | make conformance | make demo | make playground | make bench | make all

PYTHON ?= python3

.PHONY: test conformance gloss playground demo demo-full guard bench integration-demo \
        audit-demo all clean clean-all help \
        rust-parity-fixtures rust-conformance rust-diff rust-bench \
        rust-build-ffi rust-hash-parity rust-parser-golden rust-test \
        run-c-smoketest run-cpp-smoketest \
        build-zone-entry-example run-zone-entry

# ─── Core Test Suites ────────────────────────────────────────────────

test:                          ## Run all unit tests
	$(PYTHON) -m unittest discover tests

conformance:                   ## Run locked NIP-011 conformance suite
	$(PYTHON) tests/nip011/run_conformance.py

# Optional: override with CHAIN="your chain here"
CHAIN ?= shi @human_present nek
gloss:                         ## Display canonical + glossed form of a Noe chain
	@$(PYTHON) noe/gloss.py --side-by-side $(CHAIN)

# ─── Demos ───────────────────────────────────────────────────────────

demo:                          ## Run flagship shipment demo
	$(PYTHON) examples/auditor_demo/verify_shipment.py

demo-full:                     ## Run full auditor demo set
	bash examples/auditor_demo/run_demo_full.sh

guard:                         ## Run robot guard golden-vector demo (7 ticks)
	$(PYTHON) examples/robot_guard_demo.py

integration-demo:              ## Run execution-boundary integration demo (permit/veto/error)
	$(PYTHON) examples/integration_demo/run_integration_demo.py

playground:                    ## Launch interactive Noe chain evaluator (REPL)
	$(PYTHON) noe_playground.py

audit-demo:                    ## Run cert-store + replay demo
	$(PYTHON) scripts/audit_demo.py

# ─── Benchmarks ──────────────────────────────────────────────────────

bench:                         ## Run ROS bridge overhead benchmark
	$(PYTHON) benchmarks/bridge_overhead.py

# ─── Aggregate ───────────────────────────────────────────────────────

all:                           ## Run everything
	@echo ""
	@echo "── UNIT TESTS ────────────────────────────────────────────"
	$(PYTHON) -m unittest discover tests
	@echo ""
	@echo "── CONFORMANCE ───────────────────────────────────────────"
	$(PYTHON) tests/nip011/run_conformance.py
	@echo ""
	@echo "── SAFETY DEMO ───────────────────────────────────────────"
	$(PYTHON) examples/robot_guard_demo.py
	@echo ""
	@echo "── AUDIT DEMO ────────────────────────────────────────────"
	$(PYTHON) examples/auditor_demo/verify_shipment.py
	@echo ""
	@echo "── PERFORMANCE ───────────────────────────────────────────"
	$(PYTHON) benchmarks/bridge_overhead.py
	@echo ""
	@echo "════════════════════════════════════════════════════════════"
	@echo "  ✅  ALL SUITES PASSED"
	@echo "  same rule + same grounded context → same verdict"
	@echo "  stale / conflicting / missing context → non-execution"
	@echo "════════════════════════════════════════════════════════════"

# ─── Rust Runtime ────────────────────────────────────────────────────

rust-parity-fixtures:          ## Export Python ground truth for Rust conformance
	$(PYTHON) scripts/export_vectors.py

rust-conformance:              ## Run Rust NIP-011 conformance (exact JSON match)
	cd rust/noe_core && $${HOME}/.cargo/bin/cargo test --test conformance 2>&1

rust-hash-parity:              ## Run Rust canonical hash parity tests
	cd rust/noe_core && $${HOME}/.cargo/bin/cargo test --test hash_parity 2>&1

rust-parser-golden:            ## Run Rust parser precedence/associativity tests
	cd rust/noe_core && $${HOME}/.cargo/bin/cargo test --test parser_golden 2>&1

rust-test:                     ## Run all Rust tests (hash + parser + conformance)
	cd rust/noe_core && $${HOME}/.cargo/bin/cargo test 2>&1

rust-diff:                     ## Compare Rust vs Python outputs (requires ground_truth.json)
	$(PYTHON) scripts/diff_harness.py rust/noe_core/tests/vectors/ground_truth.json

rust-bench:                    ## Rust benchmarks (run rust-conformance first)
	@echo "ERROR: run 'make rust-conformance' first and verify all pass" && false

# ─── Rust FFI ────────────────────────────────────────────────────────

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

run-zone-entry: build-zone-entry-example  ## Run zone-entry demo (zone blocked + zone clear)
	@echo "=== Zone entry: zone blocked (human present) ==="
	examples/ros2_zone_entry/run_example examples/ros2_zone_entry/context_zone_blocked.json
	@echo ""
	@echo "=== Zone entry: zone clear (no human) ==="
	examples/ros2_zone_entry/run_example examples/ros2_zone_entry/context_zone_clear.json

# ─── Housekeeping ────────────────────────────────────────────────────

clean:                         ## Remove generated artifacts and caches
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	rm -f examples/auditor_demo/cert_*.json
	rm -f examples/auditor_demo/hallucination_certificate_*.json
	rm -f examples/auditor_demo/shipment_certificate_*.json
	rm -f examples/auditor_demo/*_clear_certificate.json
	rm -rf examples/integration_demo/artifacts/
	rm -rf guard_logs/

clean-all: clean               ## Deep clean including Rust build outputs and FFI binaries
	rm -rf rust/noe_core/target/
	rm -f /tmp/noe_c_smoke /tmp/noe_cpp_smoke

help:                          ## Show all available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

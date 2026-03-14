# Noe Reference Implementation — Makefile
# Usage: make test | make conformance | make demo | make bench | make all

.PHONY: test conformance demo bench guard all clean

# ─── Core Test Suites ────────────────────────────────────────────────

test:                          ## Run all unit tests
	python3 -m unittest discover tests

conformance:                   ## Run NIP-011 conformance suite (60 vectors)
	python3 tests/nip011/run_conformance.py

# ─── Demos ───────────────────────────────────────────────────────────

demo:                          ## Run flagship shipment demo
	bash examples/auditor_demo/run_demo.sh

demo-full:                     ## Run full auditor demo set
	bash examples/auditor_demo/run_demo_full.sh

guard:                         ## Run robot guard golden-vector demo (7 ticks)
	python3 examples/robot_guard_demo.py

# ─── Benchmarks ──────────────────────────────────────────────────────

bench:                         ## Run ROS bridge overhead benchmark
	python3 benchmarks/bridge_overhead.py

# ─── Aggregate ───────────────────────────────────────────────────────

all: test conformance guard demo bench   ## Run everything
	@echo ""
	@echo "════════════════════════════════════════════"
	@echo "  ✅  ALL SUITES PASSED"
	@echo "════════════════════════════════════════════"

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

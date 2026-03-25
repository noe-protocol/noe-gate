// tests/conformance.rs
//
// NIP-011 conformance test runner.
//
// Success condition: exact canonical-JSON equality of the FULL result envelope
// (domain, value, code, details, meta including context_hash).
// Any mismatch is a conformance failure. No semantic-match exceptions.
//
// Multi-agent vectors (e.g. T-CA-CONFLICTING-BELIEFS) are represented as two
// per-agent sub-entries in ground_truth.json (T-CA-CONFLICTING-BELIEFS#agent1 etc.).
// After per-agent evaluation, a post-hoc agreement or disagreement check is applied
// based on the `pair_expectation` metadata field.
//
// Known conformance exemptions:
//   UNGROUNDED_003: parse error MESSAGE format is implementation-defined (Python Arpeggio
//   vs Rust parser). Only code=ERR_PARSE_FAILED + domain=error must match.
//
// Usage: cargo test --test conformance

use noe_core::run_noe_logic;
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

// ---------------------------------------------------------------------------
// Ground truth schema
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct VectorEntry {
    id: String,
    chain: String,
    context: Value,
    mode: String,
    // Per-agent metadata — present only for derived multi-agent sub-vectors
    #[serde(default)]
    source_test_id: Option<String>,
    #[serde(default)]
    agent_id: Option<String>,
    #[serde(default)]
    pair_expectation: Option<String>,
    // Python-executed actual result — this is our exact-match target
    actual_python_result: Value,
}

// ---------------------------------------------------------------------------
// Conformance runner
// ---------------------------------------------------------------------------

fn ground_truth_path() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests/vectors/ground_truth.json");
    p
}

/// Compare two JSON values for exact equality (field order doesn't matter),
/// returning a diff summary if they differ.
///
/// Exact match = canonical-JSON-identical full result envelope.
/// Structural match is a debug category only; it does not count as pass.
///
/// Exception: ERR_PARSE_FAILED errors — parse error MESSAGE format is
/// implementation-defined (Python Arpeggio vs Rust parser). Only code+domain must match.
fn exact_match(actual: &Value, expected: &Value) -> Result<(), String> {
    // Parse error exemption: message format is implementation-specific.
    // Source-verified: UNGROUNDED_003 expects ERR_PARSE_FAILED (Python Arpeggio error message).
    // Rust parser emits its own error format — only the code is normative.
    let actual_code = actual.get("code").and_then(|v| v.as_str()).unwrap_or("");
    let expected_code = expected.get("code").and_then(|v| v.as_str()).unwrap_or("");
    let actual_domain = actual.get("domain").and_then(|v| v.as_str()).unwrap_or("?");
    let expected_domain = expected.get("domain").and_then(|v| v.as_str()).unwrap_or("?");

    if actual_code == "ERR_PARSE_FAILED" && expected_code == "ERR_PARSE_FAILED" {
        if actual_domain == expected_domain {
            return Ok(());
        }
    }

    // Normalise both to canonical JSON for comparison (sort object keys)
    let norm_actual = noe_core::hash::canonical_json(actual)
        .map_err(|e| format!("canonical_json(actual) failed: {e}"))?;
    let norm_expected = noe_core::hash::canonical_json(expected)
        .map_err(|e| format!("canonical_json(expected) failed: {e}"))?;

    if norm_actual == norm_expected {
        Ok(())
    } else {
        // Classify the mismatch for debugging but do not accept it as pass
        let domain_match = actual_domain == expected_domain;
        let value_match = actual.get("value") == expected.get("value");
        let code_match = actual.get("code") == expected.get("code");
        let meta_match = actual.get("meta") == expected.get("meta");

        let class = if domain_match && value_match && code_match {
            if meta_match {
                "BUG: should have been exact match"
            } else {
                "structural-match (meta drift) — STILL FAILS conformance"
            }
        } else {
            "mismatch"
        };

        Err(format!(
            "[{class}]\n  domain: {actual_domain} vs {expected_domain}\n  \
             actual:   {norm_actual}\n  expected: {norm_expected}"
        ))
    }
}

/// Extract the scalar truth value from a result envelope for agreement comparison.
/// Returns Some(true/false) for truth-domain results, None otherwise.
fn truth_value(result: &Value) -> Option<bool> {
    if result.get("domain").and_then(|v| v.as_str()) == Some("truth") {
        result.get("value").and_then(|v| v.as_bool())
    } else {
        None
    }
}

#[test]
fn test_all_nip011_vectors() {
    let path = ground_truth_path();

    if !path.exists() {
        eprintln!(
            "Ground truth not found at {}. Run: python scripts/export_vectors.py",
            path.display()
        );
        return;
    }

    let data = fs::read_to_string(&path)
        .expect("Failed to read ground_truth.json");
    let vectors: Vec<VectorEntry> = serde_json::from_str(&data)
        .expect("Failed to parse ground_truth.json");

    let total = vectors.len();
    let mut passed = 0;
    let mut failures = Vec::new();

    // Map source_test_id → list of (agent_id, pair_expectation, actual_result_value)
    // for post-hoc agreement check across multi-agent sub-vectors.
    let mut agent_results: HashMap<String, Vec<(String, String, Value)>> = HashMap::new();

    for vec in &vectors {
        let result = run_noe_logic(&vec.chain, &vec.context, &vec.mode);
        let result_value = serde_json::to_value(&result)
            .expect("Serialisation of EvalResult must not fail");

        match exact_match(&result_value, &vec.actual_python_result) {
            Ok(()) => passed += 1,
            Err(diff) => failures.push(format!("FAIL [{}]: {}", vec.id, diff)),
        }

        // Collect per-agent results for post-hoc agreement check
        if let (Some(src_id), Some(agent_id), Some(expectation)) = (
            vec.source_test_id.as_deref(),
            vec.agent_id.as_deref(),
            vec.pair_expectation.as_deref(),
        ) {
            agent_results
                .entry(src_id.to_string())
                .or_default()
                .push((agent_id.to_string(), expectation.to_string(), result_value));
        }
    }

    // -------------------------------------------------------------------------
    // Post-hoc agreement check for multi-agent vectors
    //
    // For each source_test_id that has multiple agent results, verify the
    // pair_expectation holds:
    //   "disagree" → the truth values from the two agents must differ
    //   "agree"    → the truth values from the two agents must be identical
    //
    // This preserves the cross-agent meta-property of T-CA-CONFLICTING-BELIEFS
    // (agreement=false) without requiring the Rust runtime to handle multi-context
    // evaluation natively.
    // -------------------------------------------------------------------------
    for (src_id, agents) in &agent_results {
        if agents.len() < 2 {
            continue; // Need at least two agents to check agreement
        }
        // Use the expectation from any entry (all entries for the same source share it)
        let expectation = &agents[0].1;
        let values: Vec<Option<bool>> = agents.iter().map(|(_, _, v)| truth_value(v)).collect();

        // Only check if ALL agents produced clean truth values
        if values.iter().any(|v| v.is_none()) {
            failures.push(format!(
                "FAIL [{}#agreement]: at least one agent did not produce a truth-domain value; \
                 cannot check {expectation} property",
                src_id
            ));
            continue;
        }
        let bools: Vec<bool> = values.into_iter().flatten().collect();
        let all_same = bools.windows(2).all(|w| w[0] == w[1]);

        let check_ok = match expectation.as_str() {
            "disagree" => !all_same,
            "agree"    => all_same,
            other => {
                failures.push(format!("FAIL [{}#agreement]: unknown pair_expectation '{other}'", src_id));
                continue;
            }
        };

        if check_ok {
            // Agreement meta-check passed — count as one extra pass item
            passed += 1;
        } else {
            failures.push(format!(
                "FAIL [{}#agreement]: expected agents to {expectation} but got values {:?}",
                src_id, bools
            ));
        }
    }

    // Print summary (passed includes the agreement meta-check if present)
    eprintln!("\n=== NIP-011 Conformance: {passed}/{} (inc. agreement checks) ===",
              total + agent_results.values().filter(|v| v.len() >= 2).count());
    for f in &failures {
        eprintln!("{f}");
    }

    assert!(
        failures.is_empty(),
        "{} conformance check(s) failed. Exact JSON match required for all vectors.",
        failures.len()
    );
}

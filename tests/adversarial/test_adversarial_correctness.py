"""
Adversarial tests for final correctness fixes (Batch 5).
Tests nested merge, action hash invariance, question hash canonicalization.
"""
import unittest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from noe.noe_parser import merge_layers_for_validation, compute_question_hash, run_noe_logic

class TestNestedMerge(unittest.TestCase):
    """CRITICAL FIX #1: Test deep merge preserves nested shard keys"""
    
    def test_spatial_thresholds_merge(self):
        """Root sets near=1, far=10; Domain overwrites far=20; Expect both preserved"""
        ctx = {
            "root": {
                "spatial": {
                    "thresholds": {"near": 1.0, "far": 10.0}
                }
            },
            "domain": {
                "spatial": {
                    "thresholds": {"far": 20.0}
                }
            },
            "local": {}
        }
        
        merged = merge_layers_for_validation(ctx)
        
        # CRITICAL: Both near and far must be present
        self.assertEqual(merged["spatial"]["thresholds"]["near"], 1.0,
                        "Deep merge lost root.spatial.thresholds.near")
        self.assertEqual(merged["spatial"]["thresholds"]["far"], 20.0,
                        "Deep merge didn't override with domain.spatial.thresholds.far")
    
    def test_modal_knowledge_merge(self):
        """Test modal.knowledge preserves both layers"""
        ctx = {
            "root": {
                "modal": {
                    "knowledge": {"@fact1": True}
                }
            },
            "local": {
                "modal": {
                    "knowledge": {"@fact2": True}
                }
            }
        }
        
        merged = merge_layers_for_validation(ctx)
        
        self.assertIn("@fact1", merged["modal"]["knowledge"])
        self.assertIn("@fact2", merged["modal"]["knowledge"])


class TestQuestionHashCanonical(unittest.TestCase):
    """CRITICAL FIX #4: Test question hash is whitespace/format invariant"""
    
    def test_whitespace_invariance(self):
        """Same chain with different whitespace should have same hash"""
        chain1 = "mek dia"
        chain2 = "mek  dia"  # Extra space
        chain3 = "mek\tdia"  # Tab
        
        ctx_hash = "abc123"
        timestamp = 1000.0
        
        hash1 = compute_question_hash(chain1, ctx_hash, timestamp)
        hash2 = compute_question_hash(chain2, ctx_hash, timestamp)
        hash3 = compute_question_hash(chain3, ctx_hash, timestamp)
        
        self.assertEqual(hash1, hash2, "Whitespace should be normalized")
        self.assertEqual(hash1, hash3, "Tab should normalize to space")
    
    def test_unicode_normalization(self):
        """Unicode variants should normalize"""
        # é can be represented as single char or e + combining accent
        chain1 = "café"  # Composed
        chain2 = "café"  # Decomposed (if editor allows)
        
        ctx_hash = "abc123"
        timestamp = 1000.0
        
        hash1 = compute_question_hash(chain1, ctx_hash, timestamp)
        hash2 = compute_question_hash(chain2, ctx_hash, timestamp)
        
        # NFKC should normalize both
        self.assertEqual(hash1, hash2, "Unicode should be NFKC normalized")
    
    def test_integer_timestamp(self):
        """Timestamp should be integer milliseconds, not float string"""
        chain = "mek dia"
        ctx_hash = "abc123"
        
        # Same timestamp as float and int
        hash1 = compute_question_hash(chain, ctx_hash, 1000.0)
        hash2 = compute_question_hash(chain, ctx_hash, 1000)
        
        self.assertEqual(hash1, hash2, "Float and int timestamps should match")


class TestActionHashProposalOnly(unittest.TestCase):
    """CRITICAL FIX #3: action_hash should be proposal-only, not outcome-dependent"""
    
    def test_action_hash_ignores_status(self):
        """Same action with different status should have same action_hash"""
        from noe.noe_parser import compute_action_hash
        
        action1 = {"type": "action", "verb": "mek", "target": "dia", "status": "pending"}
        action2 = {"type": "action", "verb": "mek", "target": "dia", "status": "completed"}
        
        hash1 = compute_action_hash(action1)
        hash2 = compute_action_hash(action2)
        
        self.assertEqual(hash1, hash2, "action_hash should ignore status field")
    
    def test_action_hash_ignores_verified(self):
        """action_hash should ignore audit result"""
        from noe.noe_parser import compute_action_hash
        
        action1 = {"type": "action", "verb": "mek", "target": "dia", "verified": True}
        action2 = {"type": "action", "verb": "mek", "target": "dia", "verified": False}
        
        hash1 = compute_action_hash(action1)
        hash2 = compute_action_hash(action2)
        
        self.assertEqual(hash1, hash2, "action_hash should ignore verified field")


class TestPhDFinalGuardrails(unittest.TestCase):
    """Guardrails requested in the final review (AST immutability, None rejection, Determinism, Isolation)"""
    
    def test_ast_immutability(self):
        """AST cache must not mutate; consecutive runs should be strictly identical."""
        chain = "shi @foo"
        ctx = {"modal": {"knowledge": {"@foo": True}}, "temporal": {"now": 1000}, "spatial": {"unit": "m"}}
        res1 = run_noe_logic(chain, ctx, debug=False, mode="partial")
        res2 = run_noe_logic(chain, ctx, debug=False, mode="partial")
        self.assertEqual(res1["value"], res2["value"], "Consecutive executes with debug=True should perfectly match")
        
    def test_explicit_none_rejection(self):
        """Explicit {'root': None} should fail strict mode validator, not mask with {}"""
        ctx = {"root": None, "domain": {}, "local": {}}
        res = run_noe_logic("shi @foo", ctx, mode="strict")
        self.assertEqual(res.get("domain"), "error")
        # Ensure it failed for structure, not something downstream
        self.assertIn(res.get("code"), ["ERR_CONTEXT_INCOMPLETE", "ERR_BAD_CONTEXT"])
        # Ensure meta includes context_hashes even on hard failure
        self.assertIn("context_hashes", res.get("meta", {}))
        
    def test_python_runtime_determinism(self):
        """Even with floats, python environment spatial logic must be strictly deterministic across calls"""
        chain = "nel @target"
        ctx = {
            "entities": {"@target": {"position": [1.234567, 8.9101112], "type": "point"}},
            "spatial": {"unit": "meters", "thresholds": {"near": 5.0}},
            "local": {"position": [0.0, 0.0]},
            "modal": {"knowledge": {}, "belief": {}, "certainty": {}},
            "axioms": {"value_system": {"accepted": [], "rejected": []}},
            "temporal": {"now": 1000, "max_skew_ms": 1.0}
        }
        res1 = run_noe_logic(chain, ctx, mode="partial")
        res2 = run_noe_logic(chain, ctx, mode="partial")
        self.assertEqual(res1["value"], res2["value"], "Float evaluation output must be strictly stable within runtime")
        
    def test_question_hash_replay(self):
        """Question hash recreation using explicit timestamp must match perfectly"""
        chain = "mek @release"
        timestamp = 1718000000
        hash1 = compute_question_hash(chain, "dummy_ctx_hash", timestamp)
        hash2 = compute_question_hash(chain, "dummy_ctx_hash", timestamp)
        self.assertEqual(hash1, hash2, "Explicit timestamp must yield identical hash")
        
    def test_evaluator_isolation(self):
        """Context dicts reused across calls must not share evaluator-specific state like _action_dag"""
        ctx = {"literals": {"@foo": "some_target"}}
        res1 = run_noe_logic("mek @foo", ctx, mode="partial")
        self.assertNotIn("_action_dag", ctx, "Action DAG leaked into caller context!")
        res2 = run_noe_logic("mek @foo", ctx, mode="partial")
        self.assertEqual(res1.get("domain"), "action")
        self.assertEqual(res2.get("domain"), "action")


if __name__ == '__main__':
    unittest.main()

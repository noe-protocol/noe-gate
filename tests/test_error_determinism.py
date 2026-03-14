import unittest
from noe.noe_validator import validate_chain

class TestErrorDeterminism(unittest.TestCase):
    def test_1_validator_determinism(self):
        """
        Phase 3D-3: Proves validate_chain() always returns a deterministic
        top-level context_error by properly sorting multiple simultaneous errors
        based on the frozen ERROR_PRIORITY table.
        """
        now_ms = 1000
        
        test_cases = [
            # 1. Stale + Delivery Mismatch + Missing Literal
            # Chain: vus @pkg
            # Context: Perfectly formed structurally, but stale. Delivery has no items. Literal @pkg missing.
            {
                "name": "Stale + Delivery Mismatch + Missing Literal",
                "chain": "vus @pkg",
                "context": {
                    "literals": {"@other": True},
                    "temporal": {"now": 1000, "timestamp": 0, "max_skew_ms": 1}, # Stale
                    "delivery": {"status": {}}, # Missing items -> ERR_DELIVERY_MISMATCH
                    "modal": {}, "axioms": {}
                },
                "expected": "ERR_CONTEXT_STALE" # Priority 2 beats Delivery (4) and Literal (5)
            },
            
            # 2. Action Misuse + Invalid Literal + Stale
            {
                "name": "Action Misuse + Invalid Literal + Stale",
                "chain": "kra sek noq @invalid)-lit sek", # Action misuse (noq mixed) + invalid literal
                "context": {
                    "literals": {},
                    "temporal": {"now": 1000, "timestamp": 0, "max_skew_ms": 1}, # Stale
                    "delivery": {"items": {}, "status": {}}, 
                    "modal": {}, "axioms": {}
                },
                "expected": "ERR_CONTEXT_STALE" # Priority 2 beats Action Misuse (3) and Invalid (5)
            },
            
            # 3. Spatial Ungroundable + Missing Literal (No Staleness)
            # Chain uses nel. No spatial thresholds -> Ungroundable. No literal.
            {
                "name": "Spatial Ungroundable + Missing Literal",
                "chain": "nel @bot",
                "context": {
                    "literals": {},
                    "temporal": {"now": 0, "timestamp": 0, "max_skew_ms": 100}, # Fresh
                    "entities": {"@bot": {"position": {"x": 1}}},
                    "spatial": {}, # No thresholds -> Ungroundable
                    "modal": {}, "axioms": {}
                },
                "expected": "ERR_SPATIAL_UNGROUNDABLE" # Priority 4 vs Literal (5) -> Spatial wins
            },
            
            # 4. Operator-Specific Incomplete + Stale
            # Context has `literals`, `temporal`, `modal`, `axioms` so it passes `validate_context_strict`.
            # Chain uses 'men' (audit). `audit` is missing, generating INCOMPLETE inside the audit gating.
            # Temporal is stale, generating STALE.
            {
                "name": "Operator Incomplete + Stale",
                "chain": "men", # Audit ops
                "context": {
                    "literals": {},
                    "temporal": {"now": 1000, "timestamp": 0, "max_skew_ms": 1}, # Stale
                    "modal": {},
                    "axioms": {}
                    # `audit` is intentionally omitted
                },
                "expected": "ERR_CONTEXT_INCOMPLETE"
            }
        ]
        
        for tc in test_cases:
            res = validate_chain(tc["chain"], tc["context"], mode="strict")
            
            self.assertFalse(res["ok"], f"Test '{tc['name']}' incorrectly yielded ok=True")
            self.assertTrue(len(res["errors"]) > 1, f"Test '{tc['name']}' did not trigger multiple errors")
            
            # 1. Ensure the top error code in the structured list matches expectations
            sorted_top_error = res["errors"][0]["code"]
            self.assertEqual(
                sorted_top_error, tc["expected"], 
                f"Test '{tc['name']}': Expected {tc['expected']} as top sorted error, got {sorted_top_error}. Errors: {[e['code'] for e in res['errors']]}"
            )
            
            # 2. Ensure the top-level string flag faithfully mirrors the sorted list
            self.assertEqual(
                res["context_error"], tc["expected"], 
                f"Test '{tc['name']}': `context_error` did not match `errors[0]['code']`."
            )

    def test_2_runtime_determinism(self):
        """
        Phase 3D-3: Proves NoeRuntime.evaluate() deterministically wraps validator
        and parser faults into uniform RuntimeResult payloads without drift.
        """
        from noe.noe_runtime import NoeRuntime
        from noe.context_manager import ContextSnapshot, ContextManager
        
        cm = ContextManager({}, {}, {})
        runtime = NoeRuntime(context_manager=cm, debug=False, strict_mode=True)
        
        # 1. Exact identical passes map to identical error payloads
        chain = "sek noq @invalid)-lit sek"
        c_root = {
            "temporal": {"now": 1000, "timestamp": 0, "max_skew_ms": 1},
            "literals": {"@k": True},
            "modal": {},
            "axioms": {}
        }
        c_domain = {}
        c_local = {"delivery": {"items": {}, "status": {}}}
        
        cm = ContextManager(c_root, c_domain, c_local)
        runtime = NoeRuntime(context_manager=cm, debug=False, strict_mode=True)
        
        # We manually fetch the snapshot here just to compare the hash
        snap_ref = cm.snapshot()
        
        res1 = runtime.evaluate(chain)
        res2 = runtime.evaluate(chain)
        
        self.assertEqual(res1.domain, "error")
        self.assertEqual(res2.domain, "error")
        self.assertEqual(res1.error, res2.error)
        self.assertEqual(res1.context_hash, res2.context_hash)
        self.assertEqual(res1.context_hash, snap_ref.composite_hash)
        
        # 2. Parse Error determinism (proving it beats context failures)
        chain_parse = "( shi @a"
        res_parse = runtime.evaluate(chain_parse)
        self.assertEqual(res_parse.domain, "error")
        self.assertTrue(res_parse.error.startswith("ERR_PARSE"))

if __name__ == "__main__":
    unittest.main()

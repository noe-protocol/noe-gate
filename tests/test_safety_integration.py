
import unittest
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from noe.noe_parser import NoeEvaluator
except ImportError:
    NoeEvaluator = None

class TestSafetyIntegration(unittest.TestCase):
    def test_undefined_propagation(self):
        """
        Verify that if C.modal.knowledge['@foo'] is None (set by hysteresis adapter),
        logic operators like 'shi' and 'an' treat it as undefined, NOT False.
        """
        if NoeEvaluator is None:
            return

        context = {
            "modal": {
                "knowledge": {
                    "@foo": None,  # Explicitly None (as set by hysteresis)
                    "@bar": True
                }
            }
        }

        evaluator = NoeEvaluator(context, mode="strict")

        # 2. Test 'shi @foo' -> None
        res_shi = evaluator._apply_unary_op("shi", "@foo", extra_key="@foo")

        self.assertIsNone(res_shi, "shi @foo (None) should return None")
        self.assertNotEqual(res_shi, False, "shi @foo (None) must not coerce to False")

        # 3. Test 'shi @foo an true' -> 'undefined' (since None an True = U in K3)
        res_an = evaluator._apply_binary_op(res_shi, "an", True)
        self.assertEqual(res_an, "undefined", f"U AND True should be undefined, got {res_an!r}")

        # 4. Test Strong Kleene (K3) Dominance
        res_k3_and = evaluator._apply_binary_op(False, "an", res_shi)
        self.assertFalse(res_k3_and, "False AND U must be False (K3 dominance)")

        res_k3_or = evaluator._apply_binary_op(True, "ur", res_shi)
        self.assertTrue(res_k3_or, "True OR U must be True (K3 dominance)")

        res_k3_or_fail = evaluator._apply_binary_op(False, "ur", res_shi)
        self.assertEqual(res_k3_or_fail, "undefined", "False OR U must be Undefined")

        # 5. Test Negation K3 (nai / nex)
        res_neg = evaluator._apply_unary_op("nai", None)
        self.assertEqual(res_neg, "undefined", f"NOT U should be undefined, got {res_neg!r}")

        res_neg_t = evaluator._apply_unary_op("nai", True)
        self.assertFalse(res_neg_t, "NOT True should be False")

        res_neg_f = evaluator._apply_unary_op("nai", False)
        self.assertTrue(res_neg_f, "NOT False should be True")

if __name__ == "__main__":
    unittest.main()

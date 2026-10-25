"""general_index/gi_tortured.py: cleaning the PPS expected text and rewriting a 5-gram."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_tortured as gt  # noqa: E402


class TestExpected(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(gt.expected_variants("back propagation (BP)"), ["back propagation"])
        self.assertEqual(gt.expected_variants("[neural network] layer outputs"), ["layer outputs"])
        self.assertEqual(gt.expected_variants("economic development/profit growth"),
                         ["economic development", "profit growth"])
        self.assertEqual(gt.expected_variants("differential equation AND 1st order"), ["differential equation"])

    def test_comments_give_nothing(self):
        self.assertEqual(gt.expected_variants("The word ‘are’ as in the conjugated verb ‘be’, see https://x"), [])


class TestRewrites(unittest.TestCase):
    def test_same_length(self):
        w = "we use profound learning model".split()
        self.assertEqual(gt.rewrites(w, 2, 2, ("deep", "learning")), [["we", "use", "deep", "learning", "model"]])

    def test_longer_gives_every_window_over_it(self):
        w = "a b counterfeit consciousness c".split()
        out = gt.rewrites(w, 2, 2, ("artificial", "general", "intelligence"))
        self.assertEqual(out, [["a", "b", "artificial", "general", "intelligence"],
                               ["b", "artificial", "general", "intelligence", "c"]])

    def test_windows_must_overlap_the_replacement(self):
        w = "x y z profound learning".split()
        out = gt.rewrites(w, 3, 2, ("deep", "learning", "method"))
        self.assertTrue(all("deep" in o or "method" in o for o in out))


if __name__ == "__main__":
    unittest.main()

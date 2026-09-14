#!/usr/bin/env python3
"""Unit tests for train_boilerplate_family_classifier.py's pure logic:
family_for_label() (keyword-based grouping of classify_dupes.TEXT_PATTERNS'
140 fixed labels into families) and predict_families()'s confidence-gating
(the actual decision function a real deployment would use -- see that
function's own docstring on why this differs from raw argmax). Training
itself (train(), the sklearn pipeline) isn't unit-tested here -- it's
exercised directly against real corpus data and validated via shadow-mode
checks (shadow_validate_against_confirmed/backtest_suite), not something a
fast, network-free unit test can meaningfully cover."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import classify_dupes as cd  # noqa: E402
import train_boilerplate_family_classifier as tbfc  # noqa: E402


class TestFamilyForLabel(unittest.TestCase):
    def test_citation_keywords(self):
        self.assertEqual(tbfc.family_for_label("shared bibliography/citation entry (external DOI)"),
                          "citation_bibliography")

    def test_funding_keywords(self):
        self.assertEqual(tbfc.family_for_label("NSF grant funding-acknowledgment boilerplate"),
                          "funding_acknowledgment")

    def test_copyright_keywords(self):
        self.assertEqual(tbfc.family_for_label("CC license boilerplate"), "copyright_license")

    def test_ethics_keywords(self):
        self.assertEqual(tbfc.family_for_label("conflict-of-interest disclaimer boilerplate"),
                          "ethics_declarations")

    def test_repository_keywords(self):
        self.assertEqual(
            tbfc.family_for_label("University of Glasgow 'Enlighten' institutional-repository deposit stamp"),
            "repository_deposit",
        )

    def test_thesis_keywords(self):
        self.assertEqual(
            tbfc.family_for_label("UNSW thesis-examination 'Candidate's Declaration' boilerplate"),
            "thesis_declaration",
        )

    def test_masthead_keywords(self):
        self.assertEqual(tbfc.family_for_label("journal masthead boilerplate (homepage link)"),
                          "journal_masthead")

    def test_navigation_keywords(self):
        self.assertEqual(tbfc.family_for_label("table-of-contents dot-leader navigation text"),
                          "navigation_chrome")

    def test_unmatched_label_falls_back_to_other(self):
        self.assertEqual(tbfc.family_for_label("some entirely novel boilerplate nobody has seen before"),
                          tbfc.OTHER_BOILERPLATE)

    def test_every_real_pattern_label_maps_to_some_family(self):
        """Every one of classify_dupes.TEXT_PATTERNS' actual fixed labels must resolve to SOME
        family (never crash, never return None) -- this is the exact input build_dataset() feeds
        it from real classify_text_patterns_only() output."""
        labels = {label for _, label in cd.TEXT_PATTERNS}
        for label in labels:
            family = tbfc.family_for_label(label)
            self.assertIsInstance(family, str)
            self.assertTrue(family)


class TestPredictFamilies(unittest.TestCase):
    """Uses a mock vectorizer/model rather than a real trained one -- this is testing the
    confidence-gating DECISION LOGIC (the safety-critical part), not sklearn itself."""

    def _make_mock(self, classes, proba_rows):
        vectorizer = MagicMock()
        vectorizer.transform.return_value = np.zeros((len(proba_rows), 1))
        model = MagicMock()
        model.classes_ = np.array(classes)
        model.predict_proba.return_value = np.array(proba_rows)
        return vectorizer, model

    def test_confident_boilerplate_prediction_passes_through(self):
        vectorizer, model = self._make_mock(
            [tbfc.NOT_BOILERPLATE, "citation_bibliography"],
            [[0.05, 0.95]],
        )
        result = tbfc.predict_families(vectorizer, model, ["some text"], min_confidence=0.9)
        self.assertEqual(result, [("citation_bibliography", 0.95)])

    def test_unconfident_boilerplate_prediction_gated_to_not_boilerplate(self):
        # top class is a boilerplate family, but confidence (0.6) is below min_confidence (0.9) --
        # must NOT be trusted as a skip decision.
        vectorizer, model = self._make_mock(
            [tbfc.NOT_BOILERPLATE, "citation_bibliography"],
            [[0.4, 0.6]],
        )
        result = tbfc.predict_families(vectorizer, model, ["some text"], min_confidence=0.9)
        family, conf = result[0]
        self.assertEqual(family, tbfc.NOT_BOILERPLATE)

    def test_not_boilerplate_top_prediction_never_gated(self):
        # not_boilerplate as the top class is never gated regardless of confidence -- the gate
        # only applies to trusting a BOILERPLATE prediction.
        vectorizer, model = self._make_mock(
            [tbfc.NOT_BOILERPLATE, "citation_bibliography"],
            [[0.55, 0.45]],
        )
        result = tbfc.predict_families(vectorizer, model, ["some text"], min_confidence=0.9)
        self.assertEqual(result[0][0], tbfc.NOT_BOILERPLATE)

    def test_confidence_exactly_at_threshold_passes(self):
        vectorizer, model = self._make_mock(
            [tbfc.NOT_BOILERPLATE, "citation_bibliography"],
            [[0.1, 0.9]],
        )
        result = tbfc.predict_families(vectorizer, model, ["some text"], min_confidence=0.9)
        self.assertEqual(result[0][0], "citation_bibliography")


if __name__ == "__main__":
    unittest.main()

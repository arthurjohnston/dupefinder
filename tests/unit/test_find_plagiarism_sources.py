#!/usr/bin/env python3
"""Unit tests for find_plagiarism_sources.py's DOI-extraction logic --
validated against the real retraction-notice text this feature was built
around (10.1038/s41598-025-98710-9), not just synthetic examples."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import find_plagiarism_sources as fps  # noqa: E402

REAL_NOTICE_TEXT = """
Retraction Note: Investigation of
the competitive nature of eMBB
and mMTC 5G services in conditions
of limited communication resource
Viacheslav Kovtun & Krzysztof Grochla
Retraction of: Scientific Reports https://doi.org/10.1038/s41598-022-20135-5, published online 26 September
2022
The Editors have retracted this Article.
Following publication, the inclusion of several questioned references in the Article was brought to the attention
of the Editors. Investigation by the Editors have uncovered further issues, including irrelevant citations and
significant textual overlaps with previously-published work with no common authors1. The Editors no longer
have confidence in the claims of the Article. The Authors agree with this retraction.

Reference

1. Belusso, C. L. M., Sawicki, S., Roos-Frantz, F. & Frantz, R. S. A study of Petri nets, Markov chains and queueing theory as
mathematical modelling languages aiming at the simulation of enterprise application integration solutions: A first step. Procedia
Comput. Sci. 100, 229-236. https://doi.org/10.1016/j.procs.2016.09.147 (2016).
"""


class TestExtractCandidateSourceDois(unittest.TestCase):
    def test_real_notice_finds_the_cited_source_and_excludes_self_references(self):
        exclude = {"10.1038/s41598-025-98710-9", "10.1038/s41598-022-20135-5"}
        result = fps.extract_candidate_source_dois(REAL_NOTICE_TEXT, exclude)
        self.assertEqual(result, ["10.1016/j.procs.2016.09.147"])

    def test_no_dois_returns_empty_list(self):
        self.assertEqual(fps.extract_candidate_source_dois("No DOIs here, sorry.", set()), [])

    def test_excludes_the_retracted_papers_own_doi(self):
        text = "Retraction of https://doi.org/10.1234/self, no other reference given."
        self.assertEqual(fps.extract_candidate_source_dois(text, {"10.1234/self"}), [])

    def test_deduplicates_repeated_dois(self):
        text = "See 10.1234/abc and again 10.1234/abc later in the text."
        self.assertEqual(fps.extract_candidate_source_dois(text, set()), ["10.1234/abc"])

    def test_case_insensitive_matching_against_exclude_set(self):
        text = "Cites 10.1234/ABC as the source."
        result = fps.extract_candidate_source_dois(text, {"10.1234/abc"})
        self.assertEqual(result, [])

    def test_multiple_distinct_candidates_preserved(self):
        text = "Overlaps with 10.1234/first and also 10.1234/second."
        result = fps.extract_candidate_source_dois(text, set())
        self.assertEqual(result, ["10.1234/first", "10.1234/second"])


class TestCleanDoi(unittest.TestCase):
    def test_strips_trailing_period(self):
        self.assertEqual(fps.clean_doi("10.1016/j.procs.2016.09.147."), "10.1016/j.procs.2016.09.147")

    def test_strips_trailing_semicolon_and_paren(self):
        self.assertEqual(fps.clean_doi("10.1234/abc);"), "10.1234/abc")

    def test_leaves_a_doi_with_no_trailing_junk_unchanged(self):
        self.assertEqual(fps.clean_doi("10.1234/abc"), "10.1234/abc")


if __name__ == "__main__":
    unittest.main()

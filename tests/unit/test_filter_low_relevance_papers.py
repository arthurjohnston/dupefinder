#!/usr/bin/env python3
"""Unit tests for filter_low_relevance_papers.py -- crank-cluster detection
(find_crank_keys), off-topic title matching (find_offtopic_keys), and the
already-extracted-in-library.sqlite3 guard (load_candidates) that keeps this
script from ever touching a paper extract_papers.py has already processed."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import filter_low_relevance_papers as flrp  # noqa: E402


def _row(key, title, doi, file_path="papers/x.pdf"):
    return (key, title, doi, file_path)


class TestFindCrankKeys(unittest.TestCase):
    def test_flags_group_of_three_or_more_same_title_same_prefix(self):
        candidates = [
            _row("doi:10.5281/zenodo.1", "Masked Intelligence: The Illusion", "10.5281/zenodo.1"),
            _row("doi:10.5281/zenodo.2", "Masked Intelligence: The Illusion", "10.5281/zenodo.2"),
            _row("doi:10.5281/zenodo.3", "Masked Intelligence: The Illusion", "10.5281/zenodo.3"),
        ]
        flagged = flrp.find_crank_keys(candidates)
        self.assertEqual(set(flagged), {"doi:10.5281/zenodo.1", "doi:10.5281/zenodo.2", "doi:10.5281/zenodo.3"})

    def test_does_not_flag_pair_below_threshold(self):
        candidates = [
            _row("doi:10.5281/zenodo.1", "Some Real Dissertation Title", "10.5281/zenodo.1"),
            _row("doi:10.5281/zenodo.2", "Some Real Dissertation Title", "10.5281/zenodo.2"),
        ]
        self.assertEqual(flrp.find_crank_keys(candidates), {})

    def test_requires_same_doi_prefix_not_just_same_title(self):
        """A preprint-then-journal pair sharing a title but from different
        registrars (different DOI prefixes) is a legitimate different
        scenario (find_duplicate_papers.py's job), not crank spam."""
        candidates = [
            _row("doi:10.5281/zenodo.1", "Same Title", "10.5281/zenodo.1"),
            _row("doi:10.1234/other.2", "Same Title", "10.1234/other.2"),
            _row("doi:10.9999/third.3", "Same Title", "10.9999/third.3"),
        ]
        self.assertEqual(flrp.find_crank_keys(candidates), {})

    def test_ignores_rows_with_no_doi(self):
        candidates = [_row(f"title:x{i}", "No Doi Title", None) for i in range(5)]
        self.assertEqual(flrp.find_crank_keys(candidates), {})

    def test_unrelated_titles_never_grouped_together(self):
        candidates = [
            _row("doi:10.5281/zenodo.1", "Title A", "10.5281/zenodo.1"),
            _row("doi:10.5281/zenodo.2", "Title B", "10.5281/zenodo.2"),
            _row("doi:10.5281/zenodo.3", "Title C", "10.5281/zenodo.3"),
        ]
        self.assertEqual(flrp.find_crank_keys(candidates), {})


class TestFindOfftopicKeys(unittest.TestCase):
    def test_on_topic_title_not_flagged(self):
        candidates = [_row("k1", "Algorithmic Bias in Hiring Systems", None)]
        self.assertEqual(flrp.find_offtopic_keys(candidates, already_flagged={}), {})

    def test_off_topic_title_flagged(self):
        candidates = [_row("k1", "Nursing Care in Emergency Departments", None)]
        flagged = flrp.find_offtopic_keys(candidates, already_flagged={})
        self.assertIn("k1", flagged)

    def test_loose_stem_match_not_exact_phrase(self):
        """Unlike bulk_retrieve_crossref.py's harvest keywords, this is a
        stem match -- a title need not contain an exact DEFAULT_KEYWORDS
        phrase to count as on-topic."""
        candidates = [_row("k1", "Bias in Hiring Algorithms: A Case Study", None)]
        self.assertEqual(flrp.find_offtopic_keys(candidates, already_flagged={}), {})

    def test_already_crank_flagged_rows_are_skipped(self):
        candidates = [_row("k1", "Nursing Care in Emergency Departments", None)]
        flagged = flrp.find_offtopic_keys(candidates, already_flagged={"k1": ("t", "d")})
        self.assertEqual(flagged, {})


class TestLoadCandidates(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "state.sqlite3"
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE papers (key TEXT PRIMARY KEY, title TEXT, authors TEXT, year INTEGER,
                         doi TEXT, status TEXT, oa_status TEXT, pdf_url TEXT, file_path TEXT, error TEXT,
                         updated_at TEXT)""")
        conn.executemany(
            "INSERT INTO papers (key, title, doi, status, file_path, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("k1", "Downloaded Not Extracted", "10.1/1", "downloaded", "papers/a.pdf", "2026-08-25T01:15:00Z"),
                ("k2", "Already Extracted", "10.1/2", "downloaded", "papers/b.pdf", "2026-08-25T01:15:00Z"),
                ("k3", "Never Downloaded", "10.1/3", "error", None, "2026-08-25T01:15:00Z"),
                ("k4", "Downloaded No File Path Somehow", "10.1/4", "downloaded", None, "2026-08-25T01:15:00Z"),
                ("k5", "Older Unrelated Batch", "10.34218/5", "downloaded", "papers/e.pdf", "2026-08-24T21:19:00Z"),
            ],
        )
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()

    def test_skips_already_extracted_and_non_downloaded_rows(self):
        candidates = flrp.load_candidates(self.conn, already_extracted={"papers/b.pdf"})
        keys = {c[0] for c in candidates}
        self.assertEqual(keys, {"k1", "k5"})

    def test_since_excludes_older_unrelated_batch(self):
        """--since is what keeps this script from sweeping up an older,
        deliberately topic-agnostic --whole-prefix batch (see module
        docstring's Scope section)."""
        candidates = flrp.load_candidates(self.conn, already_extracted={"papers/b.pdf"}, since="2026-08-25T00:00:00Z")
        keys = {c[0] for c in candidates}
        self.assertEqual(keys, {"k1"})


if __name__ == "__main__":
    unittest.main()

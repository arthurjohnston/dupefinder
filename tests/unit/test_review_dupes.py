#!/usr/bin/env python3
"""Unit tests for review_dupes.py's english_score() -- in particular the
2026-08-30 bug fix (see that function's docstring): the denominator used to
count only Latin-alphabet word-tokens, so a paragraph that's almost entirely
a non-Latin script but has even one incidental Latin token (a bibliographic
"No.", a DOI, a journal abbreviation) could score a perfect 1.0 instead of
the near-0 the filter is supposed to produce."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import review_dupes as rd  # noqa: E402


class TestEnglishScore(unittest.TestCase):
    def test_real_english_scores_well_above_threshold(self):
        text = ("The purpose of this study was to examine how climate change has affected "
                 "agricultural practices in the region over the past two decades.")
        self.assertGreater(rd.english_score(text), 0.1)

    def test_pure_cyrillic_scores_zero(self):
        text = "Обобщая приведенные точки зрения, можно прийти к выводу о социально-биологическом."
        self.assertEqual(rd.english_score(text), 0.0)

    def test_pure_greek_scores_zero(self):
        text = "Παραγωγή μεταβολικών προϊόντων κατά την αύξηση στελεχών της ζύμης"
        self.assertEqual(rd.english_score(text), 0.0)

    def test_cyrillic_with_one_stray_latin_token_still_scores_zero(self):
        # Real corpus example: a Russian bibliography entry whose only Latin token is
        # "No" (from "No 2" = "Number 2"), which also happens to be a common English
        # word. Before the fix this scored 1.0 (1 Latin token found, and it matched).
        text = ("24 Махмутов З. А., Габдрахманова Г. Ф. Особенности этнической идентичности "
                 "виртуальных татарских сообществ в социальной сети Вконтакте // "
                 "Историческая этнология. 2016. Т. 1, No 2. С. 276-292.")
        self.assertEqual(rd.english_score(text), 0.0)

    def test_french_latin_script_non_english_scores_near_zero(self):
        # A Latin-script but non-English language must still score low -- the fix's
        # character-fraction gate must not treat "is Latin script" as "is English".
        text = ("Cette étude examine les conséquences du changement climatique sur les "
                 "pratiques agricoles dans la région au cours des deux dernières décennies.")
        self.assertLess(rd.english_score(text), 0.05)

    def test_english_citation_list_still_scores_reasonably(self):
        # An English-language bibliography entry (heavy on names/initials, light on
        # function words) must not be pushed to 0 by the fix -- it's still Latin-script
        # and still English, just citation-dense.
        text = "Smith J., Jones A.B., Lee C. A study of things. Journal of Studies, 2020, 12, 34-56."
        self.assertGreater(rd.english_score(text), 0.1)

    def test_empty_text_scores_zero(self):
        self.assertEqual(rd.english_score(""), 0.0)

    def test_non_alphabetic_text_scores_zero(self):
        self.assertEqual(rd.english_score("12345 -- 678.90 () [] //"), 0.0)


class TestAgentVerdicts(unittest.TestCase):
    """apply_agent_verdict()/load_candidate_by_id() -- the machinery behind
    review_dupes.py --agent-verdict (see AGENT_ACTIONS' module-level comment for why
    agent-authored verdicts get their own status vocabulary instead of writing into
    'confirmed'/'boilerplate'/etc. directly)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "library.sqlite3"
        self.conn = db.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT, file_path TEXT, year INTEGER);
            CREATE TABLE paragraphs (
                id INTEGER PRIMARY KEY, paper_id INTEGER, para_index INTEGER, text TEXT
            );
            CREATE TABLE potential_dupes (
                id INTEGER PRIMARY KEY,
                paragraph_id_1 INTEGER, paragraph_id_2 INTEGER,
                paper_id_1 INTEGER, paper_id_2 INTEGER,
                similarity REAL, same_paper INTEGER, same_author INTEGER,
                later_cites_earlier INTEGER, earlier_paper_id INTEGER, later_paper_id INTEGER,
                lcs_ratio REAL, ngram_jaccard REAL,
                status TEXT NOT NULL DEFAULT 'unreviewed', reviewed_at TEXT
            );
            CREATE TABLE lsh_buckets (
                table_num INTEGER, bucket_key INTEGER, paragraph_id INTEGER
            );
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _seed_pair(self, pd_id, para1, para2, paper1=1, paper2=2, similarity=0.9):
        self.conn.execute(
            "INSERT OR IGNORE INTO papers (id, title, file_path, year) VALUES (?, ?, ?, ?)",
            (paper1, f"Paper {paper1}", f"paper{paper1}.pdf", 2020),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO papers (id, title, file_path, year) VALUES (?, ?, ?, ?)",
            (paper2, f"Paper {paper2}", f"paper{paper2}.pdf", 2021),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO paragraphs (id, paper_id, para_index, text) VALUES (?, ?, 0, ?)",
            (para1, paper1, f"text of paragraph {para1}"),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO paragraphs (id, paper_id, para_index, text) VALUES (?, ?, 0, ?)",
            (para2, paper2, f"text of paragraph {para2}"),
        )
        self.conn.execute(
            """INSERT INTO potential_dupes
               (id, paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2, similarity, same_paper)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (pd_id, para1, para2, paper1, paper2, similarity),
        )
        self.conn.commit()

    def test_load_candidate_by_id_missing_returns_none(self):
        self.assertIsNone(rd.load_candidate_by_id(self.conn, 999))

    def test_load_candidate_by_id_found(self):
        self._seed_pair(1, 10, 20)
        row = rd.load_candidate_by_id(self.conn, 1)
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 1)
        self.assertEqual(row["text1"], "text of paragraph 10")

    def test_agent_verdict_d_sets_agent_decided_dupe(self):
        self._seed_pair(1, 10, 20)
        row = rd.load_candidate_by_id(self.conn, 1)
        marked = rd.apply_agent_verdict(self.conn, row, "d")
        self.assertEqual(marked, {1})
        status, reviewed_at = self.conn.execute(
            "SELECT status, reviewed_at FROM potential_dupes WHERE id = 1"
        ).fetchone()
        self.assertEqual(status, "agent_decided_dupe")
        self.assertIsNotNone(reviewed_at)

    def test_agent_verdict_f_and_u(self):
        self._seed_pair(1, 10, 20)
        self._seed_pair(2, 30, 40)
        rd.apply_agent_verdict(self.conn, rd.load_candidate_by_id(self.conn, 1), "f")
        rd.apply_agent_verdict(self.conn, rd.load_candidate_by_id(self.conn, 2), "u")
        statuses = dict(self.conn.execute("SELECT id, status FROM potential_dupes"))
        self.assertEqual(statuses[1], "agent_decided_false_positive")
        self.assertEqual(statuses[2], "agent_decided_unsure")

    def test_agent_verdict_never_writes_a_human_status(self):
        # The whole point of AGENT_ACTIONS: none of its values collide with ACTIONS'
        # (human) status strings, so a human filtering --status confirmed never sees an
        # agent's unaudited call mixed in.
        self.assertTrue(set(rd.AGENT_ACTIONS.values()).isdisjoint(set(rd.ACTIONS.values())))

    def test_agent_verdict_b_cascades_to_shared_lsh_bucket(self):
        # Two candidate pairs whose paragraphs land in the same LSH bucket together --
        # marking one as boilerplate should sweep up the other too (mark_bucket_status()).
        self._seed_pair(1, 10, 20)
        self._seed_pair(2, 30, 40)
        for pid in (10, 20, 30, 40):
            self.conn.execute(
                "INSERT INTO lsh_buckets (table_num, bucket_key, paragraph_id) VALUES (0, 0, ?)", (pid,)
            )
        self.conn.commit()
        row = rd.load_candidate_by_id(self.conn, 1)
        marked = rd.apply_agent_verdict(self.conn, row, "b")
        self.assertEqual(marked, {1, 2})
        statuses = dict(self.conn.execute("SELECT id, status FROM potential_dupes"))
        self.assertEqual(statuses[1], "agent_decided_boilerplate")
        self.assertEqual(statuses[2], "agent_decided_boilerplate")

    def test_agent_verdict_c_falls_back_to_single_row_without_shared_bucket(self):
        self._seed_pair(1, 10, 20)  # no lsh_buckets rows at all -- brute-force-sourced candidate
        row = rd.load_candidate_by_id(self.conn, 1)
        marked = rd.apply_agent_verdict(self.conn, row, "c")
        self.assertEqual(marked, {1})
        status = self.conn.execute("SELECT status FROM potential_dupes WHERE id = 1").fetchone()[0]
        self.assertEqual(status, "agent_decided_citation")

    def test_agent_verdict_p_sets_same_paper_without_touching_status(self):
        self._seed_pair(1, 10, 20, paper1=5, paper2=6)
        row = rd.load_candidate_by_id(self.conn, 1)
        rd.apply_agent_verdict(self.conn, row, "p")
        same_paper, status = self.conn.execute(
            "SELECT same_paper, status FROM potential_dupes WHERE id = 1"
        ).fetchone()
        self.assertEqual(same_paper, 1)
        self.assertEqual(status, "unreviewed")  # untouched, matching the human 'p' path

    def test_agent_verdict_invalid_key_raises(self):
        self._seed_pair(1, 10, 20)
        row = rd.load_candidate_by_id(self.conn, 1)
        with self.assertRaises(ValueError):
            rd.apply_agent_verdict(self.conn, row, "x")

    def test_agent_verdict_a_sets_same_author_without_touching_status_or_same_paper(self):
        self._seed_pair(1, 10, 20, paper1=5, paper2=6)
        row = rd.load_candidate_by_id(self.conn, 1)
        rd.apply_agent_verdict(self.conn, row, "a")
        same_author, same_paper, status = self.conn.execute(
            "SELECT same_author, same_paper, status FROM potential_dupes WHERE id = 1"
        ).fetchone()
        self.assertEqual(same_author, 1)
        self.assertEqual(same_paper, 0)  # left alone -- 'a' isn't a paper-identity correction
        self.assertEqual(status, "unreviewed")  # untouched, matching the human 'a' path

    def test_agent_verdict_a_cascades_to_every_row_between_the_same_pair(self):
        # Same shape as mark_same_paper()'s own cascade: every potential_dupes row between
        # the same two paper ids gets corrected, not just the one the reviewer looked at.
        self._seed_pair(1, 10, 20, paper1=5, paper2=6)
        self._seed_pair(2, 30, 40, paper1=5, paper2=6)
        self._seed_pair(3, 50, 60, paper1=5, paper2=7)  # different pair -- must NOT be touched
        row = rd.load_candidate_by_id(self.conn, 1)
        marked = rd.apply_agent_verdict(self.conn, row, "a")
        self.assertEqual(marked, {1, 2})
        same_author = dict(self.conn.execute("SELECT id, same_author FROM potential_dupes"))
        self.assertEqual(same_author[1], 1)
        self.assertEqual(same_author[2], 1)
        self.assertIsNone(same_author[3])


if __name__ == "__main__":
    unittest.main()

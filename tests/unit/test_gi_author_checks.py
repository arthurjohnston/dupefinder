"""General Index author checks (general_index/gi_filters.py, gi_verify.py).

Pins down the GI-13 miss (2026-10-07): a conference paper and its journal version with the same seven
authors scored as cross-author, because (1) the 2022-update metadata for the proceedings side named the
volume's editor instead of the authors, and (2) the byline check searched only the
extracted paragraphs, from which extract_papers.py had dropped both bylines.
"""
import re
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_filters  # noqa: E402
import gi_verify  # noqa: E402


def doc(doi, author, source):
    return {"doi": doi, "title": "t", "author": author, "journal": None, "pub_date": None, "source": source}


class TestEditorAsAuthor(unittest.TestCase):
    # made-up names; the real editor string lives in gi_filters.EDITOR_AUTHORS
    def setUp(self):
        patcher = mock.patch.object(gi_filters, "EDITOR_AUTHORS", {"c robert; quill"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_proceedings_editor_on_a_2022_update_side_is_unknown_not_cross_author(self):
        journal = doc("10.9999/journal.2017.1", "Hale, Jun; Marsh, Peter; Lund, Erik Anders", "meta")
        proceedings = doc("10.9999/proc.2017.1", "C Robert; Quill", "alt")
        self.assertEqual(gi_filters.author_relation(journal, proceedings), "authors unknown")

    def test_a_real_author_with_the_same_surname_is_untouched(self):
        a = doc("10.1/a", "Quill, Anna; Markopoulos, Christos", "alt")
        b = doc("10.1/b", "Smith, John", "meta")
        self.assertEqual(gi_filters.author_relation(a, b), "no shared author")


class TestBylineCheck(unittest.TestCase):
    def setUp(self):
        self.lib = sqlite3.connect(":memory:")
        self.lib.executescript("""
            create table papers (id integer primary key, doi text, file_path text);
            create table authors (id integer primary key, name text);
            create table paper_authors (paper_id integer, author_id integer, author_order integer);
            create table paragraphs (paper_id integer, para_index integer, text text);
            insert into papers values (1, '10.1/journal', 'j.pdf'), (2, '10.1/proc', 'p.pdf');
            insert into authors values (1, 'Marsh, Peter'), (2, 'Le Moal, Yann'), (3, 'C Robert'), (4, 'Quill');
            insert into paper_authors values (1, 1, 1), (1, 2, 2), (2, 3, 1), (2, 4, 2);
            -- extraction dropped both bylines: title fragment, then the affiliation
            insert into paragraphs values (1, 0, 'SOFTWARE TESTING IN SMALL TEAMS'),
                (1, 1, 'Example Software AS, Bergen, Norway ABSTRACT ...'),
                (2, 0, 'Robert C. Quill et al. (Eds) : CONF 2017 NOTES ON SOFTWARE TESTING IN SMALL'),
                (2, 1, 'Example Software AS, Bergen, Norway ABSTRACT ...');
        """)
        self.page1 = {1: "CODE REVIEW ... Jun Hale, Peter Marsh, Yann Le Moal Example ...",
                      2: "A FEW THOUGHTS ... Jun Hale, Peter Marsh, Yann Le Moal Example ..."}

    def test_multi_word_surname_is_split(self):
        self.assertIn("moal", gi_verify.surnames(self.lib, 1))
        self.assertNotIn("lemoal", gi_verify.surnames(self.lib, 1))

    def test_byline_found_on_pdf_page_1_when_extraction_dropped_it(self):
        with mock.patch.object(gi_verify, "pdf_page1", lambda lib, pid: self.page1[pid]):
            opening_b = gi_verify.opening(self.lib, 2)
        # the same word-boundary match gi_verify.measure() uses
        hits = {s for s in gi_verify.surnames(self.lib, 1) if re.search(r"\b" + s + r"\b", opening_b)}
        self.assertTrue({"marsh", "moal"} <= hits)

    def test_paragraphs_alone_would_have_missed_it(self):
        with mock.patch.object(gi_verify, "pdf_page1", lambda lib, pid: ""):
            opening_b = gi_verify.opening(self.lib, 2)
        self.assertNotIn("marsh", opening_b)


if __name__ == "__main__":
    unittest.main()

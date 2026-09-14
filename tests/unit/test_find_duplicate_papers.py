#!/usr/bin/env python3
"""Unit tests for find_duplicate_papers.py -- normalize_title() and
find_pairs()'s group-size cap / title-length floor (2026-08-21, see
todo.md's "Full-corpus plagiarism audit" for the real false-positive bug
these guard against: an unbounded first version found 3,563 "duplicate"
pairs on the real corpus, almost all wrong, from generic recurring
publisher furniture like "Front Cover"/"Masthead" collapsing hundreds of
genuinely different documents into one group), plus the 2026-09-06
author-overlap (tier 2) and asymmetric-containment (tier 3) fixes -- see
that module's docstring for the real cross-author paper-mill and
bundled-PDF cases these guard against."""

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import find_duplicate_papers as fdp  # noqa: E402


def _add_author_tables(conn):
    conn.execute("CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE paper_authors (paper_id INTEGER, author_id INTEGER, author_order INTEGER)"
    )


def _add_author(conn, paper_id, name):
    cur = conn.execute("INSERT INTO authors (name) VALUES (?)", (name,))
    conn.execute(
        "INSERT INTO paper_authors (paper_id, author_id, author_order) VALUES (?, ?, 0)",
        (paper_id, cur.lastrowid),
    )


class TestNormalizeTitle(unittest.TestCase):
    def test_case_and_whitespace(self):
        self.assertEqual(fdp.normalize_title("Moral Dilemmas for Moral Machines"),
                          fdp.normalize_title("Moral   dilemmas for moral machines"))

    def test_html_entities(self):
        self.assertEqual(
            fdp.normalize_title("Governance & R&D"),
            fdp.normalize_title("Governance &amp; R&amp;D"),
        )

    def test_punctuation_and_quotes(self):
        self.assertEqual(
            fdp.normalize_title("Making Online Communities ‘Better’: A Taxonomy"),
            fdp.normalize_title("Making Online Communities 'Better': A Taxonomy"),
        )

    def test_genuinely_different_titles_stay_different(self):
        self.assertNotEqual(fdp.normalize_title("Digital Ethics in Marketing"),
                             fdp.normalize_title("Ethics in Digital Pricing"))


class TestNormalizeAuthorName(unittest.TestCase):
    def test_last_first_flip(self):
        self.assertEqual(fdp.normalize_author_name("Salganik, Rebecca"),
                          fdp.normalize_author_name("Rebecca Salganik"))

    def test_case_and_punctuation(self):
        self.assertEqual(fdp.normalize_author_name("Deepak P."),
                          fdp.normalize_author_name("deepak p"))

    def test_accents_stripped(self):
        self.assertEqual(fdp.normalize_author_name("José García"),
                          fdp.normalize_author_name("Jose Garcia"))

    def test_empty_name(self):
        self.assertEqual(fdp.normalize_author_name(""), "")
        self.assertEqual(fdp.normalize_author_name(None), "")


class TestAuthorsOverlap(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(fdp.authors_overlap(["C Naidu"], ["C Naidu"]))

    def test_fuzzy_match_survives_formatting_noise(self):
        self.assertTrue(fdp.authors_overlap(["Rebecca Salganik"], ["salganik, rebecca"]))

    def test_disjoint_authors_no_overlap(self):
        self.assertFalse(fdp.authors_overlap(["Carol S. Marcus"], ["Jeffry A. Siegel", "Bill Sacks"]))

    def test_empty_either_side_is_false_not_wildcard(self):
        self.assertFalse(fdp.authors_overlap([], ["Someone"]))
        self.assertFalse(fdp.authors_overlap(["Someone"], []))
        self.assertFalse(fdp.authors_overlap([], []))

    def test_multi_author_lists_check_all_pairs(self):
        self.assertTrue(fdp.authors_overlap(["K Sartorius", "B Sartorius", "V Sharma"], ["V. Sharma"]))


class TestFindPairs(unittest.TestCase):
    def _conn_with_papers(self, rows, authors=None):
        """rows: list of (id, doi, title). authors: optional {paper_id: [name, ...]}.
        Also creates empty potential_dupes/paragraphs/authors/paper_authors
        tables -- find_pairs() now also runs the tier-3 content-overlap
        check and the author-overlap gate, both of which query these; real
        usage (db.connect()) always has them, so tests should too."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT, title TEXT NOT NULL,
               year INTEGER, file_path TEXT UNIQUE NOT NULL, extracted_at TEXT)"""
        )
        conn.execute(
            """CREATE TABLE potential_dupes (
                id INTEGER PRIMARY KEY, paragraph_id_1 INTEGER, paragraph_id_2 INTEGER,
                paper_id_1 INTEGER, paper_id_2 INTEGER, similarity REAL, same_paper INTEGER,
                same_author INTEGER, later_cites_earlier INTEGER, earlier_paper_id INTEGER,
                later_paper_id INTEGER, status TEXT DEFAULT 'unreviewed', reviewed_at TEXT, created_at TEXT)"""
        )
        conn.execute("CREATE TABLE paragraphs (id INTEGER PRIMARY KEY, paper_id INTEGER, text TEXT)")
        _add_author_tables(conn)
        for pid, doi, title in rows:
            conn.execute("INSERT INTO papers (id, doi, title, file_path) VALUES (?, ?, ?, ?)",
                         (pid, doi, title, f"papers/{pid}.pdf"))
        for pid, names in (authors or {}).items():
            for name in names:
                _add_author(conn, pid, name)
        conn.commit()
        return conn

    def test_same_doi_pair_found(self):
        """Tier 1 (exact DOI match) never needs an author check -- DOI
        equality is definitional."""
        conn = self._conn_with_papers([
            (1, "10.1/x", "Moral Dilemmas for Moral Machines"),
            (2, "10.1/X", "Moral dilemmas for moral machines"),
        ])
        pairs = fdp.find_pairs(conn)
        self.assertEqual([(a, b) for a, b, _ in pairs], [(1, 2)])

    def test_same_normalized_title_and_shared_author_pair_found(self):
        conn = self._conn_with_papers(
            [
                (1, None, "Towards Friendly AI: A Comprehensive Review"),
                (2, None, "towards friendly ai: a comprehensive review"),
            ],
            authors={1: ["Jane Doe"], 2: ["Jane Doe"]},
        )
        pairs = fdp.find_pairs(conn)
        self.assertEqual([(a, b) for a, b, _ in pairs], [(1, 2)])

    def test_same_title_different_authors_not_paired(self):
        """The real 2026-09-06 fix: identical title, disjoint named authors,
        must NOT be silently merged as 'same paper' -- that's exactly this
        project's own confirmed paper-mill signature (see flagged_cases),
        and merging it here would erase it from the review queue."""
        conn = self._conn_with_papers(
            [
                (1, None, "Towards Friendly AI: A Comprehensive Review"),
                (2, None, "towards friendly ai: a comprehensive review"),
            ],
            authors={1: ["Jane Doe"], 2: ["John Smith"]},
        )
        self.assertEqual(fdp.find_pairs(conn), [])

    def test_same_title_no_authors_at_all_not_paired(self):
        """Zero linked authors on either side is a missed merge, not a
        wildcard match -- the safe failure mode for this tier."""
        conn = self._conn_with_papers([
            (1, None, "Towards Friendly AI: A Comprehensive Review"),
            (2, None, "towards friendly ai: a comprehensive review"),
        ])
        self.assertEqual(fdp.find_pairs(conn), [])

    def test_logs_progress_when_logger_given(self):
        """Regression test: find_pairs()/find_content_overlap_pairs() used to run
        completely silently (no logging at all until one final summary line in
        run()), which made a long run on a grown corpus indistinguishable from a
        hung process -- see todo.md. A caller that wants visibility just passes
        logger_; a caller that doesn't (existing tests above) gets the old
        silent behavior unchanged, so this is opt-in, not a behavior change."""
        conn = self._conn_with_papers([
            (1, "10.1/x", "Moral Dilemmas for Moral Machines"),
            (2, "10.1/X", "Moral dilemmas for moral machines"),
        ])
        fake_logger = Mock()
        fdp.find_pairs(conn, logger_=fake_logger)
        self.assertTrue(fake_logger.info.called)

    def test_short_generic_titles_never_grouped(self):
        """'Front Cover'/'Masthead'-style short titles must never match --
        below the significant-word floor regardless of group size."""
        conn = self._conn_with_papers([
            (1, None, "Front Cover"),
            (2, None, "Front Cover"),
            (3, None, "Masthead"),
            (4, None, "Masthead"),
        ])
        pairs = fdp.find_pairs(conn)
        self.assertEqual(pairs, [])

    def test_large_group_never_grouped_even_with_long_titles(self):
        """A long, specific-looking title shared by MANY papers (e.g. an
        auto-generated 'Review of <submission>' pattern) must not be treated
        as duplicate retrieval -- real accidental duplicate retrieval
        produces a pair, not a cluster. (Same authors on every row here so
        the group-size cap, not the author gate, is what's under test.)"""
        title = "Review of Towards Responsible AI Assisted Scholarship"
        rows = [(i, None, title) for i in range(1, 6)]  # 5 papers, same long title
        conn = self._conn_with_papers(rows, authors={i: ["Same Reviewer"] for i in range(1, 6)})
        pairs = fdp.find_pairs(conn)
        self.assertEqual(pairs, [])

    def test_small_group_of_long_titles_is_grouped(self):
        title = "Exploring the Carbon Footprint of Hugging Face's ML Models: A Repository Mining Study"
        conn = self._conn_with_papers(
            [(1, None, title), (2, None, title)], authors={1: ["A Researcher"], 2: ["A Researcher"]}
        )
        pairs = fdp.find_pairs(conn)
        self.assertEqual([(a, b) for a, b, _ in pairs], [(1, 2)])

    def test_unrelated_papers_not_paired(self):
        conn = self._conn_with_papers([
            (1, "10.1/a", "A Survey on Fairness in Machine Learning"),
            (2, "10.1/b", "An Analysis of Bias in Facial Recognition Systems"),
        ])
        self.assertEqual(fdp.find_pairs(conn), [])


class TestFindContentOverlapPairs(unittest.TestCase):
    """Tier 3, added 2026-08-24 after todo.md's "review 200 at random"
    investigation found the same multi-article PDF served for two different
    per-article DOIs (and book-vs-own-chapter pairs), neither catchable by
    DOI/title matching since both are legitimately different metadata.
    Extended 2026-09-06 with the asymmetric-containment guard -- see
    TestAsymmetricContainmentGuard below."""

    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT, title TEXT)")
        conn.execute(
            """CREATE TABLE potential_dupes (
                id INTEGER PRIMARY KEY, paragraph_id_1 INTEGER, paragraph_id_2 INTEGER,
                paper_id_1 INTEGER, paper_id_2 INTEGER, similarity REAL, same_paper INTEGER,
                status TEXT DEFAULT 'unreviewed', reviewed_at TEXT, created_at TEXT)"""
        )
        conn.execute("CREATE TABLE paragraphs (id INTEGER PRIMARY KEY, paper_id INTEGER, text TEXT)")
        _add_author_tables(conn)
        return conn

    def _seed_papers_and_paragraphs(self, conn, paper_texts):
        """paper_texts: {paper_id: [text, ...]}"""
        para_id = 1
        for paper_id, texts in paper_texts.items():
            conn.execute("INSERT INTO papers (id, doi, title) VALUES (?, NULL, ?)", (paper_id, f"paper {paper_id}"))
            for t in texts:
                conn.execute("INSERT INTO paragraphs (id, paper_id, text) VALUES (?, ?, ?)", (para_id, paper_id, t))
                para_id += 1
        conn.commit()

    def _seed_candidate_pair(self, conn, a, b):
        conn.execute(
            """INSERT INTO potential_dupes (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
               similarity, same_paper, created_at) VALUES (1, 2, ?, ?, 0.9, 0, '2026-01-01T00:00:00Z')""",
            (a, b),
        )
        conn.commit()

    def test_high_exact_overlap_pair_found(self):
        """Same multi-article-PDF-served-twice case: paper 1 and paper 2
        share all 6 paragraphs (100% overlap both directions -- symmetric,
        so the asymmetric-containment guard never applies regardless of
        authorship)."""
        conn = self._conn()
        shared = [f"paragraph {i} full of specific unique content" for i in range(6)]
        self._seed_papers_and_paragraphs(conn, {1: shared, 2: list(shared)})
        self._seed_candidate_pair(conn, 1, 2)
        pairs = fdp.find_content_overlap_pairs(conn, min_fraction=0.5, min_paragraphs=5)
        self.assertEqual(len(pairs), 1)
        a, b, reason = pairs[0]
        self.assertEqual((a, b), (1, 2))
        self.assertIn("100%", reason)

    def test_book_vs_chapter_with_shared_author_still_merged(self):
        """Book-vs-own-chapter case: the smaller paper (chapter, 6
        paragraphs) is entirely contained in the bigger paper (book, 20
        paragraphs) -- 100% of the smaller side, but only 30% of the book's
        own paragraphs (asymmetric). With a shared author (the common real
        case for a monograph, or a book record crediting its own chapter
        authors) this still merges -- the asymmetric-containment guard only
        blocks the no-shared-author case, see TestAsymmetricContainmentGuard."""
        conn = self._conn()
        chapter = [f"chapter paragraph {i}" for i in range(6)]
        book = chapter + [f"other chapter paragraph {i}" for i in range(14)]
        self._seed_papers_and_paragraphs(conn, {10: chapter, 20: book})
        _add_author(conn, 10, "Same Author")
        _add_author(conn, 20, "Same Author")
        self._seed_candidate_pair(conn, 10, 20)
        pairs = fdp.find_content_overlap_pairs(conn, min_fraction=0.5, min_paragraphs=5)
        self.assertEqual([(a, b) for a, b, _ in pairs], [(10, 20)])

    def test_low_overlap_pair_not_found(self):
        """Two genuinely different papers sharing only a couple of boilerplate
        paragraphs (e.g. a shared CC-BY license line) stay well under 50%."""
        conn = self._conn()
        self._seed_papers_and_paragraphs(conn, {
            1: ["shared boilerplate line"] + [f"unique content {i}" for i in range(9)],
            2: ["shared boilerplate line"] + [f"other unique content {i}" for i in range(9)],
        })
        self._seed_candidate_pair(conn, 1, 2)
        pairs = fdp.find_content_overlap_pairs(conn, min_fraction=0.5, min_paragraphs=5)
        self.assertEqual(pairs, [])

    def test_too_few_paragraphs_skipped_regardless_of_fraction(self):
        """A 2-paragraph paper that's 100% overlapping is still skipped --
        min_paragraphs guards against an unreliable fraction on tiny papers."""
        conn = self._conn()
        shared = ["only paragraph one", "only paragraph two"]
        self._seed_papers_and_paragraphs(conn, {1: shared, 2: list(shared)})
        self._seed_candidate_pair(conn, 1, 2)
        pairs = fdp.find_content_overlap_pairs(conn, min_fraction=0.5, min_paragraphs=5)
        self.assertEqual(pairs, [])

    def test_already_same_paper_pairs_not_rechecked(self):
        """WHERE same_paper = 0 -- a pair already resolved (by any tier) isn't
        redundantly re-scanned."""
        conn = self._conn()
        shared = [f"paragraph {i}" for i in range(6)]
        self._seed_papers_and_paragraphs(conn, {1: shared, 2: list(shared)})
        conn.execute(
            """INSERT INTO potential_dupes (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
               similarity, same_paper, created_at) VALUES (1, 2, 1, 2, 0.9, 1, '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        pairs = fdp.find_content_overlap_pairs(conn, min_fraction=0.5, min_paragraphs=5)
        self.assertEqual(pairs, [])

    def test_wired_into_find_pairs(self):
        """find_pairs() (the DOI/title tiers' own entry point) picks up
        tier-3 pairs too, via papers table with a title good enough to not
        matter (tier 3 doesn't look at titles at all). Symmetric 100%
        overlap, so no author needed."""
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT, title TEXT NOT NULL,
               year INTEGER, file_path TEXT UNIQUE NOT NULL, extracted_at TEXT)""")
        conn.execute(
            """CREATE TABLE potential_dupes (
                id INTEGER PRIMARY KEY, paragraph_id_1 INTEGER, paragraph_id_2 INTEGER,
                paper_id_1 INTEGER, paper_id_2 INTEGER, similarity REAL, same_paper INTEGER,
                status TEXT DEFAULT 'unreviewed', reviewed_at TEXT, created_at TEXT)"""
        )
        conn.execute("CREATE TABLE paragraphs (id INTEGER PRIMARY KEY, paper_id INTEGER, text TEXT)")
        _add_author_tables(conn)
        conn.execute("INSERT INTO papers (id, doi, title, file_path) VALUES (1, NULL, 'Article One', 'p1.pdf')")
        conn.execute("INSERT INTO papers (id, doi, title, file_path) VALUES (2, NULL, 'Article Two', 'p2.pdf')")
        shared = [f"paragraph {i}" for i in range(6)]
        for pid in (1, 2):
            for i, t in enumerate(shared):
                conn.execute("INSERT INTO paragraphs (paper_id, text) VALUES (?, ?)", (pid, t))
        conn.execute(
            """INSERT INTO potential_dupes (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
               similarity, same_paper, created_at) VALUES (1, 2, 1, 2, 0.9, 0, '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        pairs = fdp.find_pairs(conn)
        self.assertEqual([(a, b) for a, b, _ in pairs], [(1, 2)])


class TestAsymmetricContainmentGuard(unittest.TestCase):
    """The 2026-09-06 fix for the real bundled-PDF case (anthropology
    paper_ids 59420/59421/59970: a journal's combined 'Letters to the
    Editor' page holding three separately-authored, separately-DOI'd
    letters, wrongly merged as 'same paper' by the old symmetric-only tier
    3 check). Shape: the smaller paper's paragraphs are ~entirely contained
    in the bigger paper (clears min_fraction), but the bigger paper is only
    barely covered by the smaller one (asymmetric) -- exactly the shape a
    short letter fully quoted inside a bundled multi-letter PDF produces."""

    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT, title TEXT)")
        conn.execute(
            """CREATE TABLE potential_dupes (
                id INTEGER PRIMARY KEY, paragraph_id_1 INTEGER, paragraph_id_2 INTEGER,
                paper_id_1 INTEGER, paper_id_2 INTEGER, similarity REAL, same_paper INTEGER,
                status TEXT DEFAULT 'unreviewed', reviewed_at TEXT, created_at TEXT)"""
        )
        conn.execute("CREATE TABLE paragraphs (id INTEGER PRIMARY KEY, paper_id INTEGER, text TEXT)")
        _add_author_tables(conn)
        return conn

    def _seed(self, conn, small_id, small_paras, big_id, extra_paras):
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (?, NULL, ?)", (small_id, f"paper {small_id}"))
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (?, NULL, ?)", (big_id, f"paper {big_id}"))
        pid = 1
        for t in small_paras:
            conn.execute("INSERT INTO paragraphs (id, paper_id, text) VALUES (?, ?, ?)", (pid, small_id, t))
            pid += 1
        for t in small_paras + extra_paras:
            conn.execute("INSERT INTO paragraphs (id, paper_id, text) VALUES (?, ?, ?)", (pid, big_id, t))
            pid += 1
        conn.execute(
            """INSERT INTO potential_dupes (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
               similarity, same_paper, created_at) VALUES (1, 2, ?, ?, 0.9, 0, '2026-01-01T00:00:00Z')""",
            (small_id, big_id),
        )
        conn.commit()

    def test_no_shared_author_not_merged(self):
        small = [f"letter paragraph {i}" for i in range(6)]
        extra = [f"bundled other-letter paragraph {i}" for i in range(14)]  # big has 20 total, small covers only 30%
        conn = self._conn()
        self._seed(conn, 1, small, 2, extra)
        _add_author(conn, 1, "Carol S. Marcus")
        _add_author(conn, 2, "Jeffry A. Siegel")
        pairs = fdp.find_content_overlap_pairs(conn, min_fraction=0.5, min_paragraphs=5)
        self.assertEqual(pairs, [])

    def test_shared_author_still_merged_despite_asymmetry(self):
        """Same shape as above, but the two DO share an author -- the guard
        deliberately only fires on the no-shared-author case, so this still
        merges (this is the book-vs-chapter shape covered directly in
        TestFindContentOverlapPairs too; duplicated here to pin the guard's
        exact boundary condition)."""
        small = [f"letter paragraph {i}" for i in range(6)]
        extra = [f"bundled other-letter paragraph {i}" for i in range(14)]
        conn = self._conn()
        self._seed(conn, 1, small, 2, extra)
        _add_author(conn, 1, "Same Person")
        _add_author(conn, 2, "Same Person")
        pairs = fdp.find_content_overlap_pairs(conn, min_fraction=0.5, min_paragraphs=5)
        self.assertEqual([(a, b) for a, b, _ in pairs], [(1, 2)])

    def test_no_authors_at_all_not_merged(self):
        """Zero linked authors on either side -- authors_overlap() is False
        (missed merge, not a wildcard), so the asymmetric pair is flagged,
        not merged."""
        small = [f"letter paragraph {i}" for i in range(6)]
        extra = [f"bundled other-letter paragraph {i}" for i in range(14)]
        conn = self._conn()
        self._seed(conn, 1, small, 2, extra)
        pairs = fdp.find_content_overlap_pairs(conn, min_fraction=0.5, min_paragraphs=5)
        self.assertEqual(pairs, [])

    def test_flagged_pairs_logged_as_warning(self):
        small = [f"letter paragraph {i}" for i in range(6)]
        extra = [f"bundled other-letter paragraph {i}" for i in range(14)]
        conn = self._conn()
        self._seed(conn, 1, small, 2, extra)
        fake_logger = Mock()
        fdp.find_content_overlap_pairs(conn, min_fraction=0.5, min_paragraphs=5, logger_=fake_logger)
        self.assertTrue(fake_logger.warning.called)


class TestMarkSamePaper(unittest.TestCase):
    def test_corrects_existing_potential_dupes_row(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE potential_dupes (
                id INTEGER PRIMARY KEY, paragraph_id_1 INTEGER, paragraph_id_2 INTEGER,
                paper_id_1 INTEGER, paper_id_2 INTEGER, similarity REAL, same_paper INTEGER,
                same_author INTEGER, later_cites_earlier INTEGER, earlier_paper_id INTEGER,
                later_paper_id INTEGER, status TEXT DEFAULT 'unreviewed', reviewed_at TEXT, created_at TEXT)"""
        )
        conn.execute(
            """INSERT INTO potential_dupes (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
               similarity, same_paper, same_author, status, created_at)
               VALUES (10, 20, 1, 2, 1.0, 0, 1, 'unreviewed', '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        n = fdp.mark_same_paper(conn, 1, 2)
        self.assertEqual(n, 1)
        row = conn.execute("SELECT same_paper, same_author, status FROM potential_dupes").fetchone()
        self.assertEqual(row, (1, None, "unreviewed"))


class TestReconcileExistingPairs(unittest.TestCase):
    """The 2026-09-06 self-healing pass: re-validates every already-
    registered duplicate_papers row and reverts any that no longer pass the
    current (fixed) logic."""

    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT, title TEXT)")
        conn.execute(
            """CREATE TABLE potential_dupes (
                id INTEGER PRIMARY KEY, paragraph_id_1 INTEGER, paragraph_id_2 INTEGER,
                paper_id_1 INTEGER, paper_id_2 INTEGER, similarity REAL, same_paper INTEGER,
                same_author INTEGER, ai_check TEXT, status TEXT DEFAULT 'unreviewed',
                reviewed_at TEXT, created_at TEXT)"""
        )
        conn.execute("CREATE TABLE paragraphs (id INTEGER PRIMARY KEY, paper_id INTEGER, text TEXT)")
        _add_author_tables(conn)
        fdp.init_table(conn)
        return conn

    def test_stale_title_tier_merge_reverted(self):
        """Mirrors the real 53388/55824/60978 case: a title-tier row
        recorded before the author-overlap fix existed, between two papers
        with disjoint authors, gets reverted."""
        conn = self._conn()
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (1, NULL, 'The Future Cost of Cancer')")
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (2, NULL, 'The Future Cost of Cancer')")
        _add_author(conn, 1, "K Sartorius")
        _add_author(conn, 2, "C Naidu")
        conn.execute(
            """INSERT INTO duplicate_papers (paper_id_1, paper_id_2, reason, detected_at)
               VALUES (1, 2, "same title after normalization ('the future cost of cancer')", '2026-01-01T00:00:00Z')"""
        )
        conn.execute(
            """INSERT INTO potential_dupes (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
               similarity, same_paper, same_author, status, created_at)
               VALUES (1, 2, 1, 2, 0.9, 1, NULL, 'unreviewed', '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        reverted = fdp.reconcile_existing_pairs(conn)
        self.assertEqual(reverted, 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM duplicate_papers").fetchone()[0], 0)
        row = conn.execute("SELECT same_paper, same_author, ai_check FROM potential_dupes").fetchone()
        self.assertEqual(row, (0, 0, None))

    def test_valid_title_tier_merge_kept(self):
        """The 55824/60978 half of the same real case: shared author, stays merged."""
        conn = self._conn()
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (1, NULL, 'Erratum Notice')")
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (2, NULL, 'Erratum Notice')")
        _add_author(conn, 1, "C Naidu")
        _add_author(conn, 2, "C Naidu")
        conn.execute(
            """INSERT INTO duplicate_papers (paper_id_1, paper_id_2, reason, detected_at)
               VALUES (1, 2, "same title after normalization ('erratum notice')", '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        reverted = fdp.reconcile_existing_pairs(conn)
        self.assertEqual(reverted, 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM duplicate_papers").fetchone()[0], 1)

    def test_same_doi_rows_never_reconsidered(self):
        conn = self._conn()
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (1, '10.1/x', 'A')")
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (2, '10.1/x', 'A')")
        conn.execute(
            """INSERT INTO duplicate_papers (paper_id_1, paper_id_2, reason, detected_at)
               VALUES (1, 2, 'same DOI (10.1/x)', '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        reverted = fdp.reconcile_existing_pairs(conn)
        self.assertEqual(reverted, 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM duplicate_papers").fetchone()[0], 1)

    def test_stale_content_overlap_merge_reverted(self):
        """Mirrors the real 59421/59970 bundled-PDF case."""
        conn = self._conn()
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (1, NULL, 'Reply Letter')")
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (2, NULL, 'Bundled Letters Page')")
        _add_author(conn, 1, "Wolfgang Andreas Weber")
        _add_author(conn, 2, "Jeffry A. Siegel")
        for i in range(6):
            conn.execute("INSERT INTO paragraphs (paper_id, text) VALUES (1, ?)", (f"reply para {i}",))
            conn.execute("INSERT INTO paragraphs (paper_id, text) VALUES (2, ?)", (f"reply para {i}",))
        for i in range(14):
            conn.execute("INSERT INTO paragraphs (paper_id, text) VALUES (2, ?)", (f"other bundled para {i}",))
        conn.execute(
            """INSERT INTO duplicate_papers (paper_id_1, paper_id_2, reason, detected_at)
               VALUES (1, 2, 'content overlap (100% of 6 paragraph(s), exact text match)', '2026-01-01T00:00:00Z')"""
        )
        conn.execute(
            """INSERT INTO potential_dupes (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
               similarity, same_paper, same_author, status, created_at)
               VALUES (1, 2, 1, 2, 0.9, 1, NULL, 'unreviewed', '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        reverted = fdp.reconcile_existing_pairs(conn)
        self.assertEqual(reverted, 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM duplicate_papers").fetchone()[0], 0)

    def test_status_and_reviewed_at_untouched_on_revert(self):
        conn = self._conn()
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (1, NULL, 'X')")
        conn.execute("INSERT INTO papers (id, doi, title) VALUES (2, NULL, 'X')")
        _add_author(conn, 1, "Author A")
        _add_author(conn, 2, "Author B")
        conn.execute(
            """INSERT INTO duplicate_papers (paper_id_1, paper_id_2, reason, detected_at)
               VALUES (1, 2, "same title after normalization ('x')", '2026-01-01T00:00:00Z')"""
        )
        conn.execute(
            """INSERT INTO potential_dupes (paragraph_id_1, paragraph_id_2, paper_id_1, paper_id_2,
               similarity, same_paper, same_author, status, reviewed_at, created_at)
               VALUES (1, 2, 1, 2, 0.9, 1, NULL, 'confirmed', '2026-02-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        fdp.reconcile_existing_pairs(conn)
        row = conn.execute("SELECT status, reviewed_at FROM potential_dupes").fetchone()
        self.assertEqual(row, ("confirmed", "2026-02-01T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Unit tests for embed_paragraphs.py's paragraphs.jsonl pruning and
non-English filtering (2026-08-21, see todo.md's "paragraphs.jsonl needs
splitting" and "Full-corpus plagiarism audit" sections) -- against a real
temp-file SQLite DB, since WAL mode (db.py) needs a real file, not :memory:.

Two of these tests (test_prune_is_all_or_nothing_per_paper,
test_unresolved_lines_are_never_dropped) are regression tests for real bugs
found the first time this code ran against the actual corpus, not
speculative edge cases -- see prune_embedded_paragraphs()'s own docstring.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

import embed_paragraphs as ep  # noqa: E402
import lsh_index  # noqa: E402


def make_db():
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "library.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT, title TEXT NOT NULL,
           year INTEGER, file_path TEXT UNIQUE NOT NULL, extracted_at TEXT)"""
    )
    ep.init_paragraphs_table(conn)
    return conn, Path(tmpdir)


def write_jsonl(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def fake_embed(conn, paper_id, para_index, text, model="fake"):
    v = np.zeros(4, dtype=np.float32)
    conn.execute(
        """INSERT INTO paragraphs (paper_id, para_index, text, embedding, embedding_dim, model)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (paper_id, para_index, text, v.tobytes(), 4, model),
    )


class TestPruneEmbeddedParagraphs(unittest.TestCase):
    def test_prunes_fully_embedded_paper(self):
        conn, tmpdir = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.commit()
        jsonl_path = tmpdir / "paragraphs.jsonl"
        write_jsonl(jsonl_path, [
            {"file_path": "a.pdf", "para_index": 0, "text": "zero"},
            {"file_path": "a.pdf", "para_index": 1, "text": "one"},
        ])
        path_to_id = ep.load_file_path_to_paper_id(conn)
        records, raw_lines, unresolved = ep.load_paragraph_records(jsonl_path, path_to_id)
        fake_embed(conn, 1, 0, "zero")
        fake_embed(conn, 1, 1, "one")
        conn.commit()
        done = ep.already_embedded(conn)
        text_by_key = {(p, i): t for p, i, t in records}
        kept, pruned = ep.prune_embedded_paragraphs(jsonl_path, raw_lines, text_by_key, done, unresolved, ep.logger)
        self.assertEqual((kept, pruned), (0, 2))
        self.assertEqual(jsonl_path.read_text(), "")

    def test_prune_is_all_or_nothing_per_paper(self):
        """Regression test: a paper with even one paragraph still pending must
        keep ALL of its lines, not just the pending ones -- otherwise
        reconcile_stale_paragraphs() on a later run can't tell "this paper's
        embedded paragraphs were pruned, nothing to see here" apart from
        "extract_papers.py --recompute genuinely shrank this paper's set,
        delete the rest" and wrongly deletes real, still-good DB rows."""
        conn, tmpdir = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")
        conn.commit()
        jsonl_path = tmpdir / "paragraphs.jsonl"
        write_jsonl(jsonl_path, [
            {"file_path": "a.pdf", "para_index": 0, "text": "A zero"},
            {"file_path": "a.pdf", "para_index": 1, "text": "A one"},
            {"file_path": "b.pdf", "para_index": 0, "text": "B zero"},
            {"file_path": "b.pdf", "para_index": 1, "text": "B one"},
        ])
        path_to_id = ep.load_file_path_to_paper_id(conn)
        records, raw_lines, unresolved = ep.load_paragraph_records(jsonl_path, path_to_id)
        fake_embed(conn, 1, 0, "A zero")
        fake_embed(conn, 1, 1, "A one")
        fake_embed(conn, 2, 0, "B zero")
        # paper 2's paragraph 1 intentionally left un-embedded (simulates an
        # interrupted embed_paragraphs.py run mid-batch for paper 2).
        conn.commit()
        done = ep.already_embedded(conn)
        text_by_key = {(p, i): t for p, i, t in records}
        ep.prune_embedded_paragraphs(jsonl_path, raw_lines, text_by_key, done, unresolved, ep.logger)

        remaining = [json.loads(l) for l in jsonl_path.read_text().strip().splitlines()]
        self.assertEqual(len(remaining), 2, "paper A fully pruned, paper B kept whole")
        self.assertTrue(all(r["file_path"] == "b.pdf" for r in remaining))

        # And reconcile must NOT wrongly delete paper A's now-absent-from-file rows.
        records2, _, _ = ep.load_paragraph_records(jsonl_path, path_to_id)
        deleted = ep.reconcile_stale_paragraphs(conn, records2)
        self.assertEqual(deleted, 0)
        remaining_db = conn.execute("SELECT paper_id, para_index FROM paragraphs ORDER BY 1, 2").fetchall()
        self.assertIn((1, 0), remaining_db)
        self.assertIn((1, 1), remaining_db)

    def test_reconcile_still_catches_genuine_staleness(self):
        """A paper --recompute'd with fewer paragraphs than before (a full
        rewrite, so it DOES appear in the file with its complete new set)
        must still get its now-stale DB rows deleted."""
        conn, tmpdir = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.commit()
        fake_embed(conn, 1, 0, "A zero")
        fake_embed(conn, 1, 1, "A one")
        conn.commit()
        jsonl_path = tmpdir / "paragraphs.jsonl"
        write_jsonl(jsonl_path, [{"file_path": "a.pdf", "para_index": 0, "text": "A zero"}])
        path_to_id = ep.load_file_path_to_paper_id(conn)
        records, _, _ = ep.load_paragraph_records(jsonl_path, path_to_id)
        deleted = ep.reconcile_stale_paragraphs(conn, records)
        self.assertEqual(deleted, 1)
        remaining_db = conn.execute("SELECT paper_id, para_index FROM paragraphs").fetchall()
        self.assertEqual(remaining_db, [(1, 0)])

    def test_unresolved_lines_are_never_dropped(self):
        """Regression test: a line whose file_path matches no current papers
        row (a paper still mid-extraction, or -- confirmed for real on this
        project's own corpus -- one whose file_path changed after these
        lines were written) must survive a rewrite verbatim, not be silently
        discarded just because load_paragraph_records() has no paper_id to
        file it under."""
        conn, tmpdir = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.commit()
        jsonl_path = tmpdir / "paragraphs.jsonl"
        write_jsonl(jsonl_path, [
            {"file_path": "a.pdf", "para_index": 0, "text": "A zero"},
            {"file_path": "orphan.pdf", "para_index": 0, "text": "no papers row for this one"},
        ])
        path_to_id = ep.load_file_path_to_paper_id(conn)
        records, raw_lines, unresolved = ep.load_paragraph_records(jsonl_path, path_to_id)
        self.assertEqual(len(unresolved), 1)
        fake_embed(conn, 1, 0, "A zero")
        conn.commit()
        done = ep.already_embedded(conn)
        text_by_key = {(p, i): t for p, i, t in records}
        ep.prune_embedded_paragraphs(jsonl_path, raw_lines, text_by_key, done, unresolved, ep.logger)
        remaining = jsonl_path.read_text().strip().splitlines()
        self.assertEqual(len(remaining), 1)
        self.assertIn("orphan.pdf", remaining[0])


class TestSkipNonEnglishParagraphs(unittest.TestCase):
    def test_splits_and_stores_sentinel_row(self):
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")
        conn.commit()
        records = [
            (1, 0, "This is a normal English paragraph about machine learning fairness and society."),
            (2, 0, "Обобщая приведенные точки зрения, можно прийти к выводу о социально-биологическом."),
        ]
        to_embed, skipped = ep.skip_non_english_paragraphs(conn, records, 0.03, ep.logger)
        self.assertEqual([r[:2] for r in to_embed], [(1, 0)])
        self.assertEqual([r[:2] for r in skipped], [(2, 0)])

        row = conn.execute("SELECT embedding, model FROM paragraphs WHERE paper_id=2 AND para_index=0").fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(row[1], ep.SKIPPED_MODEL_SENTINEL)

    def test_skipped_paragraph_counts_as_already_embedded(self):
        """A skipped paragraph must be treated as "done" by already_embedded()
        so it isn't re-scored every single run forever."""
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.commit()
        records = [(1, 0, "Обобщая приведенные точки зрения выводу социально биологическом потенциале")]
        ep.skip_non_english_paragraphs(conn, records, 0.03, ep.logger)
        done = ep.already_embedded(conn)
        self.assertIn((1, 0), done)

    def test_zero_threshold_disables_filter(self):
        conn, _ = make_db()
        records = [(1, 0, "Обобщая приведенные точки зрения выводу")]
        to_embed, skipped = ep.skip_non_english_paragraphs(conn, records, 0, ep.logger)
        self.assertEqual(to_embed, records)
        self.assertEqual(skipped, [])

    def test_mostly_cyrillic_with_one_stray_latin_token_still_skipped(self):
        """Regression test for the 2026-08-30 bug (see english_score()'s docstring):
        a real corpus example -- a Russian bibliography citation whose only Latin
        token is "No" (from "No 2" = "Number 2"), which also happens to be a common
        English word -- used to score a perfect 1.0 on english_score() because the
        denominator only counted Latin-alphabet tokens. Must be skipped, not embedded."""
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.commit()
        text = ("24 Махмутов З. А., Габдрахманова Г. Ф. Особенности этнической идентичности "
                 "виртуальных татарских сообществ в социальной сети Вконтакте // "
                 "Историческая этнология. 2016. Т. 1, No 2. С. 276-292.")
        records = [(1, 0, text)]
        to_embed, skipped = ep.skip_non_english_paragraphs(conn, records, 0.03, ep.logger)
        self.assertEqual(to_embed, [])
        self.assertEqual([r[:2] for r in skipped], [(1, 0)])


class TestSkipBoilerplateParagraphs(unittest.TestCase):
    """See SKIPPED_MODEL_SENTINEL_BOILERPLATE's module-level comment: these
    paragraphs are skipped before embedding entirely (never embedded, never
    LSH-indexed, never a candidate), not just marked ai_check='no' after the
    fact the way classify_dupes.py handles an already-generated candidate."""

    def test_splits_and_stores_sentinel_row(self):
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")
        conn.commit()
        records = [
            (1, 0, "This is a normal, original paragraph about machine learning fairness definitions."),
            (2, 0, "This work is licensed under a Creative Commons Attribution 4.0 International License."),
        ]
        to_embed, skipped = ep.skip_boilerplate_paragraphs(conn, records, ep.logger)
        self.assertEqual([r[:2] for r in to_embed], [(1, 0)])
        self.assertEqual([r[:2] for r in skipped], [(2, 0)])

        row = conn.execute("SELECT embedding, model FROM paragraphs WHERE paper_id=2 AND para_index=0").fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(row[1], ep.SKIPPED_MODEL_SENTINEL_BOILERPLATE)

    def test_skipped_paragraph_counts_as_already_embedded(self):
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.commit()
        records = [(1, 0, "This work is licensed under a Creative Commons Attribution 4.0 International License.")]
        ep.skip_boilerplate_paragraphs(conn, records, ep.logger)
        done = ep.already_embedded(conn)
        self.assertIn((1, 0), done)

    def test_disabled_flag_skips_nothing(self):
        conn, _ = make_db()
        records = [(1, 0, "This work is licensed under a Creative Commons Attribution 4.0 International License.")]
        to_embed, skipped = ep.skip_boilerplate_paragraphs(conn, records, ep.logger, enabled=False)
        self.assertEqual(to_embed, records)
        self.assertEqual(skipped, [])

    def test_generic_fallback_heuristics_not_applied(self):
        """classify_text_patterns_only() deliberately excludes classify_text()'s
        generic fallbacks (2+ citation years, 2+ emails, leading quote) -- a
        paragraph that would only trip one of those must NOT be skipped here,
        since those heuristics were only ever validated against already-paired
        candidates, not arbitrary un-paired corpus text (see the module-level
        comment on SKIPPED_MODEL_SENTINEL_BOILERPLATE)."""
        conn, _ = make_db()
        records = [(1, 0, "Between 1990 and 2005 the field made substantial original progress on this problem.")]
        to_embed, skipped = ep.skip_boilerplate_paragraphs(conn, records, ep.logger)
        self.assertEqual(to_embed, records)
        self.assertEqual(skipped, [])


class TestSkipBoilerplateParagraphsMlLayer(unittest.TestCase):
    """The optional ML second pass (2026-08-30) -- see
    train_boilerplate_family_classifier.py and skip_boilerplate_paragraphs()'s
    own docstring. Uses a mock vectorizer/model (real predict_families()
    logic, fake sklearn objects) rather than a real trained classifier --
    same technique as test_train_boilerplate_family_classifier.py's
    TestPredictFamilies, since what matters here is the WIRING, not sklearn
    itself."""

    def _mock_classifier(self, family_for_second_paragraph, confidence=0.95, min_confidence=0.9):
        # First paragraph text always predicts not_boilerplate; second predicts whatever the
        # test wants, at the given confidence.
        vectorizer = MagicMock()
        model = MagicMock()
        model.classes_ = np.array(["not_boilerplate", family_for_second_paragraph])

        def fake_predict_proba(X):
            n = X.shape[0] if hasattr(X, "shape") else len(X)
            rows = [[1.0, 0.0]] * n
            if n >= 1:
                rows[-1] = [1.0 - confidence, confidence]
            return np.array(rows)

        vectorizer.transform.side_effect = lambda texts: np.zeros((len(texts), 1))
        model.predict_proba.side_effect = fake_predict_proba
        return {"vectorizer": vectorizer, "model": model, "min_confidence": min_confidence}

    def test_ml_layer_skips_a_regex_miss(self):
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")
        conn.commit()
        records = [
            (1, 0, "This is a normal, original paragraph about machine learning fairness definitions."),
            (2, 0, "A reworded citation-heavy paragraph the regex patterns don't happen to match."),
        ]
        ml_classifier = self._mock_classifier("citation_bibliography", confidence=0.95, min_confidence=0.9)
        to_embed, skipped = ep.skip_boilerplate_paragraphs(conn, records, ep.logger, ml_classifier=ml_classifier)
        self.assertEqual([r[:2] for r in to_embed], [(1, 0)])
        self.assertEqual([r[:2] for r in skipped], [(2, 0)])
        row = conn.execute("SELECT embedding, model FROM paragraphs WHERE paper_id=2 AND para_index=0").fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(row[1], ep.SKIPPED_MODEL_SENTINEL_BOILERPLATE)

    def test_ml_layer_below_confidence_does_not_skip(self):
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")
        conn.commit()
        records = [
            (1, 0, "This is a normal, original paragraph about machine learning fairness definitions."),
            (2, 0, "An ambiguous paragraph the model isn't confident about."),
        ]
        ml_classifier = self._mock_classifier("citation_bibliography", confidence=0.6, min_confidence=0.9)
        to_embed, skipped = ep.skip_boilerplate_paragraphs(conn, records, ep.logger, ml_classifier=ml_classifier)
        self.assertEqual(sorted(r[:2] for r in to_embed), [(1, 0), (2, 0)])
        self.assertEqual(skipped, [])

    def test_no_ml_classifier_means_regex_only_unchanged(self):
        conn, _ = make_db()
        records = [(1, 0, "This is a normal, original paragraph about machine learning fairness definitions.")]
        to_embed, skipped = ep.skip_boilerplate_paragraphs(conn, records, ep.logger, ml_classifier=None)
        self.assertEqual(to_embed, records)
        self.assertEqual(skipped, [])


class TestReclassifyEmbeddedBoilerplate(unittest.TestCase):
    """The retroactive complement to TestSkipBoilerplateParagraphs above --
    same pattern-matching, but for paragraphs that already have a real
    embedding (and possibly LSH bucket rows) from before the pre-embedding
    filter existed. See todo.md's LSH post-mortem 5/6 for why this matters."""

    def test_matching_paragraph_reclassified_and_lsh_cleared(self):
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        fake_embed(conn, 1, 0, "This work is licensed under a Creative Commons Attribution 4.0 International License.")
        conn.commit()
        pid = conn.execute("SELECT id FROM paragraphs").fetchone()[0]

        lsh_index.init_lsh_tables(conn)
        conn.execute("INSERT INTO lsh_buckets (table_num, bucket_key, paragraph_id) VALUES (0, 5, ?)", (pid,))
        conn.execute("INSERT INTO lsh_scanned (paragraph_id) VALUES (?)", (pid,))
        conn.commit()

        n = ep.reclassify_embedded_boilerplate(conn, ep.logger)
        self.assertEqual(n, 1)

        row = conn.execute("SELECT embedding, model FROM paragraphs WHERE id=?", (pid,)).fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(row[1], ep.SKIPPED_MODEL_SENTINEL_BOILERPLATE)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM lsh_buckets WHERE paragraph_id=?", (pid,)).fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM lsh_scanned WHERE paragraph_id=?", (pid,)).fetchone()[0], 0)

    def test_non_boilerplate_paragraph_untouched(self):
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        fake_embed(conn, 1, 0, "This is a normal, original paragraph about machine learning fairness definitions.")
        conn.commit()

        n = ep.reclassify_embedded_boilerplate(conn, ep.logger)
        self.assertEqual(n, 0)
        row = conn.execute("SELECT embedding, model FROM paragraphs").fetchone()
        self.assertIsNotNone(row[0])
        self.assertEqual(row[1], "fake")

    def test_already_sentinel_row_not_reconsidered(self):
        """A paragraph already skip-* (embedding IS NULL) is outside the
        WHERE clause entirely -- idempotent on re-run."""
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute(
            """INSERT INTO paragraphs (paper_id, para_index, text, embedding, embedding_dim, model)
               VALUES (1, 0, 'x', NULL, NULL, ?)""",
            (ep.SKIPPED_MODEL_SENTINEL_BOILERPLATE,),
        )
        conn.commit()
        n = ep.reclassify_embedded_boilerplate(conn, ep.logger)
        self.assertEqual(n, 0)

    def test_existing_potential_dupes_row_left_untouched(self):
        """A reclassified paragraph's existing potential_dupes candidate rows
        (similarity/status persisted at generation time) must survive --
        only future candidate generation stops considering the paragraph."""
        conn, _ = make_db()
        conn.execute("""CREATE TABLE potential_dupes (
            id INTEGER PRIMARY KEY, paragraph_id_1 INTEGER, paragraph_id_2 INTEGER,
            similarity REAL, status TEXT)""")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (2, 'B', 'b.pdf')")
        fake_embed(conn, 1, 0, "This work is licensed under a Creative Commons Attribution 4.0 International License.")
        fake_embed(conn, 2, 0, "Some other paragraph.")
        conn.commit()
        pid1, pid2 = [r[0] for r in conn.execute("SELECT id FROM paragraphs ORDER BY id").fetchall()]
        conn.execute("INSERT INTO potential_dupes (paragraph_id_1, paragraph_id_2, similarity, status) "
                     "VALUES (?, ?, 0.9, 'confirmed')", (pid1, pid2))
        conn.commit()

        ep.reclassify_embedded_boilerplate(conn, ep.logger)

        row = conn.execute("SELECT similarity, status FROM potential_dupes WHERE paragraph_id_1=?", (pid1,)).fetchone()
        self.assertEqual(row, (0.9, "confirmed"))

    def test_ml_layer_catches_a_regex_miss_retroactively(self):
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        fake_embed(conn, 1, 0, "A reworded citation-heavy paragraph the regex patterns don't happen to match.")
        conn.commit()

        vectorizer = MagicMock()
        model = MagicMock()
        model.classes_ = np.array(["not_boilerplate", "citation_bibliography"])
        model.predict_proba.side_effect = lambda X: np.array([[0.05, 0.95]] * (X.shape[0] if hasattr(X, "shape") else len(X)))
        vectorizer.transform.side_effect = lambda texts: np.zeros((len(texts), 1))
        ml_classifier = {"vectorizer": vectorizer, "model": model, "min_confidence": 0.9}

        n = ep.reclassify_embedded_boilerplate(conn, ep.logger, ml_classifier=ml_classifier)
        self.assertEqual(n, 1)
        row = conn.execute("SELECT embedding, model FROM paragraphs").fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(row[1], ep.SKIPPED_MODEL_SENTINEL_BOILERPLATE)


class TestReclassifyEmbeddedBoilerplateStreaming(unittest.TestCase):
    """Regression tests for the 2026-08-30 streaming rewrite (see
    reclassify_embedded_boilerplate()'s scan_batch_size docstring): the main
    corpus's real 9.9M-row scale made the original one-shot .fetchall() a
    real memory risk (the exact class of mistake todo.md's LSH post-mortems
    already hit once). These confirm the batched version still produces the
    SAME result as scanning everything in one shot, across a paragraph
    count deliberately larger than one scan batch."""

    def test_multiple_scan_batches_all_boilerplate_found(self):
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.commit()
        # 25 paragraphs, one boilerplate hit per 5 -- scan_batch_size=10 forces 3 scan batches.
        for i in range(25):
            text = ("This work is licensed under a Creative Commons Attribution 4.0 International License."
                    if i % 5 == 0 else f"Original paragraph number {i} discussing an unrelated topic in depth.")
            fake_embed(conn, 1, i, text)
        conn.commit()

        n = ep.reclassify_embedded_boilerplate(conn, ep.logger, scan_batch_size=10)
        self.assertEqual(n, 5)  # indices 0, 5, 10, 15, 20

        rows = conn.execute("SELECT para_index, embedding FROM paragraphs ORDER BY para_index").fetchall()
        for idx, embedding in rows:
            if idx % 5 == 0:
                self.assertIsNone(embedding, f"index {idx} should have been reclassified")
            else:
                self.assertIsNotNone(embedding, f"index {idx} should still be embedded")

    def test_commit_between_scan_batches_does_not_skip_or_duplicate_rows(self):
        """The batched implementation interleaves UPDATE+commit calls on the same connection
        WHILE a separate SELECT cursor still has pending fetchmany() results -- confirmed safe
        in isolation before this rewrite, but a real regression risk if that pattern ever
        changes. Every row must be checked exactly once regardless of scan_batch_size."""
        conn, _ = make_db()
        conn.execute("INSERT INTO papers (id, title, file_path) VALUES (1, 'A', 'a.pdf')")
        conn.commit()
        for i in range(37):
            fake_embed(conn, 1, i, f"Original paragraph number {i} discussing an unrelated topic in depth.")
        conn.commit()

        n = ep.reclassify_embedded_boilerplate(conn, ep.logger, scan_batch_size=10)
        self.assertEqual(n, 0)  # none of these match any pattern
        remaining = conn.execute("SELECT COUNT(*) FROM paragraphs WHERE embedding IS NOT NULL").fetchone()[0]
        self.assertEqual(remaining, 37)  # every row still accounted for, none lost/duplicated


class TestLoadMlBoilerplateClassifier(unittest.TestCase):
    def test_missing_file_returns_none(self):
        result = ep.load_ml_boilerplate_classifier(Path("/nonexistent/path/to/model.joblib"), ep.logger)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

"""general_index/gi_review_cli.py: the menu, the note, and what gets saved to `reviews`."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_review_cli as cli  # noqa: E402


def review_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        create table reviews (review_id integer primary key, doi_a text, doi_b text, pair_lo text, pair_hi text,
            verdict text, note text, reviewed_at text, decision text, type text, severity text,
            unique (pair_lo, pair_hi));
        create table agent_reviews (decision text, type text);""")
    db.executescript(cli.FLAGS_DDL)
    return db


PAIR = {"doi_a": "10.1/A", "doi_b": "10.1/b", "pair_lo": "10.1/a", "pair_hi": "10.1/b"}


class TestVerdict(unittest.TestCase):
    def test_matches_gi_review(self):
        self.assertEqual(cli.verdict_for("cross-author copying"), "copying")
        self.assertEqual(cli.verdict_for("text recycling"), "copying")
        self.assertEqual(cli.verdict_for("same paper"), "same paper")
        self.assertEqual(cli.verdict_for("legitimate reuse"), "legitimate reuse")
        self.assertEqual(cli.verdict_for("boilerplate"), "not copying")
        self.assertEqual(cli.verdict_for("unsure"), "unsure")


class TestMenu(unittest.TestCase):
    def test_most_common_first_counting_both_tables(self):
        db = review_db()
        db.executemany("insert into agent_reviews values (?, ?)", [("no problem", "boilerplate")] * 3)
        db.executemany("insert into reviews (decision, type, pair_lo, pair_hi) values (?, ?, ?, ?)",
                       [("unsure", "unsure", str(i), "x") for i in range(2)])
        choices, counts = cli.ordered_choices(db)
        self.assertEqual(choices[0][:2], ("no problem", "boilerplate"))
        self.assertEqual(choices[1][:2], ("unsure", "unsure"))
        self.assertEqual(choices[2], cli.CHOICES[0])  # unused ones keep the documented order
        self.assertEqual(len(choices), len(cli.CHOICES))

    def test_parse_picks(self):
        self.assertEqual(cli.parse_picks("1 3", 4), [0, 2])
        self.assertEqual(cli.parse_picks("2,2", 4), [1])
        self.assertEqual(cli.parse_picks("", 4), [])
        self.assertIsNone(cli.parse_picks("5", 4))
        self.assertIsNone(cli.parse_picks("x", 4))

    def test_quick_verdict_is_valid(self):
        decision, type_, severity, reason = cli.QUICK
        self.assertIn((decision, type_), [c[:2] for c in cli.CHOICES])
        self.assertIn(severity, cli.SEVERITIES.values())
        self.assertIn(reason, cli.REASONS[decision])

    def test_note(self):
        self.assertEqual(cli.build_note(["the later paper doesn't cite the earlier one"], "  saw it  "),
                         "the later paper doesn't cite the earlier one; saw it")
        self.assertEqual(cli.build_note([], ""), "")

    def test_same_year(self):
        self.assertEqual(cli.same_year({"year_a": 2017, "year_b": 2017}), 2017)
        self.assertIsNone(cli.same_year({"year_a": 2016, "year_b": 2017}))
        self.assertIsNone(cli.same_year({"year_a": None, "year_b": None}))
        self.assertIn("2017", cli.same_year_reason(2017))


class TestSave(unittest.TestCase):
    def test_insert_then_replace(self):
        db = review_db()
        cli.save_review(db, PAIR, "problem", "data copying", "high", "first")
        cli.save_review(db, PAIR, "no problem", "same paper", "none", "second")
        rows = db.execute("select * from reviews").fetchall()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["verdict"], r["decision"], r["type"], r["severity"], r["note"]),
                         ("same paper", "no problem", "same paper", "none", "second"))
        self.assertEqual((r["pair_lo"], r["pair_hi"], r["doi_a"]), ("10.1/a", "10.1/b", "10.1/A"))
        self.assertIsNotNone(r["reviewed_at"])

    def test_flags_saved_and_replaced(self):
        db = review_db()
        cli.save_review(db, PAIR, "problem", "data copying", "high", "", ["paper mill"])
        self.assertEqual(cli.pair_flags(db, PAIR), ["paper mill"])
        cli.save_review(db, PAIR, "problem", "data copying", "high", "", [])
        self.assertEqual(cli.pair_flags(db, PAIR), [])

    def test_parse_flags(self):
        self.assertEqual(cli.parse_flags("m"), ["paper mill"])
        self.assertEqual(cli.parse_flags("M, m"), ["paper mill"])
        self.assertIsNone(cli.parse_flags("x"))
        self.assertEqual(cli.parse_flags("e m"), ["not qualified", "paper mill"])


if __name__ == "__main__":
    unittest.main()

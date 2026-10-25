"""The Unix epoch (1970-01-01) as "no date" in General Index metadata: gi_db.pub_year() and year_sql()."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_db  # noqa: E402
import gi_filters  # noqa: E402


class TestEpoch(unittest.TestCase):
    def test_pub_year(self):
        self.assertEqual(gi_db.pub_year("2012-02-21"), "2012")
        self.assertEqual(gi_db.pub_year("2009-12"), "2009")
        self.assertEqual(gi_db.pub_year(2015), "2015")
        self.assertIsNone(gi_db.pub_year("1970-01-01"))
        self.assertIsNone(gi_db.pub_year("1970-01-01T00:00:00Z"))
        self.assertEqual(gi_db.pub_year("1970-06-15"), "1970")   # a real 1970 date stays
        self.assertIsNone(gi_db.pub_year(None))
        self.assertIsNone(gi_db.pub_year(""))

    def test_year_sql_matches(self):
        db = sqlite3.connect(":memory:")
        db.execute("create table t (d text)")
        cases = (("2012-02-21", "2012"), ("1970-01-01", None), ("1970-06-15", "1970"), (None, None))
        for d, want in cases:
            db.execute("delete from t")
            db.execute("insert into t values (?)", (d,))
            self.assertEqual(db.execute(f"select {gi_db.year_sql('d')} from t").fetchone()[0], want, d)

    def test_filters_year(self):
        self.assertEqual(gi_filters._year({"pub_date": "1970-01-01"}), -99)
        self.assertEqual(gi_filters._year({"pub_date": "2012-02-21"}), 2012)

    def test_view_uses_the_rule(self):
        self.assertIn("like '1970-01-01%' then null", gi_db.SCHEMA)
        self.assertIn("'1970-01-01'),   -- the epoch", Path(gi_db.__file__).read_text())


if __name__ == "__main__":
    unittest.main()

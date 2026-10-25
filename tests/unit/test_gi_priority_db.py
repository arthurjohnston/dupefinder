"""general_index/gi_priority_db.py: the DOI classification and the TSV copies."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_preprints  # noqa: E402
import gi_priority_db as pdb  # noqa: E402


class TestClassify(unittest.TestCase):
    def test_kinds(self):
        self.assertIsNone(pdb.classify("10.18203/2349-2902.isj20192387"))
        doi, server, pub, group, promotes, venue, reason = pdb.classify("10.2139/ssrn.123")
        self.assertEqual((server, pub), ("SSRN", None))
        _, server, pub, group, promotes, venue, _ = pdb.classify("10.1016/j.cell.2020.01.001")
        self.assertEqual((server, pub, group, promotes, venue), (None, "Elsevier", "core", 1, None))
        _, _, pub, _, promotes, venue, reason = pdb.classify("10.1088/1742-6596/1234/1/012001")
        self.assertEqual((pub, promotes), ("IOP Publishing", 0))
        self.assertTrue(venue and reason)

    def test_every_rank_has_a_label(self):
        for rank in (gi_preprints.PROMOTED, gi_preprints.POSSIBLY_PROMOTED, gi_preprints.ORDINARY,
                     gi_preprints.POSSIBLY, gi_preprints.LATER):
            self.assertIn(rank, pdb.LABELS)


class TestCopyTsv(unittest.TestCase):
    def test_header_and_ragged_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.tsv"
            p.write_text("# comment\nprefix\tserver\n10.1/x\tA\n10.2/y\n", encoding="utf-8")
            db = sqlite3.connect(":memory:")
            self.assertEqual(pdb.copy_tsv(db, "t", p), 2)
            self.assertEqual(db.execute("select * from t order by prefix").fetchall(),
                             [("10.1/x", "A"), ("10.2/y", "")])

    def test_real_lists_load(self):
        db = sqlite3.connect(":memory:")
        self.assertGreater(pdb.copy_tsv(db, "p", gi_preprints.TSV), 50)
        self.assertGreater(pdb.copy_tsv(db, "m", gi_preprints.MAJOR_TSV), 300)


if __name__ == "__main__":
    unittest.main()

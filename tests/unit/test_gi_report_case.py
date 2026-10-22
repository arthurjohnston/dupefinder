"""general_index/gi_report_case.py: the reported-cases record and the site index."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_report_case as gi_report  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gi_pubpeer import fd  # noqa: E402


class TestIndex(unittest.TestCase):
    def test_record_keeps_first_date_and_index_sorts(self):
        with tempfile.TemporaryDirectory() as d:
            gi_report.record(d, "GI-235", fd())
            cases = json.loads((Path(d) / "cases.json").read_text())
            cases["GI-235"]["reported"] = "2026-01-01"
            (Path(d) / "cases.json").write_text(json.dumps(cases))
            gi_report.record(d, "GI-235", fd(pct_later=70.0))
            gi_report.record(d, "GI-80", fd())
            cases = json.loads((Path(d) / "cases.json").read_text())
            self.assertEqual(cases["GI-235"]["reported"], "2026-01-01")
            self.assertEqual(cases["GI-235"]["later"]["pct"], 70.0)
            page = gi_report.write_index(d).read_text()
            self.assertLess(page.index('href="GI-80.html"'), page.index('href="GI-235.html"'))
            self.assertIn("70% / 40%", page)
            self.assertIn('New paper (2015)<br><a href="https://doi.org/10.2/new">doi:10.2/new</a>', page)
            self.assertIn("300 words", page)

    def test_not_reported(self):
        with tempfile.TemporaryDirectory() as d:
            gi_report.record(d, "GI-474", fd(), reported=False)
            cases = json.loads((Path(d) / "cases.json").read_text())
            self.assertIsNone(cases["GI-474"]["reported"])
            self.assertTrue(cases["GI-474"]["published"])
            self.assertIn("not reported", gi_report.write_index(d).read_text())
            gi_report.record(d, "GI-474", fd())   # reported later: it gets a date
            self.assertTrue(json.loads((Path(d) / "cases.json").read_text())["GI-474"]["reported"])
            gi_report.record(d, "GI-474", fd(), reported=False)   # publishing again doesn't undo that
            self.assertTrue(json.loads((Path(d) / "cases.json").read_text())["GI-474"]["reported"])


class TestSeverity(unittest.TestCase):
    def test_only_high_gets_every_email(self):
        import gi_contact
        self.assertEqual(gi_report.audiences("high"), gi_contact.EMAIL_AUDIENCES)
        for sev in ("low", "medium", None):
            self.assertEqual(gi_report.audiences(sev), ("publisher",))

    def test_one_institution_email_each(self):
        import gi_pubpeer
        insts = [{"to": "rio@u.example", "authors": ["A. One"]}, {"to": "", "authors": ["B. Two"]},
                 {"to": "rio@w.example", "authors": ["C. Three"]}]
        f = {**fd(), "case_contacts": {"institution": insts, "authors": {"to": "old@e.example"}}}
        saved = gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name
        gi_report.case_findings = lambda db, row, report_dir: f
        gi_pubpeer.write_page = lambda db, row, out, fd, name: (Path(out) / name, None)
        gi_pubpeer.publisher_page = lambda row, fd: ("<p>page</p>", None)
        gi_pubpeer.case_name = lambda row: "GI-99"
        try:
            d = Path(tempfile.mkdtemp())
            (d / "GI-99").mkdir()
            (d / "GI-99" / "GI-99-email-institution-4.eml").write_text("stale")
            paths = gi_report.write_report(None, {}, report_dir=d, site_dir=None, severity="high")
        finally:
            gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name = saved
        self.assertEqual(sorted(k for k in paths if k.startswith("email institution")),
                         ["email institution", "email institution 2"])
        self.assertIn("To: rio@w.example", paths["email institution 2"].read_text())
        self.assertIn("C. Three, affiliated", paths["email institution 2"].read_text())
        self.assertFalse((d / "GI-99" / "GI-99-email-institution-4.eml").exists())

    def test_review_severity_defaults_to_medium(self):
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        f = {"pair_lo": "10.1/a", "pair_hi": "10.2/b"}
        self.assertEqual(gi_report.review_severity(db, f), "medium")   # no reviews table
        db.execute("create table reviews (pair_lo, pair_hi, severity)")
        self.assertEqual(gi_report.review_severity(db, f), "medium")   # not reviewed
        db.execute("insert into reviews values ('10.1/a', '10.2/b', 'high')")
        self.assertEqual(gi_report.review_severity(db, f), "high")
        db.execute("update reviews set severity = 'none'")
        self.assertEqual(gi_report.review_severity(db, f), "medium")


class TestCompose(unittest.TestCase):
    def test_compose_arg(self):
        import gi_contact
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            page, jpg = d / "GI-1-comparison.html", d / "GI-1-longest.jpg"
            page.write_text("<p>x</p>")
            jpg.write_bytes(b"\xff\xd8\xff\xd9")
            f = fd()
            f["second"] = {**f["second"], "title": "It's new, really", "contact": {"to": "ed@new.example"}}
            eml = d / "GI-1-email-publisher.eml"
            eml.write_bytes(bytes(gi_contact.email_message(f, [page, jpg], sender="me@example.org")))
            arg = gi_report.compose_arg(eml)
            body = d / "compose" / "GI-1-email-publisher.txt"
            self.assertTrue(body.read_text().startswith("Dear Editor,"))
            self.assertIn("subject='Text overlap in It\u2019s new, really (doi:10.2/new)'", arg)
            self.assertIn(f"message='{body}'", arg)
            self.assertIn("from='me@example.org'", arg)
            self.assertIn("to='ed@new.example'", arg)
            self.assertNotIn("cc=", arg)
            self.assertIn(f"attachment='{page.resolve().as_uri()},{jpg.resolve().as_uri()}'", arg)
            jpg.unlink()
            with self.assertRaises(FileNotFoundError):
                gi_report.compose_arg(eml)


if __name__ == "__main__":
    unittest.main()

"""general_index/gi_report_case.py: the reported-cases record and the site index."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_report_case as gi_report  # noqa: E402
import gi_site_index  # noqa: E402
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
            # one table (both reported text cases), the larger share first: GI-235 at 70% before GI-80 at 62%
            self.assertLess(page.index('href="GI-235.html"'), page.index('href="GI-80.html"'))
            self.assertIn("70% / 40%", page)
            self.assertIn('New paper (2015)<br><a href="https://doi.org/10.2/new">doi:10.2/new</a>', page)
            self.assertIn("<th>Words shared</th>", page)
            self.assertIn(">2,480<", page)

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


    def test_groups_data_then_reported_then_not(self):
        with tempfile.TemporaryDirectory() as d:
            gi_report.record(d, "GI-1", fd(pct_later=20.0), reported=False)
            gi_report.record(d, "GI-2", fd(pct_later=30.0))
            gi_report.record(d, "GI-3", dict(fd(pct_later=10.0), type="data copying"), reported=False)
            gi_report.record(d, "GI-4", dict(fd(pct_later=90.0), data_extra=["table 2 copied"]))
            gi_report.record(d, "GI-5", fd(pct_later=95.0), reported=False)
            cases = json.loads((Path(d) / "cases.json").read_text())
            self.assertEqual([gi_site_index.index_group(cases[f"GI-{n}"]) for n in range(1, 6)],
                             ["unreported", "reported", "data", "data", "unreported"])
            page = gi_report.write_index(d).read_text()
            order = [page.index(f'href="GI-{n}.html"') for n in (4, 3, 2, 5, 1)]
            self.assertEqual(order, sorted(order))
            self.assertIn("Copied data (2)", page)
            self.assertLess(page.index("Copied data"), page.index("Reported: copied text"))
            self.assertLess(page.index("Reported: copied text"), page.index("Not reported (2)"))


class TestSiteImages(unittest.TestCase):
    def test_externalize_images(self):
        import base64
        a, b = base64.b64encode(b"jpeg-a").decode(), base64.b64encode(b"jpeg-b").decode()
        page = f'<img src="data:image/jpeg;base64,{a}"><img alt="x" src="data:image/jpeg;base64,{b}">' \
               f'<img src="data:image/jpeg;base64,{a}"><img src="https://example.org/x.jpg">'
        with tempfile.TemporaryDirectory() as d:
            stale = Path(d) / "img" / "GI-7" / "old.jpg"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"old")
            out = gi_report.externalize_images(page, d, "GI-7")
            self.assertNotIn("data:", out)
            self.assertIn('src="https://example.org/x.jpg"', out)
            files = sorted((Path(d) / "img" / "GI-7").iterdir())
            self.assertEqual(sorted(f.read_bytes() for f in files), [b"jpeg-a", b"jpeg-b"])   # the repeat once
            self.assertFalse(stale.exists())
            self.assertEqual(out.count('loading="lazy" src="img/GI-7/'), 3)
            self.assertEqual(gi_report.externalize_images(page, d, "GI-7"), out)   # same names on a re-run
            gi_report.externalize_images("<p>no images</p>", d, "GI-7")
            self.assertFalse((Path(d) / "img" / "GI-7").exists())


class TestSkipped(unittest.TestCase):
    def test_skipped_problems(self):
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""create table fulltext (page_number int, pair_lo text, pair_hi text);
            create table reviews (pair_lo text, pair_hi text, decision text, type text);
            create table review_flags (pair_lo text, pair_hi text, flag text, flagged_at text);""")
        for n, decision, type_ in ((1, "problem", "cross-author copying"), (2, "problem", "data copying"),
                                   (3, "problem", "text recycling"), (4, "no problem", None),
                                   (5, "problem", "cross-author copying"), (6, "problem", "data copying")):
            db.execute("insert into fulltext values (?, ?, ?)", (n, f"a{n}", f"b{n}"))
            db.execute("insert into reviews values (?, ?, ?, ?)", (f"a{n}", f"b{n}", decision, type_))
        db.execute("insert into review_flags values ('a6', 'b6', 'already retracted', '')")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "cases.json").write_text(json.dumps({"GI-05": {}}))
            self.assertEqual([f["page_number"] for f in gi_report.skipped_problems(db, d)], [1, 2])


class TestSeverity(unittest.TestCase):
    def test_only_high_gets_every_email(self):
        import gi_contact
        self.assertEqual(gi_report.audiences("high"), gi_contact.EMAIL_AUDIENCES)
        self.assertEqual(gi_report.audiences("medium"), ("publisher",))   # no share known
        self.assertEqual(gi_report.audiences("medium", {"second": {"pct": 50}}), ("publisher", "institution"))
        self.assertEqual(gi_report.audiences("medium", {"second": {"pct": 49.9}}), ("publisher",))
        for sev in ("low", None):
            self.assertEqual(gi_report.audiences(sev), ("publisher",))
        same = fd(byline="byline overlap: A. One")   # don't write to the authors who reused their own text
        self.assertNotIn("authors", gi_report.audiences("high", same))
        self.assertIn("authors", gi_report.audiences("high", fd()))

    def test_one_email_to_all_institutions(self):
        import gi_pubpeer
        insts = [{"to": "rio@u.example", "authors": ["A. One"]}, {"to": "", "authors": ["B. Two"]},
                 {"to": "rio@w.example", "authors": ["C. Three"]}]
        f = {**fd(), "case_contacts": {"institution": insts, "authors": {"to": "old@e.example"}}}
        saved = gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name
        gi_report.case_findings = lambda db, row, report_dir: f
        gi_pubpeer.write_page = lambda db, row, out, fd, name: (Path(out) / name, None)
        gi_pubpeer.publisher_page = lambda row, fd: ("<p>page</p>", None, None)
        gi_pubpeer.case_name = lambda row: "GI-99"
        try:
            d = Path(tempfile.mkdtemp())
            (d / "GI-99").mkdir()
            (d / "GI-99" / "GI-99-email-institution-4.eml").write_text("stale")
            paths = gi_report.write_report(None, {}, report_dir=d, site_dir=None, severity="high")
        finally:
            gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name = saved
        self.assertEqual(sorted(k for k in paths if k.startswith("email institution")), ["email institution"])
        import email
        import email.policy
        msg = email.message_from_bytes(paths["email institution"].read_bytes(), policy=email.policy.default)
        self.assertEqual(msg["Bcc"], "rio@u.example, rio@w.example")
        self.assertIn("A. One and C. Three, affiliated with your institutions", msg.get_body(("plain",)).get_content())
        self.assertFalse((d / "GI-99" / "GI-99-email-institution-4.eml").exists())

    def test_data_finding_gets_every_email(self):
        import gi_pubpeer
        f = {**fd(), "data_extra": ["Table 1 has the same means."],
             "case_contacts": {"institution": [{"to": "rio@u.example"}], "authors": {"to": "old@e.example"}}}
        saved = gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name
        gi_report.case_findings = lambda db, row, report_dir: f
        gi_pubpeer.write_page = lambda db, row, out, fd, name: (Path(out) / name, None)
        gi_pubpeer.publisher_page = lambda row, fd: ("<p>page</p>", None, None)
        gi_pubpeer.case_name = lambda row: "GI-99"
        try:
            d = Path(tempfile.mkdtemp())
            paths = gi_report.write_report(None, {}, report_dir=d, site_dir=None, severity="low")
        finally:
            gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name = saved
        self.assertTrue({"email publisher", "email institution", "email authors", "email source-publisher"}
                        <= set(paths))

    def test_authors_only(self):
        import email
        import email.policy
        import gi_pubpeer
        base = fd()
        f = {**base, "data_extra": ["Table 1 has the same means."],
             "second": {**base["second"], "journal": "J New", "contact": {"journal": "J New", "to": "ed@new.example"}},
             "case_contacts": {"institution": [{"to": "rio@u.example"}], "authors": {"to": "old@e.example"}}}
        saved = gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name
        gi_report.case_findings = lambda db, row, report_dir: dict(f)
        gi_pubpeer.write_page = lambda *a, **k: self.fail("authors only: no PubPeer page")
        gi_pubpeer.publisher_page = lambda row, fd: ("<p>page</p>", b"\xff\xd8\xff\xd9", None)
        gi_pubpeer.case_name = lambda row: "GI-99"
        try:
            d = Path(tempfile.mkdtemp())
            (d / "GI-99").mkdir()
            (d / "GI-99" / "GI-99-email-publisher.eml").write_text("from a full report before")
            paths = gi_report.write_report(None, {}, report_dir=d, site_dir=None, severity="low", authors_only=True)
            same = {**f, "byline": "byline overlap: A. One"}
            gi_report.case_findings = lambda db, row, report_dir: dict(same)
            none = gi_report.write_report(None, {}, report_dir=Path(tempfile.mkdtemp()), site_dir=None,
                                          severity="low", authors_only=True)
        finally:
            gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name = saved
        self.assertEqual(sorted(k for k in paths if k.startswith("email ")), ["email authors"])
        self.assertNotIn("pubpeer", paths)
        self.assertTrue(paths["image"].exists())
        self.assertFalse((d / "GI-99" / "GI-99-email-publisher.eml").exists())   # no stale draft left to send
        msg = email.message_from_bytes(paths["email authors"].read_bytes(), policy=email.policy.default)
        text = " ".join(msg.get_body(("plain",)).get_content().split())
        self.assertNotIn("I have reported this", text)   # the journal hasn't been told
        self.assertIn("you may want to contact J New yourselves", text)
        self.assertFalse(any(k.startswith("email ") for k in none))   # shared bylines: nobody else to tell
        self.assertIn("no email authors", none)

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
            self.assertIn("subject='Research integrity concern: a paper you published'", arg)
            self.assertIn(f"message='{body}'", arg)
            self.assertIn("from='me@example.org'", arg)
            self.assertIn("to='ed@new.example'", arg)
            self.assertNotIn("cc=", arg)
            self.assertIn(f"attachment='{page.resolve().as_uri()},{jpg.resolve().as_uri()}'", arg)
            f["case_contacts"] = {"institution": [{"to": "rio@u.example"}, {"to": "rio@v.example"}]}
            inst = d / "GI-1-email-institution.eml"
            inst.write_bytes(bytes(gi_contact.email_message(f, [page], audience="institution",
                                                            sender="me@example.org")))
            arg = gi_report.compose_arg(inst)
            self.assertIn("to='me@example.org'", arg)
            self.assertIn("bcc='rio@u.example, rio@v.example'", arg)
            jpg.unlink()
            with self.assertRaises(FileNotFoundError):
                gi_report.compose_arg(eml)


if __name__ == "__main__":
    unittest.main()

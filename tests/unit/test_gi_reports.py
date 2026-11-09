"""general_index/gi_reports.py: sends read from a Sent Mail mbox, the reported-cases tables, the notes fix."""
import json
import sqlite3
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_reports  # noqa: E402

BULK = """# GI-85

## Sent
- 2026-10-28: the user reports every draft sent:
  - publisher: info@biomedres.us (cc angela@biomedres.us), "Research integrity concern: a paper you published"
  - institution-2: no address in the draft (sent some other way, if at all), "Research integrity concern"
- 2026-10-30: a later line, kept

## Questions
- 2026-10-29: not a send:
  - publisher: x@y.z, "should not count"
"""


def raw(subject, date, to="rio@univ.edu", attach=None, body="See the comparison page."):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = "me@x.org", to, subject, date
    m["Message-ID"] = f"<{abs(hash((subject, date)))}@x>"
    m.set_content(body)
    if attach:
        m.add_attachment(b"<html></html>", maintype="text", subtype="html", filename=attach)
    return b"From - Mon Oct 27 10:00:00 2026\n" + m.as_bytes() + b"\n"


class TestAudience(unittest.TestCase):
    def test_subjects(self):
        for subject, want in (
                ("Research integrity concern: a paper you published", "publisher"),
                ("Research integrity concern: results data in a paper you published", "publisher"),
                ("Research integrity concern: a paper by someone associated with your\n institution", "institution"),
                ("Text from a paper you published, reused in another journal", "source-publisher"),
                ("Text from What determines medical students' career", "source-publisher"),
                ("Text from your article What determines", "authors"),
                ("Results data from your article \"X\" in a 2018 article", "authors"),
                ("Text overlap in Effect of SP600125", "publisher"),
                ("Re: Text overlap in Effect of SP600125", "reply"),
                ("Lunch?", "other")):
            self.assertEqual(gi_reports.audience(subject), want, subject)


class TestScan(unittest.TestCase):
    def test_only_recent_messages_naming_a_case(self):
        with tempfile.TemporaryDirectory() as d:
            box = Path(d) / "Sent Mail"
            box.write_bytes(
                raw("Research integrity concern: a paper you published", "Mon, 27 Oct 2026 10:00:00 -0700",
                    attach="GI-85-comparison.html")
                + raw("Text overlap in X", "Mon, 13 Oct 2026 10:00:00 -0700", body="page: .../GI-4.html")
                + raw("Lunch?", "Mon, 27 Oct 2026 11:00:00 -0700")                      # no case
                + raw("Text overlap in Y", "Mon, 01 Sep 2026 10:00:00 -0700", attach="GI-9-comparison.html"))
            got = gi_reports.scan_sent(box)
            self.assertEqual([m["cases"] for m in got], [["GI-85"], ["GI-04"]])
            self.assertEqual(got[0]["to"], "rio@univ.edu")


class TestSyncMarkFix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.rep, self.site = d / "dupe_publisher", d / "site"
        for case, text in (("GI-85", BULK), ("GI-90", BULK), ("GI-12", "## Sent\n- nothing yet\n")):
            (self.rep / case).mkdir(parents=True)
            (self.rep / case / "notes.md").write_text(text)
        self.site.mkdir()
        (self.site / "cases.json").write_text(json.dumps({
            "GI-85": {"reported": None, "published": "2026-10-20"},
            "GI-12": {"reported": "2026-10-13", "published": "2026-10-13"}}))
        self.messages = [{"message_id": "<1>", "sent_at": "2026-10-27T10:00:00-07:00", "to": "rio@univ.edu",
                          "cc": None, "bcc": "a@b.c",
                          "subject": "Research integrity concern: a paper by someone associated with your institution",
                          "cases": ["GI-85"]}]
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("create table fulltext (page_number, pair_lo, pair_hi);"
                              "create table reviews (pair_lo, pair_hi, decision, type, severity, reviewed_at);")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sync_and_mismatches(self):
        sends, cases = gi_reports.sync(self.db, self.messages, self.rep, self.site)
        self.assertEqual([(s["case_name"], s["audience"]) for s in sends], [("GI-85", "institution")])
        by = {c["case_name"]: c for c in cases}
        self.assertEqual(by["GI-85"]["noted_audiences"], "institution,publisher")
        sent_unmarked, marked_unsent, noted_only = gi_reports.mismatches(cases)
        self.assertEqual([c["case_name"] for c in sent_unmarked], ["GI-85"])
        self.assertEqual([c["case_name"] for c in marked_unsent], ["GI-12"])
        self.assertEqual([c["case_name"] for c in noted_only], ["GI-85", "GI-90"])   # publisher never went
        gi_reports.sync(self.db, self.messages, self.rep, self.site)   # rebuilds, doesn't add
        self.assertEqual(self.db.execute("select count(*) from report_sends").fetchone()[0], 1)
        self.assertEqual(self.db.execute("select count(*) from case_report_status").fetchone()[0], 3)

    def test_mark_reported_only_on_site_cases_with_a_send(self):
        _, cases = gi_reports.sync(self.db, self.messages, self.rep, self.site)
        import gi_site_index
        real, calls = gi_site_index.write_index, []
        gi_site_index.write_index = calls.append
        try:
            self.assertEqual(gi_reports.mark_reported(cases, self.site), ["GI-85"])
        finally:
            gi_site_index.write_index = real
        self.assertEqual(json.loads((self.site / "cases.json").read_text())["GI-85"]["reported"], "2026-10-27")
        self.assertEqual(calls, [self.site])

    def test_fix_notes_replaces_only_the_bulk_block(self):
        sends, _ = gi_reports.sync(self.db, self.messages, self.rep, self.site)
        self.assertEqual(gi_reports.fix_notes(sends, self.rep), ["GI-85", "GI-90"])
        t85 = (self.rep / "GI-85" / "notes.md").read_text()
        self.assertNotIn("every draft sent:", t85)
        self.assertIn("  - 2026-10-27 institution: rio@univ.edu (bcc a@b.c), \"Research integrity concern", t85)
        self.assertIn("- 2026-10-30: a later line, kept", t85)
        self.assertIn("  - publisher: x@y.z, \"should not count\"", t85)   # other sections untouched
        self.assertIn("nothing was sent by email", (self.rep / "GI-90" / "notes.md").read_text())
        self.assertEqual((self.rep / "GI-12" / "notes.md").read_text(), "## Sent\n- nothing yet\n")
        self.assertEqual(gi_reports.fix_notes(sends, self.rep), [])   # idempotent
        _, cases = gi_reports.sync(self.db, self.messages, self.rep, self.site)
        self.assertEqual([c["case_name"] for c in gi_reports.mismatches(cases)[2]], [])


if __name__ == "__main__":
    unittest.main()

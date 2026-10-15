"""general_index/gi_case_findings.py: findings.md parsing and ticking, and the ticked items reaching the
PubPeer Markdown and the emails."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gi_case_findings as cf  # noqa: E402
import gi_contact  # noqa: E402
import gi_pubpeer as pp  # noqa: E402
from test_gi_pubpeer import fd  # noqa: E402

TEXT = """# GI-07 findings
Proposed by the gi-cases agent on 2026-10-15.
- [x] Before any heading counts as a finding.

## Findings
- [ ] Table 2 of the later article (p. 6) reports the same sample sizes
  as Table 3 of the earlier article (p. 5).
  - checked: both PDFs,
    row by row
  - second evidence line
- [X] The same typo appears in both.
- [-] Left out.

## Questions
- [ ] Not a finding: questions are never published.
"""


class TestParse(unittest.TestCase):
    def test_items(self):
        items = cf.parse(TEXT)
        self.assertEqual([it["state"] for it in items], ["include", "pending", "include", "leave out"])
        self.assertEqual(items[1]["text"], "Table 2 of the later article (p. 6) reports the same sample sizes "
                                           "as Table 3 of the earlier article (p. 5).")
        self.assertEqual(items[1]["evidence"], ["checked: both PDFs, row by row", "second evidence line"])

    def test_update_keeps_evidence_and_rest(self):
        new = cf.update(TEXT, {1: ("include", "Reworded."), 3: ("pending", None)})
        items = cf.parse(new)
        self.assertEqual(items[1]["text"], "Reworded.")
        self.assertEqual(items[1]["state"], "include")
        self.assertEqual(items[1]["evidence"], ["checked: both PDFs, row by row", "second evidence line"])
        self.assertEqual(items[3]["state"], "pending")
        self.assertIn("## Questions\n- [ ] Not a finding", new)


class TestFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "GI-07").mkdir()
        cf.findings_path("GI-07", self.dir).write_text(TEXT, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_included_and_counts(self):
        self.assertEqual(cf.included("GI-07", self.dir),
                         ["Before any heading counts as a finding.", "The same typo appears in both."])
        self.assertEqual(cf.counts("GI-07", self.dir), {"pending": 1, "include": 2, "leave out": 1})
        self.assertEqual(cf.pending_cases(self.dir), [("GI-07", 1)])
        self.assertEqual(cf.included("GI-99", self.dir), [])

    def test_review(self):
        answers = iter(["e"])
        changed = cf.review("GI-07", ask=lambda p: next(answers), edit=lambda p, t: "  Edited  wording. ",
                            report_dir=self.dir)
        self.assertEqual(changed, 1)
        self.assertIn("Edited wording.", cf.included("GI-07", self.dir))
        self.assertEqual(cf.pending_cases(self.dir), [])

    def test_review_enter_and_quit_change_nothing(self):
        for script in (["", ""], ["q"]):
            answers = iter(script)
            self.assertEqual(cf.review("GI-07", ask=lambda p: next(answers), report_dir=self.dir), 0)
        self.assertEqual(cf.findings_path("GI-07", self.dir).read_text(encoding="utf-8"), TEXT)

    def test_pubpeer_url(self):
        self.assertIsNone(cf.pubpeer_url("GI-07", self.dir))
        cf.save_pubpeer_url("GI-07", "https://pubpeer.com/publications/ABC123", self.dir)
        self.assertEqual(cf.pubpeer_url("GI-07", self.dir), "https://pubpeer.com/publications/ABC123")
        with self.assertRaises(ValueError):
            cf.save_pubpeer_url("GI-07", "https://example.com/x", self.dir)


class TestPublished(unittest.TestCase):
    def test_markdown_and_email(self):
        d = fd()
        d["extra"] = ["The same typo appears in both."]
        d["pubpeer_post"] = "https://pubpeer.com/publications/ABC123"
        md = pp.markdown(d)
        self.assertIn("- The same typo appears in both.", md)
        self.assertLess(md.index("does not appear to cite"), md.index("same typo"))
        _, body = gi_contact.email_parts(d, "GI-07-comparison.html", name="Me")
        self.assertIn("- The same typo appears in both.", body)
        self.assertIn("posted these findings on PubPeer: https://pubpeer.com/publications/ABC123", body)

    def test_nothing_extra(self):
        _, body = gi_contact.email_parts(fd(), "GI-07-comparison.html", name="Me")
        self.assertNotIn("PubPeer", body)


if __name__ == "__main__":
    unittest.main()

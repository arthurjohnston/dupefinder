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

    def test_type_reasons_are_valid(self):
        for type_, reason in cli.TYPE_REASON.items():
            self.assertIn(("no problem", type_), [c[:2] for c in cli.CHOICES])
            self.assertIn(reason, cli.REASONS["no problem"])

    def test_type_is_its_own_reason(self):
        answers = iter(["9"])   # no problem: boilerplate; nothing else may be asked
        saved = cli.ask
        cli.ask = lambda prompt: next(answers)
        try:
            out = cli.review_one({"ai_decision": None, "year_a": 2016, "year_b": 2017}, cli.CHOICES, {})
        finally:
            cli.ask = saved
        self.assertEqual(out, ("no problem", "boilerplate", "none",
                               "the shared text is boilerplate or references", [], False))

    def test_no_reasons_step(self):
        answers = iter(["1", ""])   # problem: cross-author copying, Enter for medium; nothing else may be asked
        saved = cli.ask
        cli.ask = lambda prompt: next(answers)
        try:
            out = cli.review_one({"ai_decision": None, "ai_severity": None, "year_a": 2016, "year_b": 2017},
                                 cli.CHOICES, {})
        finally:
            cli.ask = saved
        self.assertEqual(out, ("problem", "cross-author copying", "medium", "", [], False))

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


class TestReportFlow(unittest.TestCase):
    """report_flow(): report files, agent, ticking, PubPeer link, drafts, all in the one tool."""

    def setUp(self):
        import tempfile
        import gi_case_findings
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cf = gi_case_findings
        self.saved = (gi_case_findings.REPORT_DIR, cli.ask, cli.pubpeer_page, cli.run_agent)
        self.calls = []
        for fn in ("findings_path", "pubpeer_url_path"):   # point the defaults at the temp dir
            orig = getattr(gi_case_findings, fn)
            setattr(self, "_" + fn, orig)
            setattr(gi_case_findings, fn, lambda case, report_dir=None, o=orig: o(case, self.dir))
        cli.pubpeer_page = lambda db, pair, open_it=True, severity=None: (
            self.calls.append(("report", open_it)) or {"pubpeer": self.dir / "p.html",
                                                       "email publisher": self.dir / "e.eml"})

        def agent(case):
            self.calls.append(("agent", case))
            (self.dir / case).mkdir(exist_ok=True)
            (self.dir / case / "findings.md").write_text("## Findings\n- [ ] One.\n- [ ] Two.\n")
        cli.run_agent = agent

    def tearDown(self):
        gi_case_findings = self.cf
        gi_case_findings.REPORT_DIR, cli.ask, cli.pubpeer_page, cli.run_agent = self.saved
        gi_case_findings.findings_path = self._findings_path
        gi_case_findings.pubpeer_url_path = self._pubpeer_url_path
        self.tmp.cleanup()

    def run_flow(self, answers):
        it = iter(answers)
        cli.ask = lambda prompt: next(it)
        import gi_report_case
        opened = []
        orig = gi_report_case.open_drafts
        gi_report_case.open_drafts = lambda emls: opened.append(emls)
        try:
            cli.report_flow(None, {"page_number": 7}, open_it=True)
        finally:
            gi_report_case.open_drafts = orig
        return opened

    def test_whole_flow(self):
        opened = self.run_flow(["", "y", "n", "https://pubpeer.com/publications/X1", "o"])
        self.assertEqual(self.calls, [("report", True), ("agent", "GI-07"), ("report", False), ("report", True),
                                      ("report", False)])
        self.assertEqual(self.cf.included("GI-07", self.dir), ["One."])
        self.assertEqual(self.cf.pubpeer_url("GI-07", self.dir), "https://pubpeer.com/publications/X1")
        self.assertEqual(len(opened), 1)

    def test_skip_everything(self):
        opened = self.run_flow(["s", "", ""])
        self.assertEqual(self.calls, [("report", True)])
        self.assertEqual(opened, [])

    def test_bad_url_not_saved(self):
        self.run_flow(["s", "https://example.com/x", ""])
        self.assertIsNone(self.cf.pubpeer_url("GI-07", self.dir))


class TestAgentEnv(unittest.TestCase):
    def test_api_keys_removed(self):
        import os
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        try:
            self.assertNotIn("ANTHROPIC_API_KEY", cli.agent_env())
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    def test_not_on_path(self):
        self.assertIn("isn't on PATH", cli.subscription_problem("no-such-claude-binary"))


class TestBackgroundPrep(unittest.TestCase):
    """BackgroundPrep against a fake claude: started, finished with a result, or stopped."""

    def setUp(self):
        import tempfile
        import gi_case_findings
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.orig = gi_case_findings.case_dir
        gi_case_findings.case_dir = lambda case, report_dir=None: self.dir / case
        self.cf = gi_case_findings

    def tearDown(self):
        self.cf.case_dir = self.orig
        self.tmp.cleanup()

    def fake(self, body, method="claude.ai"):
        p = self.dir / "claude"
        p.write_text("#!/bin/sh\nif [ \"$1\" = auth ]; then echo '{\"loggedIn\": true, \"authMethod\": \"%s\"}'; "
                     "exit 0; fi\n%s\n" % (method, body))
        p.chmod(0o755)
        return str(p)

    def test_finishes(self):
        prep = cli.BackgroundPrep("GI-07", claude=self.fake("echo '{\"type\": \"result\", \"result\": \"3 findings\", \"session_id\": \"s1\"}'"))
        self.assertTrue(prep.start())
        self.assertTrue(prep.wait())
        self.assertEqual(prep.result()["session_id"], "s1")

    def test_progress(self):
        ev = ('{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", '
              '"input": {"file_path": "/x/a.pdf"}}]}}')
        prep = cli.BackgroundPrep("GI-07", claude=self.fake("echo '%s'; sleep 30" % ev))
        prep.start()
        import time
        for _ in range(50):
            if prep.tool_calls():
                break
            time.sleep(0.1)
        self.assertEqual(prep.tool_calls(), ["Read  /x/a.pdf"])
        self.assertIn("working on GI-07", prep.status())
        prep.stop()

    def test_stop(self):
        prep = cli.BackgroundPrep("GI-07", claude=self.fake("sleep 30"))
        prep.start()
        self.assertTrue(prep.running())
        prep.stop()
        self.assertFalse(prep.running())
        self.assertEqual(prep.result(), {})

    def test_api_login_refused(self):
        prep = cli.BackgroundPrep("GI-07", claude=self.fake("echo ran", method="console"))
        self.assertFalse(prep.start())
        self.assertIsNone(prep.proc)

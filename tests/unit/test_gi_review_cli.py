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

    def test_not_worth_reporting_key(self):
        answers = iter(["w"])   # one key; nothing else may be asked
        saved = cli.ask
        cli.ask = lambda prompt: next(answers)
        try:
            out = cli.review_one({"ai_decision": None, "year_a": 2016, "year_b": 2017}, cli.CHOICES, {})
        finally:
            cli.ask = saved
        self.assertEqual(out, ("problem", "cross-author copying", "low", "", ["not worth reporting"], False))
        self.assertIn(cli.NOT_WORTH[1], cli.SEVERITIES.values())

    def test_note(self):
        self.assertEqual(cli.build_note(["the later paper doesn't cite the earlier one"], "  saw it  "),
                         "the later paper doesn't cite the earlier one; saw it")
        self.assertEqual(cli.build_note([], ""), "")

    def test_same_year(self):
        self.assertEqual(cli.same_year({"year_a": 2017, "year_b": 2017}), 2017)
        self.assertIsNone(cli.same_year({"year_a": 2016, "year_b": 2017}))
        self.assertIsNone(cli.same_year({"year_a": None, "year_b": None}))
        self.assertIn("2017", cli.same_year_reason(2017))


def order_library(year_1, year_2):
    """A temp verification library with papers 1 and 2, and a fulltext-like row for the pair."""
    import tempfile
    path = Path(tempfile.mkdtemp()) / "library.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("create table papers (id integer primary key, doi text, title text, year integer);")
    conn.executemany("insert into papers values (?, ?, ?, ?)",
                     [(1, "10.1/one", "One", year_1), (2, "10.1/two", "Two", year_2)])
    conn.commit()
    conn.close()
    return {"library": str(path), "paper_a": 1, "paper_b": 2, "doi_a": "10.1/one", "doi_b": "10.1/two",
            "year_a": year_1, "year_b": year_2, "ft_pct_a": 50.0, "ft_pct_b": 40.0}


class TestOrder(unittest.TestCase):
    def test_same_year_set_by_hand_then_cleared(self):
        pair = order_library(2017, 2017)
        shown = cli.orient(pair)
        self.assertIsNone(shown["earlier"])
        cli.set_earlier(shown, "b")          # paper 2 first: it becomes A, the page's left paper
        shown = cli.orient(pair)
        self.assertEqual((shown["paper_a"], shown["earlier"], shown["swapped"]), (2, "a", True))
        self.assertEqual(shown["doi_a"], "10.1/two")
        cli.set_earlier(shown, "-")
        self.assertIsNone(cli.orient(pair)["earlier"])

    def test_wrong_order_by_the_years(self):
        pair = order_library(2010, 2015)
        shown = cli.orient(pair)
        self.assertEqual((shown["paper_a"], shown["earlier"]), (1, "a"))
        cli.set_earlier(shown, "b")          # the years have it the wrong way round
        self.assertEqual((cli.orient(pair)["paper_a"], cli.orient(pair)["earlier"]), (2, "a"))
        cli.set_earlier(cli.orient(pair), "u")   # can't tell, whatever the years say
        self.assertIsNone(cli.orient(pair)["earlier"])

    def test_resolve_earlier_id_without_overrides_table(self):
        sys.path.insert(0, str(ROOT))
        from write_dupe_reports_html import resolve_earlier_id
        pair = order_library(2010, 2015)
        conn = sqlite3.connect(pair["library"])
        self.assertEqual(resolve_earlier_id(conn, 1, 2, []), 1)
        self.assertIsNone(resolve_earlier_id(sqlite3.connect(order_library(2017, 2017)["library"]), 1, 2, []))

    def run_review(self, answers, set_to):
        pair = {"ai_decision": None, "ai_severity": None, "year_a": 2017, "year_b": 2017}
        shown = {"earlier": None, "year_a": 2017, "year_b": 2017}
        asked = []
        answers = iter(answers)
        saved = cli.ask
        cli.ask = lambda prompt: (asked.append(prompt), next(answers))[1]
        try:
            return cli.review_one(pair, cli.CHOICES, {}, shown=shown,
                                  set_order=lambda ans: {**shown, "earlier": set_to}), asked
        finally:
            cli.ask = saved

    def test_problem_same_year_asks_the_order(self):
        out, asked = self.run_review(["1", "b", ""], "a")   # cross-author copying; B first; Enter for medium
        self.assertEqual(out, ("problem", "cross-author copying", "medium", "", [], False))
        self.assertTrue(asked[1].startswith("Which came first"))

    def test_problem_same_year_cant_tell_is_unsure(self):
        out, _ = self.run_review(["1", "", "", ""], None)    # Enter: can't tell; then severity and flags
        self.assertEqual(out[:3], ("unsure", "cross-author copying", "medium"))
        self.assertIn("2017", out[3])


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
            return True
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
        # the agent, then the files written and the PubPeer page opened once, then rewritten for the URL
        self.assertEqual(self.calls, [("agent", "GI-07"), ("report", True), ("report", False)])
        self.assertEqual(self.cf.included("GI-07", self.dir), ["One."])
        self.assertEqual(self.cf.pubpeer_url("GI-07", self.dir), "https://pubpeer.com/publications/X1")
        self.assertEqual(len(opened), 1)

    def test_skip_everything(self):
        opened = self.run_flow(["s", "", ""])
        self.assertEqual(self.calls, [("report", True)])
        self.assertEqual(opened, [])

    def test_agent_stopped_reports_without_it(self):
        class Stopped:   # Ctrl-C while waiting
            earlier = False

            def wait(self):
                return False
        it = iter(["", ""])
        cli.ask = lambda prompt: next(it)
        cli.report_flow(None, {"page_number": 7}, open_it=True, prep=Stopped())
        self.assertEqual(self.calls, [("report", True)])   # no offer to open the agent instead

    def finished(self, tmp):
        class Finished:   # the agent's run, done
            earlier, claude, dir = False, "claude", tmp

            def wait(self):
                return True

            def result(self):
                return {"result": "2 findings proposed", "session_id": "s1"}
        return Finished()

    def test_change_verdict_after_summary(self):
        it = iter(["v"])
        cli.ask = lambda prompt: next(it)
        self.assertEqual(cli.report_flow(None, {"page_number": 7}, prep=self.finished(self.dir)), "verdict")
        self.assertEqual(self.calls, [])   # nothing written

    def test_stop_after_discussing(self):
        import subprocess
        runs = []
        orig = subprocess.run
        subprocess.run = lambda cmd, **kw: runs.append(cmd)
        it = iter(["d", "s"])
        cli.ask = lambda prompt: next(it)
        try:
            out = cli.report_flow(None, {"page_number": 7}, prep=self.finished(self.dir))
        finally:
            subprocess.run = orig
        self.assertEqual(out, "stopped")
        self.assertIn("--resume", runs[0])
        self.assertEqual(self.calls, [])

    def test_go_on_after_discussing(self):
        import subprocess
        orig = subprocess.run
        subprocess.run = lambda cmd, **kw: None
        it = iter(["d", "", "", ""])
        cli.ask = lambda prompt: next(it)
        try:
            self.assertIsNone(cli.report_flow(None, {"page_number": 7}, prep=self.finished(self.dir)))
        finally:
            subprocess.run = orig
        self.assertEqual(self.calls, [("report", True)])

    def test_bad_url_not_saved(self):
        self.run_flow(["s", "https://example.com/x", ""])
        self.assertIsNone(self.cf.pubpeer_url("GI-07", self.dir))

    def test_findings_already_there_no_agent_prompt(self):
        (self.dir / "GI-07").mkdir()
        (self.dir / "GI-07" / "findings.md").write_text("## Findings\n- [ ] One.\n")
        self.run_flow(["y", "", ""])
        self.assertEqual(self.calls, [("report", True)])
        self.assertEqual(self.cf.included("GI-07", self.dir), ["One."])


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

    def test_previous_run(self):
        self.assertIsNone(cli.BackgroundPrep.previous("GI-07"))
        (self.dir / "GI-07").mkdir()
        (self.dir / "GI-07" / "prep-stream.jsonl").write_text(
            '{"type": "result", "result": "Read both.\\n5 findings proposed", "session_id": "s9"}\n')
        prep = cli.BackgroundPrep.previous("GI-07")
        self.assertTrue(prep.wait())
        self.assertFalse(prep.running())
        self.assertIn("prepped GI-07 on", prep.status())
        self.assertIn("5 findings proposed", prep.status())
        self.assertEqual(prep.result()["session_id"], "s9")
        prep.stop()   # nothing to stop

    def test_next_to_prep(self):
        orig = self.cf.findings_path
        self.cf.findings_path = lambda case, report_dir=None: self.dir / case / "findings.md"
        try:
            (self.dir / "GI-08").mkdir()
            (self.dir / "GI-08" / "findings.md").write_text("")
            rest = [{"page_number": 8}, {"page_number": 9}, {"page_number": 10}]
            self.assertEqual(cli.next_to_prep(rest), "GI-09")   # GI-08 is prepped already
            self.assertIsNone(cli.next_to_prep(rest[:1]))
            self.assertIsNone(cli.next_to_prep([]))
        finally:
            self.cf.findings_path = orig

    def test_api_login_refused(self):
        prep = cli.BackgroundPrep("GI-07", claude=self.fake("echo ran", method="console"))
        self.assertFalse(prep.start())
        self.assertIsNone(prep.proc)


class TestCheckFindings(unittest.TestCase):
    """f at the menu: the case on screen first, the others only if asked; reports rewritten only once reported."""

    def setUp(self):
        import tempfile
        import gi_case_findings
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cf = gi_case_findings
        self.saved = (gi_case_findings.pending_cases, gi_case_findings.case_dir, gi_case_findings.findings_path,
                      cli.ask, cli.pubpeer_page)
        case_dir, findings_path, pending = (gi_case_findings.case_dir, gi_case_findings.findings_path,
                                            gi_case_findings.pending_cases)
        gi_case_findings.pending_cases = lambda report_dir=None: pending(self.dir)
        gi_case_findings.case_dir = lambda case, report_dir=None: case_dir(case, self.dir)
        gi_case_findings.findings_path = lambda case, report_dir=None: findings_path(case, self.dir)
        self.reports = []
        cli.pubpeer_page = lambda db, f, open_it=True, severity=None: self.reports.append(f["page_number"])
        for case, text in (("GI-07", "- [ ] Seven."), ("GI-100", "- [ ] Hundred."), ("GI-12", "- [x] Done.")):
            (self.dir / case).mkdir()
            (self.dir / case / "findings.md").write_text(f"## Findings\n{text}\n")
        import sqlite3
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("create table fulltext (page_number integer)")
        self.db.executemany("insert into fulltext values (?)", [(7,), (12,), (100,)])

    def tearDown(self):
        (self.cf.pending_cases, self.cf.case_dir, self.cf.findings_path, cli.ask, cli.pubpeer_page) = self.saved
        self.tmp.cleanup()

    def run_f(self, case, answers, prep=None):
        it = iter(answers)
        prompts = []
        cli.ask = lambda prompt: prompts.append(prompt) or next(it)
        cli.check_findings(self.db, case, open_it=False, prep=prep)
        return prompts

    def test_numeric_case_order(self):
        self.assertEqual([c for c, _ in self.cf.pending_cases()], ["GI-07", "GI-100"])

    def test_current_case_first_and_only(self):
        prompts = self.run_f("GI-100", ["y", ""])
        self.assertEqual(self.cf.included("GI-100", self.dir), ["Hundred."])
        self.assertEqual(self.cf.included("GI-07", self.dir), [])   # the other case offered, not entered
        self.assertIn("GI-07 1", prompts[-1])
        self.assertEqual(self.reports, [])   # not reported: no report files written

    def test_all_decided_shows_them_again(self):
        self.run_f("GI-12", ["n", ""])
        self.assertEqual(self.cf.included("GI-12", self.dir), [])

    def test_reported_case_rewritten(self):
        (self.dir / "GI-07" / "GI-07-pubpeer.html").write_text("x")
        self.run_f("GI-07", ["y", "o", "y"])   # this case, then the others too
        self.assertEqual(self.reports, [7])   # GI-100 isn't reported
        self.assertEqual(self.cf.included("GI-100", self.dir), ["Hundred."])

    def test_agent_still_running(self):
        class Running:
            def running(self):
                return True

            def status(self):
                return "agent: working"
        self.run_f("GI-07", [""], prep=Running())
        self.assertEqual(self.cf.included("GI-07", self.dir), [])


class TestTakeDown(unittest.TestCase):
    def test_take_down(self):
        import json
        import tempfile
        import gi_report_case
        with tempfile.TemporaryDirectory() as t:
            site, rep = Path(t) / "site", Path(t) / "rep"
            site.mkdir()
            (rep / "GI-07" / "compose").mkdir(parents=True)
            entry = {"file": "x", "reported": "2026-10-20", "published": "2026-10-20", "later": {}, "earlier": {}}
            (site / "cases.json").write_text(json.dumps({"GI-07": entry, "GI-08": entry}))
            (site / "GI-07.html").write_text("x")
            for name in ("GI-07-pubpeer.html", "GI-07-email-publisher.eml", "findings.md", "contacts.json"):
                (rep / "GI-07" / name).write_text("x")
            orig = gi_report_case.write_index
            gi_report_case.write_index = lambda d: None
            try:
                self.assertEqual(gi_report_case.is_reported("GI-07", site), "2026-10-20")
                moved = gi_report_case.take_down("GI-07", site, rep)
            finally:
                gi_report_case.write_index = orig
            self.assertEqual(len(moved), 3)
            self.assertIsNone(gi_report_case.is_reported("GI-07", site))
            self.assertEqual(gi_report_case.is_reported("GI-08", site), "2026-10-20")
            self.assertFalse((site / "GI-07.html").exists())
            self.assertEqual(sorted(p.name for p in (rep / "GI-07").iterdir()),
                             ["contacts.json", "findings.md", "withdrawn"])


if __name__ == "__main__":
    unittest.main()

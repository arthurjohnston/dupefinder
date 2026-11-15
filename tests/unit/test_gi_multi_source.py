"""A case whose later article has several sources (sources.json, MULTI_SOURCE_REPORTING.md option A): the union
arithmetic (gi_sources.summarize()), the PubPeer Markdown and email wording, severity from the union, the
per-source drafts (gi_report_case.write_source_drafts()) and [sN] finding tags."""
import email
import email.policy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gi_case_findings as cf  # noqa: E402
import gi_contact as gc  # noqa: E402
import gi_pubpeer as pp  # noqa: E402
import gi_report_case as gi_report  # noqa: E402
import gi_sources  # noqa: E402
from test_gi_pubpeer import fd  # noqa: E402

SOURCE = {"n": 1, "title": "Second review", "year": 2006, "journal": "J Two", "doi": "10.3/two",
          "authors": ["M. Geier", "R. Butler"], "cited": True, "shared": "", "shared_authors": False,
          "kind": "text"}


def measured(paired, s1, total=1000):
    def c(covered, runs, longest):
        return {"covered": set(covered), "runs": runs, "longest": longest, "own": len(covered), "own_words": 2000,
                "passages": [{"words": longest, "excerpt": f"excerpt {longest}"}]}
    return {"words": total, "paired": c(paired, 13, 143), 1: c(s1, 28, 40)}


def multi_fd(paired=range(0, 245), s1=range(300, 480), other=(), **kw):
    f = fd(pct_later=24.5, **kw)
    f["multi"] = gi_sources.summarize(measured(paired, s1), [SOURCE], list(other))
    return f


class TestSummarize(unittest.TestCase):
    def test_union_and_letters(self):
        m = multi_fd()["multi"]
        self.assertEqual((m["union_words"], m["union_pct"], m["overlap"]), (425, 42.5, 0))
        self.assertEqual(m["paired"]["letter"], "A")
        s = m["sources"][0]
        self.assertEqual((s["letter"], s["matched"], s["pct"], s["runs"], s["own_pct"]), ("B", 180, 18.0, 28, 9.0))

    def test_overlap_counted_once(self):
        m = multi_fd(paired=range(0, 200), s1=range(150, 250))["multi"]
        self.assertEqual((m["union_words"], m["overlap"]), (250, 50))

    def test_no_sources(self):
        self.assertIsNone(pp.multi(fd()))
        self.assertIsNone(pp.multi({**fd(), "multi": {"sources": []}}))


class TestMarkdown(unittest.TestCase):
    def test_union_leads(self):
        md = pp.markdown(multi_fd(cites=True))
        self.assertTrue(md.startswith("This article appears to reuse substantial text from two earlier articles."))
        self.assertIn("- About **42%** of this article's text (425 of its about 1,000 words) is also found in two "
                      "earlier articles (the two don't overlap):", md)
        self.assertIn("    - **24%** in [Old (paper)](https://doi.org/10.1/old)", md)
        self.assertIn("This article's reference list does appear to cite it.", md)
        self.assertIn("    - **18%** in [Second review](https://doi.org/10.3/two) (2006; doi:10.3/two): 180 matched "
                      "words in 28 passages, the longest 40 words. This article cites it.", md)
        self.assertNotIn("About **24%** of this article's text", md)   # not the single-pair line

    def test_overlap_and_shared_sentence(self):
        f = multi_fd(paired=range(0, 200), s1=range(150, 250))
        f["multi"]["sources"][0]["shared"] = "The SCFA subsection; cited once (ref 32), for one sentence."
        md = pp.markdown(f)
        self.assertIn("50 of those words are in more than one of them, counted once", md)
        self.assertIn("Shared: the SCFA subsection; cited once (ref 32), for one sentence.", md)
        self.assertNotIn("This article cites it.", md)   # the shared sentence says so already

    def test_references_only_source(self):
        other = [{"n": 2, "title": "Clove paper", "year": 2009, "doi": "10.4/clove", "kinds": ["references"],
                  "refs": {"later": "3-22", "count": 20, "order": "in nearly the same order"}}]
        md = pp.markdown(multi_fd(other=other))
        self.assertIn("- 20 of this article's references (refs 3-22) are in the reference list of [Clove paper]"
                      "(https://doi.org/10.4/clove) (2009; doi:10.4/clove), in nearly the same order.", md)

    def test_largely(self):
        self.assertIn("appears to be largely made up of text from two earlier articles",
                      pp.markdown(multi_fd(paired=range(0, 400), s1=range(400, 600))))


def body(f, audience, **kw):
    return gc.email_parts(f, "GI-1-comparison.html", kw.pop("shot", "GI-1-longest.jpg"), audience=audience,
                          name="Arthur", **kw)


class TestEmails(unittest.TestCase):
    def test_journal_gets_every_source(self):
        subject, e = body(multi_fd(passages=4), "publisher")
        self.assertIn(gc.ABOUT_ME_MULTI + " I am writing about an article in your journal that appears to reuse "
                      "substantial text from two earlier articles.", e)
        self.assertIn("Earlier articles:\nA. Old [paper] (2011), https://doi.org/10.1/old\n"
                      "B. Second review (2006), J Two, https://doi.org/10.3/two\n", e)
        self.assertIn("  - 24% in article A:", e)
        self.assertIn("  - 18% in article B: 180 matched words", e)
        self.assertIn("With article A:\n1. 300 words", e)
        self.assertIn("With article B:\n1. 40 words: \"excerpt 40\"", e)
        self.assertIn("GI-1-longest.jpg: the longest passage shared with article A", e)
        self.assertNotIn("**", e)

    def test_institution_plural(self):
        f = {**multi_fd(), "case_contacts": {"institution": [{"to": "rio@u.example", "authors": ["A. One"]}]}}
        e = body(f, "institution")[1]
        self.assertIn("A. One, affiliated with your institution, which appears to reuse substantial text from two "
                      "earlier articles.", e)
        self.assertIn("  - 18% in article B", e)

    def test_paired_authors_short_with_overview_line(self):
        e = body(multi_fd(), "authors")[1]
        self.assertIn("Earlier article: Old [paper]", e)
        self.assertIn("- About 24% of the later article's text is also found in the earlier article", e)
        self.assertIn("- The later article also shares text with one other earlier article: in all, about 42% of it "
                      "is found in these two articles.", e)
        self.assertNotIn("article B", e)

    def test_single_source_unchanged(self):
        self.assertEqual(body(fd(), "publisher"), gc.email_parts(fd(), "GI-1-comparison.html", "GI-1-longest.jpg",
                                                                 audience="publisher", name="Arthur"))
        self.assertIn(gc.ABOUT_ME + " I am writing about an article in your journal that appears to reuse "
                      "substantial text from an earlier article.", body(fd(pct_later=30.0), "publisher")[1])


class TestJournalNameEscaped(unittest.TestCase):
    def test_unescaped_entry_found(self):
        data = {"dois": {}, "journals": {"Cancer Biology & Therapy": {"to": "ethics@x.example", "cc": "",
                                                                       "page": "", "notes": ""}}}
        get = lambda *a, **k: Resp({"container-title": ["Cancer Biology &amp; Therapy"], "publisher": "T&F"})
        c = gc.journal_contact("10.4161/x", data, get=get)
        self.assertEqual(c["to"], "ethics@x.example")
        self.assertEqual(list(data["journals"]), ["Cancer Biology & Therapy"])   # no blank duplicate


class Resp:
    def __init__(self, m):
        self.m = m

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": self.m}


class TestSeverity(unittest.TestCase):
    def test_medium_uses_union_share(self):
        self.assertEqual(gi_report.audiences("medium", fd(pct_later=15.0)), ("publisher",))
        f = multi_fd(paired=range(0, 150), s1=range(300, 480))   # 15% + 18% = 33%
        self.assertEqual(gi_report.audiences("medium", f), ("publisher", "institution"))


class TestSourceDrafts(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.out = self.dir / "GI-99"
        self.out.mkdir()
        (self.out / "GI-99-comparison.html").write_text("<p>page</p>")
        (self.out / "findings.md").write_text("## Findings\n- [x] Untagged.\n- [x] [s1] About the second review.\n"
                                              "- [x] [s2] About another.\n")
        self.saved = gc.load_contacts, gc.save_contacts
        gc.load_contacts = lambda: {"dois": {"10.3/two": {"journal": "J Two", "publisher": "P", "issn": []}},
                                    "journals": {"J Two": {"to": "ed@two.example", "cc": "", "page": "", "notes": ""}}}
        gc.save_contacts = lambda data: None

    def tearDown(self):
        gc.load_contacts, gc.save_contacts = self.saved

    def write(self, f, wanted=gc.EMAIL_AUDIENCES):
        return gi_report.write_source_drafts(f, self.out, "GI-99", [self.out / "GI-99-comparison.html"], wanted,
                                             "me@example.org", report_dir=self.dir)

    def test_drafts_per_source(self):
        f = {**multi_fd(), "case_contacts": {"sources": {"s1": {"to": "geier@a.example", "names": ["M. Geier"]}}}}
        paths = self.write(f)
        self.assertEqual(sorted(k for k in paths if k.startswith("email ")),
                         ["email authors-s1", "email source-publisher-s1"])
        msg = email.message_from_bytes(paths["email authors-s1"].read_bytes(), policy=email.policy.default)
        self.assertEqual((msg["To"], msg["Cc"]), ("me@example.org", "geier@a.example"))
        self.assertEqual(msg["Subject"], 'Text from your article "Second review" in a 2015 article')
        text = msg.get_body(("plain",)).get_content()
        self.assertTrue(text.startswith("Dear M. Geier,"))
        self.assertIn("Earlier article: Second review (2006), J Two", text)
        self.assertIn("- About 18% of the later article's text is also found in the earlier article (180 matched "
                      "words in 28 passages", text)
        self.assertIn("one other earlier article: in all, about 42%", text)
        self.assertIn("- About the second review.", text)   # its own [s1] finding
        self.assertNotIn("Untagged.", text)
        self.assertNotIn("About another.", text)
        self.assertEqual([a.get_filename() for a in msg.iter_attachments()], ["GI-99-comparison.html"])
        pub = email.message_from_bytes(paths["email source-publisher-s1"].read_bytes(), policy=email.policy.default)
        self.assertEqual(pub["To"], "ed@two.example")

    def test_no_address_no_shared_authors_not_high(self):
        paths = self.write(multi_fd())
        self.assertIn("no email authors-s1", paths)
        self.assertIn("email source-publisher-s1", paths)
        f = multi_fd()
        f["multi"]["sources"][0]["shared_authors"] = True
        self.assertIn("no email authors-s1", self.write(f))
        self.assertEqual(self.write(multi_fd(), wanted=("publisher",)), {})
        self.assertEqual(list(self.out.glob("*-s1.eml")), [])   # the stale drafts went

    def test_stale_source_removed(self):
        (self.out / "GI-99-email-authors-s3.eml").write_text("old")
        self.write(multi_fd())
        self.assertFalse((self.out / "GI-99-email-authors-s3.eml").exists())
        self.assertTrue((self.out / "GI-99-email-source-publisher-s1.eml").exists())

    def test_source0_filenames_unchanged(self):
        import gi_pubpeer
        f = {**multi_fd(), "case_contacts": {"authors": {"to": "old@e.example"},
                                             "sources": {"s1": {"to": "geier@a.example"}}}}
        saved = gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name, \
            gi_sources.summary, gi_sources.add_sources
        gi_report.case_findings = lambda db, row, report_dir: dict(f)
        gi_pubpeer.write_page = lambda db, row, out, fd, name: (Path(out) / name, None)
        gi_pubpeer.publisher_page = lambda row, fd: ("<p>page</p>", None, None)
        gi_pubpeer.case_name = lambda row: "GI-99"
        gi_sources.summary = lambda row, case_dir: f["multi"]
        gi_sources.add_sources = lambda page, row, fd, case_dir: (page, None)
        try:
            paths = gi_report.write_report(None, {}, report_dir=self.dir, site_dir=None, severity="high")
        finally:
            (gi_report.case_findings, gi_pubpeer.write_page, gi_pubpeer.publisher_page, gi_pubpeer.case_name,
             gi_sources.summary, gi_sources.add_sources) = saved
        self.assertEqual(sorted(k for k in paths if k.startswith("email ")),
                         ["email authors", "email authors-s1", "email publisher", "email source-publisher",
                          "email source-publisher-s1"])
        self.assertTrue((self.out / "GI-99-email-authors.eml").exists())


class TestSourceTags(unittest.TestCase):
    def test_tags(self):
        self.assertEqual(cf.tags("[data] [s2] Table 1."), (True, 2, "Table 1."))
        self.assertEqual(cf.tags("[S1] [data] Table 1."), (True, 1, "Table 1."))
        self.assertEqual(cf.tags("Plain."), (False, None, "Plain."))

    def test_included(self):
        d = Path(tempfile.mkdtemp())
        (d / "GI-05").mkdir()
        (d / "GI-05" / "findings.md").write_text("## Findings\n- [x] One.\n- [x] [s1] Two.\n- [x] [data] [s1] Three.\n"
                                                 "- [ ] [s1] Pending.\n")
        self.assertEqual(cf.included("GI-05", d), ["One.", "Two.", "Three."])   # tags stripped, all published
        self.assertEqual(cf.included("GI-05", d, data=False), ["One.", "Two."])
        self.assertEqual(cf.included("GI-05", d, data=False, source=1), ["Two."])
        self.assertEqual(cf.included("GI-05", d, data=True), ["Three."])


if __name__ == "__main__":
    unittest.main()

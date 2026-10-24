"""general_index/gi_contact.py: the emails about a reported pair."""
import email
import email.policy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gi_contact as gc  # noqa: E402
from test_gi_pubpeer import fd  # noqa: E402


def body(f=None, **kw):
    kw.setdefault("name", "Arthur")
    return gc.email_parts(f or fd(), kw.pop("attachment", "p.html"), **kw)


class TestEmail(unittest.TestCase):
    def test_later_known(self):
        subject, e = body(fd(passages=12), attachment="GI-1-x.html", shot_name="GI-1-longest.jpg")
        self.assertEqual(subject, 'Text overlap in "New paper" (doi:10.2/new)')
        self.assertTrue(e.startswith("Dear Editor,\n\n" + gc.ABOUT_ME + " I am writing about an article in your journal"))
        self.assertIn("Later article: New paper (2015), https://doi.org/10.2/new", e)
        self.assertIn("Earlier article: Old [paper] (2011), https://doi.org/10.1/old", e)
        self.assertIn("About 62% of the later article's text", e)
        self.assertIn("Extracts of the 3 longest shared passages", e)
        self.assertIn('3. 298 words: "passage "2" text"', e)
        self.assertNotIn('passage "3"', e)
        self.assertIn("I also attached:\n- GI-1-x.html: all 12 shared passages, the two texts", e)
        self.assertIn("all 77 shared passages", body({**fd(), "runs": 77})[1])
        self.assertIn("both articles end to end as one diff", body(fd(pct_later=95.0))[1])
        self.assertIn("- GI-1-longest.jpg: the longest shared passage of running text, as printed in both articles (earlier article left", e)
        self.assertNotIn("online at", e)
        self.assertNotIn("**", e)
        self.assertNotIn("PubPeer", e)
        self.assertNotIn("made automatically", e)
        # attachments, then extracts, then the request and sign-off
        order = [e.index(t) for t in ("Later article:", "I also attached:", "Extracts of",
                                      "I would be grateful", "Thanks\nArthur\n")]
        self.assertEqual(order, sorted(order))
        self.assertTrue(e.endswith("let me know what you decide.\n\nThanks\nArthur\n"))

    def test_audiences(self):
        mails = {a: body(audience=a) for a in gc.EMAIL_AUDIENCES}
        self.assertIn("affiliated with your institution", mails["institution"][1])
        self.assertIn("your article appears to have been reused", mails["authors"][1])
        self.assertEqual(mails["authors"][0], 'Text from your article "Old [paper]" in a 2015 article')
        self.assertIn("an article you published appears to have been reused", mails["source-publisher"][1])
        for a, (_, m) in mails.items():   # every one has the share and the attachment, only the journal extracts
            self.assertIn("About 62% of the later article's text", m)
            self.assertIn("- p.html:", m)
            self.assertEqual("Extracts of the 2 longest shared passages" in m, a == "publisher", a)
            about = gc.ABOUT_ME_AUTHORS if a == "authors" else gc.ABOUT_ME   # no "no connection" to the authors
            self.assertTrue(m.startswith(m.split("\n")[0] + "\n\n" + about + " I am writing"), a)
        for a in ("authors", "source-publisher"):   # the earlier side: no citation/byline checks
            self.assertNotIn("share no names", mails[a][1])
            self.assertNotIn("reference list", mails[a][1])
        self.assertIn("share no names", mails["institution"][1])
        self.assertNotIn("Extracts", mails["institution"][1])
        with self.assertRaises(ValueError):
            body(audience="nobody")

    def test_institution_names_its_authors(self):
        inst = {"to": "rio@u.example", "name": "U", "authors": ["A. One", "B. Two", "C. Three"]}
        f = {**fd(), "case_contacts": {"institution": [inst, {"to": "rio@v.example", "authors": ["D. Four"]}]}}
        e = body(f, audience="institution", contact=inst)[1]
        self.assertIn("an article by A. One, B. Two and C. Three, affiliated with your institution, which", e)
        self.assertEqual(gc.recipients("institution", f, inst), ("rio@u.example", ""))
        self.assertEqual(len(gc.institutions(f)), 2)
        self.assertIn("by authors affiliated with your institution", body(audience="institution")[1])
        self.assertEqual(gc.name_list(["A", "B"]), "A and B")

    def test_victims_greeted_by_name(self):
        one = {**fd(), "first": {**fd()["first"], "authors": ["Old Author"]}}
        e = body(one, audience="authors")[1]
        self.assertTrue(e.startswith("Dear Old Author,"))
        self.assertIn("contact the journal that published the later article yourself.", e)
        two = {**fd(), "first": {**fd()["first"], "authors": ["X", "Y"]}}
        self.assertTrue(body(two, audience="authors")[1].startswith("Dear X and Y,"))
        three = {**fd(), "first": {**fd()["first"], "authors": ["X", "Y", "Z"]}}
        self.assertTrue(body(three, audience="authors")[1].startswith("Dear authors,"))
        # contacts.json's names (as printed) win over the index metadata
        named = {**two, "case_contacts": {"authors": {"to": "x@e.example", "names": ["Xavier Ex"]}}}
        self.assertTrue(body(named, audience="authors")[1].startswith("Dear Xavier Ex,"))

    def test_journal_written_to_only_when_it_has_an_address(self):
        for a in ("institution", "authors", "source-publisher"):
            e = body(audience=a)[1]
            self.assertNotIn("I have written to", e, a)
            self.assertNotIn("I have reported this", e, a)
        e = body(audience="source-publisher")[1]
        self.assertIn("I couldn't find a research-integrity contact for the journal that published the later", e)
        self.assertIn("grateful if you could raise it with the journal", e)
        f = fd()
        f["second"] = {**f["second"], "journal": "New Journal", "contact": {"to": "ed@new.example"}}
        self.assertIn("I have written to the journal that published it as well.", body(f, audience="institution")[1])
        self.assertIn("I have reported this to New Journal. I thought", body(f, audience="authors")[1])
        e = body(f, audience="source-publisher")[1]
        self.assertIn("I have reported this to New Journal.", e)
        self.assertNotIn("couldn't find", e)

    def test_data_findings_lead(self):
        f = {**fd(pct_later=20.0), "case": "GI-535", "data_extra": ["Table 1 has the same means."],
             "extra": ["A typo is carried over."]}
        subject, e = body(f)
        self.assertEqual(subject, 'Results data in "New paper" (doi:10.2/new) appear to match an earlier article')
        self.assertIn("appears to report the same results data as an earlier article by other authors.", e)
        order = [e.index(t) for t in ("Table 1 has the same", "A typo", "About 20%", "share no names")]
        self.assertEqual(order, sorted(order))   # data, other findings (share under half), share, checks
        _, a = body(f, audience="authors")
        self.assertIn("the results data of your article appear to have been reused", a)
        self.assertIn("- Table 1 has the same means.", a)
        self.assertNotIn("A typo", a)   # the earlier side gets the data findings only
        self.assertTrue(body(f, audience="authors")[0].startswith("Results data from your article"))
        # without data, over half: the share before the other findings
        g = {**fd(), "extra": ["A typo is carried over."]}
        self.assertLess(body(g)[1].index("About 62%"), body(g)[1].index("A typo"))

    def test_shared_authors(self):
        f = fd(byline="byline overlap: A. One")
        self.assertTrue(gc.shared_authors(f))
        self.assertFalse(gc.shared_authors(fd()))
        subject, e = body(f)
        self.assertEqual(subject, 'Possible redundant publication: "New paper" (doi:10.2/new)')
        self.assertIn("appears to be largely a republication of an earlier article that shares authors with it.", e)

    def test_table_extracts_skipped(self):
        f = fd(passages=4)
        f["passages"][0] = {"words": 400, "excerpt": "Mean 4.81 (1.00) 4.60 (1.03) 4.93 (0.97) balance 4.87"}
        e = body(f)[1]
        self.assertNotIn("4.81", e)
        self.assertIn("Extracts of 3 of the longest shared passages", e)
        self.assertNotIn("online at", e)   # no link to the project site

    def test_order_unknown(self):
        for a in gc.EMAIL_AUDIENCES:
            e = "\n".join(body(fd(earlier_known=False), audience=a))
            self.assertIn("can't be told from the publication years", e, a)
            self.assertNotIn("earlier article", e, a)
            self.assertNotIn("reused", e, a)
            self.assertNotIn(".jpg", e, a)


class FakeResponse:
    def __init__(self, message):
        self.message = message

    def raise_for_status(self):
        if self.message is None:
            raise OSError("404")

    def json(self):
        return {"message": self.message}


class TestContacts(unittest.TestCase):
    def test_journal_contact_cached_and_blank_until_filled(self):
        calls = []

        def get(url, **kw):
            calls.append(url)
            return FakeResponse({"container-title": ["Journal of Old"], "publisher": "Pub", "ISSN": ["1234-5678"]})

        data = {"journals": {}, "dois": {}}
        c = gc.journal_contact("10.1/old", data, get)
        self.assertEqual((c["journal"], c["publisher"], c["to"]), ("Journal of Old", "Pub", ""))
        data["journals"]["Journal of Old"]["to"] = "editor@old.example"
        self.assertEqual(gc.journal_contact("10.1/old", data, get)["to"], "editor@old.example")
        self.assertEqual(len(calls), 1)   # Crossref asked once per DOI
        self.assertIsNone(gc.journal_contact("gi:abc", data, get))
        self.assertIsNone(gc.journal_contact("10.9/missing", data, lambda url, **kw: FakeResponse(None)))
        self.assertNotIn("10.9/missing", data["dois"])   # a failed lookup is tried again next time

    def test_save_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            self.assertEqual(gc.load_contacts(p), {"journals": {}, "dois": {}})
            gc.save_contacts({"journals": {"J": {"to": "x"}}, "dois": {}}, p)
            self.assertEqual(gc.load_contacts(p)["journals"]["J"]["to"], "x")

    def test_recipients_and_journal_in_email(self):
        f = fd()
        f["second"] = {**f["second"], "journal": "New Journal", "contact": {"to": "ed@new.example", "cc": "x@y"}}
        f["first"] = {**f["first"], "journal": "Journal of Old", "contact": {"to": "ed@old.example", "cc": ""}}
        self.assertEqual(gc.recipients("publisher", f), ("ed@new.example", "x@y"))
        self.assertEqual(gc.recipients("source-publisher", f), ("ed@old.example", ""))
        self.assertEqual(gc.recipients("institution", f), ("", ""))
        f["case_contacts"] = {"institution": {"to": "rio@univ.example", "source": "https://univ.example"}}
        self.assertEqual(gc.recipients("institution", f), ("rio@univ.example", ""))
        self.assertEqual(gc.recipients("authors", f), ("", ""))
        self.assertEqual(gc.recipients("publisher", fd()), ("", ""))
        self.assertIn("Later article: New paper (2015), New Journal, https://doi.org/10.2/new", body(f)[1])

    def test_report_page_only_without_an_email(self):
        f = fd()
        f["second"] = {**f["second"], "contact": {"to": "", "page": "https://new.example/complaints"}}
        f["first"] = {**f["first"], "contact": {"to": "ed@old.example", "page": "https://old.example/contact"}}
        self.assertEqual(gc.report_page("publisher", f), "https://new.example/complaints")
        self.assertEqual(gc.report_page("source-publisher", f), "")   # it has an address
        self.assertEqual(gc.report_page("institution", f), "")
        self.assertEqual(gc.report_page("publisher", fd()), "")


class TestEmailMessage(unittest.TestCase):
    def test_draft_with_attachments(self):
        with tempfile.TemporaryDirectory() as d:
            page, jpg = Path(d) / "GI-1-comparison.html", Path(d) / "GI-1-longest.jpg"
            page.write_text("<p>x</p>")
            jpg.write_bytes(b"\xff\xd8\xff\xd9")
            raw = bytes(gc.email_message(fd(), [page, jpg], audience="authors", sender="me@example.org",
                                         name="Arthur"))
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        self.assertEqual(msg["To"], "")
        self.assertEqual(msg["From"], "me@example.org")
        self.assertEqual(msg["X-Unsent"], "1")
        self.assertTrue(msg["Subject"].startswith('Text from your article "Old [paper]"'))
        text = msg.get_body(("plain",)).get_content()
        self.assertTrue(text.startswith("Dear authors,"))
        self.assertIn("- GI-1-comparison.html:", text)
        self.assertIn("- GI-1-longest.jpg:", text)
        files = {p.get_filename(): p.get_content_type() for p in msg.iter_attachments()}
        f = {**fd(), "second": {**fd()["second"], "contact": {"to": "ed@new.example", "cc": "x@y"}}}
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "p.html"
            page.write_text("x")
            m2 = gc.email_message(f, [page], audience="publisher")
        self.assertEqual((m2["To"], m2["Cc"]), ("ed@new.example", "x@y"))
        self.assertEqual(files, {"GI-1-comparison.html": "text/html", "GI-1-longest.jpg": "image/jpeg"})


if __name__ == "__main__":
    unittest.main()

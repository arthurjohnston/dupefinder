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
        self.assertEqual(subject, "Text overlap in New paper (doi:10.2/new)")
        self.assertTrue(e.startswith("Dear Editor,\n\nI am writing about an article in your journal"))
        self.assertIn("Later article: New paper (2015), https://doi.org/10.2/new", e)
        self.assertIn("Earlier article: Old [paper] (2011), https://doi.org/10.1/old", e)
        self.assertIn("About 62% of the later article's text", e)
        self.assertIn("Extracts of the 10 longest shared passages", e)
        self.assertIn('10. 291 words: "passage "9" text"', e)
        self.assertNotIn('passage "10"', e)
        self.assertIn("I also attached:\n- GI-1-x.html: all 12 shared passages, the two texts", e)
        self.assertIn("all 77 shared passages", body({**fd(), "runs": 77})[1])
        self.assertIn("both articles end to end as one diff", body(fd(pct_later=95.0))[1])
        self.assertIn("- GI-1-longest.jpg: passage 1 as printed in both articles (earlier article left", e)
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
        self.assertEqual(mails["authors"][0], "Text from your article Old [paper] in a 2015 article")
        self.assertIn("an article you published appears to have been reused", mails["source-publisher"][1])
        for _, m in mails.values():   # every one carries the same evidence
            self.assertIn("Extracts of the 2 longest shared passages", m)
            self.assertIn("- p.html:", m)
        with self.assertRaises(ValueError):
            body(audience="nobody")

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
        self.assertTrue(msg["Subject"].startswith("Text from your article Old [paper]"))
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

#!/usr/bin/env python3
"""Unit tests for bulk_retrieve_author_homepages.py's offline parts: title matching,
link/context extraction from a publications page, and the skipped-domain filter."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import bulk_retrieve_author_homepages as bah  # noqa: E402


class MatchTitleTests(unittest.TestCase):
    TITLE = bah.normalize("Facing Animals: A Relational, Other-Oriented Approach to Moral Standing")

    def test_exact_title_in_citation_line(self):
        ctx = bah.normalize("Coeckelbergh, M. (2014). Facing animals: a relational, other-oriented "
                            "approach to moral standing. J Agric Environ Ethics 27. [PDF]")
        self.assertTrue(bah.match_title(self.TITLE, ctx))

    def test_minor_typo_still_matches(self):
        ctx = bah.normalize("Facing animals: a relational other-orientated approach to moral standing")
        self.assertTrue(bah.match_title(self.TITLE, ctx))

    def test_different_paper_same_author_does_not_match(self):
        ctx = bah.normalize("Facing robots: a relational approach to machine moral standing")
        self.assertFalse(bah.match_title(self.TITLE, ctx))

    def test_short_titles_never_match(self):
        self.assertFalse(bah.match_title(bah.normalize("Robot ethics"), "a book on robot ethics"))


class HtmlLinksTests(unittest.TestCase):
    PAGE = b"""<html><body><script>var x = "Facing animals";</script><ul>
      <li>Facing animals: a relational approach. J Ethics. <a href="/pdfs/facing.pdf">PDF</a></li>
      <li>Another paper entirely. <a href="https://example.org/other.pdf">PDF</a></li>
    </ul></body></html>"""

    def test_bare_pdf_anchor_gets_citation_context_and_absolute_url(self):
        links = bah.html_links(self.PAGE, "https://prof.example.edu/publications/")
        first = links[0]
        self.assertEqual(first["url"], "https://prof.example.edu/pdfs/facing.pdf")
        self.assertEqual(first["anchor"], "PDF")
        self.assertIn("Facing animals", first["context"])

    def test_script_text_excluded(self):
        links = bah.html_links(self.PAGE, "https://prof.example.edu/")
        self.assertNotIn("var x", links[0]["context"])


class SkippedDomainTests(unittest.TestCase):
    def test_social_and_aggregators_skipped(self):
        for url in ("https://twitter.com/someone", "https://www.researchgate.net/profile/X",
                    "https://scholar.google.com/citations?user=abc"):
            self.assertTrue(bah.is_skipped_domain(url), url)

    def test_personal_site_kept(self):
        self.assertFalse(bah.is_skipped_domain("https://coeckelbergh.net/publications/"))


if __name__ == "__main__":
    unittest.main()

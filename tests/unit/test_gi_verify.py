"""gi_verify.collaborations() / byline_check(): a collaboration's paper and a member's write-up of it share a
byline even though the member's name is only in the paper's author appendix (GI-213, GI-242)."""
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "general_index"))

import gi_verify as gv  # noqa: E402

# GI-242's two page 1s, as pdftotext reads them (shortened)
JHEP_PAGE1 = """Published for SISSA by Springer
Received: December 19, 2013

The CMS collaboration
E-mail: cms-publication-committee-chair@cern.ch
Abstract: Double parton scattering is investigated in proton-proton collisions ...
Open Access, Copyright CERN, for the benefit of the CMS Collaboration.
The ATLAS collaboration has carried out a similar DPS measurement using W + 2-jet events
"""
POS_PAGE1 = """Measurement of double parton interactions in W+2-jets events with the CMS detector
Anastasia Grebenyuk∗
on behalf of the CMS collaboration
IIHE - Université Libre de Bruxelles
"""
PLAIN_PAGE1 = """A study of milk
J. Smith, K. Jones
We follow the method of the ATLAS collaboration and the BaBar Collaboration's tool.
"""


class TestCollaborations(unittest.TestCase):
    def test_byline_forms(self):
        self.assertEqual(gv.collaborations(JHEP_PAGE1), {"cms": "author"})   # ATLAS only in a sentence
        self.assertEqual(gv.collaborations(POS_PAGE1), {"cms": "member"})
        self.assertEqual(gv.collaborations("A. Letessier-Selvon for the Pierre Auger Collaboration"),
                         {"auger": "member"})
        self.assertEqual(gv.collaborations("Pierre Auger Collaboration\nAbstract"), {"auger": "author"})
        self.assertEqual(gv.collaborations("H1 Collaboration\n"), {"h1": "author"})

    def test_a_mention_is_not_a_byline(self):
        self.assertEqual(gv.collaborations(PLAIN_PAGE1), {})
        self.assertEqual(gv.collaborations(""), {})


class TestBylineCheck(unittest.TestCase):
    def run_check(self, page1, full, authors):
        lib = sqlite3.connect(":memory:")
        lib.executescript("""create table papers (id, file_path); create table authors (id, name);
                             create table paper_authors (paper_id, author_id); create table paragraphs
                             (paper_id, para_index, text);""")
        for pid, names in authors.items():
            lib.execute("insert into papers values (?, ?)", (pid, f"p{pid}.pdf"))
            for i, n in enumerate(names):
                lib.execute("insert into authors values (?, ?)", (pid * 100 + i, n))
                lib.execute("insert into paper_authors values (?, ?)", (pid, pid * 100 + i))
        text = lambda _lib, pid, last_page=None: page1[pid] if last_page == 1 else full[pid]
        with mock.patch.object(gv, "pdf_text", side_effect=text):
            return gv.byline_check(lib, 1, 2)

    def test_gi_242(self):
        # the catalogue keeps three CMS names; Grebenyuk is on page 31 of the paper
        got = self.run_check({1: JHEP_PAGE1, 2: POS_PAGE1},
                             {1: JHEP_PAGE1 + "\n... A. Geiser, A. Grebenyuk, P. Gunnellini ...", 2: POS_PAGE1},
                             {1: ["S. Chatrchyan", "V. Khachatryan", "A. M. Sirunyan…"], 2: ["Anastasia Grebenyuk"]})
        self.assertIn("byline overlap", got)
        self.assertIn("cms collaboration", got)

    def test_collaboration_author_list_without_member_line(self):
        page2 = "Some talk\nA. Grebenyuk\nDESY\n"   # no "on behalf of": found in the paper's author list
        got = self.run_check({1: JHEP_PAGE1, 2: page2}, {1: JHEP_PAGE1 + "\nA. Grebenyuk, P. Gunnellini", 2: page2},
                             {1: ["S. Chatrchyan"], 2: ["Anastasia Grebenyuk"]})
        self.assertEqual(got, "byline overlap: grebenyuk")

    def test_a_citation_is_not_the_author_list(self):
        # GI-507: a page-1 "Borexino Collaboration" line, and the other side's author cited in the references
        page1 = "Geoneutrinos\nBorexino Collaboration\n"
        full = page1 + "an ambitious project is lena [wurm et al. 2012]. wurm, m., et al. (2012). M. Wurm et al. 2012"
        got = self.run_check({1: page1, 2: "Other\nM. Wurm\n"}, {1: full, 2: "Other\nM. Wurm\n"},
                             {1: ["G. Bellini"], 2: ["Michael Wurm"]})
        self.assertEqual(got, "no byline overlap")

    def test_unrelated_authors_stay_apart(self):
        got = self.run_check({1: PLAIN_PAGE1, 2: "Other paper\nM. Brown\n"},
                             {1: PLAIN_PAGE1 + " Brown", 2: "Other paper\nM. Brown\n"},
                             {1: ["J. Smith", "K. Jones"], 2: ["M. Brown"]})
        self.assertEqual(got, "no byline overlap")   # full text not searched: neither is a collaboration paper


if __name__ == "__main__":
    unittest.main()

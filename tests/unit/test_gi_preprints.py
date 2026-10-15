"""general_index/gi_preprints.py: preprint/repository DOIs, and demoting copying BY a preprint.

The list (preprint_doi_prefixes.tsv) has prefixes that are wholly a preprint server's and prefixes shared
with journals, where only a suffix form is the preprint: bioRxiv's 10.1101 beside the Cold Spring Harbor
journals, JMIR's 10.2196/preprints.*, Copernicus discussion papers beside the final articles. The demotion
applies to the LATER (copying) side only; a preprint that was copied from is an ordinary case.
"""
import csv
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_preprints as gp  # noqa: E402
import gi_write_pages as gwp  # noqa: E402


class TestIsPreprintDoi(unittest.TestCase):
    def test_whole_prefix_servers(self):
        for doi, server in [("10.48550/arXiv.2101.00001", "arXiv"), ("10.2139/ssrn.3456789", "SSRN"),
                            ("10.5281/zenodo.123456", "Zenodo"), ("10.13140/RG.2.2.12345.67890", "ResearchGate"),
                            ("10.21203/rs.3.rs-123456/v1", "Research Square"),
                            ("10.20944/preprints202001.0001.v1", "Preprints.org"),
                            ("10.31234/osf.io/abcde", "PsyArXiv"), ("10.26434/chemrxiv.1234567", "ChemRxiv"),
                            ("10.36227/techrxiv.1234567", "TechRxiv")]:
            self.assertEqual(gp.is_preprint_doi(doi), server, doi)

    def test_journal_dois_are_not_preprints(self):
        for doi in ["10.1016/j.cell.2020.01.001", "10.1109/5.771073", "10.1021/ja00001a001", "", None, "gi:abc123"]:
            self.assertIsNone(gp.is_preprint_doi(doi), doi)

    def test_biorxiv_shares_its_prefix_with_cold_spring_harbor_journals(self):
        self.assertEqual(gp.is_preprint_doi("10.1101/2020.03.22.002386"), "bioRxiv/medRxiv")
        self.assertEqual(gp.is_preprint_doi("10.1101/574970"), "bioRxiv/medRxiv")      # old numeric bioRxiv
        self.assertEqual(gp.is_preprint_doi("10.1101/19004390"), "bioRxiv/medRxiv")    # old numeric medRxiv
        self.assertEqual(gp.is_preprint_doi("10.1101/574970v2"), "bioRxiv/medRxiv")
        for journal in ["10.1101/gr.123456.111", "10.1101/gad.1234", "10.1101/cshperspect.a012345",
                        "10.1101/pdb.prot5432", "10.1101/87505", "10.1101/1234567"]:
            self.assertIsNone(gp.is_preprint_doi(journal), journal)

    def test_shared_prefix_suffix_rules(self):
        self.assertEqual(gp.is_preprint_doi("10.2196/preprints.12345"), "JMIR Preprints")
        self.assertIsNone(gp.is_preprint_doi("10.2196/12345"))
        self.assertTrue(gp.is_preprint_doi("10.5194/acp-2015-1000"))
        self.assertTrue(gp.is_preprint_doi("10.5194/acpd-14-1-2014"))
        self.assertIsNone(gp.is_preprint_doi("10.5194/acp-19-1234-2019"))   # the final article
        self.assertIsNone(gp.is_preprint_doi("10.5194/gmd-12-1234-2019"))   # ends in d, but a journal
        self.assertEqual(gp.is_preprint_doi("10.7287/peerj.preprints.1000v1"), "PeerJ Preprints")
        self.assertIsNone(gp.is_preprint_doi("10.7287/peerj.1234v0.1/reviews/1"))

    def test_normalizes_case_urls_and_trailing_junk(self):
        self.assertEqual(gp.is_preprint_doi(" https://doi.org/10.2139/SSRN.1 "), "SSRN")
        self.assertEqual(gp.is_preprint_doi("doi:10.5281/zenodo.1"), "Zenodo")
        self.assertEqual(gp.is_preprint_doi("10.7287/PEERJ.PREPRINTS.1v1|CC-BY4.0OpenAccess|rec"), "PeerJ Preprints")

    def test_every_tsv_row_is_well_formed(self):
        with open(gp.TSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        self.assertEqual(list(rows[0]), ["prefix", "suffix_regex", "server", "registration_agency",
                                         "how_verified", "count_in_leads"])
        for r in rows:
            self.assertRegex(r["prefix"], r"^10\.\d{4,5}$")
            self.assertTrue(r["server"] and r["how_verified"], r)
            if r["suffix_regex"]:
                re.compile(r["suffix_regex"])


class TestDemotion(unittest.TestCase):
    PRE, JOURNAL = "10.2139/ssrn.1", "10.1016/j.x.2019.1"

    def test_later_side_preprint_is_demoted(self):
        rank, note = gp.preprint_demotion(self.JOURNAL, self.PRE, "a")
        self.assertEqual((rank, note), (gp.LATER, "later paper is a preprint (SSRN)"))

    def test_earlier_side_preprint_is_ordinary(self):
        self.assertEqual(gp.preprint_demotion(self.PRE, self.JOURNAL, "a"), (gp.ORDINARY, None))
        self.assertEqual(gp.preprint_demotion(self.PRE, "10.9999/x.1", "a"), (gp.ORDINARY, None))

    def test_unknown_order_is_the_milder_demotion(self):
        rank, note = gp.preprint_demotion(self.JOURNAL, self.PRE, None)
        self.assertEqual(rank, gp.POSSIBLY)
        self.assertIn("SSRN", note)
        self.assertEqual(gp.preprint_demotion(self.JOURNAL, self.JOURNAL, None), (gp.ORDINARY, None))
        self.assertTrue(gp.ORDINARY < gp.POSSIBLY < gp.LATER)

    def test_earlier_by_year(self):
        self.assertEqual(gp.earlier_by_year("2015", 2018), "a")
        self.assertEqual(gp.earlier_by_year("2019-05-01", "2018"), "b")
        self.assertIsNone(gp.earlier_by_year(2018, "2018"))
        self.assertIsNone(gp.earlier_by_year(None, "2018"))


class TestPreprintStatusFollowsThePage(unittest.TestCase):
    """gi_write_pages.priority_status() decides 'earlier' as the page does (library years), and only falls
    back to fulltext's own years without a library."""

    def library(self, years):
        f = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        conn = sqlite3.connect(f.name)
        conn.execute("create table papers (id integer primary key, year integer)")
        conn.executemany("insert into papers values (?, ?)", years.items())
        conn.commit()
        conn.close()
        return f.name

    def row(self, lib, year_a, year_b):
        return {"doi_a": "10.1016/j.x.1", "doi_b": "10.2139/ssrn.1", "paper_a": 5, "paper_b": 9,
                "library": lib, "year_a": year_a, "year_b": year_b}

    def test_library_years_decide(self):
        lib = self.library({5: 2012, 9: 2016})   # the SSRN side (9) is later
        self.assertEqual(gwp.priority_status(self.row(lib, "2020", "2010"))[0], gp.LATER)
        lib = self.library({5: 2016, 9: 2012})   # the SSRN side was copied from, by an Elsevier paper
        self.assertEqual(gwp.priority_status(self.row(lib, "2010", "2020"))[0], gp.PROMOTED)

    def test_library_same_year_is_unknown_even_if_fulltext_years_differ(self):
        lib = self.library({5: 2016, 9: 2016})
        self.assertEqual(gwp.priority_status(self.row(lib, "2010", "2020"))[0], gp.POSSIBLY)

    def test_no_library_falls_back_to_fulltext_years(self):
        self.assertEqual(gwp.priority_status(self.row(None, "2010", "2020"))[0], gp.LATER)



class TestMajorPublisher(unittest.TestCase):
    def test_core_publishers_and_imprints(self):
        for doi, name in [("10.1016/j.cell.2020.01.001", "Elsevier"), ("10.1016/S0140-6736(20)30183-5", "Elsevier"),
                          ("10.1038/s41586-020-2012-7", "Springer Nature"), ("10.1186/s12889-019-1234-5", "Springer Nature"),
                          ("10.1057/s41599-020-0001-1", "Springer Nature"), ("10.2165/00003495-199900001-00001", "Springer Nature"),
                          ("10.1007/s10551-019-04159-w", "Springer Nature"), ("10.1002/anie.201900001", "Wiley"),
                          ("10.1111/j.1467-8624.2010.01234.x", "Wiley"), ("10.1080/09540121.2019.1234567", "Taylor & Francis"),
                          ("10.4324/9780203123456", "Taylor & Francis"), ("10.1201/9781420012345", "Taylor & Francis"),
                          ("10.1177/0002764219123456", "SAGE"), ("10.1088/1361-6528/ab1234", "IOP Publishing"),
                          ("10.1371/journal.pone.0123456", "PLOS")]:
            p = gp.major_publisher(doi)
            self.assertIsNotNone(p, doi)
            self.assertEqual((p.name, p.group, p.promote), (name, "core", True), doi)

    def test_etc_and_debatable_groups(self):
        for doi in ["10.1093/nar/gkz001", "10.1017/S0033291719000001", "10.1109/TPAMI.2019.1234567",
                    "10.1145/3290605.3300001", "10.1126/science.aaa1234", "10.1073/pnas.1900001116",
                    "10.1056/NEJMoa2001017", "10.1001/jama.2020.1585", "10.1097/01.ccm.0000123456.12345.ab",
                    "10.7554/eLife.12345", "10.15252/embj.2019101234"]:
            self.assertEqual(gp.major_publisher(doi).group, "etc", doi)
        for doi in ["10.3390/su12010001", "10.3389/fpsyg.2020.00001", "10.1155/2021/1234567",
                    "10.2147/OTT.S123456", "10.2174/1381612825666190101", "10.1504/IJBIS.2020.10001234",
                    "10.7759/cureus.1234", "10.4103/0019-5545.123456"]:
            self.assertEqual(gp.major_publisher(doi).group, "debatable", doi)

    def test_not_a_major_publisher(self):
        for doi in ["10.9999/x.1", "10.3233/JIFS-191234", "10.2991/assehr.k.200101.001", "", None, "gi:abc"]:
            self.assertIsNone(gp.major_publisher(doi), doi)

    def test_preprints_are_never_major(self):
        # SSRN is Elsevier's prefix, Nature Precedings Springer Nature's, ESSOAr Wiley's, ChemRxiv ACS's
        for doi in ["10.2139/ssrn.3456789", "10.1038/npre.2008.1684.1", "10.1002/essoar.10501234.1",
                    "10.26434/chemrxiv.1234567", "10.21203/rs.3.rs-123456/v1"]:
            self.assertIsNone(gp.major_publisher(doi), doi)

    def test_paper_mill_venue_exclusions(self):
        for doi, venue in [("10.1088/1742-6596/1168/3/032004", "Journal of Physics: Conference Series"),
                           ("10.1088/1757-899X/806/1/012009", "IOP Conference Series: Materials Science and Engineering"),
                           ("10.1088/1755-1315/1676/1/012006", "IOP Conference Series: Earth and Environmental Science"),
                           ("10.1016/j.matpr.2020.07.009", "Materials Today: Proceedings"),
                           ("10.1007/978-3-030-39162-1_8", "Springer proceedings series AISC / LNEE / LNNS"),
                           ("10.1007/978-3-030-39162-1", "Springer proceedings series AISC / LNEE / LNNS"),
                           ("10.1007/s12517-020-05689-w", "Arabian Journal of Geosciences"),
                           ("10.1177/0020720917750956", "International Journal of Electrical Engineering & Education"),
                           ("10.1111/iwj.13627", "International Wound Journal"),
                           ("10.1145/3465631.3465632", "ICIMTECH 21 (ACM conference proceedings)")]:
            p = gp.major_publisher(doi)
            self.assertFalse(p.promote, doi)
            self.assertEqual(p.venue, venue, doi)
            self.assertTrue(p.reason, doi)
        # the same prefixes' ordinary journals and books are still promoted
        for doi in ["10.1088/1361-6528/ab1234", "10.1016/j.cell.2020.01.001", "10.1007/978-3-319-12345-6_1",
                    "10.1007/s10551-019-04159-w", "10.1177/0002764219123456", "10.1111/j.1467-8624.2010.01234.x",
                    "10.1145/3290605.3300001"]:
            self.assertTrue(gp.major_publisher(doi).promote, doi)

    def test_every_tsv_row_is_well_formed(self):
        with open(gp.MAJOR_TSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        self.assertEqual(list(rows[0]), ["prefix", "suffix_regex", "publisher", "crossref_member", "imprint_notes",
                                         "group", "promote", "reason", "how_verified", "count_in_leads"])
        whole = [r["prefix"] for r in rows if not r["suffix_regex"]]
        self.assertEqual(len(whole), len(set(whole)), "a prefix has two whole-prefix rows")
        for r in rows:
            self.assertRegex(r["prefix"], r"^10\.\d{4,5}$")
            self.assertIn(r["group"], ("core", "etc", "debatable"), r)
            self.assertIn(r["promote"], ("yes", "no"), r)
            self.assertTrue(r["publisher"] and r["how_verified"], r)
            if r["promote"] == "no":
                self.assertTrue(r["reason"], r)
            if r["suffix_regex"] and not r["suffix_regex"].startswith("@"):
                re.compile(r["suffix_regex"])


class TestPairPriority(unittest.TestCase):
    ELS, WILEY, OTHER, PRE = "10.1016/j.x.2019.1", "10.1002/x.1", "10.9999/x.1", "10.2139/ssrn.1"
    MILL, MDPI = "10.1088/1742-6596/1168/3/032004", "10.3390/su12010001"

    def test_order_of_the_ranks(self):
        self.assertTrue(gp.PROMOTED < gp.POSSIBLY_PROMOTED < gp.ORDINARY < gp.POSSIBLY < gp.LATER)

    def test_later_side_major_is_promoted(self):
        self.assertEqual(gp.pair_priority(self.OTHER, self.ELS, "a"), (gp.PROMOTED, "later paper: Elsevier"))
        self.assertEqual(gp.pair_priority(self.ELS, self.OTHER, "a"), (gp.ORDINARY, None))  # copied FROM Elsevier
        self.assertEqual(gp.pair_priority(self.PRE, self.ELS, "a")[0], gp.PROMOTED)        # a journal copying a preprint

    def test_unknown_order(self):
        rank, note = gp.pair_priority(self.OTHER, self.ELS, None)
        self.assertEqual(rank, gp.POSSIBLY_PROMOTED)
        self.assertIn("Elsevier", note)
        self.assertEqual(gp.pair_priority(self.WILEY, self.ELS, None)[0], gp.PROMOTED)  # either way a major
        # a preprint on a side wins: most often the authors' own preprint of the journal paper
        self.assertEqual(gp.pair_priority(self.PRE, self.ELS, None)[0], gp.POSSIBLY)

    def test_preprint_demotion_unchanged(self):
        self.assertEqual(gp.pair_priority(self.ELS, self.PRE, "a")[0], gp.LATER)
        self.assertEqual(gp.pair_priority(self.OTHER, self.OTHER, None), (gp.ORDINARY, None))

    def test_excluded_venue_is_ordinary_with_a_note(self):
        rank, note = gp.pair_priority(self.OTHER, self.MILL, "a")
        self.assertEqual(rank, gp.ORDINARY)
        self.assertIn("Journal of Physics: Conference Series", note)
        self.assertIn("not promoted", note)
        self.assertEqual(gp.pair_priority(self.OTHER, self.MILL, None)[0], gp.ORDINARY)
        self.assertEqual(gp.pair_priority(self.MILL, self.ELS, None)[0], gp.POSSIBLY_PROMOTED)

    def test_debatable_group_is_off_by_default(self):
        self.assertEqual(gp.pair_priority(self.OTHER, self.MDPI, "a"), (gp.ORDINARY, None))
        self.assertEqual(gp.pair_priority(self.OTHER, self.MDPI, "a", groups={"core", "etc", "debatable"}),
                         (gp.PROMOTED, "later paper: MDPI"))
        self.assertEqual(gp.pair_priority(self.OTHER, self.ELS, "a", groups={"etc"}), (gp.ORDINARY, None))


class TestReviewQueueOrder(unittest.TestCase):
    """gi_review_cli.queue_key(): the agent's grouping leads; within a group major-publisher pairs first; any
    preprint demotion goes last overall; overlap breaks ties. order="overlap": overlap first in the
    cross-author / data group, the publisher breaking ties."""

    def row(self, name, decision, typ, pct):
        return {"name": name, "ai_decision": decision, "ai_type": typ, "ft_pct_a": pct, "ft_pct_b": pct / 2}

    def test_order(self):
        import gi_review_cli as cli
        rows = [
            (self.row("major no-problem", "no problem", "legitimate", 90), gp.PROMOTED),
            (self.row("ordinary cross-author", "problem", "cross-author copying", 40), gp.ORDINARY),
            (self.row("major cross-author", "problem", "cross-author copying", 30), gp.PROMOTED),
            (self.row("ordinary cross-author tie", "problem", "cross-author copying", 30), gp.ORDINARY),
            (self.row("possibly-major cross-author", "problem", "data copying", 35), gp.POSSIBLY_PROMOTED),
            (self.row("ordinary other problem", "problem", "duplicate", 95), gp.ORDINARY),
            (self.row("major other problem", "problem", "recycling", 20), gp.PROMOTED),
            (self.row("preprint cross-author", "problem", "cross-author copying", 99), gp.LATER),
            (self.row("possibly preprint", "problem", "cross-author copying", 99), gp.POSSIBLY),
            (self.row("ordinary unreviewed big", None, None, 80), gp.ORDINARY),
            (self.row("ordinary unreviewed small", None, None, 10), gp.ORDINARY),
        ]
        got = [r["name"] for r, p in sorted(rows, key=lambda rp: cli.queue_key(*rp))]
        self.assertEqual(got, ["major cross-author", "possibly-major cross-author", "ordinary cross-author",
                               "ordinary cross-author tie",
                               "major other problem", "ordinary other problem",
                               "major no-problem", "ordinary unreviewed big", "ordinary unreviewed small",
                               "possibly preprint", "preprint cross-author"])
        got = [r["name"] for r, p in sorted(rows, key=lambda rp: cli.queue_key(*rp, order="overlap"))]
        self.assertEqual(got, ["ordinary cross-author", "possibly-major cross-author", "major cross-author",
                               "ordinary cross-author tie",
                               "major other problem", "ordinary other problem",
                               "major no-problem", "ordinary unreviewed big", "ordinary unreviewed small",
                               "possibly preprint", "preprint cross-author"])


if __name__ == "__main__":
    unittest.main()

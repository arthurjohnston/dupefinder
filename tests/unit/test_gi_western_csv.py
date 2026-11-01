"""general_index/gi_western_csv.py: which side's academics count, and the likely_merge flag.

OpenAlex can merge different people with one name into one author, who then shows several unrelated
universities. likely_merge flags MERGE_UNIVERSITIES or more with none from ORCID, which the person keeps.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_western_csv as gw  # noqa: E402

OA, ORCID = gw.OPENALEX_SRC, gw.ORCID_SRC


class TestCopyingSides(unittest.TestCase):
    def test_later_paper_copies(self):
        self.assertEqual(gw.copying_sides("a"), (["b"], ""))
        self.assertEqual(gw.copying_sides("b"), (["a"], ""))

    def test_order_unknown_counts_both(self):
        self.assertEqual(gw.copying_sides(None), (["a", "b"], " (order unknown)"))


class TestMergeFlag(unittest.TestCase):
    def test_three_openalex_universities_flagged(self):
        unis = {"Harvard University": ("US", OA), "University of Calgary": ("CA", OA),
                "University of California, Davis": ("US", OA)}
        self.assertEqual(gw.merge_flag(unis), (3, "yes"))

    def test_orcid_job_clears_it(self):
        unis = {"A": ("US", OA), "B": ("US", OA), "C": ("US", ORCID)}
        self.assertEqual(gw.merge_flag(unis), (3, ""))
        unis = {"A": ("US", OA), "B": ("US", OA), "C": ("US", OA + " + " + ORCID)}
        self.assertEqual(gw.merge_flag(unis), (3, ""))

    def test_two_universities_not_flagged(self):
        self.assertEqual(gw.merge_flag({"A": ("US", OA), "B": ("GB", OA)}), (2, ""))


class TestProceedingsNoise(unittest.TestCase):
    VOLUME = ("[IEEE 2009 Chinese Control and Decision Conference (CCDC) - Guilin, China (2009.6.17-2009.6.19)] "
              "2009 Chinese Control and Decision Conference")
    OTHER_VOLUME = "SPIE Proceedings [SPIE Microlithography 2005 - San Jose, CA] Proceedings of SPIE Microlithography"
    PAPER = "New Fault Injection Approach for Network on Chip"

    def test_volume_records(self):
        self.assertTrue(gw.venue_only(self.VOLUME))
        self.assertTrue(gw.venue_only(self.OTHER_VOLUME))
        self.assertTrue(gw.venue_only("[IEEE 2005 European Microwave Conference - Paris]"))
        self.assertFalse(gw.venue_only(self.PAPER))
        self.assertFalse(gw.venue_only("[IEEE ICRA 2010 - Anchorage] Grasp planning for soft hands"))

    def test_two_volumes_dropped_volume_and_paper_kept(self):
        self.assertTrue(gw.proceedings_noise(self.VOLUME, self.OTHER_VOLUME))
        self.assertFalse(gw.proceedings_noise(self.PAPER, self.VOLUME))

    def test_front_matter_either_side(self):
        self.assertTrue(gw.proceedings_noise(self.PAPER, "[IEEE ICRA 2010 - Anchorage] Table of contents"))
        self.assertTrue(gw.proceedings_noise("Front Matter", self.PAPER))
        self.assertFalse(gw.proceedings_noise("A Clinical Competency Committee", self.PAPER))


if __name__ == "__main__":
    unittest.main()

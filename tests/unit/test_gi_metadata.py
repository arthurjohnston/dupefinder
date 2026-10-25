#!/usr/bin/env python3
"""Unit tests for gi_metadata.py: orcid_affiliations() reading every affiliation section of an ORCID record,
and target_dois() / resolve() picking the right DOIs out of fulltext and leads."""

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "general_index"))
sys.path.insert(0, str(ROOT))

import gi_metadata as gm  # noqa: E402


def _aff(kind, org, role=None, start=None, end=None, country="FR"):
    d = lambda y: {"year": {"value": y}, "month": None, "day": None} if y else None
    return {"summaries": [{f"{kind}-summary": {"organization": {"name": org, "address": {"country": country}},
                                               "role-title": role, "start-date": d(start), "end-date": d(end)}}]}


class TestOrcidAffiliations(unittest.TestCase):
    def test_every_section(self):
        raw = {"activities-summary": {
            "employments": {"affiliation-group": [_aff("employment", "Univ A", "Professor", "2018")]},
            "educations": {"affiliation-group": [_aff("education", "Univ B", "PhD", "2010", "2014")]},
            "invited-positions": {"affiliation-group": [_aff("invited-position", "Inst C")]},
            "memberships": None}}
        got = gm.orcid_affiliations(raw)
        self.assertIn(("employments", "Univ A", "Professor", "2018", "", "FR"), got)
        self.assertIn(("educations", "Univ B", "PhD", "2010", "2014", "FR"), got)
        self.assertIn(("invited-positions", "Inst C", None, "", "", "FR"), got)
        self.assertEqual(len(got), 3)

    def test_empty(self):
        self.assertEqual(gm.orcid_affiliations({}), [])
        self.assertEqual(gm.orcid_affiliations(None), [])


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            create table fulltext (doi_a text, doi_b text, run text, ft_verdict text, page_number integer);
            create table leads (pair_id integer, doi_a text, doi_b text);
            insert into fulltext values ('10.1/A', '10.1/b', 'useu_s16', null, null);
            insert into fulltext values ('10.1/c', 'gi:xyz', 'useu2_s16', 'no real overlap', null);
            insert into fulltext values ('10.1/d', '10.1/e', 'strat4_s16', 'real overlap', 12);
            insert into fulltext values ('10.1/f', '10.1/g', 'strat4_s16', 'no real overlap', null);
            insert into leads values (7, '10.1/H', '10.1/i');""")

    def test_target_dois(self):
        self.assertEqual(gm.target_dois(self.db, ["useu%"]), ["10.1/a", "10.1/b", "10.1/c", "10.1/d", "10.1/e"])
        self.assertEqual(gm.target_dois(self.db, ["useu%"], cases=False), ["10.1/a", "10.1/b", "10.1/c"])

    def test_resolve(self):
        self.assertEqual(gm.resolve(self.db, "GI-12")[0], ["10.1/d", "10.1/e"])
        self.assertEqual(gm.resolve(self.db, "7")[0], ["10.1/h", "10.1/i"])
        self.assertEqual(gm.resolve(self.db, "10.1/X")[0], ["10.1/x"])
        self.assertEqual(gm.resolve(self.db, "GI-99")[0], [])


if __name__ == "__main__":
    unittest.main()

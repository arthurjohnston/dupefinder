"""general_index/gi_affiliations.py author checks: what reading a case established about a copying paper's
authors, overriding the OpenAlex/ORCID cache for that paper. One rule (western_universities()) decides who is
Western for the review tool's --western-only and order (through doi_scores()) and for gi_western_csv.py, so
the two can't disagree; the case agent's verdicts arrive as case-folder author_checks.json files, synced so the
agent's rows are exactly what the files say, and yours (checked_by 'you') win and are never synced away.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_affiliations as ga  # noqa: E402

NOW = ga.ACTIVE_SINCE


def inst(name, country, type_="education"):
    return {"name": name, "country": country, "type": type_}


def author(insts, last_year=NOW):
    return {"last_year": last_year, "institutions": json.dumps(insts), "orcid": None}


def check(verdict, university="", author="X", now_at="", now_country="", by="agent"):
    return {"author": author, "university": university, "verdict": verdict, "now_at": now_at,
            "now_country": now_country, "basis": "", "checked_by": by}


def make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ga.ensure_tables(db)
    db.execute("""create table fulltext (page_number integer, doi_a text, doi_b text, library text, paper_a text,
                                         paper_b text, year_a integer, year_b integer)""")
    return db


def add_work(db, doi, authors):
    """authors: [(author_id, name, insts)]."""
    db.execute("insert into oa_works values (?, 1, 'x')", (doi,))
    for n, (aid, name, insts) in enumerate(authors):
        db.execute("insert into oa_authorships (doi, position, author_id, name, institutions) values (?, ?, ?, ?, '[]')",
                   (doi, n, aid, name))
        db.execute("insert or replace into oa_authors values (?, ?, null, 1, ?, ?, 'x', null)",
                   (aid, name, NOW, json.dumps(insts)))


class TestNames(unittest.TestCase):
    def test_accents_case_and_comma_order(self):
        self.assertEqual(ga.name_key("Rahérison, Chantal"), ["chantal", "raherison"])
        self.assertTrue(ga.same_name("Chantal Rahérison", "RAHERISON, Chantal", loose=False))

    def test_loose_is_surname_and_first_initial(self):
        self.assertTrue(ga.same_name("M. V. Lapresa-Alcalde", "María Victoria Lapresa-Alcalde"))
        self.assertFalse(ga.same_name("M. V. Lapresa-Alcalde", "María Victoria Lapresa-Alcalde", loose=False))
        self.assertFalse(ga.same_name("A. Smith", "B. Smith"))

    def test_assign_exact_before_loose(self):
        names = ["Alberto Alexandre", "Andrea Maria Alexandre"]
        got = ga.assign_checks([check("byline", author="Andrea Maria Alexandre")], names)
        self.assertEqual(list(got), [1])

    def test_one_person_listed_twice_takes_it_on_both(self):
        names = ["Alberto Alexandre", "Andrea Maria Alexandre", "Andrea Maria Alexandre"]
        self.assertEqual(sorted(ga.assign_checks([check("confirmed", author="Andrea Maria Alexandre")], names)),
                         [1, 2])

    def test_ambiguous_loose_match_is_left_out(self):
        names = ["Anna Alexandre", "Andrea Alexandre"]
        self.assertEqual(ga.assign_checks([check("elsewhere", author="A. Alexandre")], names), {})


class TestWesternUniversities(unittest.TestCase):
    A = author([inst("Harvard University", "US"), inst("University of Bonn", "DE"), inst("Cairo Univ", "EG")])

    def counting(self, checks):
        return [u[0] for u in ga.western_universities(self.A, None, checks) if u[4]]

    def test_no_check_is_the_cache(self):
        self.assertEqual(self.counting([]), ["Harvard University", "University of Bonn"])

    def test_author_wide_verdict_drops_every_university(self):
        for v in ga.NOT_WESTERN:
            self.assertEqual(self.counting([check(v)]), [], v)

    def test_a_verdict_on_one_university_drops_only_it(self):
        self.assertEqual(self.counting([check("elsewhere", "Harvard University")]), ["University of Bonn"])

    def test_the_specific_check_wins_over_the_author_wide_one(self):
        checks = [check("elsewhere"), check("confirmed", "University of Bonn")]
        self.assertEqual(self.counting(checks), ["University of Bonn"])

    def test_yours_win_over_the_agents(self):
        self.assertEqual(self.counting([check("elsewhere", by="agent"), check("byline", by="you")]),
                         ["Harvard University", "University of Bonn"])

    def test_moved_counts_only_to_a_western_country(self):
        self.assertEqual(self.counting([check("moved", now_at="Cairo Univ", now_country="EG")]), [])
        self.assertEqual(self.counting([check("moved", now_at="Univ Toledo", now_country="us")]),
                         ["Harvard University", "University of Bonn"])

    def test_a_check_can_add_a_western_university_the_cache_lacks(self):
        elsewhere = author([inst("Cairo Univ", "EG")])
        unis = ga.western_universities(elsewhere, None, [check("confirmed", now_at="KU Leuven", now_country="BE")])
        self.assertEqual([(u[0], u[1], u[2], u[4]) for u in unis], [("KU Leuven", "BE", ga.CHECK_SRC, True)])
        self.assertEqual(ga.western_universities(elsewhere, None, [check("byline")]), [])


class TestScoresApplyChecks(unittest.TestCase):
    """doi_scores() -- and so pair_rank(), the review tool's --western-only and its order -- reads the checks."""

    def setUp(self):
        self.db = make_db()
        add_work(self.db, "10.1/merge", [("A1", "Ann Merge", [inst("Harvard University", "US")])])
        add_work(self.db, "10.1/moved", [("A2", "Bo Moved", [inst("Cairo Univ", "EG")])])
        add_work(self.db, "10.1/plain", [("A3", "Cy Plain", [inst("Harvard University", "US")])])

    def put(self, doi, name, verdict, **kw):
        self.db.execute("insert into author_checks values (?, ?, ?, ?, ?, ?, '', 'GI-01', ?, 'x')",
                        (doi, name, kw.get("university", ""), verdict, kw.get("now_at", ""),
                         kw.get("now_country", ""), kw.get("by", "agent")))

    def test_checks_change_the_rank(self):
        self.put("10.1/merge", "Ann Merge", "elsewhere")
        self.put("10.1/moved", "Bo Moved", "moved", now_at="Univ Toledo", now_country="US")
        s = ga.doi_scores(self.db, ["10.1/merge", "10.1/moved", "10.1/plain"])
        self.assertEqual(s["10.1/merge"][0], ga.ACADEMIC)
        self.assertIn("author check: elsewhere", s["10.1/merge"][1][0][2])
        self.assertEqual(s["10.1/moved"][0], ga.TARGET)
        self.assertEqual(s["10.1/plain"][0], ga.TARGET)

    def test_csv_and_scores_agree(self):
        self.put("10.1/merge", "Ann Merge", "not on byline")
        s = ga.doi_scores(self.db, ["10.1/merge"])
        rows = ga.western_academics(self.db, "10.1/merge")
        self.assertNotEqual(s["10.1/merge"][0], ga.TARGET)
        self.assertFalse(any(u[4] for _, _, unis in rows for u in unis))


class TestSync(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.dir = Path(tempfile.mkdtemp())
        add_work(self.db, "10.1/early", [("E1", "Eve Early", [inst("Univ Ghana", "GH")])])
        add_work(self.db, "10.1/late", [("L1", "Lee Late", [inst("Harvard University", "US")]),
                                        ("L2", "Max Late", [inst("Yale University", "US")])])
        for n in (1, 2):    # two pages sharing the later paper
            self.db.execute("insert into fulltext values (?, '10.1/early', '10.1/late', null, null, null, 2010, 2015)",
                            (n,))

    def write(self, case, entries):
        (self.dir / case).mkdir(exist_ok=True)
        (self.dir / case / ga.CHECKS_FILE).write_text(json.dumps(entries))

    def rows(self):
        return sorted(tuple(r) for r in self.db.execute(
            "select doi, author, verdict, checked_by from author_checks order by 1, 2, 4"))

    def test_files_become_the_agents_rows_under_the_later_paper(self):
        self.write("GI-01", [{"author": "Lee Late", "verdict": "elsewhere"}])
        self.assertEqual(ga.sync_agent_checks(self.db, self.dir), [])
        self.assertEqual(self.rows(), [("10.1/late", "Lee Late", "elsewhere", "agent")])
        self.assertEqual([r["author"] for r in ga.case_checks(self.db, "GI-02")], ["Lee Late"])   # same paper

    def test_a_removed_entry_goes_and_yours_stay(self):
        self.write("GI-01", [{"author": "Lee Late", "verdict": "elsewhere"}])
        ga.sync_agent_checks(self.db, self.dir)
        self.db.execute("insert into author_checks values ('10.1/late', 'Max Late', '', 'confirmed', '', '', '', "
                        "'GI-01', 'you', 'x')")
        self.write("GI-01", [])
        ga.sync_agent_checks(self.db, self.dir)
        self.assertEqual(self.rows(), [("10.1/late", "Max Late", "confirmed", "you")])

    def test_unchanged_files_write_nothing(self):
        self.write("GI-01", [{"author": "Lee Late", "verdict": "byline"}])
        self.write("GI-02", [{"author": "Lee Late", "verdict": "byline"}])
        ga.sync_agent_checks(self.db, self.dir)
        before = self.db.total_changes
        ga.sync_agent_checks(self.db, self.dir)
        self.assertEqual(self.db.total_changes, before)

    def test_bad_entries_are_reported_not_stored(self):
        self.write("GI-01", [{"author": "Lee Late", "verdict": "probably"}, {"verdict": "byline"}])
        problems = ga.sync_agent_checks(self.db, self.dir)
        self.assertEqual(len(problems), 2)
        self.assertEqual(self.rows(), [])


class TestLookedAt(unittest.TestCase):
    def test_flagged_pairs(self):
        import gi_review_cli as cli
        db = sqlite3.connect(":memory:")
        self.assertEqual(cli.looked_at_pairs(db), set())
        db.executescript(cli.FLAGS_DDL)
        db.execute("insert into review_flags values ('p1', 'p2', ?, 'x')", (cli.LOOKED_AT,))
        db.execute("insert into review_flags values ('p3', 'p4', 'paper mill', 'x')")
        self.assertEqual(cli.looked_at_pairs(db), {("p1", "p2")})


class TestDoiCountries(unittest.TestCase):
    """doi_countries(): the review tool's --country -- the byline's countries, the authors' current universities,
    and the checks (a disowned university doesn't count, an 'on earlier paper' author counts for nothing)."""

    def setUp(self):
        self.db = make_db()
        add_work(self.db, "10.1/x", [("A1", "Asha Rao", [inst("IIT Delhi", "IN")]),
                                     ("A2", "Ben Ko", [inst("Seoul Natl Univ", "KR"), inst("A Lab", "US", "company")])])
        self.db.execute("""update oa_authorships set institutions = ? where doi = '10.1/x' and position = 0""",
                        (json.dumps([inst("Dhaka Univ", "BD")]),))

    def add_check(self, verdict, author, university="", now_country=""):
        self.db.execute("insert into author_checks (doi, author, university, verdict, now_country, checked_by) "
                        "values ('10.1/x', ?, ?, ?, ?, 'agent')", (author, university, verdict, now_country))

    def test_byline_and_current_universities(self):
        self.assertEqual(ga.doi_countries(self.db, ["10.1/X"]), {"10.1/x": {"BD", "IN", "KR"}})

    def test_checks(self):
        self.add_check("name only", "Ben Ko", "Seoul Natl Univ")
        self.add_check("on earlier paper", "Asha Rao")
        self.assertEqual(ga.doi_countries(self.db, ["10.1/x"])["10.1/x"], set())
        self.add_check("moved", "Ben Ko", now_country="nz")
        self.assertEqual(ga.doi_countries(self.db, ["10.1/x"])["10.1/x"], {"NZ"})

    def test_no_data(self):
        self.assertEqual(ga.doi_countries(self.db, ["10.1/none"]), {"10.1/none": set()})

    def test_pair_countries_and_parse(self):
        c = {"10.1/a": {"US"}, "10.1/b": {"IN"}}
        self.assertEqual(ga.pair_countries(c, "10.1/a", "10.1/b", "a"), {"IN"})
        self.assertEqual(ga.pair_countries(c, "10.1/a", "10.1/b", None), {"US", "IN"})
        self.assertEqual(ga.parse_countries("in, pk"), {"IN", "PK"})
        self.assertIn("NZ", ga.parse_countries("west"))
        with self.assertRaises(ValueError):
            ga.parse_countries("India")


if __name__ == "__main__":
    unittest.main()

"""general_index/gi_affiliations.py: ranking cases by who the later paper's authors are now.

An author is target (still publishing, at a US/European university), academic (still publishing, at a
university elsewhere), known (neither) or unknown (no data); a DOI takes its best author, a pair its later
paper. Europe is everything west of Russia: Ukraine and Belarus in, Russia and Turkey out.
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_affiliations as ga  # noqa: E402

NOW = ga.ACTIVE_SINCE


def inst(name, country, type_="education"):
    return {"name": name, "country": country, "type": type_}


def author(last_year, insts, orcid=None):
    return {"last_year": last_year, "institutions": json.dumps(insts), "orcid": orcid}


class TestCountries(unittest.TestCase):
    def test_europe_is_west_of_russia(self):
        for cc in ("DE", "GB", "UA", "BY", "MD", "NO", "CH", "RS", "IS"):
            self.assertIn(cc, ga.TARGET_COUNTRIES, cc)
        for cc in ("RU", "TR", "GE", "AM", "AZ", "KZ", "CN", "IN"):
            self.assertNotIn(cc, ga.TARGET_COUNTRIES, cc)
        self.assertIn("US", ga.TARGET_COUNTRIES)


class TestAuthorStatus(unittest.TestCase):
    def test_active_at_a_target_university(self):
        st, why = ga.author_status(author(NOW, [inst("Univ Bonn", "DE")]), None)
        self.assertEqual(st, "target")
        self.assertIn("Univ Bonn", why)

    def test_target_wins_over_another_academic_institution(self):
        st, _ = ga.author_status(author(NOW, [inst("IIT Delhi", "IN"), inst("MIT", "US")]), None)
        self.assertEqual(st, "target")

    def test_active_at_a_university_elsewhere(self):
        self.assertEqual(ga.author_status(author(NOW + 1, [inst("Ankara Univ", "TR")]), None)[0], "academic")

    def test_stopped_publishing_is_known(self):
        self.assertEqual(ga.author_status(author(NOW - 1, [inst("Harvard", "US")]), None)[0], "known")
        self.assertEqual(ga.author_status(author(None, [inst("Harvard", "US")]), None)[0], "known")

    def test_active_at_a_company_is_known(self):
        st, why = ga.author_status(author(NOW, [inst("Acme", "US", "company")]), None)
        self.assertEqual(st, "known")
        self.assertIn("not typed a university", why)

    def test_active_with_no_institution_is_unknown(self):
        self.assertEqual(ga.author_status(author(NOW, []), None)[0], "unknown")
        self.assertEqual(ga.author_status(author(NOW, []), [])[0], "unknown")   # ORCID, but no current job

    def test_current_orcid_job_makes_an_author_active_and_academic(self):
        jobs = [{"name": "Universität Wien", "country": "AT"}]
        st, why = ga.author_status(author(NOW - 5, []), jobs)
        self.assertEqual(st, "target")
        self.assertIn("ORCID", why)

    def test_current_orcid_job_outside_academia(self):
        self.assertEqual(ga.author_status(author(NOW - 5, []), [{"name": "Pfizer", "country": "US"}])[0],
                         "known")


class TestOrcidCurrent(unittest.TestCase):
    def test_only_jobs_with_no_end_date(self):
        data = {"affiliation-group": [
            {"summaries": [{"employment-summary": {"organization": {"name": "Brown University",
                                                                    "address": {"country": "US"}},
                                                   "end-date": None}}]},
            {"summaries": [{"employment-summary": {"organization": {"name": "Old Place",
                                                                    "address": {"country": "FR"}},
                                                   "end-date": {"year": {"value": "2015"}}}}]}]}
        self.assertEqual(ga.orcid_current(data), [{"name": "Brown University", "country": "US"}])
        self.assertEqual(ga.orcid_current({}), [])


def make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ga.ensure_tables(db)
    return db


def add_work(db, doi, authors):
    """authors: [(author_id, last_year, insts)]; author_id None for an authorship with no author record."""
    db.execute("insert into oa_works values (?, 1, 'x')", (doi,))
    for n, (aid, year, insts) in enumerate(authors):
        db.execute("insert into oa_authorships (doi, position, author_id, name, institutions) "
                   "values (?, ?, ?, ?, '[]')", (doi, n, aid, f"name {aid}"))
        if aid:
            db.execute("insert or replace into oa_authors values (?, ?, null, 1, ?, ?, 'x', null)",
                       (aid, f"name {aid}", year, json.dumps(insts)))


class TestScores(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        add_work(self.db, "10.1/target", [("A1", NOW - 3, [inst("Harvard", "US")]),
                                          ("A2", NOW, [inst("KU Leuven", "BE")])])
        add_work(self.db, "10.1/academic", [("A3", NOW, [inst("Cairo Univ", "EG")])])
        add_work(self.db, "10.1/other", [("A4", NOW - 8, [inst("Harvard", "US")])])
        add_work(self.db, "10.1/noid", [(None, None, None)])
        self.db.execute("insert into oa_works values ('10.1/notfound', 0, 'x')")
        self.scores = ga.doi_scores(self.db, ["10.1/TARGET", "10.1/academic", "10.1/other", "10.1/noid",
                                              "10.1/notfound", "10.1/unfetched", "gi:abc"])

    def test_a_doi_takes_its_best_author(self):
        self.assertEqual(self.scores["10.1/target"][0], ga.TARGET)
        self.assertEqual(self.scores["10.1/academic"][0], ga.ACADEMIC)
        self.assertEqual(self.scores["10.1/other"][0], ga.OTHER)

    def test_no_data_is_unknown_not_other(self):
        for d in ("10.1/noid", "10.1/notfound", "10.1/unfetched", "gi:abc"):
            self.assertEqual(self.scores[d][0], ga.UNKNOWN, d)

    def test_pair_takes_the_later_papers_rank(self):
        s = self.scores
        self.assertEqual(ga.pair_rank(s, "10.1/target", "10.1/other", "a"), (ga.OTHER, "b"))
        self.assertEqual(ga.pair_rank(s, "10.1/target", "10.1/other", "b"), (ga.TARGET, "a"))
        self.assertEqual(ga.pair_rank(s, "10.1/other", "10.1/target", None), (ga.TARGET, "b"))

    def test_describe_names_the_qualifying_author(self):
        line = ga.describe(self.scores, "10.1/target")
        self.assertTrue(line.startswith("US/Europe academic"))
        self.assertIn("name A2", line)
        self.assertNotIn("name A1", line)     # stopped publishing: not why it ranks


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls, self.headers = handler, [], {}

    def get(self, url, params=None, timeout=None, stream=False):
        self.calls.append((url, params))
        return self.handler(url, params)


class TestFetcher(unittest.TestCase):
    def handler(self, url, params):
        if "/works" in url:
            return FakeResp(200, {"results": [{"doi": "https://doi.org/10.1/a", "authorships": [
                {"author": {"id": "https://openalex.org/A9", "display_name": "Jo Doe"},
                 "institutions": [{"display_name": "Univ Oslo", "country_code": "NO", "type": "education"}]}]}]})
        return FakeResp(200, {"results": [{"id": "https://openalex.org/A9", "display_name": "Jo Doe",
                                           "orcid": None, "works_count": 5,
                                           "counts_by_year": [{"year": NOW, "works_count": 1}],
                                           "last_known_institutions": [{"display_name": "Univ Oslo",
                                                                        "country_code": "NO",
                                                                        "type": "education"}]}]})

    def test_fetch_caches_and_marks_missing_dois(self):
        db = make_db()
        session = FakeSession(self.handler)
        f = ga.Fetcher(db, session=session, orcid=False)
        f.limiter = ga.rp.RateLimiter(0)
        f.ensure(["10.1/A", "10.1/missing", "gi:xyz"])
        self.assertEqual(len(session.calls), 2)                     # one works batch, one authors batch
        self.assertNotIn("gi:xyz", session.calls[0][1]["filter"])
        self.assertEqual(dict(db.execute("select doi, found from oa_works").fetchall()),
                         {"10.1/a": 1, "10.1/missing": 0})
        self.assertEqual(ga.doi_scores(db, ["10.1/a"])["10.1/a"][0], ga.TARGET)
        f.ensure(["10.1/A", "10.1/missing"])                         # all cached: no new calls
        self.assertEqual(len(session.calls), 2)

    def test_rate_limit_stops_openalex_for_the_run(self):
        db = make_db()
        session = FakeSession(lambda url, params: FakeResp(429, {}))
        f = ga.Fetcher(db, session=session, orcid=False)
        f.limiter = ga.rp.RateLimiter(0)
        msg = f.ensure([f"10.1/{i}" for i in range(120)])
        self.assertEqual(len(session.calls), 1)
        self.assertIn("429", msg)
        self.assertEqual(db.execute("select count(*) from oa_works").fetchone()[0], 0)   # retried next run

    def test_call_budget(self):
        db = make_db()
        session = FakeSession(lambda url, params: FakeResp(200, {"results": []}))
        f = ga.Fetcher(db, max_calls=2, session=session, orcid=False)
        f.limiter = ga.rp.RateLimiter(0)
        self.assertIn("--max-calls 2", f.ensure([f"10.1/{i}" for i in range(200)]))
        self.assertEqual(len(session.calls), 2)


class TestWholeRecordKept(unittest.TestCase):
    def test_raw_stored_and_backfill_refetches_old_rows(self):
        db = make_db()
        f = ga.Fetcher(db, session=FakeSession(TestFetcher().handler), orcid=False)
        f.ensure(["10.1/a"])
        raw = json.loads(db.execute("select raw from oa_authorships").fetchone()[0])
        self.assertEqual(raw["institutions"][0]["display_name"], "Univ Oslo")
        self.assertIsNotNone(db.execute("select raw from oa_authors").fetchone()[0])
        self.assertEqual(json.loads(db.execute("select institutions from oa_authorships").fetchone()[0])[0]["name"],
                         "Univ Oslo")
        # rows from before `raw` existed: backfill refetches exactly those
        db.execute("update oa_authorships set raw = null")
        db.execute("update oa_authors set raw = null")
        session = FakeSession(TestFetcher().handler)
        got = ga.Fetcher(db, session=session, orcid=False).backfill()
        self.assertEqual(got, {"works": 1, "authors": 1})
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(db.execute("select count(*) from oa_authorships where raw is null").fetchone()[0], 0)
        self.assertEqual(db.execute("select count(*) from oa_authors where raw is null").fetchone()[0], 0)
        self.assertEqual(ga.Fetcher(db, session=FakeSession(TestFetcher().handler), orcid=False).backfill(),
                         {"works": 0, "authors": 0})

    def test_migration_adds_raw_to_old_tables(self):
        db = sqlite3.connect(":memory:")
        db.execute("create table oa_authors (author_id text primary key, name text, orcid text, "
                   "works_count integer, last_year integer, institutions text, fetched_at text)")
        db.execute("insert into oa_authors values ('A1', 'x', null, 1, 2020, '[]', 'x')")
        ga.ensure_tables(db)
        self.assertIn("raw", [r[1] for r in db.execute("pragma table_info(oa_authors)")])
        self.assertEqual(db.execute("select raw from oa_authors").fetchone()[0], None)

    def test_orcid_all_keeps_past_jobs(self):
        body = {"affiliation-group": [{"summaries": [
            {"employment-summary": {"organization": {"name": "Univ A", "address": {"country": "DE", "city": "Bonn"},
                                                     "disambiguated-organization": {
                                                         "disambiguation-source": "ROR",
                                                         "disambiguated-organization-identifier": "https://ror.org/x"}},
                                    "role-title": "Lecturer", "start-date": {"year": {"value": "2010"}},
                                    "end-date": {"year": {"value": "2015"}, "month": {"value": "06"}}}},
            {"employment-summary": {"organization": {"name": "Univ B"}, "end-date": None}}]}]}
        jobs = ga.orcid_all(body)
        self.assertEqual(jobs[0], {"name": "Univ A", "country": "DE", "city": "Bonn", "department": None,
                                   "role": "Lecturer", "start": "2010", "end": "2015-06",
                                   "org_id": "ROR https://ror.org/x"})
        self.assertEqual((jobs[1]["name"], jobs[1]["end"]), ("Univ B", None))
        self.assertEqual(ga.orcid_current(body), [{"name": "Univ B", "country": None}])


class TestBadDoiBisection(unittest.TestCase):
    """One malformed DOI makes OpenAlex reject its whole batch (HTTP 400): halve until it's alone, record it
    as not found, and keep the other 49."""

    def test_bad_doi_is_isolated(self):
        def handler(url, params):
            ids = params["filter"].split(":", 1)[1].split("|")
            if "10.1/bad" in ids:
                return FakeResp(400, {})
            return FakeResp(200, {"results": [{"doi": "https://doi.org/" + d, "authorships": []} for d in ids]})
        db = make_db()
        f = ga.Fetcher(db, session=FakeSession(handler), orcid=False)
        f.limiter = ga.rp.RateLimiter(0)
        f.ensure([f"10.1/{i}" for i in range(49)] + ["10.1/bad"])
        found = dict(db.execute("select doi, found from oa_works").fetchall())
        self.assertEqual(len(found), 50)
        self.assertEqual(found["10.1/bad"], 0)
        self.assertEqual(sum(found.values()), 49)


class TestChunking(unittest.TestCase):
    def test_more_dois_than_sqlite_variables(self):
        db = make_db()
        dois = [f"10.1/{i}" for i in range(40_000)]
        db.executemany("insert into oa_works values (?, 0, 'x')", [(d,) for d in dois])
        scores = ga.doi_scores(db, dois)
        self.assertEqual(len(scores), 40_000)


class TestScorePairs(unittest.TestCase):
    def test_stores_later_papers_rank_and_skips_on_stop(self):
        db = make_db()
        add_work(db, "10.1/old", [("A1", NOW - 9, [inst("Harvard", "US")])])
        add_work(db, "10.1/new", [("A2", NOW, [inst("Univ Oslo", "NO")])])
        f = ga.Fetcher(db, session=FakeSession(lambda u, p: FakeResp(200, {"results": []})), orcid=False)
        rows = [(1, "lo", "hi", "10.1/old", "10.1/new", 4, 7, "2001", "2015"),
                (2, "lo2", "hi2", "10.1/new", "10.1/old", 4, 8, "2001", "2015")]
        ga.score_pairs(db, f, rows)
        self.assertEqual(ga.stored_ranks(db, [1, 2]), {1: ga.TARGET, 2: ga.OTHER})
        f.stopped = "budget"
        self.assertIsNone(ga.score_pairs(db, f, [(3, "a", "b", "10.1/x", "10.1/y", 4, 9, None, None)]))
        self.assertEqual(ga.stored_ranks(db, [3]), {})


class TestReviewQueueAffiliationOrder(unittest.TestCase):
    """gi_review_cli.queue_key(order="affiliation"): preprint demotion still last; the agent's problem calls
    before the rest; within each, the authors' rank, then the publisher, then overlap."""

    def test_order(self):
        import gi_preprints as gp
        import gi_review_cli as cli
        row = lambda name, decision, typ, pct: {"name": name, "ai_decision": decision, "ai_type": typ,
                                                "ft_pct_a": pct, "ft_pct_b": pct}
        rows = [
            (row("major cross-author, elsewhere", "problem", "cross-author copying", 90), gp.PROMOTED, ga.ACADEMIC),
            (row("ordinary cross-author, target", "problem", "cross-author copying", 20), gp.ORDINARY, ga.TARGET),
            (row("other problem, target", "problem", "duplicate", 10), gp.ORDINARY, ga.TARGET),
            (row("cross-author, unknown", "problem", "cross-author copying", 99), gp.ORDINARY, None),
            (row("cross-author, not in academia", "problem", "cross-author copying", 99), gp.ORDINARY, ga.OTHER),
            (row("no problem, target", "no problem", "legitimate", 99), gp.ORDINARY, ga.TARGET),
            (row("preprint, target", "problem", "cross-author copying", 99), gp.LATER, ga.TARGET),
        ]
        got = [r["name"] for r, p, a in sorted(rows, key=lambda x: cli.queue_key(x[0], x[1], "affiliation", x[2]))]
        self.assertEqual(got, ["ordinary cross-author, target", "other problem, target",
                               "major cross-author, elsewhere", "cross-author, unknown",
                               "cross-author, not in academia", "no problem, target", "preprint, target"])


class TestProfessors(unittest.TestCase):
    def job(self, role, country="DE", end=None):
        return {"name": "Univ A", "country": country, "role": role, "end": end}

    def test_professor_job(self):
        for role in ("Professor", "Associate Professor", "Prof. Dr.", "Senior Lecturer", "Privatdozent",
                     "Profesor titular", "Maître de conférences"):
            self.assertIsNotNone(ga.professor_job([self.job(role)]), role)
        for job in (self.job("Professor Emeritus"), self.job("Visiting Professor"), self.job("Researcher"),
                    self.job(None), self.job("Professor", country="CN"), self.job("Professor", end="2019")):
            self.assertIsNone(ga.professor_job([job]), job)
        self.assertEqual(ga.professor_job([self.job("Researcher"), self.job("Lecturer", "US")])["role"], "Lecturer")

    def test_doi_professors_and_pair(self):
        db = make_db()
        add_work(db, "10.1/a", [("A1", NOW, []), ("A2", NOW, [])])
        add_work(db, "10.1/b", [("A3", NOW, [])])
        db.execute("update oa_authors set orcid = 'O1' where author_id = 'A2'")
        db.execute("insert into orcid_employments values ('O1', '[]', 'x', ?)",
                   (json.dumps([self.job("Professor emeritus"), self.job("Assistant Professor", "NL")]),))
        profs = ga.doi_professors(db, ["10.1/A", "10.1/b"])
        self.assertEqual(profs, {"10.1/a": [("name A2", "Assistant Professor, Univ A, NL")]})
        self.assertEqual(ga.pair_professors(profs, "10.1/a", "10.1/b", "b"), profs["10.1/a"])
        self.assertEqual(ga.pair_professors(profs, "10.1/a", "10.1/b", "a"), [])
        self.assertEqual(ga.pair_professors(profs, "10.1/a", "10.1/b", None), profs["10.1/a"])


class TestReviewQueueProfessorOrder(unittest.TestCase):
    """queue_key(order="professor"): a US/European professor first, then the affiliation rank, above the
    agent's grouping and the preprint demotion; those still order pairs within a rank."""

    def test_order(self):
        import gi_preprints as gp
        import gi_review_cli as cli
        row = lambda name, decision, typ, pct: {"name": name, "ai_decision": decision, "ai_type": typ,
                                                "ft_pct_a": pct, "ft_pct_b": pct}
        rows = [
            (row("cross-author, elsewhere", "problem", "cross-author copying", 90), gp.ORDINARY, ga.ACADEMIC, False),
            (row("no problem, target", "no problem", "legitimate", 99), gp.ORDINARY, ga.TARGET, False),
            (row("cross-author, target", "problem", "cross-author copying", 20), gp.ORDINARY, ga.TARGET, False),
            (row("preprint, professor", "problem", "cross-author copying", 99), gp.LATER, ga.TARGET, True),
            (row("no problem, professor", "no problem", "legitimate", 50), gp.ORDINARY, ga.TARGET, True),
            (row("cross-author, professor", "problem", "cross-author copying", 10), gp.ORDINARY, ga.TARGET, True),
            (row("cross-author, unknown", "problem", "cross-author copying", 99), gp.ORDINARY, None, False),
        ]
        got = [r["name"] for r, p, a, prof in sorted(rows, key=lambda x: cli.queue_key(x[0], x[1], "professor",
                                                                                       x[2], x[3]))]
        self.assertEqual(got, ["cross-author, professor", "no problem, professor", "preprint, professor",
                               "cross-author, target", "no problem, target", "cross-author, elsewhere",
                               "cross-author, unknown"])


if __name__ == "__main__":
    unittest.main()

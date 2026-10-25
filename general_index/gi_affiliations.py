"""Who the authors are now: OpenAlex (and ORCID) author data for case and lead DOIs, and a rank for targeting.

The point is to spend review and download effort on cases whose authors are still in academia, at a US or
European (west of Russia) university first. For each DOI, OpenAlex's work record gives its authors
(disambiguated author ids); each author's record gives the years they published in (counts_by_year) and their
last known institutions (with ROR type and country); an author with an ORCID also has ORCID's employment list,
whose entries with no end date are current jobs. An author is:
  target    active (a work in ACTIVE_SINCE or later, or a current ORCID job) and at an academic institution
            (OpenAlex type 'education', or a current ORCID job whose name looks academic) in TARGET_COUNTRIES
  academic  active and at an academic institution elsewhere
  known     has an OpenAlex record but is neither (stopped publishing, or not at a university)
  unknown   no record, or a record with no institution and recent works (can't tell)
A DOI takes its best author: rank TARGET (0) if any author is target, ACADEMIC (1) if any is academic, UNKNOWN
(2) if any is unknown or the DOI has no OpenAlex record, OTHER (3) when every author is known and neither. A
pair takes the rank of its later (copying) paper; with the order unknown, the better of the two sides.

Caveats: OpenAlex merges some same-name people into one author (a common name can look active when it isn't;
the ORCID check helps where there is one), and ~45% of authorships carry no institution at all. Both make
UNKNOWN common, and it ranks above OTHER on purpose: no data is not evidence against.

Cached in gi.sqlite3 (oa_works, oa_authorships, oa_authors, orcid_employments); nothing is refetched once
stored. Each row also keeps the whole record it came from (`raw`: raw affiliation strings, institution ids and
ROR ids, an author's affiliation history with years, citation counts, ORCID's past employments), beyond the few
fields the ranking reads, so later targeting can use it without refetching. OpenAlex calls are filter calls (1
per 50 DOIs / 50 authors), so the free daily budget (10,000) covers tens of thousands of DOIs; --max-calls caps a run, and a 429 stops OpenAlex fetching for the run (the rest stay
unknown and are fetched next time). The key comes from $OPENALEX_API_KEY only.

Usage:
  gi_affiliations.py fetch [--max-calls 3000] [--no-orcid]   (every full-text-checked pair's DOIs)
  gi_affiliations.py leads [--sets tier2 band4 rest4] [--max-calls 400000] [--orcid] [--rescore]
      (the leads download batches draw from -- LEAD_SETS, ~1.08M pairs -- in lead_rank order, into
      pair_affiliation; resumable: already scored leads are skipped. ORCID off by default: a request per author
      is days at this size. Past the free 10,000 calls a day, OpenAlex draws on the prepaid balance)
  gi_affiliations.py backfill [--max-calls 400000] [--orcid]  (refetch rows cached before `raw` was kept)
  gi_affiliations.py show GI-12 [GI-40 ...]                  (each side's authors and why they rank as they do)
  gi_affiliations.py summary                                 (unreviewed result pages by rank)
gi_review_cli.py --order affiliation / --us-europe-only and gi_verify.py select --order affiliation use it.
"""
import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402

import gi_db  # noqa: E402

DDL = """
create table if not exists oa_works (doi text primary key, found integer, fetched_at text);
create table if not exists oa_authorships (   -- one row per author of a work, in byline order
    doi text, position integer, author_id text, name text,
    institutions text,          -- json [{name, country, type, id, ror}] as printed on that paper
    raw text,                   -- json: OpenAlex's whole authorship (raw affiliation strings, lineage, ...)
    primary key (doi, position));
create index if not exists idx_oa_authorships_author on oa_authorships(author_id);
create table if not exists oa_authors (
    author_id text primary key, name text, orcid text, works_count integer,
    last_year integer,          -- latest year with a work (counts_by_year: the last ~10 years only)
    institutions text,          -- json [{name, country, type, id, ror}]: last_known_institutions
    fetched_at text,
    raw text);                  -- json: the whole author record as fetched (AUTHOR_FIELDS: affiliation history
                                --   with years, citation counts, name variants, ...)
create table if not exists pair_affiliation (   -- `leads`: a lead's rank, from the cache above
    pair_id integer primary key, pair_lo text, pair_hi text, tier integer, lead_rank integer,
    rank integer,               -- TARGET 0 / ACADEMIC 1 / UNKNOWN 2 / OTHER 3
    side text,                  -- 'a' / 'b': the side the rank is from (the later paper, or the better side)
    scored_at text);
create table if not exists orcid_employments (
    orcid text primary key,
    current text,               -- json [{name, country}]: employments with no end date
    fetched_at text,
    raw text);                  -- json: every employment, past ones too (orcid_all)
"""

# columns added after the tables first held data (`backfill` fills them in for rows fetched before)
MIGRATIONS = {"oa_authorships": ["raw"], "oa_authors": ["raw"], "orcid_employments": ["raw"]}

TARGET, ACADEMIC, UNKNOWN, OTHER = 0, 1, 2, 3
LABELS = {TARGET: "US/Europe academic", ACADEMIC: "academic elsewhere", UNKNOWN: "unknown",
          OTHER: "not active in academia"}

# The US, and Europe west of Russia: Russia itself, Turkey and the Caucasus are left out, Belarus, Ukraine and
# Moldova are in. ISO 3166-1 alpha-2, as OpenAlex and ORCID give them (XK: Kosovo).
EUROPE = frozenset("""AD AL AT BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GI GR HR HU IE IM IS IT LI LT LU LV
                      MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM UA VA XK""".split())
TARGET_COUNTRIES = EUROPE | {"US"}
ACADEMIC_TYPES = frozenset({"education"})       # OpenAlex / ROR institution types
ACTIVE_SINCE = date.today().year - 2             # a work in this year or later is "still publishing"
# An ORCID employer has no type: a current job counts as academic when its name looks like a university's
ACADEMIC_NAME = re.compile(r"univ|college|hochschule|polytechn|politecnic|école|ecole|escuela|institut|"
                           r"academ|faculty|school of|universidad|università|universit|uniwersytet|egyetem|"
                           r"yliopisto|högskola|hogskole|eth zurich|epfl|\bkth\b|\bmit\b", re.I)
# a teaching post, from an ORCID job's role title (English, German, Dutch/Nordic, Romance spellings); an
# emeritus / retired / former one is no longer working
PROFESSOR_ROLE = re.compile(r"\bprof|lecturer|\breader\b|dozent|docent|catedr|ma[iî]tre de conf", re.I)
NOT_WORKING = re.compile(r"emerit|retired|former|honorary|visiting", re.I)

AUTHORS_API = "https://api.openalex.org/authors"
ORCID_API = "https://pub.orcid.org/v3.0/{}/employments"
# everything an author record has on who they are and where they've been; `raw` keeps all of it
AUTHOR_FIELDS = ("id,display_name,display_name_alternatives,orcid,works_count,cited_by_count,summary_stats,"
                 "counts_by_year,affiliations,last_known_institutions")
BATCH = rp.OPENALEX_BATCH_SIZE
CHUNK = 5000      # ids per SQL "in (...)" lookup: SQLite allows 32,766 variables in a query
log = logging.getLogger("gi_affiliations")


def ensure_tables(db):
    db.executescript(DDL)
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in db.execute(f"pragma table_info({table})")}
        for c in cols:
            if c not in have:
                db.execute(f"alter table {table} add column {c} text")
    db.commit()


def _now():
    return f"{datetime.now():%Y-%m-%d %H:%M}"


def _short(openalex_id):
    return (openalex_id or "").rsplit("/", 1)[-1] or None


def _insts(raw):
    return [{"name": i.get("display_name"), "country": i.get("country_code"), "type": i.get("type"),
             "id": _short(i.get("id")), "ror": _short(i.get("ror"))}
            for i in raw or [] if i.get("display_name") or i.get("country_code")]


def _json(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def queryable(doi):
    """A real DOI OpenAlex's doi filter can take ('gi:' keys have none; ',' and '|' would break the filter)."""
    d = (doi or "").lower()
    return d.startswith("10.") and not any(c in d for c in ",|")


class Fetcher:
    """OpenAlex batches under a call budget, and ORCID one id at a time. A 429 (or the budget running out)
    stops OpenAlex fetching for the rest of the run; what's left stays unfetched, so ranks as unknown. A batch
    OpenAlex rejects (HTTP 400: one malformed DOI spoils all 50) is split in halves until the bad DOI is alone,
    and that one is recorded as not found."""

    def __init__(self, db, max_calls=3000, orcid=True, session=None):
        self.db, self.max_calls, self.orcid = db, max_calls, orcid
        self.session = session or requests.Session()
        self.orcid_session = requests.Session()
        self.orcid_session.headers["Accept"] = "application/json"   # ORCID answers XML otherwise
        self.limiter = rp.RateLimiter(0.11)
        self.calls, self.stopped, self.status = 0, None, None
        ensure_tables(db)

    def _openalex(self, url, filt, select):
        self.status = None
        if self.stopped:
            return None
        if self.calls >= self.max_calls:
            self.stopped = f"--max-calls {self.max_calls} reached"
            return None
        params = {"filter": filt, "per-page": BATCH, "select": select}
        key = os.environ.get(rp.OPENALEX_API_KEY_ENV_VAR)
        if key:
            params["api_key"] = key
        self.calls += 1
        try:
            resp = rp.http_get(self.session, url, params, rate_limiter=self.limiter, max_retries=0, timeout=60,
                               logger=log)
        except rp.RateLimited:
            self.stopped = "OpenAlex rate limit (429)"
            return None
        except rp.RetrievalError as exc:
            log.warning("OpenAlex request failed: %s", exc)
            return None
        self.status = resp.status_code
        if resp.status_code == 429:
            self.stopped = "OpenAlex rate limit (429)"
            return None
        if resp.status_code != 200:
            log.warning("OpenAlex HTTP %d (%d ids)", resp.status_code, filt.count("|") + 1)
            return None
        return resp.json().get("results", [])

    def _store_works(self, batch, got):
        _retry_locked(self.db, self._write_works, batch, got)

    def _write_works(self, batch, got):
        now = _now()
        for d in batch:
            self.db.execute("insert or replace into oa_works values (?, ?, ?)", (d, int(d in got), now))
            self.db.execute("delete from oa_authorships where doi = ?", (d,))
            self.db.executemany("insert into oa_authorships (doi, position, author_id, name, institutions, raw) "
                                "values (?, ?, ?, ?, ?, ?)", [
                (d, n, _short((a.get("author") or {}).get("id")), (a.get("author") or {}).get("display_name")
                 or a.get("raw_author_name"), json.dumps(_insts(a.get("institutions")), ensure_ascii=False),
                 _json(a))
                for n, a in enumerate(got.get(d, []))])
        self.db.commit()

    def _works_batch(self, batch):
        res = self._openalex(rp.OPENALEX_API, "doi:" + "|".join(batch), "doi,authorships")
        if res is None:
            if self.status == 400:
                if len(batch) == 1:
                    log.warning("OpenAlex rejects DOI %r: recorded as not found", batch[0])
                    self._store_works(batch, {})
                else:
                    self._works_batch(batch[:len(batch) // 2])
                    self._works_batch(batch[len(batch) // 2:])
            return            # otherwise a failed batch: left unfetched, retried next run
        got = {}
        for w in res:
            d = (rp.normalize_doi(w.get("doi")) or "").lower()
            if d:
                got[d] = w.get("authorships") or []
        self._store_works(batch, got)

    def works(self, dois):
        """dois: lowercased, at most CHUNK of them."""
        have = {r[0] for r in self.db.execute(f"select doi from oa_works where doi in ({_qs(dois)})", dois)}
        need = sorted({d for d in dois if queryable(d)} - have)
        for i in range(0, len(need), BATCH):
            self._works_batch(need[i:i + BATCH])
            if self.stopped:
                break
        return len(need)

    def _authors_batch(self, batch):
        res = self._openalex(AUTHORS_API, "openalex:" + "|".join(batch), AUTHOR_FIELDS)
        if res is None:
            if self.status == 400 and len(batch) > 1:
                self._authors_batch(batch[:len(batch) // 2])
                self._authors_batch(batch[len(batch) // 2:])
            return
        _retry_locked(self.db, self._write_authors, res)

    def _write_authors(self, res):
        now = _now()
        for a in res:
            years = [c["year"] for c in a.get("counts_by_year") or [] if c.get("works_count")]
            self.db.execute("insert or replace into oa_authors values (?, ?, ?, ?, ?, ?, ?, ?)", (
                _short(a.get("id")), a.get("display_name"), _short(a.get("orcid")), a.get("works_count"),
                max(years) if years else None,
                json.dumps(_insts(a.get("last_known_institutions")), ensure_ascii=False), now, _json(a)))
        self.db.commit()

    def authors(self, dois):
        rows = self.db.execute(f"""select distinct author_id from oa_authorships
                                   where author_id is not null and doi in ({_qs(dois)})
                                   and author_id not in (select author_id from oa_authors)""", dois).fetchall()
        need = sorted(r[0] for r in rows)
        for i in range(0, len(need), BATCH):
            self._authors_batch(need[i:i + BATCH])
            if self.stopped:
                break
        return len(need)

    def orcids(self, dois):
        """ORCID jobs for every author of these DOIs with an ORCID: current ones for the rank, and the role titles
        (professor_job) even for authors OpenAlex alone already ranks target."""
        if not self.orcid:
            return 0
        rows = self.db.execute(f"""select distinct au.* from oa_authorships s join oa_authors au using (author_id)
                                   where s.doi in ({_qs(dois)}) and au.orcid is not null
                                   and au.orcid not in (select orcid from orcid_employments)""", dois).fetchall()
        need = [r["orcid"] for r in rows]
        self.fetch_orcids(need)
        return len(need)

    def fetch_orcids(self, need):
        limiter = rp.RateLimiter(0.1)
        for n, orcid in enumerate(need, 1):
            try:
                resp = rp.http_get(self.orcid_session, ORCID_API.format(orcid), None, rate_limiter=limiter,
                                   max_retries=2, timeout=30, logger=log)
            except rp.RetrievalError as exc:
                log.warning("ORCID %s: %s", orcid, exc)
                continue
            if resp.status_code not in (200, 404):
                continue
            body = resp.json() if resp.status_code == 200 else {}
            self.db.execute("insert or replace into orcid_employments values (?, ?, ?, ?)",
                            (orcid, json.dumps(orcid_current(body), ensure_ascii=False), _now(),
                             _json(orcid_all(body))))
            if n % 50 == 0:
                self.db.commit()
                log.info("ORCID %d/%d", n, len(need))
        self.db.commit()

    def backfill(self, orcid=False):
        """Refetch rows stored before `raw` existed, so every cached work and author keeps the whole record:
        works first, then authors, then (with orcid) ORCID employment histories. Resumable, under the budget."""
        got = {"works": 0}
        works = [r[0] for r in self.db.execute(
            "select distinct doi from oa_authorships where raw is null and doi is not null")]
        log.info("works without the whole record: %d", len(works))
        for i in range(0, len(works), CHUNK):
            chunk = [d for d in works[i:i + CHUNK] if queryable(d)]
            for j in range(0, len(chunk), BATCH):
                self._works_batch(chunk[j:j + BATCH])
                if self.stopped:
                    break
            got["works"] += len(chunk)
            log.info("works %d/%d (%d calls)", min(i + CHUNK, len(works)), len(works), self.calls)
            if self.stopped:
                return got
        authors = [r[0] for r in self.db.execute("select author_id from oa_authors where raw is null")]
        log.info("authors without the whole record: %d", len(authors))
        for i in range(0, len(authors), BATCH):
            self._authors_batch(authors[i:i + BATCH])
            if self.stopped:
                return got
            if (i // BATCH) % 200 == 0:
                log.info("authors %d/%d (%d calls)", i + BATCH, len(authors), self.calls)
        got["authors"] = len(authors)
        if orcid:
            need = [r[0] for r in self.db.execute("select orcid from orcid_employments where raw is null")]
            log.info("ORCID records without the history: %d", len(need))
            self.fetch_orcids(need)
            got["orcid"] = len(need)
        return got

    def ensure(self, dois):
        """Fetch whatever these DOIs still lack, CHUNK at a time; returns a one-line report."""
        dois = sorted({d.lower() for d in dois if d})
        w = a = o = 0
        for i in range(0, len(dois), CHUNK):
            chunk = dois[i:i + CHUNK]
            w += self.works(chunk)
            a += self.authors(chunk)
            o += self.orcids(chunk)
            if self.stopped:
                break
        msg = f"affiliations: fetched {w} works, {a} authors, {o} ORCID records ({self.calls} OpenAlex calls)"
        return msg + (f"; stopped early: {self.stopped}" if self.stopped else "")


def _qs(items):
    return ",".join("?" * len(items)) or "null"


def orcid_current(employments):
    """[{name, country}] of the employments with no end date, from ORCID's /employments JSON."""
    out = []
    for g in employments.get("affiliation-group") or []:
        for s in g.get("summaries") or []:
            e = s.get("employment-summary") or {}
            if e.get("end-date") is None and e.get("organization"):
                org = e["organization"]
                out.append({"name": org.get("name"), "country": (org.get("address") or {}).get("country")})
    return out


def orcid_all(employments):
    """Every employment in ORCID's /employments JSON, past ones too: [{name, country, city, department, role,
    start, end, org_id}] (start/end as 'YYYY[-MM]', end None for a current job)."""
    def when(d):
        d = d or {}
        parts = [(d.get(k) or {}).get("value") for k in ("year", "month")]
        return "-".join(x for x in parts if x) or None
    out = []
    for g in employments.get("affiliation-group") or []:
        for s in g.get("summaries") or []:
            e = s.get("employment-summary") or {}
            org = e.get("organization") or {}
            addr = org.get("address") or {}
            dis = org.get("disambiguated-organization") or {}
            out.append({"name": org.get("name"), "country": addr.get("country"), "city": addr.get("city"),
                        "department": e.get("department-name"), "role": e.get("role-title"),
                        "start": when(e.get("start-date")), "end": when(e.get("end-date")),
                        "org_id": " ".join(x for x in (dis.get("disambiguation-source"),
                                                       dis.get("disambiguated-organization-identifier")) if x)
                        or None})
    return out


def professor_job(jobs):
    """The first current ORCID job (orcid_all) that is a working professor/lecturer post in TARGET_COUNTRIES,
    or None."""
    for j in jobs or []:
        role = j.get("role") or ""
        if (j.get("end") is None and PROFESSOR_ROLE.search(role) and not NOT_WORKING.search(role)
                and (j.get("country") or "").upper() in TARGET_COUNTRIES):
            return j
    return None


def doi_professors(db, dois):
    """{doi (lower): [(name, 'role, institution, country')]}: the DOI's authors whose ORCID lists a current
    professor/lecturer post at a US/European institution (professor_job). Cache only; ORCID records are
    fetched by `fetch` (case DOIs) and `backfill --orcid`, so leads without them simply have none."""
    ensure_tables(db)
    dois = sorted({(d or "").lower() for d in dois if d})
    out = {}
    for i in range(0, len(dois), CHUNK):
        chunk = dois[i:i + CHUNK]
        for d, name, raw in db.execute(f"""select s.doi, coalesce(s.name, au.name), o.raw
                                           from oa_authorships s join oa_authors au using (author_id)
                                           join orcid_employments o on o.orcid = au.orcid
                                           where o.raw is not null and s.doi in ({_qs(chunk)})
                                           order by s.doi, s.position""", chunk):
            j = professor_job(json.loads(raw))
            if j:
                out.setdefault(d, []).append((name or "?", f"{j['role'].strip()}, {j.get('name') or '?'}, "
                                                          f"{j.get('country') or '?'}"))
    return out


def pair_professors(profs, doi_a, doi_b, earlier):
    """The later paper's US/European professors (both papers' when the order is unknown)."""
    get = lambda d: profs.get((d or "").lower(), [])
    if earlier == "a":
        return get(doi_b)
    if earlier == "b":
        return get(doi_a)
    return get(doi_a) + get(doi_b)


def author_status(author, current_jobs):
    """(status, reason) for an oa_authors row; current_jobs: its ORCID current employments, or None when there
    is no ORCID record. status: 'target', 'academic', 'known', 'unknown' (module docstring)."""
    insts = json.loads(author["institutions"] or "[]")
    jobs = current_jobs or []
    active = (author["last_year"] or 0) >= ACTIVE_SINCE or bool(jobs)
    academic = [i for i in insts if i.get("type") in ACADEMIC_TYPES]
    academic += [{**j, "type": "orcid"} for j in jobs if ACADEMIC_NAME.search(j.get("name") or "")]
    year = f"last published {author['last_year']}" if author["last_year"] else "no works in the last 10 years"
    if active and academic:
        where = [i for i in academic if (i.get("country") or "").upper() in TARGET_COUNTRIES]
        pick = (where or academic)[0]
        why = f"{pick['name']}, {pick.get('country') or '?'}" + (" (ORCID, current)" if pick["type"] == "orcid"
                                                                  else "") + f"; {year}"
        return ("target" if where else "academic"), why
    if not active:
        return "known", year
    if not insts and not jobs:
        return "unknown", f"no institution on record; {year}"
    names = ", ".join(i["name"] for i in (insts or jobs) if i.get("name"))
    return "known", f"{names} (not typed a university by OpenAlex/ROR); {year}"


STATUS_RANK = {"target": TARGET, "academic": ACADEMIC, "unknown": UNKNOWN, "known": OTHER}


def doi_scores(db, dois):
    """{doi (lower): (rank, [(name, status, reason)])} for the given DOIs, from the cache only."""
    ensure_tables(db)
    dois = sorted({(d or "").lower() for d in dois if d})
    out = {d: (UNKNOWN, []) for d in dois}
    if not dois:
        return out
    found, rows = {}, []
    for i in range(0, len(dois), CHUNK):
        chunk = dois[i:i + CHUNK]
        found.update(db.execute(f"select doi, found from oa_works where doi in ({_qs(chunk)})", chunk).fetchall())
        rows += db.execute(f"""select s.doi, s.position, s.name sname, au.* , o.current
                               from oa_authorships s left join oa_authors au using (author_id)
                               left join orcid_employments o on o.orcid = au.orcid
                               where s.doi in ({_qs(chunk)}) order by s.doi, s.position""", chunk).fetchall()
    by_doi = {}
    for r in rows:
        if r["author_id"] is None:
            st, why = "unknown", "no OpenAlex author record"
        else:
            st, why = author_status(r, json.loads(r["current"]) if r["current"] is not None else None)
        by_doi.setdefault(r["doi"], []).append((r["sname"] or r["name"] or "?", st, why))
    for d in dois:
        authors = by_doi.get(d, [])
        if not found.get(d) or not authors:
            out[d] = (UNKNOWN, authors)
        else:
            out[d] = (min(STATUS_RANK[a[1]] for a in authors), authors)
    return out


def pair_rank(scores, doi_a, doi_b, earlier):
    """(rank, side) for a pair: its later paper's rank ('a'/'b'), or with earlier None the better side's."""
    get = lambda d: scores.get((d or "").lower(), (UNKNOWN, []))[0]
    if earlier == "a":
        return get(doi_b), "b"
    if earlier == "b":
        return get(doi_a), "a"
    ra, rb = get(doi_a), get(doi_b)
    return (ra, "a") if ra <= rb else (rb, "b")


def describe(scores, doi, limit=3):
    """One line: the DOI's rank and its best few authors."""
    rank, authors = scores.get((doi or "").lower(), (UNKNOWN, []))
    best = sorted(authors, key=lambda a: STATUS_RANK[a[1]])[:limit]
    who = "; ".join(f"{n} ({why})" for n, st, why in best if STATUS_RANK[st] == rank) if best else ""
    return f"{LABELS[rank]}" + (f" -- {who}" if who else "") + (" (no OpenAlex data)" if not authors else "")


def case_dois(db):
    return [d for r in db.execute("select doi_a, doi_b from fulltext where ft_verdict = 'real overlap'")
            for d in r]


def cmd_fetch(db, args):
    print(Fetcher(db, args.max_calls, not args.no_orcid).ensure(case_dois(db)))


# `leads`: which leads to score, in lead_rank order -- what download batches draw from. tier2: every tier-2 lead
# (both abstracts share a 20+ word run); band4: tier 4's two-sided, no-shared-author 15-50% band, where the
# strat4_s16 sample found real overlap (run_band4_batch.sh). No retraction tiers (1, 3).
LEAD_SETS = {
    "tier2": "l.tier = 2",
    "band4": """l.tier = 4 and l.shape = 'two-sided' and l.authors = 'no shared author'
                and max(p.pct_a, p.pct_b) >= 15 and max(p.pct_a, p.pct_b) < 50""",
    # 2026-10-22: the rest of tier 4's no-shared-author leads (one-sided, or two-sided outside band4), so the
    # US/European search (affiliation_hunt/useu_find.py) can draw on them; paid OpenAlex credit
    "rest4": """l.tier = 4 and l.authors = 'no shared author' and not (l.shape = 'two-sided'
                and max(p.pct_a, p.pct_b) >= 15 and max(p.pct_a, p.pct_b) < 50)""",
}


def _retry_locked(db, fn, *a):
    """fn(*a), waiting out a database another job holds (gi_write_pages.py can for minutes)."""
    for attempt in range(60):
        try:
            return fn(*a)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == 59:
                raise
            db.rollback()
            log.warning("database locked, retrying in 60s")
            time.sleep(60)


def lead_rows(db, sets):
    """(pair_id, pair_lo, pair_hi, doi_a, doi_b, tier, lead_rank, year_a, year_b) for the given LEAD_SETS, in
    lead_rank order: primary pairs that gi_verify.py select could pick (titles on both sides, not a
    partner-unidentified or identical-abstract artifact, not already full-text checked)."""
    cond = " or ".join(f"({LEAD_SETS[k]})" for k in sets)
    return db.execute(f"""select l.pair_id, l.pair_lo, l.pair_hi, l.doi_a, l.doi_b, l.tier, l.lead_rank,
                                 {gi_db.year_sql("da.pub_date")}, {gi_db.year_sql("db.pub_date")}
                          from leads l join pairs p using (pair_id)
                          left join documents da on da.dkey = p.dkey_a left join documents db on db.dkey = p.dkey_b
                          where l.primary_pair = 1 and ({cond})
                          and l.abstracts not like 'partner unidentified%' and l.abstracts != 'identical (artifact)'
                          and coalesce(da.title, '') != '' and coalesce(db.title, '') != ''
                          and not exists (select 1 from fulltext f where f.pair_lo = l.pair_lo
                                          and f.pair_hi = l.pair_hi)
                          order by l.lead_rank""").fetchall()


def stored_ranks(db, pair_ids):
    """{pair_id: rank} from pair_affiliation."""
    ensure_tables(db)
    out = {}
    for i in range(0, len(pair_ids), CHUNK):
        chunk = list(pair_ids[i:i + CHUNK])
        out.update(db.execute(f"select pair_id, rank from pair_affiliation where pair_id in ({_qs(chunk)})",
                              chunk).fetchall())
    return out


def score_pairs(db, fetcher, rows):
    """Fetch what these lead rows' DOIs lack, then store each pair's rank. Returns the ranks, or None when the
    fetcher stopped (budget / 429) before finishing, so nothing is stored for them."""
    import gi_preprints
    dois = [d for r in rows for d in (r[3], r[4])]
    report = fetcher.ensure(dois)
    if fetcher.stopped:
        log.info(report)
        return None
    scores = doi_scores(db, dois)
    now, out = _now(), []
    for pid, lo, hi, da, dbb, tier, lrank, ya, yb in rows:
        rank, side = pair_rank(scores, da, dbb, gi_preprints.earlier_by_year(ya, yb))
        out.append((pid, lo, hi, tier, lrank, rank, side, now))
    db.executemany("insert or replace into pair_affiliation values (?, ?, ?, ?, ?, ?, ?, ?)", out)
    db.commit()
    return out


def cmd_leads(db, args):
    """Score the leads download batches draw from, in lead_rank order, resuming where a previous run stopped."""
    ensure_tables(db)
    t0 = time.time()
    log.info("reading leads (%s) ...", ", ".join(args.sets))
    rows = _retry_locked(db, lead_rows, db, args.sets)
    done = {r[0] for r in db.execute("select pair_id from pair_affiliation")} if not args.rescore else set()
    todo = [r for r in rows if r[0] not in done]
    log.info("%d leads, %d already scored, %d to go", len(rows), len(rows) - len(todo), len(todo))
    fetcher = Fetcher(db, args.max_calls, args.orcid)
    tally, n = {}, 0
    for i in range(0, len(todo), args.chunk_pairs):
        out = _retry_locked(db, score_pairs, db, fetcher, todo[i:i + args.chunk_pairs])
        if out is None:
            break
        n += len(out)
        for r in out:
            tally[r[5]] = tally.get(r[5], 0) + 1
        mins = (time.time() - t0) / 60
        log.info("%d/%d scored, %d OpenAlex calls, %.0f min; so far: %s", n, len(todo), fetcher.calls, mins,
                 ", ".join(f"{LABELS[k]} {v}" for k, v in sorted(tally.items())))
    log.info("done: %d scored this run, %d OpenAlex calls%s", n, fetcher.calls,
             f"; stopped early: {fetcher.stopped} (run it again to go on)" if fetcher.stopped else "")


def cmd_show(db, args):
    import gi_write_pages
    for case in args.cases:
        n = int(case.upper().removeprefix("GI-"))
        f = db.execute("select * from fulltext where page_number = ?", (n,)).fetchone()
        if not f:
            print(f"{case}: no result page")
            continue
        earlier = gi_write_pages.earlier_side(f)
        scores = doi_scores(db, [f["doi_a"], f["doi_b"]])
        rank, side = pair_rank(scores, f["doi_a"], f["doi_b"], earlier)
        print(f"GI-{n:02d}: {LABELS[rank]} (later paper: {earlier and {'a': 'B', 'b': 'A'}[earlier] or 'unknown'})")
        for s in "ab":
            r, authors = scores[f[f"doi_{s}"].lower()]
            print(f"  {s.upper()} {f[f'doi_{s}']}: {LABELS[r]}")
            for name, st, why in authors or [("(no OpenAlex record)", "", "")]:
                print(f"     {st:9} {name}" + (f" -- {why}" if why else ""))


def cmd_summary(db, args):
    import gi_write_pages
    rows = db.execute("""select f.* from fulltext f where f.ft_verdict = 'real overlap'
                         and f.byline = 'no byline overlap' and f.page_number is not null
                         and not exists (select 1 from reviews r where r.pair_lo = f.pair_lo
                                         and r.pair_hi = f.pair_hi and r.decision is not null)""").fetchall()
    scores = doi_scores(db, [d for r in rows for d in (r["doi_a"], r["doi_b"])])
    tally = {}
    for r in rows:
        k = pair_rank(scores, r["doi_a"], r["doi_b"], gi_write_pages.earlier_side(r))[0]
        tally[k] = tally.get(k, 0) + 1
    print(f"{len(rows)} unreviewed result pages, by the later paper's authors (active since {ACTIVE_SINCE}):")
    for k in sorted(tally):
        print(f"  {LABELS[k]:24} {tally[k]}")
    ensure_tables(db)
    leads = db.execute("select tier, rank, count(*) from pair_affiliation group by 1, 2").fetchall()
    if leads:
        print("scored leads (gi_affiliations.py leads), by tier:")
        for tier in sorted({r[0] for r in leads}):
            print(f"  tier {tier}: " + ", ".join(f"{LABELS[r[1]]} {r[2]:,}" for r in leads if r[0] == tier))


def cmd_backfill(db, args):
    fetcher = Fetcher(db, args.max_calls, args.orcid)
    got = fetcher.backfill(args.orcid)
    log.info("backfill: %s; %d OpenAlex calls%s", got, fetcher.calls,
             f"; stopped: {fetcher.stopped} (rerun to continue)" if fetcher.stopped else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["fetch", "leads", "backfill", "show", "summary"])
    ap.add_argument("cases", nargs="*", help="show: GI-NN ...")
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--max-calls", type=int, default=3000,
                    help="fetch / leads / backfill: OpenAlex calls at most (free: 10,000/day; leads and backfill "
                         "default to 400,000)")
    ap.add_argument("--no-orcid", action="store_true", help="fetch: skip the ORCID employment lookups")
    ap.add_argument("--sets", nargs="+", choices=list(LEAD_SETS), default=list(LEAD_SETS),
                    help="leads: which leads to score (default both)")
    ap.add_argument("--chunk-pairs", type=int, default=2000, help="leads: pairs per fetch-and-store step")
    ap.add_argument("--orcid", action="store_true",
                    help="leads: also look up ORCID employments (off: ~1 request per author, days at this size); "
                         "backfill: also refetch stored ORCID records for the full employment history")
    ap.add_argument("--rescore", action="store_true", help="leads: re-rank already scored leads from the cache")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = sqlite3.connect(args.db, timeout=300)
    db.row_factory = sqlite3.Row
    if args.command in ("leads", "backfill") and args.max_calls == 3000:
        args.max_calls = 400_000      # about $40 past the free 10,000/day
    {"fetch": cmd_fetch, "leads": cmd_leads, "backfill": cmd_backfill, "show": cmd_show,
     "summary": cmd_summary}[args.command](db, args)


if __name__ == "__main__":
    main()

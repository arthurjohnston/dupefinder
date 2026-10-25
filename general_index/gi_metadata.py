"""Everything the open scholarly APIs hold on the papers of likely pairs, and on the people who wrote them, cached
in gi.sqlite3 -- so a pair can be judged (and its authors' institutions found) without either PDF.

Per paper (DOI):
  OpenAlex   the whole work record (venue, publisher, type, language, dates, references, cited-by counts,
             funders and grants, topics, keywords, retraction flag, abstract, every location) -> oa_work_records;
             its authorships (each author's printed institutions and raw affiliation strings) -> oa_authorships,
             the table gi_affiliations.py ranks from
  Crossref   the publisher's deposit (affiliation strings and ORCIDs as deposited, funders, licence, subjects,
             received/accepted dates, update-to: corrections and retractions, references where open)
             -> crossref_works
  Europe PMC biomedical papers: per-author affiliations, MeSH terms, grants, publication types -> epmc_works
Per person (every author of those papers):
  OpenAlex   the author record (affiliation history with years, last known institutions, ORCID, name
             variants, works and citation counts) -> oa_authors
  ORCID      the whole public record (employments, educations, qualifications, invited positions,
             memberships, services, distinctions, fundings, other names, country, websites) -> orcid_records;
             employments also go to orcid_employments, as gi_affiliations.py fetches them
Per institution (on any byline or affiliation history above): OpenAlex's record (ROR id, type, country, city,
parent institutions, homepage) -> oa_institutions.

Nothing is refetched once stored (--refresh to). OpenAlex calls are filter calls (50 ids each) on the paid key;
Crossref (50 DOIs a call) and Europe PMC (25) are free; ORCID is one request per person.

Usage:
  gi_metadata.py fetch [--runs useu% top30_s16] [--no-cases] [--max-calls 50000] [--workers 6]
      the DOIs of every pair full-text-checked in those runs (SQL LIKE patterns), plus every real-overlap pair
  gi_metadata.py show DOI | PAIR_ID | GI-N ...   what is cached, per paper and per author
"""
import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402

import gi_affiliations as ga  # noqa: E402
import gi_db  # noqa: E402

DDL = """
create table if not exists oa_work_records (doi text primary key, raw text, fetched_at text);
create table if not exists crossref_works (doi text primary key, found integer, raw text, fetched_at text);
create table if not exists epmc_works (doi text primary key, found integer, raw text, fetched_at text);
create table if not exists orcid_records (orcid text primary key, found integer, raw text, fetched_at text);
create table if not exists oa_institutions (inst_id text primary key, name text, country text, type text,
    ror text, raw text, fetched_at text);
"""
CROSSREF_API = "https://api.crossref.org/works"
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ORCID_RECORD = "https://pub.orcid.org/v3.0/{}/record"
INSTITUTIONS_API = "https://api.openalex.org/institutions"
CROSSREF_BATCH, EPMC_BATCH = 50, 25
log = logging.getLogger("gi_metadata")


def _now():
    return f"{datetime.now():%Y-%m-%d %H:%M}"


def _json(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def target_dois(db, runs, cases=True):
    """Lowercased DOIs of both sides of every fulltext pair in these runs (LIKE patterns), and of every
    real-overlap pair when cases."""
    where = " or ".join("run like ?" for _ in runs) or "0"
    if cases:
        where += " or ft_verdict = 'real overlap'"
    out = set()
    for a, b in db.execute(f"select doi_a, doi_b from fulltext where {where}", runs):
        out.update(d.lower() for d in (a, b) if ga.queryable(d))
    return sorted(out)


def missing(db, table, col, ids):
    have = set()
    for i in range(0, len(ids), ga.CHUNK):
        chunk = ids[i:i + ga.CHUNK]
        have.update(r[0] for r in db.execute(f"select {col} from {table} where {col} in ({ga._qs(chunk)})", chunk))
    return [x for x in ids if x not in have]


class Metadata:
    def __init__(self, db, args):
        self.db, self.args = db, args
        db.executescript(DDL)
        ga.ensure_tables(db)
        self.oa = ga.Fetcher(db, args.max_calls, orcid=False)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = rp.USER_AGENT_TEMPLATE.format(email=args.email)
        self.limiter = rp.RateLimiter(0.2)
        self.lock = threading.Lock()

    def _get(self, url, params, session=None, limiter=None):
        try:
            resp = rp.http_get(session or self.session, url, params, rate_limiter=limiter or self.limiter,
                               max_retries=2, timeout=60, logger=log)
        except rp.RetrievalError as exc:
            log.warning("%s: %s", url, exc)
            return None
        if resp.status_code not in (200, 404):
            log.warning("%s: HTTP %d", url, resp.status_code)
            return None
        return resp

    def _write(self, sql, rows):
        with self.lock:
            ga._retry_locked(self.db, self._write_now, sql, rows)

    def _write_now(self, sql, rows):
        self.db.executemany(sql, rows)
        self.db.commit()

    # -- OpenAlex works: whole record, authorships into gi_affiliations' tables
    def works(self, dois):
        need = missing(self.db, "oa_work_records", "doi", dois)
        log.info("OpenAlex works: %d to fetch", len(need))
        for n, i in enumerate(range(0, len(need), ga.BATCH)):
            batch = need[i:i + ga.BATCH]
            res = self.oa._openalex(rp.OPENALEX_API, "doi:" + "|".join(batch), None)
            if res is None:
                if self.oa.stopped:
                    log.warning("OpenAlex stopped: %s", self.oa.stopped)
                    return
                continue
            got = {(rp.normalize_doi(w.get("doi")) or "").lower(): w for w in res}
            now = _now()
            self._write("insert or replace into oa_work_records values (?, ?, ?)",
                        [(d, _json(got[d]) if d in got else None, now) for d in batch])
            self.oa._store_works(batch, {d: w.get("authorships") or [] for d, w in got.items()})
            if n % 100 == 0:
                log.info("  works %d/%d (%d calls)", i + len(batch), len(need), self.oa.calls)

    # -- OpenAlex authors and institutions
    def authors(self, dois):
        for i in range(0, len(dois), ga.CHUNK):
            self.oa.authors(dois[i:i + ga.CHUNK])
            if self.oa.stopped:
                return
        log.info("OpenAlex authors done (%d calls)", self.oa.calls)

    def institutions(self, dois):
        ids = set()
        for i in range(0, len(dois), ga.CHUNK):
            chunk = dois[i:i + ga.CHUNK]
            for inst, raw in self.db.execute(f"""select s.institutions, a.raw from oa_authorships s
                    left join oa_authors a using (author_id) where s.doi in ({ga._qs(chunk)})""", chunk):
                ids.update(x.get("id") for x in json.loads(inst or "[]"))
                for aff in (json.loads(raw) if raw else {}).get("affiliations") or []:
                    ids.add(ga._short((aff.get("institution") or {}).get("id")))
        need = missing(self.db, "oa_institutions", "inst_id", sorted(x for x in ids if x))
        log.info("OpenAlex institutions: %d to fetch", len(need))
        for i in range(0, len(need), ga.BATCH):
            batch = need[i:i + ga.BATCH]
            res = self.oa._openalex(INSTITUTIONS_API, "openalex:" + "|".join(batch), None)
            if res is None:
                if self.oa.stopped:
                    return
                continue
            now = _now()
            self._write("insert or replace into oa_institutions values (?, ?, ?, ?, ?, ?, ?)", [
                (ga._short(r.get("id")), r.get("display_name"), r.get("country_code"), r.get("type"),
                 ga._short(r.get("ror")), _json(r), now) for r in res])

    # -- Crossref
    def crossref(self, dois):
        need = missing(self.db, "crossref_works", "doi", dois)
        log.info("Crossref: %d to fetch", len(need))
        batches = [need[i:i + CROSSREF_BATCH] for i in range(0, len(need), CROSSREF_BATCH)]

        def one(batch):
            # a comma in a DOI would split the filter: those go one at a time by path
            plain = [d for d in batch if "," not in d]
            got = {}
            if plain:
                resp = self._get(CROSSREF_API, {"filter": ",".join(f"doi:{d}" for d in plain),
                                                "rows": len(plain), "mailto": self.args.email})
                if resp is None or resp.status_code != 200:
                    return
                for w in resp.json().get("message", {}).get("items", []):
                    got[(w.get("DOI") or "").lower()] = w
            for d in batch:
                if "," in d:
                    resp = self._get(f"{CROSSREF_API}/{d}", {"mailto": self.args.email})
                    if resp is not None and resp.status_code == 200:
                        got[d] = resp.json().get("message")
            now = _now()
            self._write("insert or replace into crossref_works values (?, ?, ?, ?)",
                        [(d, int(d in got), _json(got[d]) if d in got else None, now) for d in batch])

        self._pool(one, batches, "Crossref", 2)

    # -- Europe PMC
    def epmc(self, dois):
        need = missing(self.db, "epmc_works", "doi", dois)
        log.info("Europe PMC: %d to look up", len(need))
        batches = [need[i:i + EPMC_BATCH] for i in range(0, len(need), EPMC_BATCH)]

        def one(batch):
            q = " OR ".join(f'DOI:"{d}"' for d in batch if '"' not in d)
            resp = self._get(EPMC_SEARCH, {"query": q, "format": "json", "resultType": "core", "pageSize": 100})
            if resp is None or resp.status_code != 200:
                return
            got = {(r.get("doi") or "").lower(): r for r in resp.json().get("resultList", {}).get("result", [])}
            now = _now()
            self._write("insert or replace into epmc_works values (?, ?, ?, ?)",
                        [(d, int(d in got), _json(got[d]) if d in got else None, now) for d in batch])

        self._pool(one, batches, "Europe PMC", 3)

    # -- ORCID
    def orcids(self, dois):
        ids = set()
        for i in range(0, len(dois), ga.CHUNK):
            chunk = dois[i:i + ga.CHUNK]
            ids.update(r[0] for r in self.db.execute(f"""select distinct a.orcid from oa_authorships s
                       join oa_authors a using (author_id) where s.doi in ({ga._qs(chunk)})
                       and a.orcid is not null""", chunk))
        need = missing(self.db, "orcid_records", "orcid", sorted(ids))
        log.info("ORCID: %d people to fetch", len(need))
        osession = requests.Session()
        osession.headers["Accept"] = "application/json"
        olimit = rp.RateLimiter(0.06)       # ORCID's public API: 24 requests a second

        def one(orcid):
            resp = self._get(ORCID_RECORD.format(orcid), None, osession, olimit)
            if resp is None:
                return
            body = resp.json() if resp.status_code == 200 else {}
            emp = ((body.get("activities-summary") or {}).get("employments")) or {}
            now = _now()
            self._write("insert or replace into orcid_records values (?, ?, ?, ?)",
                        [(orcid, int(bool(body)), _json(body) if body else None, now)])
            self._write("insert or replace into orcid_employments values (?, ?, ?, ?)",
                        [(orcid, json.dumps(ga.orcid_current(emp), ensure_ascii=False), now,
                          _json(ga.orcid_all(emp)))])

        self._pool(one, need, "ORCID", self.args.workers)

    def _pool(self, fn, items, label, workers):
        t0 = time.time()
        with ThreadPoolExecutor(workers) as pool:
            for n, _ in enumerate(pool.map(fn, items), 1):
                if n % 500 == 0:
                    log.info("  %s %d/%d, %.0f min", label, n, len(items), (time.time() - t0) / 60)
        log.info("%s done", label)


def cmd_fetch(db, args):
    dois = target_dois(db, args.runs, not args.no_cases)
    log.info("%d DOIs (runs %s%s)", len(dois), ", ".join(args.runs), " + real-overlap cases" if not args.no_cases
             else "")
    m = Metadata(db, args)
    m.works(dois)
    m.authors(dois)
    m.institutions(dois)
    m.crossref(dois)
    m.epmc(dois)
    m.orcids(dois)
    log.info("done: %d OpenAlex calls%s", m.oa.calls, f"; stopped: {m.oa.stopped}" if m.oa.stopped else "")


# ---- show
def _date(d):
    if not d:
        return ""
    parts = [(d.get(k) or {}).get("value") for k in ("year", "month", "day")]
    return "-".join(p for p in parts if p)


def orcid_affiliations(raw):
    """[(section, organisation, role, start, end, country)] from an ORCID record, every affiliation section."""
    out = []
    acts = (raw or {}).get("activities-summary") or {}
    for section in ("employments", "educations", "qualifications", "invited-positions", "memberships",
                    "services", "distinctions"):
        key = section[:-1] + "-summary"
        for g in (acts.get(section) or {}).get("affiliation-group") or []:
            for s in g.get("summaries") or []:
                e = s.get(key) or next(iter(s.values()), {})
                org = e.get("organization") or {}
                out.append((section, org.get("name"), e.get("role-title"), _date(e.get("start-date")),
                            _date(e.get("end-date")), (org.get("address") or {}).get("country")))
    return out


def resolve(db, item):
    """DOIs and a label for a DOI, a pair_id or a GI-N page number."""
    if item.lower().startswith("10."):
        return [item.lower()], item
    m = re.fullmatch(r"(?:gi-)?(\d+)", item, re.I)
    if item.lower().startswith("gi-") and m:
        r = db.execute("select doi_a, doi_b from fulltext where page_number = ?", (int(m.group(1)),)).fetchone()
    elif m:
        r = db.execute("select doi_a, doi_b from leads where pair_id = ?", (int(m.group(1)),)).fetchone()
    else:
        r = None
    return ([d.lower() for d in r if d] if r else []), item


def show_doi(db, doi):
    print(f"\n== {doi}")
    w = db.execute("select raw from oa_work_records where doi = ?", (doi,)).fetchone()
    w = json.loads(w[0]) if w and w[0] else {}
    cr = db.execute("select raw from crossref_works where doi = ?", (doi,)).fetchone()
    cr = json.loads(cr[0]) if cr and cr[0] else {}
    ep = db.execute("select raw from epmc_works where doi = ?", (doi,)).fetchone()
    ep = json.loads(ep[0]) if ep and ep[0] else {}
    src = (w.get("primary_location") or {}).get("source") or {}
    print(f"   {w.get('title') or (cr.get('title') or [''])[0]}")
    print(f"   {w.get('publication_date') or ''} · {src.get('display_name') or (cr.get('container-title') or [''])[0]}"
          f" · {src.get('host_organization_name') or cr.get('publisher') or ''} · {w.get('type') or cr.get('type') or ''}"
          f" · cited by {w.get('cited_by_count', '?')}" + (" · RETRACTED (OpenAlex)" if w.get("is_retracted") else ""))
    for u in cr.get("update-to") or []:
        print(f"   Crossref update: {u.get('type')} {u.get('DOI')} {_date({'year': {'value': str(u.get('updated', {}).get('date-parts', [['']])[0][0])}})}")
    funders = {g.get("funder_display_name") for g in w.get("grants") or []} | {f.get("name") for f in cr.get("funder") or []}
    if funders - {None}:
        print("   funders:", "; ".join(sorted(funders - {None})))
    if ep.get("meshHeadingList"):
        print("   MeSH:", "; ".join(h.get("descriptorName", "") for h in ep["meshHeadingList"].get("meshHeading", [])[:12]))
    cr_aff = {(a.get("family") or "").lower(): [x.get("name") for x in a.get("affiliation") or []] for a in cr.get("author") or []}
    ep_aff = {}
    for a in (ep.get("authorList") or {}).get("author") or []:
        ep_aff[(a.get("lastName") or "").lower()] = [x.get("affiliation") for x in
                                                   (a.get("authorAffiliationDetailsList") or {}).get("authorAffiliation") or []]
    for pos, aid, name, insts, raw in db.execute("select position, author_id, name, institutions, raw from "
                                                 "oa_authorships where doi = ? order by position", (doi,)):
        au = db.execute("select orcid, raw from oa_authors where author_id = ?", (aid,)).fetchone()
        araw = json.loads(au[1]) if au and au[1] else {}
        orcid = au[0] if au else None
        print(f"   {pos + 1}. {name}" + (f"  ORCID {orcid}" if orcid else "") + (f"  ({aid})" if aid else ""))
        printed = [f"{i['name']} ({i.get('country') or '?'})" for i in json.loads(insts or "[]")]
        strings = dict.fromkeys((json.loads(raw) if raw else {}).get("raw_affiliation_strings") or [])
        if printed:
            print("      on this paper:", "; ".join(printed))
        for s in strings:
            print("      as printed:", s[:200])
        last = (name or "").split()[-1].lower() if name else ""
        for s in cr_aff.get(last) or []:
            print("      Crossref deposit:", s[:200])
        for s in ep_aff.get(last) or []:
            print("      Europe PMC:", (s or "")[:200])
        hist = araw.get("affiliations") or []
        if hist:
            print("      OpenAlex history:", "; ".join(
                f"{(h.get('institution') or {}).get('display_name')} ({(h.get('institution') or {}).get('country_code')}) "
                f"{min(h.get('years') or [0])}-{max(h.get('years') or [0])}" for h in hist[:8]))
        if orcid:
            o = db.execute("select raw from orcid_records where orcid = ?", (orcid,)).fetchone()
            for sec, org, role, start, end, cty in orcid_affiliations(json.loads(o[0]) if o and o[0] else {}):
                print(f"      ORCID {sec[:-1]}: {org} ({cty}) {role or ''} {start}–{end or 'now'}")


def cmd_show(db, args):
    for item in args.items:
        dois, label = resolve(db, item)
        if not dois:
            print(f"{label}: not found")
            continue
        print(f"#### {label}")
        for d in dois:
            show_doi(db, d)
        if len(dois) == 2:
            refs = [set(json.loads(r[0]).get("referenced_works") or []) if r and r[0] else set()
                    for r in (db.execute("select raw from oa_work_records where doi = ?", (d,)).fetchone() for d in dois)]
            ids = [json.loads(r[0]).get("id") if r and r[0] else None
                   for r in (db.execute("select raw from oa_work_records where doi = ?", (d,)).fetchone() for d in dois)]
            print(f"\n   citations (OpenAlex): {dois[0]} cites the other: {ids[1] in refs[0]}; "
                  f"{dois[1]} cites the other: {ids[0] in refs[1]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["fetch", "show"])
    ap.add_argument("items", nargs="*", help="show: DOI, pair_id or GI-N")
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--runs", nargs="+", default=["useu%", "top30_s16"], help="fetch: fulltext runs (LIKE patterns)")
    ap.add_argument("--no-cases", action="store_true", help="fetch: leave out the real-overlap pairs of other runs")
    ap.add_argument("--max-calls", type=int, default=50_000, help="fetch: OpenAlex calls at most")
    ap.add_argument("--workers", type=int, default=6, help="fetch: parallel ORCID requests")
    ap.add_argument("--email", default="arthur.j.johnston@gmail.com")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    db = sqlite3.connect(args.db, timeout=300, check_same_thread=False)
    {"fetch": cmd_fetch, "show": cmd_show}[args.command](db, args)


if __name__ == "__main__":
    main()

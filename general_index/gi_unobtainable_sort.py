"""The unobtainable list (gi_western_csv.py unobtainable), re-ordered as a download worklist.

One row per academic, as in the input, with these columns first:
  doi, copied_doi   the copying paper's and the copied paper's DOI ('gi:<dkey>' for an index document with no
                    DOI: find it by title_a/title_b), each BLANK when we already hold that PDF (local_pdfs points
                    at a file on disk, or a fulltext run retrieved that side), so the non-blank cells are exactly
                    the PDFs to get. Re-run after importing (gi_manual_import.py) and fetched papers drop out.
  title, university, copied_pct, title_a, title_b (title = the copying paper's), have_pdf (neither / copying / copied / both),
  n_authors (the copying paper's author count), uni_h_index, author, country, pair_id; the input's other
  columns after.

Written twice: OUT.csv (plain text) and OUT.xlsx, the same table with clickable links (openpyxl): each DOI to
doi.org, and each title to a Google search for it, but only titles of papers still needed. (HYPERLINK formulas in
the CSV were tried first; LibreOffice doesn't reliably evaluate them on CSV import.)

Left out: pairs whose copying paper has more than --max-authors (5) authors, counted from its cached OpenAlex
authorships (oa_authorships; the index's own author list when there is none). Large author lists are
consortium and multi-centre papers, where shared text is mostly the team reusing its own methods.

Order: by score (gi_lead_score.py: how likely the pair is to turn out a medium/high-severity copying case,
from the index's overlap shares and 5-gram counts plus the abstract check, learned from the pairs already
checked in full text and reviewed; cross-validated precision 75% in the top 100, 64% top 500, AUC 0.89), in
bands of SCORE_BAND (0.1); within a band, by the most prestigious university among the copying paper's
academics, measured by OpenAlex's institution h-index (oa_institutions, cached; a university name OpenAlex
spells differently gets none and sorts last in its band); then by score. A paper's rows stay together, most
prestigious university first. The score is a ranking, not a calibrated probability: the training pairs were
picked by earlier runs, not at random from this list. (The order before 2026-11-11 was abstract check, then
20% bands of copied_pct: wrong outside "shared text", where high index overlap is almost always a dud.)

Author check (byline_check column): the list says an academic is Western from OpenAlex's last known institution
or a current ORCID job, not from the paper, and OpenAlex merges namesakes, so an author of a Chinese conference
paper can show up "at" Oxford. Each row is checked against the copying paper's own byline in the cached OpenAlex
record (oa_authorships: the institutions OpenAlex matched for that author on that paper), the test the case agent
applies to every case (author_checks, gi_affiliations.CHECK_VERDICTS):
  byline           a Western institution (TARGET_COUNTRIES) for this author on this paper
  not on byline    institutions on this paper, every one outside the West: dropped, as the case agent's
                   "not on byline" verdict drops an author (the commonest false positive among the checked cases)
  no affiliation   OpenAlex matched no institution for the author on this paper (or none with a country): kept,
                   unverified
  not found        no author of that name on the paper's OpenAlex record: kept, unverified
  moved            institutions on this paper all outside the West, but the author's ORCID record lists that
                   byline institution (a job or a degree) AND a current Western job (not visiting, emeritus or
                   honorary): the same person, now in the West: kept. ORCID records are the person's own; a
                   namesake merged into the OpenAlex profile brings an ORCID whose history doesn't match the
                   byline. (The byline's country alone was tried and is noise: see mover().)
                   Records not cached are listed at the end of a run; --fetch-orcids fetches them first.
  on earlier paper the pair's order was unknown (a year missing from the index), the cached OpenAlex/Crossref
                   records give both years, and the academic is only on the earlier paper: a source author, not
                   a copying one (a reputable 2003 conference paper against a 2010 predatory-journal one): dropped
CHECKS (data/unobtainable_author_checks.tsv: doi, author, university ('' = every row of that author), verdict,
basis, checked) holds verdicts reached by reading, mostly for "no affiliation" rows (OpenAlex's raw
affiliation string, the venue, the co-authors); it overrides the automatic one, and a verdict in
gi_affiliations.NOT_WESTERN drops the row. So do the case agent's and your own author_checks for that paper,
where there are any. byline_institutions lists what OpenAlex has for the author on the paper, to eyeball a "byline" row whose
listed university isn't there (often the author moved there later; sometimes a namesake: record those in CHECKS
with the university filled in). A paper with no row left drops; under --top30, a paper with no top-30 row left drops.
--keep-unverified-authors keeps every row and only fills the column.

Also left out: pairs between two issues of one recurring column (SERIES_COLUMNS, both titles starting with
it). NeoReviews' "Strip of the Month" (64 pairs on 2026-11-13; 16 of the 42 top-30 papers scoring 0.5+) is a
monthly fetal-heart-rate case written to a fixed format by rotating authors, so every issue shares the column's
standard text with every other.

--top30: keep only copying papers with at least one academic at one of the 27 Western universities on the
top-30 list (affiliation_hunt/top30_find.py's TOP30, less Tsinghua, Peking and NUS), matched on the university
column's OpenAlex name, including the medical-school/health-system variants in TOP30_NAMES. The paper's other
academics' rows are kept with it. Written to OUT_top30.csv/.xlsx.

--min-score S: keep only pairs scoring at least S (OUT_scoreS.csv/.xlsx).

Usage: gi_unobtainable_sort.py [IN.csv] [OUT.csv] [--max-authors 5] [--top30] [--min-score S]
  defaults: data/unobtainable_western_2026-10-30.csv -> data/unobtainable_western_2026-10-30_by_band_prestige.csv
"""
import argparse
import collections
import csv
import html
import json
import re
import urllib.parse
import sqlite3
from pathlib import Path

import gi_affiliations as ga
import gi_db
import gi_lead_score
import gi_metadata
import gi_preprints

SCORE_BAND = 0.1

# The Western 27 of affiliation_hunt/top30_find.py's TOP30 (OpenAlex display names), plus spellings seen in the
# unobtainable list's university column (2026-11-11). University of Michigan-Dearborn is deliberately not here.
TOP30_NAMES = {n.lower() for n in [
    "University of Oxford", "University of Oxford University College Oxford", "Massachusetts Institute of Technology",
    "Harvard University", "Princeton University", "University of Cambridge",
    "Stanford University", "Stanford Medicine", "Stanford University School of Medicine",
    "California Institute of Technology", "University of California, Berkeley", "Imperial College London",
    "Yale University", "ETH Zurich", "University of Pennsylvania",
    "Johns Hopkins University", "Johns Hopkins Medicine", "School of Medicine, Johns Hopkins University",
    "University of Chicago", "The University of Chicago", "University of California, Los Angeles",
    "Cornell University", "Weill Cornell Medicine", "Columbia University", "Columbia University Irving Medical Center",
    "University of Michigan", "University of Michigan–Ann Arbor", "University of Toronto", "University College London",
    "University of Edinburgh", "Carnegie Mellon University", "University of Washington", "New York University",
    "Technical University of Munich", "Duke University", "Duke University Health System",
    "Duke University School of Medicine", "Northwestern University", "Northwestern University - Chicago"]}

IN = gi_db.DATA / "unobtainable_western_2026-10-30.csv"
OUT = gi_db.DATA / "unobtainable_western_2026-10-30_by_band_prestige.csv"
SERIES_COLUMNS = ("strip of the month:",)
CHECKS = gi_db.DATA / "unobtainable_author_checks.tsv"
LEAD = ["doi", "copied_doi", "score", "abstract_check", "title", "university", "byline_check", "byline_institutions", "copied_pct", "title_a", "title_b", "have_pdf",
        "n_authors",
        "uni_h_index", "author", "country", "pair_id"]


def clean_title(t):
    return " ".join(re.sub(r"<[^>]+>", " ", re.sub(r"\\[rnt]", " ", html.unescape(t or ""))).split())


def doi_url(doi):
    return "https://doi.org/" + doi if doi and not doi.startswith("gi:") else None


def google_url(title):
    """A Google search for the title in quotes (minus a proceedings record's "[IEEE 2011 Conference ... ]" prefix,
    which names the volume, not the paper)."""
    q = re.sub(r"^(?:SPIE Proceedings\s*)?\[.*?\]\s*", "", title) or title
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus('"' + q + '"')


def write_xlsx(path, fields, rows):
    """The same table as the CSV, with the cells in each row's "_links" ({column: url}) as real hyperlinks."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(write_only=False)
    ws = wb.active
    ws.title = "worklist"
    ws.append(fields)
    ws.freeze_panes = "A2"
    link_font = Font(color="0563C1", underline="single")
    col = {f: i + 1 for i, f in enumerate(fields)}
    for n, r in enumerate(rows, 2):
        ws.append([r.get(f, "") for f in fields])
        for f, url in r["_links"].items():
            c = ws.cell(row=n, column=col[f])
            c.hyperlink, c.font = url, link_font
    for f, w in (("doi", 28), ("copied_doi", 28), ("title", 50), ("title_a", 50), ("title_b", 50), ("university", 30)):
        ws.column_dimensions[ws.cell(row=1, column=col[f]).column_letter].width = w
    wb.save(path)


def held_dois(db):
    """Lowercased DOIs / 'gi:<dkey>' ids we hold a PDF for."""
    have = {d.lower() for (d,) in db.execute("select doi from local_pdfs where doi is not null")}
    have |= {"gi:" + k for (k,) in db.execute("select dkey from local_pdfs")}
    for a, b, ret in db.execute("select doi_a, doi_b, retrieved from fulltext"):
        if "A" in (ret or "") and a:
            have.add(a.lower())
        if "B" in (ret or "") and b:
            have.add(b.lower())
    return have


def h_indexes(db):
    """(name, country) and (name, None) -> the institution's OpenAlex h-index."""
    h = {}
    for name, country, hi in db.execute(
            "select name, country, json_extract(raw, '$.summary_stats.h_index') from oa_institutions"):
        if hi is not None:
            for k in ((name, country), (name, None)):
                h[k] = max(h.get(k, 0), hi)
    return h


def author_counts(db, dois, pair_ids):
    """DOI -> author count: OpenAlex authorships, else the index's ';'-separated author list for that side."""
    n = {}
    dois = sorted(dois)
    for i in range(0, len(dois), 900):
        ch = dois[i:i + 900]
        n.update(db.execute(f"select doi, count(*) from oa_authorships where doi in ({','.join('?' * len(ch))}) "
                            "group by doi", ch))
    pair_ids = sorted(pair_ids)
    for i in range(0, len(pair_ids), 900):
        ch = pair_ids[i:i + 900]
        for da, db_, aa, ab in db.execute(f"select doi_a, doi_b, author_a, author_b from lead_details "
                                          f"where pair_id in ({','.join('?' * len(ch))})", ch):
            for d, a in ((da, aa), (db_, ab)):
                if d and d.lower() not in n and a:
                    n[d.lower()] = len([x for x in a.split(";") if x.strip()])
    return n


def load_checks(db, dois):
    """{(doi, author lower): [(university, verdict, basis)]}: CHECKS, then the agent's and your author_checks."""
    out = collections.defaultdict(list)
    if CHECKS.exists():
        with open(CHECKS, newline="", encoding="utf-8") as f:
            for c in csv.DictReader(f, delimiter="\t"):
                if c["verdict"] not in ga.CHECK_VERDICTS:
                    raise SystemExit(f"{CHECKS}: unknown verdict {c['verdict']!r} for {c['author']}")
                out[(c["doi"].lower(), c["author"].lower())].append((c["university"], c["verdict"], c["basis"]))
    rdb = sqlite3.connect(f"file:{gi_db.DB_PATH}?mode=ro", uri=True)
    rdb.row_factory = sqlite3.Row
    for c in ga.load_author_checks(rdb, dois, auto=False).values():
        for r in sorted(c, key=lambda r: r["checked_by"] == "you"):     # yours last, so they win
            out[(r["doi"], r["author"].lower())].append((r["university"], r["verdict"], r["basis"] or ""))
    return out


def pub_years(db, dois):
    """doi (lower) -> publication year from the cached OpenAlex work record, else Crossref's issued date."""
    out = {}
    dois = sorted(dois)
    for i in range(0, len(dois), 900):
        ch = dois[i:i + 900]
        qs = ",".join("?" * len(ch))
        for d, y in db.execute(f"select doi, json_extract(raw, '$.\"issued\".\"date-parts\"[0][0]') "
                               f"from crossref_works where doi in ({qs})", ch):
            if y:
                out[d.lower()] = int(y)
        for d, y in db.execute(f"select doi, json_extract(raw, '$.publication_year') from oa_work_records "
                               f"where doi in ({qs})", ch):
            if y:
                out[d.lower()] = int(y)
    return out


def byline_checks(db, rows, papers):
    """Each row's byline_check (see the docstring): CHECKS / author_checks for that author and paper, else the
    cached OpenAlex byline, with the author's ORCID record to tell a mover from a namesake. papers(r): the DOIs
    the academic may be on (both when the order is unknown). Returns the ORCID iDs of "not on byline" authors
    whose record isn't cached (--fetch-orcids fetches them)."""
    dois = {d.lower() for r in rows for d in papers(r) if d and not d.startswith("gi:")}
    by_doi = collections.defaultdict(list)
    ds = sorted(dois)
    for i in range(0, len(ds), 900):
        ch = ds[i:i + 900]
        for d, name, insts, orcid in db.execute(
                f"select s.doi, s.name, s.institutions, a.orcid from oa_authorships s left join oa_authors a "
                f"using (author_id) where s.doi in ({','.join('?' * len(ch))})", ch):
            by_doi[d].append((name, json.loads(insts or "[]"), orcid))
    checks = load_checks(db, dois)
    years = pub_years(db, {r[f"doi_{x}"].lower() for r in rows if "unknown" in r["copying_paper"] for x in "ab"})
    histories, need = {}, set()
    for r in rows:
        verdict = None
        if "unknown" in r["copying_paper"]:
            ya, yb = (str(r[f"year_{x}"] or years.get(r[f"doi_{x}"].lower(), "")) for x in "ab")
            earlier = gi_preprints.earlier_by_year(ya, yb)
            on = {x for x in "ab" for name, _, _ in by_doi.get(r[f"doi_{x}"].lower(), [])
                  if ga.same_name(name, r["author"])}
            r["_earlier_only"] = bool(earlier and on == {earlier})
        for d in (x.lower() for x in papers(r)):
            for uni, v, basis in checks.get((d, r["author"].lower()), []):
                if uni in ("", r["university"]):
                    verdict = v
        found = [(insts, orcid) for d in papers(r) for name, insts, orcid in by_doi.get(d.lower(), [])
                 if ga.same_name(name, r["author"])]
        r["byline_institutions"] = "; ".join(dict.fromkeys(f"{i.get('name')} ({i.get('country') or '?'})"
                                                           for insts, _ in found for i in insts))
        if verdict is None and r.get("_earlier_only"):
            verdict = "on earlier paper"
        if verdict is None:
            countries = {(i.get("country") or "").upper() for insts, _ in found for i in insts} - {""}
            verdict = ("not found" if not found else "no affiliation" if not countries
                       else "byline" if countries & ga.TARGET_COUNTRIES else "not on byline")
            if verdict == "not on byline":
                verdict = mover(db, r, found, histories, need) or verdict
            elif verdict == "no affiliation" and not any(
                    {(i.get("country") or "").upper() for i in insts} & ga.TARGET_COUNTRIES
                    for d in papers(r) for name, insts, _ in by_doi.get(d.lower(), [])
                    if not ga.same_name(name, r["author"])):
                verdict = "name only"           # no Western co-author either: gi_affiliations.byline_checks()
        r["byline_check"] = verdict
    return need


def mover(db, r, found, histories, need):
    """'moved' when the author's ORCID record shows the paper's author since moved to the West
    (gi_affiliations.moved_to_west()); None otherwise. Uncached records go in need."""
    byline = [i.get("name") for insts, _ in found for i in insts]
    for orcid in dict.fromkeys(o for _, o in found if o):
        if orcid not in histories:
            histories[orcid] = ga.orcid_history(db, orcid)
        if histories[orcid] is None:
            need.add(orcid)
        elif ga.moved_to_west(histories[orcid], byline):
            return "moved"
    return None


def pair_features(db, pair_ids):
    """pair_id -> the inputs gi_lead_score.features() needs."""
    out = {}
    pair_ids = sorted(pair_ids)
    for i in range(0, len(pair_ids), 900):
        ch = pair_ids[i:i + 900]
        for r in db.execute(f"""select p.pair_id, case when l.abstracts = 'not checked'
                                           then coalesce(ac.verdict, l.abstracts) else l.abstracts end,
                                       p.pct_a, p.pct_b, p.shared, p.shared_common, da.total_5grams, db.total_5grams
                                from pairs p join leads l using (pair_id)
                                left join abstract_checks ac on ac.doi_a = lower(l.doi_a) and ac.doi_b = lower(l.doi_b)
                                left join documents da on da.dkey = p.dkey_a left join documents db on db.dkey = p.dkey_b
                                where p.pair_id in ({','.join('?' * len(ch))})""", ch):
            # abstract_checks: gi_abstract_fill.py's verdicts, there before gi_rank_leads.py copies them into leads
            out[r[0]] = dict(zip(("abstracts", "pct_a", "pct_b", "shared", "shared_common", "tot_a", "tot_b"), r[1:]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp", nargs="?", type=Path, default=IN)
    ap.add_argument("out", nargs="?", type=Path, default=OUT)
    ap.add_argument("--max-authors", type=int, default=5)
    ap.add_argument("--top30", action="store_true", help="only papers with an academic at a Western top-30 university")
    ap.add_argument("--fetch-orcids", action="store_true",
                    help="first fetch the ORCID records the author check needs (public API, free)")
    ap.add_argument("--keep-unverified-authors", action="store_true",
                    help="keep rows the author check says aren't Western (only fill byline_check)")
    ap.add_argument("--min-score", type=float, default=0.0, help="only pairs scoring at least this")
    args = ap.parse_args()
    if args.top30:
        args.out = args.out.with_name(args.out.stem + "_top30.csv")
    if args.min_score:
        args.out = args.out.with_name(f"{args.out.stem}_score{args.min_score:g}.csv")
    db = sqlite3.connect(f"file:{gi_db.DB_PATH}?mode=ro", uri=True)
    csv.field_size_limit(10**9)
    with open(args.inp, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    have, h = held_dois(db), h_indexes(db)
    side = lambda r: r["copying_paper"][0].lower()
    copying = lambda r: r[f"doi_{side(r)}"]
    counts = author_counts(db, {copying(r).lower() for r in rows}, {int(r["pair_id"]) for r in rows})
    feats = pair_features(db, {int(r["pair_id"]) for r in rows})
    ids = sorted(feats)
    scores = dict(zip(ids, gi_lead_score.fit().predict_proba(gi_lead_score.features([feats[i] for i in ids]))[:, 1]))
    kept, dropped = [], set()
    series = {r["pair_id"] for r in rows
              if any(r["title_a"].lower().startswith(c) and r["title_b"].lower().startswith(c) for c in SERIES_COLUMNS)}
    rows_in = len(rows)
    rows = [r for r in rows if r["pair_id"] not in series]
    for r in rows:
        n = counts.get(copying(r).lower())
        if n is not None and n > args.max_authors:
            dropped.add((r["pair_id"], copying(r)))
            continue
        s, o = side(r), "b" if side(r) == "a" else "a"
        cp, cd = r[f"doi_{s}"], r[f"doi_{o}"]
        held = {x for x, d in ((s, cp), (o, cd)) if d.lower() in have}
        r["_links"] = {}
        r["doi"] = "" if s in held else cp
        r["copied_doi"] = "" if o in held else cd
        for col, d in (("doi", r["doi"]), ("copied_doi", r["copied_doi"])):
            if doi_url(d):
                r["_links"][col] = doi_url(d)
        for x in "ab":                       # titles: a Google search link only for a paper still to get
            r[f"title_{x}"] = clean_title(r[f"title_{x}"])
            if x not in held:
                r["_links"][f"title_{x}"] = google_url(r[f"title_{x}"])
        r["title"] = r[f"title_{s}"]
        if s not in held:
            r["_links"]["title"] = google_url(r["title"])
        r["have_pdf"] = ("both" if len(held) == 2 else "copying" if s in held else "copied" if o in held
                         else "neither")
        r["n_authors"] = "" if n is None else n
        r["abstract_check"] = (feats.get(int(r["pair_id"])) or {}).get("abstracts") or ""
        r["score"] = f"{scores.get(int(r['pair_id']), 0):.3f}"
        v = h.get((r["university"], r["country"]), h.get((r["university"], None)))
        r["uni_h_index"] = "" if v is None else v
        kept.append(r)
    papers = lambda r: [r["doi_a"], r["doi_b"]] if "unknown" in r["copying_paper"] else [copying(r)]
    need = byline_checks(db, kept, papers)
    if need and args.fetch_orcids:
        wdb = sqlite3.connect(gi_db.DB_PATH, timeout=300, check_same_thread=False)
        gi_metadata.Metadata(wdb, argparse.Namespace(max_calls=0, workers=6, email=gi_metadata.CONTACT_EMAIL)
                             ).orcid_records(sorted(need))
        wdb.close()
        need = byline_checks(db, kept, papers)
    if need:
        print(f"{len(need)} 'not on byline' authors have an ORCID iD whose record isn't cached "
              "(--fetch-orcids fetches them, free, to tell movers from namesakes)")
    before = {(r["pair_id"], copying(r)) for r in kept}
    not_western = [r for r in kept if r["byline_check"] in ga.NOT_WESTERN]
    if not args.keep_unverified_authors:
        kept = [r for r in kept if r["byline_check"] not in ga.NOT_WESTERN]
    groups = collections.defaultdict(list)
    for r in kept:
        groups[(r["pair_id"], copying(r))].append(r)
    groups_all = set(groups)
    if args.top30:
        groups = {g: rs for g, rs in groups.items() if any(r["university"].lower() in TOP30_NAMES
                                                           and r["byline_check"] not in ga.NOT_WESTERN for r in rs)}
        kept = [r for rs in groups.values() for r in rs]
    if args.min_score:
        groups = {g: rs for g, rs in groups.items() if float(rs[0]["score"]) >= args.min_score}
        kept = [r for rs in groups.values() for r in rs]
    score = lambda g: float(groups[g][0]["score"])
    order = sorted(groups, key=lambda g: (-int(score(g) / SCORE_BAND),
                                          -max(r["uni_h_index"] or 0 for r in groups[g]), -score(g)))
    drop = {"need_doi", "other_doi"}          # columns from older versions of this file
    fields = LEAD + [c for c in rows[0] if c not in LEAD and c not in drop and not c.startswith("_")]
    out_rows = [r for g in order for r in sorted(groups[g], key=lambda r: -(r["uni_h_index"] or 0))]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)
    write_xlsx(args.out.with_suffix(".xlsx"), fields, out_rows)
    tally = collections.Counter(r["have_pdf"] for r in kept)
    print(f"{rows_in} rows in; {len(series)} pairs within a recurring column left out; "
          f"{len(dropped)} copying papers with more than {args.max_authors} authors left out; "
          f"{len(kept)} rows ({len(groups)} copying papers) -> {args.out} and .xlsx")
    print(f"author check: {len(not_western)} rows not Western on the paper "
          + ("kept (--keep-unverified-authors)" if args.keep_unverified_authors else
             f"left out, and with them {len(before - set(groups_all))} copying papers with no Western academic left")
          + "; " + ", ".join(f"{k} {v}" for k, v in collections.Counter(r["byline_check"] for r in kept).most_common()))
    print("abstract_check: " + ", ".join(f"{k or '(none)'} {v}" for k, v in
                                         collections.Counter(r["abstract_check"] for r in kept).most_common()))
    print("have_pdf: " + ", ".join(f"{k} {v}" for k, v in tally.most_common()))


if __name__ == "__main__":
    main()

"""Step 4: full-text check of chosen leads, into the `fulltext` table (gi.sqlite3).

  select   picks leads (by tier; one row per DOI pair, primary_pair = 1; partner-unidentified and
           identical-abstract artifacts left out; pairs already in `fulltext` skipped), adds them to
           `fulltext` under --run with no verdict yet, and writes the retrieval list
           data/<run>.starting.json (retrieve_papers.py input format). A side with no DOI is included
           and retrieved by its title.
  paths    after retrieval, records each downloaded PDF in `local_pdfs` (document key -> local_path),
           so a document has its local path whether or not it has a DOI.
  load-text  puts the extracted paragraphs' text into the library (no embeddings: measure doesn't use
           them).
  move     after measuring, moves the PDFs from the internal disk to the external drive and updates
           state.sqlite3, the library and local_pdfs to the new paths.
  measure  after gi_verify.sh has retrieved, extracted and embedded the papers into an isolated
           library, fills each of the run's rows: retrieved ('AB', 'A-', '-B', '--'); for pairs with both
           papers, rank_paper_pairs.text_check() (10-word exact shingles, lockstep X-drop 3) ->
           ft_pct_a/ft_pct_b (% of each paper's words inside a shared run), ft_matched_words,
           ft_longest, ft_runs; wrong_pdf_side(); the verdict ('real overlap' at >= --min-words matched
           words, 'no real overlap', 'wrong pdf'); and a byline check (any author surname of one side in
           the first paragraphs of the other's text). --fix-years replaces the library's years (taken
           from the index's metadata, often a PDF date) with OpenAlex's publication_year first.

Usage:
  gi_verify.py select --run NAME [--tiers 1 2] [--limit N] [--db data/gi.sqlite3]
  gi_verify.py select --run NAME --per-band N [--seed 1]   (stratified by index overlap, see stratified())
  gi_verify.py paths --run NAME --state WORKDIR/state.sqlite3
  gi_verify.py load-text --run NAME --library WORKDIR/library.sqlite3 --paragraphs-file WORKDIR/paragraphs.jsonl
  gi_verify.py move --run NAME --state WORKDIR/state.sqlite3 --library WORKDIR/library.sqlite3 --dest DIR
  gi_verify.py measure --run NAME --library WORKDIR/library.sqlite3 [--min-words 300] [--fix-years]
  gi_verify.py epoch-years --library WORKDIR/library.sqlite3 [--state WORKDIR/state.sqlite3] [--dry-run]
           (a year of 1970 from a 1970-01-01 "no date" -> the index's real year, or empty; see epoch_years())
gi_verify.sh NAME runs select -> retrieve -> paths -> extract -> load-text -> measure -> move.
"""
import argparse
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import compare_two_papers as ctp  # noqa: E402
import rank_paper_pairs as rpp  # noqa: E402
import retrieve_papers as rp  # noqa: E402

import gi_db  # noqa: E402

PLAIN = lambda t: unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def select(db, args):
    """A side with no DOI (doi "gi:<dkey>": a 2022-update title, no DOI) is included and retrieved by
    title (retrieve_papers.py resolves it through Crossref). data/<run>.keys.json maps each
    retrieval entry's state key to the document key(s) it stands for, for the paths step."""
    if args.per_band:
        rows = stratified(db, args)
    else:
        rows = ranked(db, args)
    write_selection(db, args, rows)


STRATA = [(5, 15), (15, 30), (30, 50), (50, 70), (70, 90), (90, 101)]


def stratified(db, args):
    """--per-band N random cross-author leads in each band of the larger side's index overlap (STRATA),
    for calibrating index overlap against full-text overlap. Kept leads only (no filter reason), no
    shared author, not a retraction tier (1, 3), both sides with a title; one per DOI pair. Draws random
    pair_ids until every band is full, so rare bands (90%+) cost more draws but no full scan.
    --strat-tiers limits it to those tiers instead (5 admits only the "near-identical copy" kind: the
    other kind has a side with no metadata at all, so nothing to retrieve it by); --min-band drops the
    bands below it (tiers 2-5 hold only pairs at 15%+, gi_rank_leads.py --min-pct)."""
    import random
    rng = random.Random(args.seed)
    lo, hi = db.execute("select min(pair_id), max(pair_id) from pairs").fetchone()
    need = {b: (args.per_band if b[0] >= args.min_band else 0) for b in STRATA}
    if args.strat_tiers:
        tier_sql = f"ld.tier in ({','.join(str(int(t)) for t in args.strat_tiers)})"
    else:
        tier_sql = "coalesce(ld.tier, 0) not in (1, 3)"
    unidentified_ok = "partner unidentified (near-identical copy)" if 5 in (args.strat_tiers or []) else ""
    seen, chosen, draws = set(), [], 0
    done = {(r[0], r[1]) for r in db.execute("select pair_lo, pair_hi from fulltext")}
    while any(need.values()) and draws < args.max_draws:
        draws += 5000
        # band from pairs alone (one cheap lookup); full details only for bands still short
        ids = sorted({rng.randint(lo, hi) for _ in range(5000)})
        ids = [i for i, a, b in db.execute(f"select pair_id, pct_a, pct_b from pairs where pair_id in "
                                           f"({','.join('?' * len(ids))})", ids)
               if any(need[x] and x[0] <= max(a, b) < x[1] for x in STRATA)]
        if not ids:
            continue
        for r in db.execute(f"""select ld.*, p.dkey_a, p.dkey_b from lead_details ld join pairs p using (pair_id)
                                where ld.pair_id in ({','.join('?' * len(ids))}) and coalesce(ld.reason, '') = ''
                                and ld.authors = 'no shared author' and {tier_sql}
                                and (coalesce(ld.abstracts, '') not like 'partner unidentified%'
                                     or ld.abstracts = ?)
                                and coalesce(ld.abstracts, '') != 'identical (artifact)'
                                and coalesce(ld.title_a, '') != '' and coalesce(ld.title_b, '') != ''""", (*ids, unidentified_ok)):
            mx = max(r["pct_a"], r["pct_b"])
            band = next((b for b in STRATA if b[0] <= mx < b[1]), None)
            key = (r["pair_lo"], r["pair_hi"])
            if band and need[band] and key not in seen and key not in done:
                seen.add(key)
                need[band] -= 1
                chosen.append(r)
    print(f"{draws:,} pair_ids drawn; per band: " + ", ".join(
        f"{a}-{b if b <= 100 else ''}%: {args.per_band - need[(a, b)]}" for a, b in STRATA if a >= args.min_band))
    return chosen


def ranked(db, args):
    tiers = ",".join(str(int(t)) for t in args.tiers)
    rows = db.execute(f"""select ld.*, p.dkey_a, p.dkey_b from lead_details ld join pairs p using (pair_id)
                          where ld.tier in ({tiers}) and ld.primary_pair = 1
                          and ld.abstracts not like 'partner unidentified%' and ld.abstracts != 'identical (artifact)'
                          and coalesce(ld.title_a, '') != '' and coalesce(ld.title_b, '') != ''
                          and not exists (select 1 from fulltext f where f.pair_lo = ld.pair_lo
                                          and f.pair_hi = ld.pair_hi)
                          order by ld.lead_rank""").fetchall()
    # Within a tier, by gi_preprints.pair_priority(): a later (copying) paper from a major publisher first (a
    # copy in a major journal is the most consequential finding), order unknown with one side major next; copying
    # BY a preprint/repository upload last, "possibly by" one just before -- those are most often the authors'
    # own or an aggregator's copy. So a --limit batch spends its retrievals on the promoted pairs first. Stable:
    # lead_rank order holds otherwise. (stratified() is a calibration sample and is deliberately left unsorted.)
    import gi_preprints
    rows.sort(key=lambda r: (r["tier"], gi_preprints.pair_priority(
        r["doi_a"], r["doi_b"], gi_preprints.earlier_by_year(r["year_a"], r["year_b"]))[0]))
    return rows[:args.limit] if args.limit else rows


def write_selection(db, args, rows):
    want, keys = {}, {}
    for r in rows:
        db.execute("insert into fulltext (pair_lo, pair_hi, doi_a, doi_b, run) values (?,?,?,?,?)",
                   (r["pair_lo"], r["pair_hi"], r["doi_a"], r["doi_b"], args.run))
        for side in ("a", "b"):
            y, doi = r[f"year_{side}"], r[f"doi_{side}"].lower()
            entry = want.setdefault(doi, {
                "title": html.unescape(r[f"title_{side}"] or ""),
                "authors": [s.strip() for s in (r[f"author_{side}"] or "").split(";") if s.strip()],
                "year": int(y) if (y or "").isdigit() else None,
                **({} if doi.startswith("gi:") else {"doi": doi})})
            ks = keys.setdefault(rp.make_key(entry), [])
            if r[f"dkey_{side}"] not in ks:
                ks.append(r[f"dkey_{side}"])
    db.commit()
    out = gi_db.DATA / f"{args.run}.starting.json"
    out.write_text(json.dumps(list(want.values()), indent=1, ensure_ascii=False))
    (gi_db.DATA / f"{args.run}.keys.json").write_text(json.dumps(keys, indent=1))
    no_doi = sum(d.startswith("gi:") for d in want)
    print(f"run {args.run}: {len(rows)} pairs, {len(want)} papers ({no_doi} without a DOI) -> {out}")


def paths(db, args):
    """Record every PDF the run's retrieval downloaded in local_pdfs, by document key. A state row is
    matched to its documents through data/<run>.keys.json (written by select), or, for runs from before
    that file existed, by DOI."""
    kf = gi_db.DATA / f"{args.run}.keys.json"
    keys = json.loads(kf.read_text()) if kf.exists() else {}
    st = sqlite3.connect(args.state)
    st.row_factory = sqlite3.Row
    n_files = n_docs = 0
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for r in st.execute("select key, doi, file_path from papers where status = 'downloaded' and file_path is not null"):
        path = str((Path(args.state).resolve().parent / r["file_path"]).resolve())  # relative = to the workdir
        if not Path(path).exists():
            continue
        dkeys = keys.get(r["key"]) or ([d for (d,) in db.execute("select dkey from documents where lower(doi) = ?",
                                                                (r["doi"].lower(),))] if r["doi"] else [])
        for dk in dkeys:
            db.execute("insert or replace into local_pdfs values (?,?,?,?,?)", (dk, r["doi"], path, args.run, now))
        n_files += 1
        n_docs += len(dkeys)
    db.commit()
    print(f"run {args.run}: {n_files} PDFs recorded in local_pdfs for {n_docs} documents")


def load_text(args):
    """Put extract_papers.py's paragraphs (paragraphs.jsonl) into the library's paragraphs table as text
    only, no embedding. measure, the byline check and the HTML pages read the text; nothing here uses the
    vectors, and embed_paragraphs.py (which is what otherwise stores the text) spends ~10-15 min of CPU
    per batch computing them. Same table, same columns (embedding left null, model 'text-only'), so a
    later embed_paragraphs.py run on this library still works. Per paper: its rows are replaced."""
    lib = sqlite3.connect(args.library)
    lib.execute("""CREATE TABLE IF NOT EXISTS paragraphs (
        id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id), para_index INTEGER NOT NULL,
        text TEXT NOT NULL, embedding BLOB, embedding_dim INTEGER, model TEXT, UNIQUE(paper_id, para_index))""")
    pid = dict(lib.execute("select file_path, id from papers"))
    by_paper, unknown = {}, 0
    with open(args.paragraphs_file) as f:
        for line in f:
            r = json.loads(line)
            if r["file_path"] in pid:
                by_paper.setdefault(pid[r["file_path"]], {})[r["para_index"]] = r["text"]
            else:
                unknown += 1
    for p, paras in by_paper.items():
        lib.execute("delete from paragraphs where paper_id = ?", (p,))
        lib.executemany("insert into paragraphs (paper_id, para_index, text, model) values (?, ?, ?, 'text-only')",
                        [(p, i, t) for i, t in sorted(paras.items())])
    lib.commit()
    print(f"run {args.run}: text of {sum(map(len, by_paper.values()))} paragraphs for {len(by_paper)} papers "
          f"loaded{f'; {unknown} lines for papers not in the library skipped' if unknown else ''}")


def move(db, args):
    """Move the run's downloaded PDFs to --dest, and point every record at the new place: state.sqlite3 and the library's
    papers.file_path (the same string, which is how extract_papers.py matches a paper) and local_pdfs.
    Each file is copied, its size checked, the records updated and committed, and only then the original
    deleted, so an interrupted move (the drive dropping off USB) leaves every record pointing at a file
    that exists. Rerunnable: files already under --dest are skipped."""
    import shutil
    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.state).resolve().parent
    st, lib = sqlite3.connect(args.state), sqlite3.connect(args.library)
    moved = skipped = 0
    for old, in st.execute("select file_path from papers where status = 'downloaded' and file_path is not null").fetchall():
        src = (workdir / old).resolve()
        if src.parent == dest or not src.exists():
            skipped += 1
            continue
        new = dest / src.name
        if not (new.exists() and new.stat().st_size == src.stat().st_size):  # a same-size copy already there: reuse it
            shutil.copy2(src, new)
        if new.stat().st_size != src.stat().st_size:
            raise OSError(f"copy of {src} to {new} is the wrong size")
        st.execute("update papers set file_path = ? where file_path = ?", (str(new), old))
        lib.execute("update papers set file_path = ? where file_path = ?", (str(new), old))
        db.execute("update local_pdfs set local_path = ? where local_path = ?", (str(new), str(src)))
        st.commit(); lib.commit(); db.commit()
        src.unlink()
        moved += 1
    print(f"run {args.run}: {moved} PDFs moved to {dest}, {skipped} already there or missing")


def fix_years(lib):
    key = os.environ.get("OPENALEX_API_KEY")
    rows = lib.execute("select id, doi, year from papers where doi is not null").fetchall()
    dois, years = [r[1].lower() for r in rows], {}
    for i in range(0, len(dois), 50):
        params = {"filter": "doi:" + "|".join(dois[i:i + 50]), "per-page": 50,
                  "select": "doi,publication_year,publication_date"}
        if key:
            params["api_key"] = key
        resp = requests.get("https://api.openalex.org/works", params=params, timeout=60)
        if resp.status_code == 200:
            for w in resp.json().get("results", []):
                # OpenAlex gives 1970-01-01 for a work with no date: that's no year, keep the library's
                if gi_db.pub_year(w.get("publication_date") or w.get("publication_year")):
                    years[(w.get("doi") or "").replace("https://doi.org/", "").lower()] = w.get("publication_year")
    n = 0
    for pid, doi, y in rows:
        oy = years.get(doi.lower())
        if oy and str(oy) != str(y):
            lib.execute("update papers set year = ? where id = ?", (oy, pid))
            n += 1
    lib.commit()
    print(f"years corrected from OpenAlex: {n} of {len(rows)}")


def epoch_years(db, args):
    """Undo the epoch: a library year of 1970 that came from a 1970-01-01 date (gi_db.EPOCH_DATE, "no date"
    in the index's metadata and in OpenAlex) becomes the index's real year for that DOI when it has one, else
    empty. Fixes the library, the run's state.sqlite3 (--state) and the fulltext rows that copied the year.
    --dry-run only prints."""
    lib = sqlite3.connect(args.library)
    st = sqlite3.connect(args.state) if args.state else None
    changes = []
    for pid, doi in lib.execute("select id, doi from papers where year = 1970").fetchall():
        dates = [d for (d,) in db.execute("select pub_date from documents where lower(doi) = ?", ((doi or "").lower(),))]
        years = sorted({gi_db.pub_year(d) for d in dates} - {None})
        if any(str(d or "").startswith("1970") and not str(d).startswith(gi_db.EPOCH_DATE) for d in dates):
            continue   # the index has a real 1970 date (not the epoch): leave it
        new = int(years[0]) if years else None
        changes.append((pid, doi, new))
        print(f"  {doi}: 1970 -> {new or 'none'}  (index dates: {', '.join(map(str, dates)) or 'none'})")
    if args.dry_run:
        print(f"{args.library}: {len(changes)} epoch years (dry run, nothing written)")
        return
    for pid, doi, new in changes:
        lib.execute("update papers set year = ? where id = ?", (new, pid))
        if st:
            st.execute("update papers set year = ? where lower(doi) = ? and year = 1970", (new, (doi or "").lower()))
        for side in ("a", "b"):
            db.execute(f"update fulltext set year_{side} = ? where library = ? and paper_{side} = ?",
                       (str(new) if new else None, str(args.library), pid))
    lib.commit()
    if st:
        st.commit()
    db.commit()
    print(f"{args.library}: {len(changes)} epoch years fixed")


def surnames(lib, pid):
    out = set()
    for (n,) in lib.execute("select a.name from paper_authors pa join authors a on a.id = pa.author_id "
                            "where pa.paper_id = ?", (pid,)):
        n = PLAIN(n)
        last = n.split(",")[0] if "," in n else (n.split() or [""])[-1]
        # each part of a multi-word surname on its own: "Le Moal" -> "moal" (joined into "lemoal"
        # it could never match the text, which keeps the space)
        out.update(tok for tok in re.findall(r"[a-z]+", last) if len(tok) >= 4)
    return out


def pdf_page1(lib, pid):
    """Page 1 of the retrieved PDF itself (pdftotext), "" if unavailable."""
    row = lib.execute("select file_path from papers where id = ?", (pid,)).fetchone()
    db_file = lib.execute("pragma database_list").fetchone()[2]
    if not row or not row[0] or not db_file:
        return ""
    path = Path(row[0]) if Path(row[0]).is_absolute() else Path(db_file).parent / row[0]
    try:
        return subprocess.run(["pdftotext", "-l", "1", str(path), "-"], capture_output=True, text=True,
                              timeout=60).stdout if path.exists() else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def opening(lib, pid, n=6):
    """The text a byline should be in: the first n extracted paragraphs AND page 1 of the PDF.
    extract_papers.py can drop the title/byline block as a header fragment -- in GI-13 (2026-10-07)
    neither paper's author list survived extraction, so the paragraphs alone could never match."""
    paras = " ".join(r[0] for r in lib.execute(
        "select text from paragraphs where paper_id = ? order by para_index limit ?", (pid, n)))
    return PLAIN(paras + " " + pdf_page1(lib, pid))


def measure(db, args):
    lib = sqlite3.connect(args.library)
    if args.fix_years:
        fix_years(lib)
    by_doi = {d.lower(): i for i, d in lib.execute("select id, doi from papers where doi is not null")}
    libdir = Path(args.library).resolve().parent
    by_path = {str((libdir / f).resolve()): i for i, f in lib.execute("select id, file_path from papers")}

    def paper_id(side):
        """The library paper for a fulltext side ("gi:<dkey>" or a DOI), through local_pdfs first, so a
        side with no DOI is found by its document key; by DOI for runs from before local_pdfs."""
        q, key = (("select local_path from local_pdfs where dkey = ?", side[3:]) if side.startswith("gi:") else
                  ("select local_path from local_pdfs where lower(doi) = ?", side.lower()))
        for (path,) in db.execute(q, (key,)):
            if path in by_path:
                return by_path[path]
        return by_doi.get(side.lower())
    year = lambda pid: (lib.execute("select year from papers where id = ?", (pid,)).fetchone() or [None])[0]
    check_args = argparse.Namespace(min_doc_words=300, shingle_size=10, x_drop=3, gap_open=None,
                                    gap_extend=ctp.DEFAULT_GAP_EXTEND, max_gap=ctp.DEFAULT_MAX_GAP)
    rows = db.execute("select * from fulltext where run = ?", (args.run,)).fetchall()
    counts = {}
    for r in rows:
        a, b = paper_id(r["doi_a"]), paper_id(r["doi_b"])
        upd = {"retrieved": f"{'A' if a else '-'}{'B' if b else '-'}", "paper_a": a, "paper_b": b,
               "library": str(args.library), "year_a": str(year(a)) if a and year(a) else None,
               "year_b": str(year(b)) if b and year(b) else None, "ft_verdict": None, "ft_pct_a": None,
               "ft_pct_b": None, "ft_matched_words": None, "ft_longest": None, "ft_runs": None,
               "wrong_pdf": None, "byline": None}
        if a and b:
            stats = rpp.text_check(lib, (a, b), check_args)
            upd["wrong_pdf"] = rpp.wrong_pdf_side(lib, a, b) or None
            words = stats["matched_words"] if stats else 0
            if stats:
                upd.update(ft_pct_a=round(stats["pct_a"]), ft_pct_b=round(stats["pct_b"]),
                           ft_matched_words=words, ft_longest=stats["longest"], ft_runs=stats["runs"])
            else:
                upd.update(ft_pct_a=0, ft_pct_b=0, ft_matched_words=0, ft_longest=0, ft_runs=0)
            upd["ft_verdict"] = ("wrong pdf" if upd["wrong_pdf"] else
                                 "real overlap" if words >= args.min_words else "no real overlap")
            oa, ob, sa, sb = opening(lib, a), opening(lib, b), surnames(lib, a), surnames(lib, b)
            hit = sorted({s for s in sa if re.search(r"\b" + s + r"\b", ob)} |
                         {s for s in sb if re.search(r"\b" + s + r"\b", oa)})
            upd["byline"] = "byline overlap: " + ",".join(hit) if hit else "no byline overlap"
        counts[upd["ft_verdict"] or f"not both retrieved"] = counts.get(upd["ft_verdict"] or "not both retrieved", 0) + 1
        db.execute("update fulltext set " + ", ".join(f"{k} = :{k}" for k in upd) +
                   " where pair_lo = :lo and pair_hi = :hi", {**upd, "lo": r["pair_lo"], "hi": r["pair_hi"]})
    db.commit()
    print(f"run {args.run}: {len(rows)} pairs: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["select", "paths", "load-text", "measure", "move", "epoch-years"])
    ap.add_argument("--run", required=True)
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--tiers", nargs="+", type=int, default=[1, 2])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--per-band", type=int, default=0,
                    help="select: N random cross-author leads per index-overlap band instead of by rank")
    ap.add_argument("--strat-tiers", nargs="+", type=int,
                    help="--per-band: only these tiers (default: every tier but the retraction tiers 1 and 3)")
    ap.add_argument("--min-band", type=int, default=5, help="--per-band: skip bands starting below this %%")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-draws", type=int, default=5_000_000)
    ap.add_argument("--library")
    ap.add_argument("--state", help="paths, move: the run's retrieval state.sqlite3")
    ap.add_argument("--dest", help="move: where the PDFs go (the external drive)")
    ap.add_argument("--paragraphs-file", help="load-text: extract_papers.py's paragraphs.jsonl")
    ap.add_argument("--min-words", type=int, default=300)
    ap.add_argument("--fix-years", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="epoch-years: print the changes, write nothing")
    args = ap.parse_args()
    if args.command == "epoch-years" and args.dry_run:   # a dry run doesn't touch gi.sqlite3 at all
        db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
        db.row_factory = sqlite3.Row
    else:
        db = gi_db.connect(args.db)
    if args.command == "select":
        select(db, args)
    elif args.command == "load-text":
        if not (args.library and args.paragraphs_file):
            ap.error("load-text needs --library and --paragraphs-file")
        load_text(args)
    elif args.command == "move":
        if not (args.state and args.library and args.dest):
            ap.error("move needs --state, --library and --dest")
        move(db, args)
    elif args.command == "epoch-years":
        if not args.library:
            ap.error("epoch-years needs --library (and --state for the run's state.sqlite3)")
        epoch_years(db, args)
    elif args.command == "paths":
        if not args.state:
            ap.error("paths needs --state")
        paths(db, args)
    else:
        if not args.library:
            ap.error("measure needs --library")
        measure(db, args)


if __name__ == "__main__":
    main()

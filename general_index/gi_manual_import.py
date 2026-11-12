"""Hand-downloaded PDFs for the unobtainable list -> full-text check -> comparison pages, in one command.

Save PDFs into the drop folder (data/unobtainable_downloads/) under whatever name the browser gives them, then
run this. It works out which needed paper each PDF is, imports it, and measures every pair that now has both
papers, so you never rename a file.

Which papers are needed: every pair in the unobtainable CSV(s) (--csv, default the dated list in data/), each
side counted as held when local_pdfs points at a file that exists on disk. A paper needed by several pairs is
one paper; importing it completes all of them whose other side is held.

Matching a PDF (match_pdf()), all against the needed papers only, never the whole index:
  1. DOI: any needed DOI printed on the first two pages or in the PDF's metadata (Info dict + XMP). Exact.
  2. Title: a needed paper's title (normalized: accents, case, punctuation; and again with spaces removed, for
     PDFs that lose inter-word spaces) found on the first two pages. Titles under 3 words / 20 characters
     are never matched by title alone (too common).
  Several candidates: those that both print their DOI and title win; then the longest title (a short title
  can sit inside a longer one); a tie, or a title match while page 1 prints a different known DOI (often
  the other side of a same-title pair), is left for you.
  Anything not matched stays in the drop folder, listed with the reason in _unmatched.md. Non-PDFs too.

The full-text check runs in one working folder for every hand download, data/verify_manual_s16/ (run
manual_s16). gi_verify.py measure compares two papers only inside one run's library, which is why pairs whose
two PDFs were fetched by different runs were never measured; here the PDF that was already held is referenced
in place (its absolute path in this run's state.sqlite3), not copied. Each pair completed moves to this run:
fulltext.run is set to manual_s16 (the old run logged in data/manual_s16.moved.tsv). Then extract_papers.py,
gi_verify.py load-text and measure --fix-years (same 10-word shingle check as every run), and
gi_write_pages.py, which numbers and writes a page for each real cross-author overlap (GI-NN). The Western
tagging needs nothing extra: the review tool's --western-only reads the cached affiliations these pairs were
listed by.

Idempotent: matched PDFs are moved out of the drop folder, a paper already held is reported and its file left,
and re-running with nothing new only re-measures (--no-measure skips that).

Usage: gi_manual_import.py [--drop DIR] [--csv FILE ...] [--dry-run] [--no-measure] [--no-pages]
"""
import argparse
import csv
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402

import gi_db  # noqa: E402

RUN = "manual_s16"
HERE = Path(__file__).resolve().parent
DROP = gi_db.DATA / "unobtainable_downloads"
WORK = gi_db.DATA / f"verify_{RUN}"
CSVS = [gi_db.DATA / "unobtainable_western_2026-10-30.csv"]
PAGES_DIR = gi_db.DATA / "fulltext_pages_s16"
PY = str(HERE.parent / ".venv" / "bin" / "python3")
MIN_TITLE_WORDS, MIN_TITLE_CHARS = 4, 20
TITLE_TOP = 0.5     # a title-only match must start in the top half of page 1 (references come lower)
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def norm(text):
    """Lowercase ASCII words separated by single spaces."""
    text = re.sub(r"\\[rnt]", " ", text or "")       # literal "\\r" etc. in index titles
    text = unicodedata.normalize("NFKD", TAG_RE.sub("", html.unescape(text))).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def dois_in(text):
    """DOIs in text, lowercased, with trailing punctuation trimmed (and each shorter prefix, since a DOI printed
    at a line end often runs into the next word)."""
    out = set()
    for m in DOI_RE.findall(text or ""):
        d = m.lower().rstrip(".,;:)]}'⟩")
        out.add(d)
        while d and d[-1] in ".,;:)]}'":
            d = d[:-1]
            out.add(d)
    return out


def held_path(db, side, dkey):
    """An existing local PDF for a pair side (DOI or 'gi:<dkey>'), or None."""
    q = [("select local_path from local_pdfs where dkey = ?", dkey)]
    if not side.startswith("gi:"):
        q.append(("select local_path from local_pdfs where lower(doi) = ?", side.lower()))
    for sql, key in q:
        for (p,) in db.execute(sql, (key,)):
            if p and Path(p).exists():
                return p
    return None


def load_needs(db, csv_paths):
    """pairs: pair_id -> {lo, hi, sides: {a/b: {id, dkey, title, year, authors, path}}};
    needed: paper id (DOI lowercased, or 'gi:<dkey>') -> {title, year, authors, dkeys, pairs}."""
    csv.field_size_limit(10**9)
    ids = []
    for p in csv_paths:
        with open(p, newline="", encoding="utf-8") as f:
            ids += [int(r["pair_id"]) for r in csv.DictReader(f)]
    ids = list(dict.fromkeys(ids))
    pairs, needed = {}, {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        for r in db.execute(f"""select ld.pair_id, ld.pair_lo, ld.pair_hi, ld.doi_a, ld.doi_b, p.dkey_a, p.dkey_b,
                                       ld.title_a, ld.title_b, ld.year_a, ld.year_b, ld.author_a, ld.author_b
                                from lead_details ld join pairs p using (pair_id)
                                where ld.pair_id in ({','.join('?' * len(chunk))})""", chunk):
            r = dict(zip(("pair_id", "lo", "hi", "doi_a", "doi_b", "dkey_a", "dkey_b", "title_a", "title_b",
                          "year_a", "year_b", "author_a", "author_b"), r))
            sides = {}
            for s in "ab":
                pid = r[f"doi_{s}"] if r[f"doi_{s}"].startswith("gi:") else r[f"doi_{s}"].lower()
                sides[s] = {"id": pid, "dkey": r[f"dkey_{s}"], "title": html.unescape(r[f"title_{s}"] or ""),
                            "year": r[f"year_{s}"],
                            "authors": [a.strip() for a in (r[f"author_{s}"] or "").split(";") if a.strip()],
                            "path": held_path(db, r[f"doi_{s}"], r[f"dkey_{s}"])}
            pairs[r["pair_id"]] = {"lo": r["lo"], "hi": r["hi"], "sides": sides}
            for s in sides.values():
                if not s["path"]:
                    n = needed.setdefault(s["id"], {**{k: s[k] for k in ("title", "year", "authors")},
                                                    "dkeys": set(), "pairs": set()})
                    n["dkeys"].add(s["dkey"])
                    n["pairs"].add(r["pair_id"])
    return pairs, needed


def title_keys(needed):
    """paper id -> (normalized title, the same with no spaces), for titles worth matching on: at least
    MIN_TITLE_WORDS words and MIN_TITLE_CHARS characters, and not text-extraction junk ("R E V I E W A RT I C L E":
    half the words one or two letters long). Matching on a title's last few words instead (for index titles that
    lead with a book's or conference's name) was tried and dropped: it matched boilerplate endings like "...of
    the Creative Commons Attribution" and took the wrong paper 8 times in 300 instead of 2."""
    out = {}
    for pid, n in needed.items():
        t = norm(n["title"])
        w = t.split()
        if len(w) < MIN_TITLE_WORDS or len(t) < MIN_TITLE_CHARS or sum(len(x) <= 2 for x in w) * 2 > len(w):
            continue
        out[pid] = (t, t.replace(" ", ""))
    return out


def surnames(authors):
    """Normalized surnames ("Last, First" or "First Last"), 3+ letters, for the page-1 author check."""
    out = set()
    for a in authors or []:
        last = a.split(",")[0] if "," in a else (a.split() or [""])[-1]
        last = norm(last)
        if len(last) >= 3:
            out.add(last)
    return out


def pdf_text(path):
    """([page 1 text, page 2 text], metadata text), or None if it doesn't open as a PDF."""
    try:
        doc = fitz.open(path)
    except Exception:
        return None
    try:
        pages = [doc[i].get_text() for i in range(min(2, doc.page_count))]
        meta = " ".join(str(v) for v in (doc.metadata or {}).values() if v) + " " + (doc.get_xml_metadata() or "")
    finally:
        doc.close()
    return pages, meta


def title_pos(keys, nt, ns):
    """Where in the text the title starts, as a fraction of the text (0 = top), or None."""
    for k, txt in ((keys[0], nt), (keys[1], ns)):
        i = txt.find(k)
        if i >= 0:
            return i / max(len(txt), 1)
    return None


def after_references(nt, tpos):
    """True when a "references" heading comes before the title on the page."""
    i = nt.find("references")
    return 0 <= i < tpos * len(nt)


def rank_zone(text, meta, needed, tkeys):
    """Needed papers named in text (+ metadata), best first, each with its sort key and how it was found:
    a DOI in the PDF's own metadata first; then a title, the nearer the top the better (a paper's title is at
    the top of its page 1, references to other papers come lower); then a DOI only on the page, by position."""
    nt = norm(text)
    ns = nt.replace(" ", "")
    low = text.lower()
    meta_dois = dois_in(meta) & needed.keys()
    page_dois = dois_in(text) & needed.keys()
    found = {}
    for pid in meta_dois | page_dois:
        found[pid] = None
    for pid, keys in tkeys.items():
        if keys[0] in nt or keys[1] in ns:
            found.setdefault(pid, None)
    ranked = []
    for pid in found:
        tpos = title_pos(tkeys[pid], nt, ns) if pid in tkeys else None
        if pid in meta_dois:
            key, how = (0, tpos if tpos is not None else 1), "DOI in PDF metadata" + (" + title" if tpos is not None else "")
        elif tpos is not None and after_references(nt, tpos):
            continue          # cited in a reference list printed on this page (a page shared with another article)
        elif tpos is not None and pid in page_dois:
            key, how = (1, tpos), "title + DOI"
        elif tpos is not None:
            names = surnames(needed[pid]["authors"])
            if tpos > TITLE_TOP or not names or not any(f" {n} " in f" {nt} " for n in names):
                continue      # lower on the page, or without its authors on it: a reference, or a generic title
            key, how = (1, tpos), "title + author"
        else:
            key, how = (2, low.find(pid) / max(len(low), 1)), "DOI on the page"
        ranked.append((key, pid, how))
    return sorted(ranked)


def match_pdf(path, needed, tkeys, known_dois):
    """(paper id, how) or (None, reason). Page 1 and the metadata first; page 2 only when they name no needed
    paper (a cover sheet), because page 2 is often where the references start. Measured on 300 held PDFs mixed
    into the real needed list (see gi_manual_import's docstring)."""
    got = pdf_text(path)
    if got is None:
        return None, "doesn't open as a PDF"
    pages, meta = got
    if len(" ".join(pages).strip()) < 50:
        return None, "no text layer on the first pages (scanned?): match it by hand"
    printed = set()
    for text, m, where in ((pages[0], meta, "page 1"), (" ".join(pages[1:]), "", "page 2")):
        printed |= dois_in(text) | dois_in(m)
        ranked = rank_zone(text, m, needed, tkeys)
        if not ranked:
            continue
        (key, pid, how), rest = ranked[0], ranked[1:]
        ties = [p for k, p, _ in rest if k == key]
        if ties:
            return None, "matches several needed papers equally: " + "; ".join([pid] + ties[:4])
        top = page_top(pages[0])
        other = ((dois_in(top) | dois_in(meta)) & known_dois) - {pid}
        if not how.startswith("DOI") and "DOI" not in how and other:
            return None, f"title matches {pid} but the top of page 1 prints DOI {sorted(other)[0]}"
        return pid, f"{how}, {where}"
    if printed:
        return None, "prints DOI " + ", ".join(sorted(printed)[:3]) + ", which no listed pair needs"
    return None, "no needed DOI or title found on the first two pages"


def page_top(text, frac=0.25):
    return text[:max(int(len(text) * frac), 600)]


def state_store():
    WORK.mkdir(parents=True, exist_ok=True)
    return rp.PaperStore(WORK / "state.sqlite3")


def state_key(pid):
    return pid if pid.startswith("gi:") else "doi:" + pid


def add_state(store, pid, info, file_path):
    store.upsert(state_key(pid), title=info["title"], authors=json.dumps(info["authors"]),
                 year=int(info["year"]) if str(info["year"] or "").isdigit() else None,
                 doi=None if pid.startswith("gi:") else pid, status="downloaded", oa_status="manual",
                 pdf_url=None, file_path=file_path, error=None)


def import_files(db, args, pairs, needed):
    tkeys = title_keys(needed)
    known = {d.lower() for p in pairs.values() for s in p["sides"].values() for d in [s["id"]]
             if not d.startswith("gi:")}
    store = None if args.dry_run else state_store()
    (WORK / "papers").mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    done, unmatched = [], []
    for f in sorted(p for p in args.drop.iterdir() if p.is_file() and not p.name.startswith("_")):
        if f.suffix.lower() != ".pdf":
            unmatched.append((f.name, "not a .pdf file (still downloading, or a saved web page?)"))
            continue
        pid, how = match_pdf(f, needed, tkeys, known)
        if pid is None:
            unmatched.append((f.name, how))
            continue
        info = needed[pid]
        dest = WORK / "papers" / (rp.slugify(info["title"], 100) + "-" + rp.slugify(pid, 80) + ".pdf")
        print(f"  {f.name} -> {pid} ({how}): {info['title'][:80]}")
        done.append(pid)
        if args.dry_run:
            continue
        shutil.move(str(f), dest)
        add_state(store, pid, info, str(dest.relative_to(WORK)))
        dkeys = set(info["dkeys"])
        if not pid.startswith("gi:"):
            dkeys |= {d for (d,) in db.execute("select dkey from documents where lower(doi) = ?", (pid,))}
        for dk in dkeys:
            db.execute("insert or replace into local_pdfs values (?,?,?,?,?)",
                       (dk, None if pid.startswith("gi:") else pid, str(dest.resolve()), RUN, now))
        db.commit()
        for pair_id in info["pairs"]:
            for s in pairs[pair_id]["sides"].values():
                if s["id"] == pid:
                    s["path"] = str(dest.resolve())
        del needed[pid]
        tkeys.pop(pid, None)
    report = args.drop / "_unmatched.md"
    if unmatched:
        lines = ["# PDFs not imported", "",
                 "Each stays in this folder. Fix the cause (or tell Claude which paper it is) and run "
                 "gi_manual_import.py again.", ""]
        lines += [f"- `{n}`: {why}" for n, why in unmatched]
        if not args.dry_run:
            report.write_text("\n".join(lines) + "\n")
    elif report.exists() and not args.dry_run:
        report.unlink()
    print(f"matched {len(done)}, not matched {len(unmatched)}" + (f" (see {report})" if unmatched else ""))
    return store


def complete_pairs(db, args, pairs, store):
    """Move every listed pair with both PDFs held, and not yet measured with both, into this run."""
    moved = []
    log = gi_db.DATA / f"{RUN}.moved.tsv"
    for pair_id, p in pairs.items():
        sides = p["sides"]
        if not all(s["path"] for s in sides.values()):
            continue
        row = db.execute("select run, retrieved from fulltext where pair_lo = ? and pair_hi = ?",
                         (p["lo"], p["hi"])).fetchone()
        if row and row[0] == RUN:
            continue
        if row and row[1] == "AB":
            continue                      # already measured with both papers in some run
        moved.append((pair_id, row[0] if row else ""))
        if args.dry_run:
            continue
        for s in sides.values():
            if not store.get(state_key(s["id"])):
                add_state(store, s["id"], s, s["path"])      # the PDF already held, referenced in place
        if row:
            db.execute("update fulltext set run = ? where pair_lo = ? and pair_hi = ?", (RUN, p["lo"], p["hi"]))
        else:
            db.execute("insert into fulltext (pair_lo, pair_hi, doi_a, doi_b, run) values (?,?,?,?,?)",
                       (p["lo"], p["hi"], *(s["id"] for s in sides.values()), RUN))
    if moved and not args.dry_run:
        db.commit()
        new = not log.exists()
        with open(log, "a") as f:
            if new:
                f.write("pair_id\told_run\tmoved_at\n")
            f.writelines(f"{pid}\t{old}\t{time.strftime('%Y-%m-%d %H:%M:%S')}\n" for pid, old in moved)
    print(f"pairs now complete and moved to run {RUN}: {len(moved)}")
    return moved


def run(cmd, cwd):
    print("$ " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", type=Path, default=DROP)
    ap.add_argument("--csv", type=Path, nargs="+", default=CSVS)
    ap.add_argument("--dry-run", action="store_true", help="match and report only; move and write nothing")
    ap.add_argument("--no-measure", action="store_true", help="import only; skip extract/measure/pages")
    ap.add_argument("--no-pages", action="store_true", help="measure, but don't write pages")
    args = ap.parse_args()
    args.drop.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(gi_db.DB_PATH)
    pairs, needed = load_needs(db, args.csv)
    print(f"{len(pairs)} listed pairs, {len(needed)} papers needed")
    store = import_files(db, args, pairs, needed)
    if args.dry_run:
        complete_pairs(db, args, pairs, store)
        return
    complete_pairs(db, args, pairs, store)
    if args.no_measure or not db.execute("select 1 from fulltext where run = ?", (RUN,)).fetchone():
        return
    db.close()
    keyf = Path.home() / ".config" / "dupefinder" / "openalex_api_key"
    if keyf.exists():
        os.environ["OPENALEX_API_KEY"] = keyf.read_text().strip()
    run([PY, HERE.parent / "extract_papers.py", "--state-db", "state.sqlite3", "--library-db", "library.sqlite3",
         "--paragraphs-file", "paragraphs.jsonl"], WORK)
    run([PY, HERE / "gi_verify.py", "load-text", "--run", RUN, "--library", WORK / "library.sqlite3",
         "--paragraphs-file", WORK / "paragraphs.jsonl"], HERE)
    run([PY, HERE / "gi_verify.py", "measure", "--run", RUN, "--library", WORK / "library.sqlite3", "--fix-years"],
        HERE)
    if not args.no_pages:
        run([PY, HERE / "gi_write_pages.py", PAGES_DIR, "--sample-rate", "8", "--run", RUN], HERE)
    db = sqlite3.connect(f"file:{gi_db.DB_PATH}?mode=ro", uri=True)
    print("\nrun " + RUN + ": " + ", ".join(f"{v or 'not both retrieved'} {n}" for v, n in db.execute(
        "select ft_verdict, count(*) from fulltext where run = ? group by 1 order by 2 desc", (RUN,))))
    for pid, page, pa, pb in db.execute("""select pair_lo || ' / ' || pair_hi, page_number, ft_pct_a, ft_pct_b
                                           from fulltext where run = ? and ft_verdict = 'real overlap'
                                           and byline = 'no byline overlap' order by page_number""", (RUN,)):
        print(f"  GI-{page}: {pid} ({pa}% / {pb}%)")


if __name__ == "__main__":
    main()

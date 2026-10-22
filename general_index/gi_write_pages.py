"""Write neutral HTML comparison pages for full-text-confirmed General Index leads (from gi.sqlite3).

One page per pair in `fulltext` with verdict 'real overlap' and no byline overlap, not reviewed as
'same paper' (`reviews`), via write_dupe_reports_screenshots.py --paper-ids (no potential_dupes table
needed): each overlap as crops of both PDFs side by side with the shared words highlighted, images in
OUT_DIR/img/GI-NN/ (--text-pages: the old text diffs, write_dupe_reports_html.py). With an "Overlap
evidence" block at the top:
  - full text (from `fulltext`): each paper's % of words inside a run shared with the other, matched
    words, longest run, verdict;
  - the General Index evidence that put the pair on the list (from `leads` + `pairs`): shared sampled
    5-grams and the estimated total, each document's share of its own sampled 5-grams found in the
    other, the band of the larger, the lead tier and the abstract evidence.

Each page also links to the local copy of both PDFs (the verification library's papers.file_path,
as a link relative to OUT_DIR so it works opened from disk), or says "(no local copy)".

Page numbers (GI-NN) live in fulltext.page_number, so a page keeps its number across reruns; a pair
without one gets the next free number. Writes fulltext.page, OUT_DIR/_index.tsv and OUT_DIR/index.html:
a landing page ranking every page by the averaged overlap score, the mean of the two full-text
percentages ((ft_pct_a + ft_pct_b) / 2), with the agent's and the human's decisions from `results`.
Pairs reviewed as "same paper" have no page and are listed, marked, at the end of the index. Pairs whose later
(copying) paper is from a major publisher are ranked first, those whose later paper is a preprint/repository
upload after the rest, all tagged (gi_preprints.pair_priority()).

--no-db-writes opens gi.sqlite3 read-only and skips the fulltext.page / page_number updates (every
pair must already have a page_number); --index-only rewrites index.html from the existing pages.

Usage: gi_write_pages.py [OUT_DIR] [--db data/gi.sqlite3] [--sample-rate 4] [--no-db-writes] [--index-only] [--text-pages]
                         [--pages 10,40,44]
OUT_DIR defaults to data/fulltext_pages_s0123.
"""
import argparse
import csv
import html
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import gi_db

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python3")
TIERS = {"1": "a Retraction Watch plagiarism retraction on one side",
         "2": "the two abstracts share a 20+ word verbatim run",
         "3": "a Retraction Watch retraction (other reason) on one side",
         "4": "index evidence only",
         "5": "one side unidentified"}


def fulltext_lines(res):
    """Full-text figures from a `fulltext` row (already in this page's A/B order)."""
    if not res or not res["ft_verdict"]:
        return ["<strong>Full text</strong>: not checked (both PDFs not available)"]
    pct = lambda v: "?" if v is None else f"{v:.0f}%"
    return [
        "<strong>Full text</strong> (both PDFs, 10-word exact word-shingle matching, x-drop 3)",
        f"Paper A (left): {pct(res['ft_pct_a'])} of its words are inside a run shared with paper B",
        f"Paper B (right): {pct(res['ft_pct_b'])} of its words are inside a run shared with paper A",
        f"Matched words: {res['ft_matched_words']:,}; longest shared run: {res['ft_longest']:,} words; "
        f"verdict: {html.escape(res['ft_verdict'] or '')}",
    ]


def earlier_paper(lib, a, b):
    """The earlier of two library papers (resolve_earlier_id(): by year), or None when that isn't known."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from write_dupe_reports_html import resolve_earlier_id
    lo, hi = sorted((a, b))
    conn = sqlite3.connect(f"file:{lib}?mode=ro", uri=True)
    try:
        return resolve_earlier_id(conn, lo, hi, [])
    finally:
        conn.close()


def page_order(lib, a, b):
    """(left, right) paper ids as the comparison page lays them out: the earlier paper on the left, else the
    lower id -- write_dupe_reports_screenshots.py / _html.py sort --paper-ids and orient by
    resolve_earlier_id(). The evidence block has to follow this, not fulltext's own A/B order."""
    lo, hi = sorted((a, b))
    return (hi, lo) if earlier_paper(lib, a, b) == hi else (lo, hi)


def priority_status(f):
    """(rank, note) for a fulltext row: gi_preprints.pair_priority() -- is the later (copying) paper from a
    major publisher (promoted, rank < 0) or a preprint (demoted, rank > 0)? gi_preprints.py has the rule and
    why. Which paper came first is decided as on the page (earlier_paper()), falling back to fulltext's own
    years only when there is no library to ask (a library that says "same year" wins: its years were
    corrected from the publisher, fulltext's may be a PDF date)."""
    import gi_preprints
    return gi_preprints.pair_priority(f["doi_a"], f["doi_b"], earlier_side(f))


def earlier_side(f):
    """'a' / 'b' for a fulltext row's earlier paper, or None: as priority_status() decides it."""
    import gi_preprints
    try:
        if not (f["library"] and f["paper_a"] and f["paper_b"]):
            raise LookupError
        e = earlier_paper(f["library"], f["paper_a"], f["paper_b"])
        return "a" if e == f["paper_a"] else "b" if e == f["paper_b"] else None
    except (LookupError, sqlite3.Error, TypeError):
        return gi_preprints.earlier_by_year(f["year_a"], f["year_b"])


def oriented(res, left):
    """The fulltext row's A/B fields, swapped when its paper_b is the page's left paper."""
    row = dict(res)
    if left == res["paper_b"]:
        for x in ("doi", "year", "paper", "ft_pct"):
            row[f"{x}_a"], row[f"{x}_b"] = res[f"{x}_b"], res[f"{x}_a"]
    return row


def local_pdf(lib, pid, out_dir):
    """(relative link from out_dir, file name) for a paper's local PDF, or None if there isn't one."""
    conn = sqlite3.connect(f"file:{lib}?mode=ro", uri=True)
    row = conn.execute("select file_path from papers where id = ?", (pid,)).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    path = Path(row[0]) if Path(row[0]).is_absolute() else Path(lib).parent / row[0]
    if not path.exists():
        return None
    return os.path.relpath(path, out_dir), path.name


def pdf_line(label, link):
    if not link:
        return f"{label}: (no local copy)"
    href, name = link
    return f'{label}: <a href="{html.escape(href, quote=True)}">{html.escape(name)}</a>'


def evidence_note(rep, lead, sample_rate, doi_a, res, pdf_a=None, pdf_b=None):
    """rep: the pair's first non-too-short `leads` row (report order) joined to `pairs`; lead: its
    ranked (primary) row. Both may list the pair in the other order; re-oriented to this page's A.
    rep None (the pair's lead rows are gone, e.g. after the 16-shard rebuild): full-text evidence only."""
    local = ["<strong>Local PDFs</strong>", pdf_line("Paper A (left)", pdf_a), pdf_line("Paper B (right)", pdf_b)]
    if rep is None:
        return "<br>".join(local + ["<strong>Overlap evidence</strong>"] + fulltext_lines(res)
                           + ["<strong>General Index</strong>: no lead row for this pair any more (the ranked "
                              "list was rebuilt), so the index figures aren't shown"])
    flip = rep["doi_a"].lower() != doi_a.lower()
    pa, pb = (rep["pct_b"], rep["pct_a"]) if flip else (rep["pct_a"], rep["pct_b"])
    sa, sb = (rep["sampled_b"], rep["sampled_a"]) if flip else (rep["sampled_a"], rep["sampled_b"])
    shared = int(rep["shared"])
    tier = str(lead["tier"]) if lead and lead["tier"] is not None else "?"
    abstracts = lead["abstracts"] if lead else "not checked"
    run = f", longest shared run {lead['abs_run']} words" if lead and lead["abs_run"] is not None else ""
    lines = [
        f"Shared sampled 5-grams: {shared:,} (1 in {sample_rate} of all 5-grams are sampled, so roughly "
        f"{shared * sample_rate:,} shared in all)",
        f"Paper A (left): {pa:.1f}% of its {int(sa):,} sampled 5-grams found in paper B",
        f"Paper B (right): {pb:.1f}% of its {int(sb):,} sampled 5-grams found in paper A",
        f"Band (the larger share): {html.escape(rep['band'])}",
        f"Lead tier: {html.escape(tier)} ({html.escape(TIERS.get(tier, ''))})",
        f"Abstracts: {html.escape(abstracts)}{html.escape(run)}",
        "<em>The index's 5-grams are lemmatized and skip windows that start or end on a stopword, so its "
        "shares are an estimate from a different text model than the full-text figures above.</em>",
    ]
    return "<br>".join(local + ["<strong>Overlap evidence</strong>"] + fulltext_lines(res)
                       + ["<strong>General Index</strong> (sampled 5-grams)"] + lines)


INDEX_CSS = """
.ranking { width: 100%; border-collapse: collapse; font-size: 0.92rem; margin-top: 1.2rem; }
.ranking th, .ranking td { border-bottom: 1px solid var(--border); padding: 0.45rem 0.5rem; vertical-align: top;
  text-align: left; }
.ranking th { font-family: var(--font-body); color: var(--ink-muted); font-weight: 600; position: sticky; top: 0;
  background: var(--bg); }
.ranking td.num { font-family: var(--font-mono); white-space: nowrap; }
.ranking td.score { font-family: var(--font-mono); font-weight: 700; color: var(--accent-strong); }
.ranking .paper { margin-bottom: 0.35rem; }
.ranking .paper .meta { color: var(--ink-muted); font-size: 0.85rem; }
.ranking tr.reviewed td { color: var(--ink-muted); }
.ranking .tag { display: inline-block; font-size: 0.8rem; padding: 0 0.4em; border-radius: 3px;
  background: var(--surface-2); margin: 0 0.2em 0.2em 0; }
.table-wrap { overflow-x: auto; }
"""


def _lib_paper(lib, pid):
    if not lib or not pid:
        return None
    conn = sqlite3.connect(f"file:{lib}?mode=ro", uri=True)
    row = conn.execute("select title, year from papers where id = ?", (pid,)).fetchone()
    conn.close()
    return {"title": row[0], "year": row[1]} if row else None


def write_index(db, out_dir, pages=None):
    """OUT_DIR/index.html: every page ranked by the averaged overlap score (ft_pct_a + ft_pct_b) / 2,
    highest first, with the agent's and the human's decisions from `results`. Pairs reviewed as
    "same paper" have no page; they are listed, marked, at the end. A fragment like the pages (no
    html/head/body skeleton, for publishing as a Claude Artifact), linking by relative path so it
    works opened from disk."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from write_dupe_reports_html import STYLE  # the pages' own stylesheet, so the index matches them
    out_dir = Path(out_dir)
    have_results = db.execute("select 1 from sqlite_master where name = 'results'").fetchone() is not None
    flags = {}
    if db.execute("select 1 from sqlite_master where name = 'review_flags'").fetchone():
        for r in db.execute("select pair_lo, pair_hi, flag from review_flags order by flag"):
            flags.setdefault((r[0], r[1]), []).append(r[2])
    rows = db.execute("""select f.*, r.verdict as human_verdict from fulltext f
                         left join reviews r on r.pair_lo = f.pair_lo and r.pair_hi = f.pair_hi
                         where f.ft_verdict = 'real overlap' and f.byline = 'no byline overlap'""").fetchall()
    entries = []
    for f in rows:
        key = (f["pair_lo"], f["pair_hi"])
        page = (pages or {}).get(key) or f["page"]
        if page and not (out_dir / page).exists():
            page = None
        res = db.execute("select * from results where pair_lo = ? and pair_hi = ?", key).fetchone() \
            if have_results else None
        pa, pb = _lib_paper(f["library"], f["paper_a"]), _lib_paper(f["library"], f["paper_b"])
        score = ((f["ft_pct_a"] or 0) + (f["ft_pct_b"] or 0)) / 2
        demote, pnote = priority_status(f)
        entries.append(dict(f=f, res=res, page=page, pa=pa, pb=pb, score=score, demote=demote, pnote=pnote,
                            reviewed=f["human_verdict"], flags=flags.get(key, [])))
    # gi_preprints.pair_priority() first (demote < 0 promoted, > 0 demoted), then the score. Pairs whose later
    # (copying) paper is from a major publisher rank first (-2), order unknown with one side major next (-1);
    # those whose later paper is a preprint/repository upload rank after the rest (2), order unknown and a side
    # one just before them (1): such uploads are most often the authors' own or an aggregator's copy, rarely a
    # publishable finding. The index has no AI-call grouping to keep (it is by score alone), so the priority
    # leads here. Tagged, not hidden; verdicts untouched.
    shown = sorted([e for e in entries if e["page"]],
                   key=lambda e: (e["demote"], -e["score"], -(e["f"]["ft_matched_words"] or 0)))
    no_page = sorted([e for e in entries if not e["page"]], key=lambda e: (e["demote"], -e["score"]))

    def paper_cell(paper, doi, year_fallback):
        title = html.escape((paper["title"] if paper else "") or "(untitled)")
        year = (paper["year"] if paper and paper["year"] else year_fallback) or ""
        link = f'<a href="https://doi.org/{html.escape(doi, quote=True)}">{html.escape(doi)}</a>' \
            if doi and not doi.startswith("gi:") else html.escape(doi or "")
        return f'<div class="paper">{title}<div class="meta">{html.escape(str(year))} &middot; {link}</div></div>'

    def decision_cell(res, reviewed, flags=(), pnote=None):
        tags = [html.escape(pnote)] if pnote else []
        if res is not None:
            if res["type"]:
                tags.append(f'type: {html.escape(res["type"])} ({html.escape(res["type_source"] or "")})')
            if res["severity"]:
                tags.append(f'severity: {html.escape(res["severity"])} ({html.escape(res["severity_source"] or "")})')
            if res["ai_decision"]:
                local = " (local model)" if "local:" in (res["ai_prompt_version"] or "") else ""
                tags.append(f'AI{local}: {html.escape(res["ai_decision"])}')
            if res["human_decision"]:
                tags.append(f'human: {html.escape(res["human_decision"])}')
        if reviewed:
            tags.append(f'reviewed: {html.escape(reviewed)}')
        tags += [f'flag: {html.escape(fl)}' for fl in flags]
        return "".join(f'<span class="tag">{t}</span>' for t in tags)

    def row_html(rank, e):
        f = e["f"]
        page = f'<a href="{html.escape(e["page"], quote=True)}">GI-{f["page_number"]:02d}</a>' if e["page"] \
            else f'GI-{f["page_number"]:02d} (no page)' if f["page_number"] else "(no page)"
        cls = ' class="reviewed"' if e["reviewed"] else ""
        return (f'<tr{cls}><td class="num">{rank}</td><td class="score">{e["score"]:.1f}%</td>'
                f'<td class="num">{f["ft_pct_a"]:.0f}% / {f["ft_pct_b"]:.0f}%</td>'
                f'<td class="num">{f["ft_matched_words"]:,}</td><td class="num">{f["ft_longest"]:,}</td>'
                f'<td>{paper_cell(e["pa"], f["doi_a"], f["year_a"])}{paper_cell(e["pb"], f["doi_b"], f["year_b"])}</td>'
                f'<td>{decision_cell(e["res"], e["reviewed"], e["flags"], e["pnote"])}</td><td>{page}</td></tr>')

    head = ("<tr><th>#</th><th>Score</th><th>A / B</th><th>Matched words</th><th>Longest run</th>"
            "<th>Papers (A, then B)</th><th>Decisions</th><th>Page</th></tr>")
    body = "\n".join(row_html(i, e) for i, e in enumerate(shown, 1))
    extra = ""
    if no_page:
        extra = ("<h2>Without a page</h2><p>Reviewed as the same paper (or no page written), so not ranked above.</p>"
                 f'<div class="table-wrap"><table class="ranking">{head}'
                 + "\n".join(row_html("&ndash;", e) for e in no_page) + "</table></div>")
    n_later = sum(e["demote"] == 2 for e in shown)
    n_possibly = sum(e["demote"] == 1 for e in shown)
    n_major = sum(e["demote"] == -2 for e in shown)
    n_major_possibly = sum(e["demote"] == -1 for e in shown)
    doc = f"""<title>General Index full-text leads</title>
<style>{STYLE}{INDEX_CSS}</style>
<div class="page">
  <div class="lede">
    <p class="eyebrow">Text-overlap comparisons</p>
    <h1>General Index full-text leads, ranked</h1>
    <p class="source-note">Ranked by the averaged overlap score: the mean of the two full-text percentages
    (each paper's share of its words inside a run shared with the other; 10-word exact shingles, x-drop 3).
    {len(shown)} pages; decisions come from the agent review (AI) and your reviews, yours taking precedence.
    Pairs whose later (copying) paper is from a major publisher ({n_major}) are ranked first and tagged, then
    those where it is unknown which paper came first and one side is ({n_major_possibly}): a copy that got
    into a major journal is the most consequential finding. Known paper-mill venues inside those publishers
    (IOP's conference series, Materials Today: Proceedings, Springer's AISC/LNEE/LNNS, ...) are tagged but
    rank normally.
    Pairs whose later (copying) paper is a preprint or repository upload ({n_later}) are ranked last and
    tagged, and those where it is unknown which paper came first and one side is a preprint ({n_possibly})
    just before them: such uploads are most often the authors' own or an aggregator's copy, rarely a
    publishable finding. A preprint that was copied from ranks normally.</p>
  </div>
  <div class="table-wrap"><table class="ranking">{head}
{body}
  </table></div>
  {extra}
</div>
"""
    (out_dir / "index.html").write_text(doc, encoding="utf-8")
    print(f"index.html: {len(shown)} ranked pages, {len(no_page)} without a page")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default=str(gi_db.DATA / "fulltext_pages_s0123"))
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--sample-rate", type=int, default=4)
    ap.add_argument("--no-db-writes", action="store_true",
                    help="open gi.sqlite3 read-only; don't update fulltext.page / page_number")
    ap.add_argument("--pages", type=lambda s: {int(x) for x in s.split(",")},
                    help="only (re)write these page numbers, e.g. 10,40,44 (index.html is still rebuilt)")
    ap.add_argument("--index-only", action="store_true", help="only rewrite OUT_DIR/index.html")
    ap.add_argument("--text-pages", action="store_true",
                    help="write the old text-diff pages (write_dupe_reports_html.py) instead of the default "
                         "screenshot pages (write_dupe_reports_screenshots.py: PDF crops with the overlap "
                         "highlighted, images in OUT_DIR/img/)")
    args = ap.parse_args()
    if args.no_db_writes or args.index_only:
        db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
        db.row_factory = sqlite3.Row
    else:
        db = gi_db.connect(args.db)
    if args.index_only:
        write_index(db, args.out_dir)
        return
    same_paper = gi_db.reviewed_same_paper(db)
    rows = db.execute("""select * from fulltext where ft_verdict = 'real overlap' and byline = 'no byline overlap'
                         order by coalesce(page_number, 1e9), ft_matched_words desc""").fetchall()
    next_n = (db.execute("select max(page_number) from fulltext").fetchone()[0] or 0) + 1
    os.makedirs(args.out_dir, exist_ok=True)
    index_rows, written, pages_written = [], 0, {}
    for res in rows:
        key = (res["pair_lo"], res["pair_hi"])
        if key in same_paper:
            continue
        n = res["page_number"]
        if args.pages is not None and n not in args.pages:
            continue
        if n is None and args.no_db_writes:
            print(f"{res['doi_a']} | {res['doi_b']}: no page_number and --no-db-writes, skipped")
            continue
        if n is None:
            n, next_n = next_n, next_n + 1
            db.execute("update fulltext set page_number = ? where pair_lo = ? and pair_hi = ?", (n, *key))
        rep = db.execute("""select l.*, p.shared, p.pct_a, p.pct_b, p.sampled_a, p.sampled_b from leads l
                            join pairs p using (pair_id) where l.pair_lo = ? and l.pair_hi = ?
                            and l.reason != 'too short' order by l.pair_id limit 1""", key).fetchone()
        lead = db.execute("select * from leads where pair_lo = ? and pair_hi = ? and primary_pair = 1",
                          key).fetchone()
        a, b, lib = res["paper_a"], res["paper_b"], res["library"]
        if not (a and b and lib):
            print(f"GI-{n:02d}: missing library papers, skipped")
            continue
        for old in Path(args.out_dir).glob(f"GI-{n:02d}-*.html"):
            old.unlink()
        before = set(os.listdir(args.out_dir))
        view = oriented(res, page_order(lib, a, b)[0])  # paper A = the page's left card
        note = evidence_note(rep, lead, args.sample_rate, view["doi_a"], view,
                             local_pdf(lib, view["paper_a"], args.out_dir),
                             local_pdf(lib, view["paper_b"], args.out_dir))
        script = "write_dupe_reports_html.py" if args.text_pages else "write_dupe_reports_screenshots.py"
        p = subprocess.run([PY, str(ROOT / script), "--library-db", lib,
                            "--paper-ids", f"{a},{b}", "--out-dir", args.out_dir, "--neutral",
                            "--shingle-size", "10", "--x-drop", "3", "--case-number", f"GI-{n:02d}",
                            # every pair here passed the full-text check already: a few long runs is a page
                            "--min-shingle-matches", "0",
                            "--source-note", note, "--found-by", ""], capture_output=True, text=True)
        new = sorted(f for f in set(os.listdir(args.out_dir)) - before if f.endswith(".html"))
        if p.returncode != 0 or not new:
            print(f"GI-{n:02d}: {script.removesuffix('.py')} failed: {(p.stderr or p.stdout)[-300:]}", file=sys.stderr)
            continue
        page = Path(args.out_dir) / new[0]
        raw = page.read_bytes()
        if b"\x00" in raw:  # NULs from a PDF's extracted text: invalid in HTML, and they make grep see a binary file
            page.write_bytes(raw.replace(b"\x00", b""))
        written += 1
        pages_written[key] = new[0]
        if not args.no_db_writes:
            db.execute("update fulltext set page = ? where pair_lo = ? and pair_hi = ?", (new[0], *key))
        flip = bool(rep) and rep["doi_a"].lower() != res["doi_a"].lower()
        rep = rep or {"shared": "", "pct_a": "", "pct_b": "", "band": ""}
        conn = sqlite3.connect(lib)
        title = lambda pid: (conn.execute("select title from papers where id = ?", (pid,)).fetchone() or [""])[0] or ""
        index_rows.append({"n": n, "page": new[0], "shared_5grams_sampled": rep["shared"],
                           "gi_pct_a": rep["pct_b"] if flip else rep["pct_a"],
                           "gi_pct_b": rep["pct_a"] if flip else rep["pct_b"],
                           "band": rep["band"], "tier": lead["tier"] if lead else "",
                           "fulltext_matched_words": res["ft_matched_words"],
                           "fulltext_pct_a": res["ft_pct_a"], "fulltext_pct_b": res["ft_pct_b"],
                           "doi_a": res["doi_a"], "year_a": res["year_a"], "title_a": title(a)[:150],
                           "doi_b": res["doi_b"], "year_b": res["year_b"], "title_b": title(b)[:150]})
    if not args.no_db_writes:
        db.commit()
    index_rows.sort(key=lambda r: r["n"])
    if args.pages is not None:  # a partial run: don't overwrite _index.tsv with just these rows
        print(f"{written} pages written to {args.out_dir}")
        write_index(db, args.out_dir, pages_written)
        return
    with open(Path(args.out_dir) / "_index.tsv", "w") as f:
        w = csv.DictWriter(f, fieldnames=list(index_rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(index_rows)
    print(f"{written} pages written to {args.out_dir}")
    write_index(db, args.out_dir, pages_written)


if __name__ == "__main__":
    main()

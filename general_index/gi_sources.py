"""The other sources of a case's later article, on its comparison page (the email attachment and the site page).

A General Index pair is one earlier article against one later article. When the later article also takes text
from other works (found and checked by the trace-other-sources skill, recorded in
data/dupe_publisher/GI-NN/sources.json), add_sources() puts them all on the one comparison page:
  - an overview at the top: the share of the later article found in each work and in all of them together,
    and whether the later article cites each one;
  - the paired article's passages (the page gi_pubpeer.publisher_page() wrote), labelled "Article A";
  - then each source's passages, "Article B", "C", ..., from write_dupe_reports_screenshots.py at the GI
    settings (10-word shingles, x-drop 3), run on a scratch library holding the later article and the source.

Only sources whose "kind" includes "text" and whose "fulltext" file is in the case folder are shown; the
others (references-only, abstract-only, text not saved yet) are listed under the table and reach the reports
only as ticked findings. A source's text comes from source-N.pdf beside its fulltext file when there is one
(extract_papers.py's extraction, so the passages can be shown as printed), else from the text file itself.

sources.json fields used here: "later_title" (optional, top level: the later article's title as printed, for
when the catalogue's is garbled; it replaces the catalogue's everywhere on the page), and per source: title, year, doi, kind, fulltext, cited; optional: journal,
authors (list, as printed), url (when there's no DOI), shared (what is shared, one sentence, e.g. "Its
Section 2, as the later Section III.A; its Table 1, as the later Table I"). gi_report_case.py calls this;
nothing is sent or pushed.

Usage: gi_sources.py GI_NUMBER   (prints the measured shares; writes nothing)
"""
import argparse
import html
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import gi_db
import gi_share_pages
import gi_write_pages

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SOURCE_ID = 1_000_000   # source k's paper id in the scratch library (the later article keeps its own)
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
TD = 'style="padding:0.35rem 0.5rem;vertical-align:top"'
ROW = 'style="border-bottom:1px solid rgba(127,127,127,0.3)"'


def _ctp():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import compare_two_papers
    return compare_two_papers


def kinds(s):
    k = s.get("kind") or []
    return [k] if isinstance(k, str) else list(k)


def load(case_dir):
    """(text sources, other sources) from case_dir/sources.json, each in file order with its "n" (1-based
    position in the file). A text source is one whose kind includes "text" and whose fulltext file exists."""
    path = Path(case_dir) / "sources.json"
    if not path.exists():
        return [], []
    text, other = [], []
    for n, s in enumerate(json.loads(path.read_text(encoding="utf-8")).get("sources", []), 1):
        s = {**s, "n": n}
        full = s.get("fulltext") and Path(case_dir) / s["fulltext"]
        if "text" in kinds(s) and full and full.is_file():
            text.append(s)
        else:
            other.append(s)
    return text, other


def source_paragraphs(case_dir, s):
    """(paragraphs, pdf path or None) for one source: from the PDF beside its fulltext file (same name, .pdf)
    with extract_papers.py's extraction, else the text file split at blank lines."""
    full = Path(case_dir) / s["fulltext"]
    pdf = full.with_suffix(".pdf")
    if pdf.is_file():
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import extract_papers
        paras = extract_papers.extract_paper(pdf)[0]
        if sum(len(p.split()) for p in paras) >= 100:   # no text layer: fall back to the saved text
            return paras, pdf
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", full.read_text(encoding="utf-8", errors="replace"))]
    return [p for p in paras if len(p.split()) >= 3], (pdf if pdf.is_file() else None)


def scratch_library(path, lib, later_id, case_dir, sources):
    """A library at path holding the later article (copied from lib, its PDF path made absolute) and each
    source as paper SOURCE_ID + k. Returns {source n: paper id}."""
    lib = Path(lib)
    out = sqlite3.connect(path)
    out.executescript("""
        create table papers (id integer primary key, doi text, title text not null, year integer,
                             file_path text unique not null, extracted_at text);
        create table authors (id integer primary key, name text unique not null);
        create table paper_authors (paper_id integer not null, author_id integer not null,
                                    author_order integer not null, primary key (paper_id, author_id));
        create table paragraphs (id integer primary key, paper_id integer not null, para_index integer not null,
                                 text text not null);""")
    src = sqlite3.connect(f"file:{lib}?mode=ro", uri=True)
    try:
        doi, title, year, fp = src.execute("select doi, title, year, file_path from papers where id = ?",
                                           (later_id,)).fetchone()
        fp = str(Path(fp) if Path(fp).is_absolute() else lib.parent / fp)
        out.execute("insert into papers (id, doi, title, year, file_path) values (?, ?, ?, ?, ?)",
                    (later_id, doi, title, year, fp))
        out.executemany("insert into paragraphs (paper_id, para_index, text) values (?, ?, ?)",
                        src.execute("select paper_id, para_index, text from paragraphs where paper_id = ?"
                                    " order by para_index", (later_id,)))
        authors = src.execute("select a.name, pa.author_order from authors a join paper_authors pa"
                              " on pa.author_id = a.id where pa.paper_id = ? order by pa.author_order",
                              (later_id,)).fetchall()
    finally:
        src.close()

    def add_authors(pid, names):
        for order, name in enumerate(names):
            out.execute("insert or ignore into authors (name) values (?)", (name,))
            aid = out.execute("select id from authors where name = ?", (name,)).fetchone()[0]
            out.execute("insert or ignore into paper_authors values (?, ?, ?)", (pid, aid, order))

    add_authors(later_id, [a for a, _ in authors])
    ids = {}
    for k, s in enumerate(sources, 1):
        pid = SOURCE_ID + k
        paras, pdf = source_paragraphs(case_dir, s)
        out.execute("insert into papers (id, doi, title, year, file_path) values (?, ?, ?, ?, ?)",
                    (pid, s.get("doi") or None, s.get("title") or f"Source {s['n']}", s.get("year"),
                     str(pdf or (Path(case_dir) / s["fulltext"]).resolve())))
        out.executemany("insert into paragraphs (paper_id, para_index, text) values (?, ?, ?)",
                        [(pid, i, p) for i, p in enumerate(paras)])
        add_authors(pid, s.get("authors") or [])
        ids[s["n"]] = pid
    out.commit()
    out.close()
    return ids


def later_and_earlier(f):
    """(later id, earlier id) of the fulltext row's pair, or None when which came first isn't known."""
    a, b = f["paper_a"], f["paper_b"]
    earlier = gi_write_pages.earlier_paper(f["library"], a, b)
    if earlier is None:
        return None
    return (b if earlier == a else a), earlier


def measure(scratch, later_id, ids, f, earlier_id):
    """Covered words of the later article, per work: {"paired": {...}, n: {...}} each with covered (a set of
    word positions), runs and longest; plus "words" (the later article's length)."""
    ctp = _ctp()

    def cover(later, other):
        runs = ctp.find_shingle_matches(later, other, shingle_size=10, x_drop=3)
        return {"covered": {i for r in runs for i in range(r.start_a, r.end_a)}, "runs": len(runs),
                "longest": max((r.length for r in runs), default=0)}

    case_lib = sqlite3.connect(f"file:{f['library']}?mode=ro", uri=True)
    try:
        later = ctp.load_paper_words(case_lib, later_id)
        out = {"words": len(later), "paired": cover(later, ctp.load_paper_words(case_lib, earlier_id))}
    finally:
        case_lib.close()
    lib = sqlite3.connect(f"file:{scratch}?mode=ro", uri=True)
    try:
        for n, pid in ids.items():
            out[n] = cover(later, ctp.load_paper_words(lib, pid))
    finally:
        lib.close()
    return out


def render_source(scratch, later_id, pid, case):
    """write_dupe_reports_screenshots.py's page for the later article against one source, images inlined."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        p = subprocess.run([PY, str(ROOT / "write_dupe_reports_screenshots.py"), "--library-db", str(scratch),
                            "--paper-ids", f"{later_id},{pid}", "--out-dir", str(d), "--neutral",
                            "--shingle-size", "10", "--x-drop", "3", "--case-number", case,
                            "--min-shingle-matches", "1", "--found-by", "", "--embed-images", "--no-title-shots"],
                           capture_output=True, text=True, cwd=ROOT)
        pages = sorted(d.glob("*.html"))
        if p.returncode != 0 or not pages:
            raise RuntimeError(f"{case}: page writer failed: {(p.stderr or p.stdout)[-300:]}")
        page, _ = gi_share_pages.shareable(pages[0].read_text(encoding="utf-8").replace("\x00", ""), d)
        return page


def _pct(words, total):
    v = 100 * words / total if total else 0
    return f"{v:.1f}%" if v < 10 else f"{v:.0f}%"


def _work(title, year, journal, authors, doi, url):
    """One cell: authors, title, journal (year), DOI or link."""
    who = "; ".join(authors) if isinstance(authors, list) else (authors or "")
    parts = [html.escape(who) + ", " if who else "", html.escape(title or "")]
    if journal:
        parts.append(f", <i>{html.escape(journal)}</i>")
    if year:
        parts.append(f" ({year})")
    if doi and doi.startswith("10."):
        parts.append(f', <a href="https://doi.org/{html.escape(doi)}">doi:{html.escape(doi)}</a>')
    elif url:
        parts.append(f', <a href="{html.escape(url, quote=True)}">{html.escape(re.sub(r"^https?://([^/]+).*", r"\1", url))}</a>')
    return "".join(parts)


def _yes_no(v):
    return "yes" if v else ("no" if v is not None else "?")


def overview_html(fd, text_sources, other, m, later_title=None, cards=""):
    later_title = later_title or fd["second"]["title"]
    total = m["words"]
    keys = ["paired", *[s["n"] for s in text_sources]]
    union = set().union(*(m[k]["covered"] for k in keys))
    counted = sum(len(m[k]["covered"]) for k in keys)
    overlap = counted - len(union)
    n = len(keys)
    first = fd["first"]
    rows = []
    for letter, k in zip(LETTERS, keys):
        if k == "paired":
            work = _work(first["title"], first["year"], first.get("journal"), first.get("authors"), first["doi"], None)
            cited, shared = fd.get("later_cites_earlier"), None
        else:
            s = next(s for s in text_sources if s["n"] == k)
            work = _work(s.get("title"), s.get("year"), s.get("journal"), s.get("authors"), s.get("doi"),
                         s.get("url") or s.get("fulltext_url"))
            cited, shared = s.get("cited"), s.get("shared")
        c = m[k]
        what = (html.escape(shared) + ". " if shared else "") + \
            f"{len(c['covered']):,} words in {c['runs']} passage{'s' if c['runs'] != 1 else ''}, the longest {c['longest']}"
        rows.append(f'<tr {ROW}><td {TD}><a href="#article-{letter.lower()}">{letter}.</a> {work}</td>'
                    f'<td {TD}>{what}</td><td {TD}>{_yes_no(cited)}</td>'
                    f'<td {TD}><strong>{_pct(len(c["covered"]), total)}</strong></td></tr>')
    not_shown = ""
    if other:
        items = "".join(f"<li>{html.escape(s.get('title') or s.get('doi') or 'untitled')}"
                        f"{' (' + str(s['year']) + ')' if s.get('year') else ''}: "
                        f"{html.escape(', '.join(kinds(s)) or 'no kind given')}"
                        f"{'' if 'text' not in kinds(s) else ', text not saved, so not measured'}</li>"
                        for s in other)
        not_shown = f'<p style="font-size:0.88rem">Also traced, not measured here:</p><ul style="font-size:0.88rem">{items}</ul>'
    links = ", ".join(f'<a href="#article-{l.lower()}">{l}</a>' for l in LETTERS[:n])
    overlap_txt = ("which don&rsquo;t overlap with each other" if not overlap else
                   f"{overlap:,} of those words are in more than one of them, counted once")
    return f'''<div class="lede" id="overview" style="margin-bottom:2.5rem;padding-bottom:1.5rem;border-bottom:2px solid rgba(127,127,127,0.3)">
    <p class="eyebrow">Text-overlap comparison &middot; several sources</p>
    <h1>{html.escape(later_title)} ({fd["second"]["year"]}): text shared with {n} earlier works</h1>
    <p>About <strong>{_pct(len(union), total)}</strong> of the later article&rsquo;s text ({len(union):,} of its
    {total:,} words) is also found in {n} earlier works, {overlap_txt}.</p>
    {cards}
    <div style="overflow-x:auto">
    <table style="border-collapse:collapse;width:100%;font-size:0.92rem;margin:0.6rem 0">
      <thead><tr style="text-align:left;border-bottom:1px solid currentColor">
        <th {TD}>Earlier work</th><th {TD}>What is shared</th><th {TD}>Cited by the later article</th>
        <th {TD}>Share of the later article</th></tr></thead>
      <tbody>
      {"".join(rows)}
      </tbody>
    </table>
    </div>
    <p style="font-size:0.88rem">A passage is a run of at least ten consecutive words in common, allowing isolated
    single-word differences. The figures are minimums: reworded text and table cells aren&rsquo;t counted. A work
    available only as a web page or text has its passages shown as text rather than as images. The passages from
    each work follow: {links}.</p>
    {not_shown}
  </div>
'''


CARD = re.compile(r'<div class="paper-card">.*?</div>', re.S)
CARDS_STYLE = """<style>
.sources-map { align-items: center; }
.sources-map .source-stack { display: flex; flex-direction: column; gap: 0.7rem; }
.sources-map .paper-card .share { font-size: 0.85rem; margin: 0.45rem 0 0; }
.sources-map .paper-card .share a { color: var(--accent-strong); }
@media (max-width: 640px) { .sources-map .connector { display: flex; transform: rotate(90deg); } }
</style>"""


def _card(section, role):
    """The paper card with this role (its .role text, e.g. "EARLIER / SOURCE") in a section's top box."""
    return next((c for c in CARD.findall(section) if f">{role}</p>" in c), None)


def cards_html(sections, later_card, union_txt):
    """The overview's top box: every earlier work's card (as its own section shows it: role "EARLIER / SOURCE
    A", its share and a link to its passages) stacked on the left, the later article's card on the right, as in
    the single-pair box. sections: [(letter, section html, share text)]."""
    left = []
    for letter, sec, share in sections:
        c = _card(sec, f"EARLIER / SOURCE {letter}")
        if not c:
            continue
        left.append(c[:-len("</div>")] + f'<p class="share"><strong>{share}</strong> of the later article &middot; '
                    f'<a href="#article-{letter.lower()}">its passages</a></p></div>')
    if not left or not later_card:
        return ""
    right = later_card[:-len("</div>")] + f'<p class="share"><strong>{union_txt}</strong> of its text is in these ' \
        f'{len(left)} earlier works</p></div>'
    return (f'{CARDS_STYLE}<div class="comparison sources-map"><div class="source-stack">{"".join(left)}</div>'
            f'<div class="connector">&rarr;</div>{right}</div>')


def _tag(section, letter, year=None):
    section = section.replace('<div class="lede">', f'<div class="lede" id="article-{letter.lower()}">', 1)
    section = section.replace('<p class="eyebrow">Text-overlap comparison</p>',
                              f'<p class="eyebrow">Article {letter} &middot; text-overlap comparison</p>', 1)
    section = section.replace("<h3>Shingle match ", f"<h3>Article {letter}: shingle match ")
    section = section.replace(">EARLIER / SOURCE</p>", f">EARLIER / SOURCE {letter}</p>", 1)
    return section.replace(">EARLIER</span>", f">ARTICLE {letter}{f' ({year})' if year else ''}</span>")


def add_sources(page, f, fd, case_dir):
    """The comparison page (gi_pubpeer.publisher_page()'s) with the case's other text sources added, and a
    summary for cases.json ({"sources", "union_pct", "union_words"}); (page, None) unchanged when the case has
    no text source to show, or which article came first isn't known."""
    text_sources, other = load(case_dir)
    order = text_sources and later_and_earlier(f)
    if not order:
        return page, None
    later_id, earlier_id = order
    case = Path(case_dir).name
    later_title = json.loads((Path(case_dir) / "sources.json").read_text(encoding="utf-8")).get("later_title")
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d) / "library.sqlite3"
        ids = scratch_library(scratch, f["library"], later_id, case_dir, text_sources)
        m = measure(scratch, later_id, ids, f, earlier_id)
        sections = []
        for letter, s in zip(LETTERS[1:], text_sources):
            src = render_source(scratch, later_id, ids[s["n"]], f"{case}-s{s['n']}")
            i, j = src.find('<div class="lede">'), src.find("<footer")
            sec = re.sub(r'\s*<p class="dek">.*?</p>', "", src[i:j], count=1, flags=re.S)
            c = m[s["n"]]
            own = f'<span>shared text <strong>{_pct(len(c["covered"]), m["words"])}</strong> of the later article ' \
                  f'({fd["second"]["year"]})</span> <span>matched words <strong>{len(c["covered"]):,}</strong></span> '
            sec = re.sub(r"<span>candidate passages <strong>[^<]*</strong></span>", "", sec, count=1)
            sec = sec.replace('<div class="summary-bar">', '<div class="summary-bar">' + own, 1)
            sections.append((letter, _tag(sec, letter, s.get("year")), _pct(len(c["covered"]), m["words"])))
    i = page.find('<div class="lede">')
    paired = _tag(page[i:], "A", fd["first"]["year"])
    union = set().union(*(m[k]["covered"] for k in ["paired", *ids]))
    cards = cards_html([("A", paired, _pct(len(m["paired"]["covered"]), m["words"])), *sections],
                       _card(paired, "LATER / FLAGGED"), _pct(len(union), m["words"]))
    page = page[:i] + overview_html(fd, text_sources, other, m, later_title, cards) + paired
    k = page.rfind("<footer")
    k = k if k >= 0 else page.rfind("</div>")
    page = page[:k] + "".join('<hr style="margin:2.5rem 0;border:0;border-top:2px solid currentColor;opacity:0.3">\n'
                              + sec for _, sec, _ in sections) + page[k:]
    if later_title:
        with sqlite3.connect(f"file:{f['library']}?mode=ro", uri=True) as lib:
            catalogue = (lib.execute("select title from papers where id = ?", (later_id,)).fetchone()[0] or "").strip()
        if catalogue:
            page = page.replace(html.escape(catalogue, quote=False), html.escape(later_title, quote=False))
    page = re.sub(r"<title>.*?</title>", f"<title>{case}: {1 + len(text_sources)} sources</title>", page, count=1)
    return page, {"sources": 1 + len(text_sources), "union_words": len(union),
                  "union_pct": round(100 * len(union) / m["words"], 1) if m["words"] else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gi", type=int)
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
    db.row_factory = sqlite3.Row
    f = db.execute("select * from fulltext where page_number = ?", (args.gi,)).fetchone()
    if not f:
        sys.exit(f"GI-{args.gi}: no such page")
    case_dir = gi_db.DATA / "dupe_publisher" / f"GI-{args.gi:02d}"
    text_sources, other = load(case_dir)
    order = later_and_earlier(f)
    if not order:
        sys.exit("which article came first isn't known: sources aren't added")
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d) / "library.sqlite3"
        ids = scratch_library(scratch, f["library"], order[0], case_dir, text_sources)
        m = measure(scratch, order[0], ids, f, order[1])
    for k in ["paired", *ids]:
        c = m[k]
        print(f"{'paired' if k == 'paired' else 'source ' + str(k):>9}  {_pct(len(c['covered']), m['words']):>6}"
              f"  {len(c['covered']):>5} words  {c['runs']:>3} runs  longest {c['longest']}")
    union = set().union(*(m[k]["covered"] for k in ["paired", *ids]))
    print(f"{'union':>9}  {_pct(len(union), m['words']):>6}  {len(union):>5} of {m['words']} words")
    for s in other:
        print(f"not measured: source {s['n']} ({', '.join(kinds(s))}): {s.get('title')}")


if __name__ == "__main__":
    main()

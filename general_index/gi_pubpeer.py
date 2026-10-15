"""A shareable page per result pair, for posting a finding on PubPeer.

For a full-text-checked pair (gi.sqlite3 fulltext, one GI-NN result page), writes OUT_DIR/GI-NN-<slug>.html:
one self-contained file (a few hundred KB) with
  - a PubPeer box at the top: a link to the later (copying) article on PubPeer (pubpeer.com/search?q=DOI,
    which opens the article's page when PubPeer has one), a link to the earlier one, and the findings as
    Markdown in a text box with a Copy button, ready to paste into a PubPeer comment;
  - the title and byline of both papers as printed (images from the PDFs);
  - the shared passages as text diffs (write_dupe_reports_screenshots.py --text-exhibits), not crops.

The Markdown states only what was measured: how much of the later article's text is in the earlier one
(and the reverse), whether the author lists share a name, whether the later article's references appear
to cite the earlier one, and the TOP_PASSAGES (30) longest shared passages with their length and an excerpt. Which
article is later comes from the publication years as on the result page (gi_write_pages.earlier_paper());
when that isn't known (same year), the text says so and makes no claim about direction.

gi_review_cli.py writes this page when you save a problem verdict, and on t at its prompt.

It also has what gi_report_case.py (everything for reporting a case) builds on: findings() and publisher_page(), the
comparison page for the journal and the project site. The emails are gi_contact.py.

Usage: gi_pubpeer.py GI_NUMBER [GI_NUMBER ...] [--problems] [--out-dir data/pubpeer_pages] [--db data/gi.sqlite3]
       --problems: every pair you've reviewed as a problem
"""
import argparse
import base64
import html
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import gi_db
import gi_share_pages
import gi_write_pages

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
OUT_DIR = gi_db.DATA / "pubpeer_pages"
TOP_PASSAGES = 30
WHOLE_DOCUMENT_PCT = 90  # above this share of either article, the comparison page is one interleaved diff
EXCERPT_HEAD, EXCERPT_TAIL = 30, 10   # words quoted from the start and end of a long passage


def pubpeer_url(doi):
    """PubPeer's search for a DOI, which goes to the article's page when PubPeer knows it. None for an
    index-only id (gi:...) that has no DOI."""
    if not doi or not doi.startswith("10."):
        return None
    return "https://pubpeer.com/search?q=" + quote(doi, safe="/")


def doi_url(doi):
    return f"https://doi.org/{doi}" if doi and doi.startswith("10.") else None


def excerpt(text, head=EXCERPT_HEAD, tail=EXCERPT_TAIL):
    words = text.split()
    if len(words) <= head + tail + 5:
        return " ".join(words)
    return " ".join(words[:head]) + " … " + " ".join(words[-tail:])


def clean_title(title):
    """Whitespace collapsed, and a title the metadata repeats after a quotation mark cut back to its first
    copy: 'STUDY OF SKIN STAPLES ... TRIAL "Study of Skin Staples ... Control' -> 'STUDY OF SKIN STAPLES ...
    TRIAL'. Italic markup from the metadata (<i>, <em>) becomes Markdown italics, other tags are dropped."""
    title = re.sub(r"\s*<(i|em)>\s*(.*?)\s*</\1>\s*", r" *\2* ", title or "", flags=re.IGNORECASE)
    title = html.unescape(re.sub(r"<[^>]+>", "", title))
    title = " ".join(title.split())
    for q in ('"', "\u201c"):
        head, sep, rest = title.partition(" " + q)
        if sep and len(head) >= 20 and rest.lower().startswith(head[:20].lower()):
            return head
    return title


def findings(db, f):
    """What the Markdown reports, for one fulltext row: the two papers as earlier/later (or first/second
    when the order is unknown), the coverage figures, the byline and citation checks, the longest runs."""
    sys.path.insert(0, str(ROOT))
    import compare_two_papers as ctp
    from gi_agent_review import citation_check, paper_meta

    lib_path, a, b = f["library"], f["paper_a"], f["paper_b"]
    lib = sqlite3.connect(f"file:{lib_path}?mode=ro", uri=True)
    try:
        earlier = gi_write_pages.earlier_paper(lib_path, a, b)
        words = {a: ctp.load_paper_words(lib, a), b: ctp.load_paper_words(lib, b)}
        runs = ctp.find_shingle_matches(words[a], words[b], shingle_size=10, x_drop=3)
        first, second = (b, a) if earlier == b else (a, b)   # first = earlier, when known
        side = {a: "a", b: "b"}
        papers = {}
        for pid in (first, second):
            m = paper_meta(lib, pid)
            doi = f[f"doi_{side[pid]}"]
            papers[pid] = {"title": clean_title(m["title"]), "year": m["year"] or f[f"year_{side[pid]}"],
                           "doi": doi, "words": len(words[pid]),
                           "pct": f[f"ft_pct_{side[pid]}"]}
        cites = citation_check(lib, papers[first]["title"], second)["cites_other_by_title"] if earlier else None
    finally:
        lib.close()

    def text_of(r, pid):
        return r.text_a if pid == a else r.text_b

    longest = sorted(runs, key=lambda r: -max(r.length, len(r.text_b.split())))[:TOP_PASSAGES]
    passages = [{"words": len(text_of(r, second).split()), "excerpt": excerpt(text_of(r, second))} for r in longest]
    return {"earlier_known": earlier is not None, "first": papers[first], "second": papers[second],
            "matched_words": f["ft_matched_words"], "runs": len(runs), "byline": f["byline"],
            "later_cites_earlier": cites, "passages": passages}


def _cite(p):
    """[Title](https://doi.org/...) (Year; doi:...) for the Markdown. (The index has no journal names.)"""
    title = (p["title"] or p["doi"] or "").replace("[", "(").replace("]", ")")
    link = doi_url(p["doi"])
    head = f"[{title}]({link})" if link else f"*{title}*"
    year = f"{p['year']}; " if p["year"] else ""
    return head + (f" ({year}doi:{p['doi']})" if link else f" ({p['year']})" if p["year"] else "")


def pct(v):
    return "?" if v is None else f"{v:.0f}%"


def markdown(fd):
    first, second = fd["first"], fd["second"]
    lines = []
    if fd["earlier_known"]:
        share = second["pct"] or 0
        verb = "to be largely a duplicate of" if share >= 50 else "to reuse substantial text from"
        lines.append(f"This article appears {verb} an earlier article: {_cite(first)}.")
        lines.append("")
        lines.append(f"- About **{pct(second['pct'])}** of this article's text is also found in the earlier article "
                     f"({fd['matched_words'] or 0:,} matched words in {fd['runs']} passages; this article has about "
                     f"{second['words']:,} words). That is {pct(first['pct'])} of the earlier article's text.")
    else:
        lines.append(f"This article shares extensive text with another article published the same year: "
                     f"{_cite(first)}. Which one came first can't be told from the publication years.")
        lines.append("")
        lines.append(f"- About **{pct(second['pct'])}** of this article's text is also found in the other article, "
                     f"and {pct(first['pct'])} of the other article's text in this one "
                     f"({fd['matched_words'] or 0:,} matched words in {fd['runs']} passages).")
    if fd["byline"] == "no byline overlap":
        lines.append("- The two author lists share no names.")
    other = "the earlier article" if fd["earlier_known"] else "the other article"
    if fd["later_cites_earlier"] is False:
        lines.append(f"- This article's reference list does not appear to cite {other}.")
    elif fd["later_cites_earlier"]:
        lines.append(f"- This article's reference list appears to cite {other}.")
    lines += [f"- {t}" for t in fd.get("extra", ())]   # the findings you ticked (gi_case_findings.py)
    if fd["passages"]:
        n = len(fd["passages"])
        lines.append("")
        lines.append(f"The {'longest shared passage' if n == 1 else f'{n} longest shared passages'} "
                     f"(word counts in this article)"
                     + (". The attached image shows passage 1 as printed in both articles, the shared words "
                        "highlighted:" if fd.get("shot") else ":"))
        lines.append("")
        for i, p in enumerate(fd["passages"], 1):
            text = p["excerpt"].replace('"', "'")
            lines.append(f"{i}. **{p['words']:,} words**: \"{text}\"")
    return "\n".join(lines)


COPY_JS = """<script>
function ppCopy() {
  var t = document.getElementById("pp-md"), done = document.getElementById("pp-copied");
  function ok() { done.textContent = "Copied"; setTimeout(function () { done.textContent = ""; }, 2000); }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(t.value).then(ok, function () { t.select(); document.execCommand("copy"); ok(); });
  } else { t.select(); document.execCommand("copy"); ok(); }
}
</script>"""

BOX_STYLE = """<style>
.pubpeer-box { border: 2px solid var(--accent, #2b6cb0); border-radius: 8px; padding: 12px 16px; margin: 16px auto;
  max-width: 760px; box-sizing: border-box; }
.pubpeer-box h3 { margin: 0 0 6px; }
.pubpeer-box p { margin: 4px 0; }
.pubpeer-box textarea { width: 100%; box-sizing: border-box; min-height: 16em; font: 13px/1.4 ui-monospace, monospace;
  background: inherit; color: inherit; border: 1px solid #8888; border-radius: 4px; padding: 8px; }
.pubpeer-box button { margin-top: 6px; padding: 6px 14px; font-size: 14px; cursor: pointer; }
.pubpeer-box #pp-copied { margin-left: 8px; }
.pubpeer-box .pp-shot { display: block; max-width: 100%; height: auto; margin-top: 6px; border: 1px solid #8888;
  background: #fff; }
</style>"""


def jpeg_uri(data):
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def shot_html(shot, name, earlier_known=True):
    """The longest passage's image (JPEG bytes) in the box, with a link that saves it (both data: URIs, so
    the page stays one file)."""
    if not shot:
        return ""
    uri = jpeg_uri(shot)
    name = html.escape(name, quote=True)
    sides = "earlier article left, later right" if earlier_known else "the two articles, order unknown"
    return (f'<p><b>Longest shared passage, as printed</b> ({sides}): '
            f'<a download="{name}" href="{uri}">save the image</a> and attach it to the comment with '
            f'PubPeer\'s image upload.</p>\n  <img class="pp-shot" src="{uri}" alt="The longest shared passage '
            f'in both PDFs, the shared words highlighted">')


def box_html(fd, md, shot=None, shot_name="longest.jpg"):
    first, second = fd["first"], fd["second"]
    links = []
    for p, label in ((second, "this article (the later one)" if fd["earlier_known"] else "this article"),
                     (first, "the earlier article" if fd["earlier_known"] else "the other article")):
        url = pubpeer_url(p["doi"])
        doi = html.escape(p["doi"] or "")
        links.append(f'<a href="{url}">PubPeer: {label}</a> ({doi})' if url else f"{label}: no DOI, not on PubPeer")
    where = ("Post it on the later article." if fd["earlier_known"] else
             "The order is unknown: consider posting on both, and say so.")
    return f"""{BOX_STYLE}
<section class="pubpeer-box">
  <h3>For PubPeer</h3>
  <p>{' &middot; '.join(links)}</p>
  <p>{where} The text below is Markdown, which PubPeer comments accept; check it against the passages below before posting.</p>
  <textarea id="pp-md" readonly>{html.escape(md)}</textarea>
  <button type="button" onclick="ppCopy()">Copy Markdown</button><span id="pp-copied"></span>
  {shot_html(shot, shot_name, fd["earlier_known"])}
</section>
{COPY_JS}"""


def insert_box(page_html, box):
    """The box goes above the title images (the first exhibit section), else right after the header."""
    i = page_html.find('<section class="exhibit title-shots">')
    if i < 0:
        m = re.search(r"</header>", page_html)
        i = m.end() if m else 0
    return page_html[:i] + box + "\n" + page_html[i:]


def case_name(f):
    n = f["page_number"]
    return f"GI-{n:02d}" if n is not None else f"{f['paper_a']}-{f['paper_b']}"


def render(f, case, title_shots=True, whole_document=False):
    """Run the page writer for one fulltext row in a scratch directory: every shared passage (the page writer's default), or with
    `whole_document` both papers end to end as one interleaved diff. Returns (page, filename, shot): the
    page with its images inlined, the writer's file name for it (GI-NN-<slug>.html), and the longest
    passage's JPEG bytes (None when neither PDF could be used)."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        shot = d / "longest.jpg"
        p = subprocess.run([PY, str(ROOT / "write_dupe_reports_screenshots.py"), "--library-db", f["library"],
                            "--paper-ids", f"{f['paper_a']},{f['paper_b']}", "--out-dir", str(d), "--neutral",
                            "--shingle-size", "10", "--x-drop", "3", "--case-number", case,
                            "--min-shingle-matches", "0", "--found-by", "", "--text-exhibits", "--embed-images",
                            "--longest-shot", str(shot),
                            *([] if title_shots else ["--no-title-shots"]),
                            *(["--whole-document"] if whole_document else [])],
                           capture_output=True, text=True)
        pages = sorted(d.glob("*.html"))
        if p.returncode != 0 or not pages:
            raise RuntimeError(f"{case}: page writer failed: {(p.stderr or p.stdout)[-300:]}")
        page, _ = gi_share_pages.shareable(pages[0].read_text(encoding="utf-8").replace("\x00", ""), d)
        return page, pages[0].name, (shot.read_bytes() if shot.exists() else None)


def overlap_html(fd):
    """The shared-text figures for the page's summary bar (the page writer itself reports only the run
    count)."""
    first, second = fd["first"], fd["second"]
    if fd["earlier_known"]:
        names = (f"the later article ({second['year']})", f"the earlier ({first['year']})")
    else:
        names = (f"doi:{second['doi']}", f"doi:{first['doi']}")
    return (f'<span>shared text <strong>{pct(second["pct"])}</strong> of {html.escape(names[0])} &middot; '
            f'<strong>{pct(first["pct"])}</strong> of {html.escape(names[1])}</span> '
            f'<span>matched words <strong>{fd["matched_words"] or 0:,}</strong></span> ')


def add_overlap(page, fd):
    bar = '<div class="summary-bar">'
    return page.replace(bar, bar + overlap_html(fd), 1)


def write_page(db, f, out_dir=OUT_DIR, fd=None, name=None):
    """Write the PubPeer page for one fulltext row (as `name`, default the writer's GI-NN-<slug>.html) and
    the longest passage's image (GI-NN-longest.jpg) to out_dir; returns (path, markdown)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case = case_name(f)
    page, slug_name, shot = render(f, case)
    fd = fd or findings(db, f)
    fd["shot"] = shot is not None
    md = markdown(fd)
    shot_path = out_dir / f"{case}-longest.jpg"
    if shot:
        shot_path.write_bytes(shot)
    page = insert_box(add_overlap(page, fd), box_html(fd, md, shot, shot_path.name))
    if name is None:   # a re-run replaces the case's old page, whose slug may have changed
        for old in out_dir.glob(f"{case}-*shingle-matches.html"):
            old.unlink()
    path = out_dir / (name or slug_name)
    path.write_text(page, encoding="utf-8")
    return path, md


def shot_section(shot, earlier_known=True):
    """The longest passage's image (JPEG bytes) as an exhibit of its own, for the publisher's page."""
    sides = "earlier article left, later right" if earlier_known else "order unknown"
    return f"""{BOX_STYLE}
<section class="exhibit">
  <div class="exhibit-head"><h3>Longest shared passage, as printed ({sides})</h3></div>
  <img class="pp-shot" style="max-width:100%;height:auto" src="{jpeg_uri(shot)}" alt="The longest shared passage in both PDFs, the shared words highlighted">
</section>"""


def whole_document(fd):
    """Whether the comparison page should be one interleaved diff of both articles: more than
    WHOLE_DOCUMENT_PCT of either one is shared, so the finding is one document, not many passages."""
    return max(fd["first"]["pct"] or 0, fd["second"]["pct"] or 0) > WHOLE_DOCUMENT_PCT


def publisher_page(f, fd):
    """The comparison page for the journal (and the project site): no PubPeer box, no title images, the
    shared-text figures in the summary bar and the longest passage's image above the matches. Every shared
    passage, or (whole_document(fd)) both articles end to end as one diff. Returns (page html, longest
    passage JPEG bytes or None)."""
    page, _, shot = render(f, case_name(f), title_shots=False, whole_document=whole_document(fd))
    page = add_overlap(page, fd)
    if shot:
        # above the list of matches (its heading), after the papers and the summary figures
        i = next((j for j in (page.find('<div class="exhibit-head"'), page.find('<section class="exhibit"'))
                  if j >= 0), 0)
        page = page[:i] + shot_section(shot, fd["earlier_known"]) + "\n" + page[i:]
    return page, shot


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gi", nargs="*", type=int, help="GI page numbers")
    ap.add_argument("--problems", action="store_true", help="every pair you've reviewed as a problem")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
    db.row_factory = sqlite3.Row
    rows = [db.execute("select * from fulltext where page_number = ?", (n,)).fetchone() or n for n in args.gi]
    if args.problems:
        rows += db.execute("""select f.* from fulltext f join reviews r using (pair_lo, pair_hi)
                              where r.decision = 'problem' and f.page_number is not null
                              order by f.page_number""").fetchall()
    if not rows:
        ap.error("name some GI numbers, or --problems")
    for f in rows:
        if isinstance(f, int):
            print(f"GI-{f}: no such page", file=sys.stderr)
            continue
        try:
            path, _ = write_page(db, f, args.out_dir)
            print(f"{path}  {path.stat().st_size / 1e3:.0f} KB")
        except Exception as e:  # one pair failing shouldn't stop a batch
            print(f"GI-{f['page_number']}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

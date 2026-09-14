#!/usr/bin/env python3
"""List papers that couldn't be auto-downloaded, for hand-fetching.

retrieve_papers.py/bulk_retrieve_arxiv.py/bulk_retrieve_crossref.py already
resolved a real DOI (so we know exactly which paper this is) but couldn't
get a PDF automatically -- no OA copy Unpaywall knows about (`no_oa`), the
OA link didn't actually serve a PDF (`oa_url_not_pdf`), a download attempt
failed (`error`), or Unpaywall doesn't know the DOI (`doi_unknown_to_unpaywall`).
This is CLAUDE.md's retrieve_papers.py "Known gap" made into a repeatable
tool instead of "misses have so far been re-fetched by hand" with no
supporting script -- see README's "Runbook: getting a manually-downloaded
paper into the pipeline" for the full hand-off.

Writes two files to --out-dir (default manual_downloads/):
  - _pending.md: human-readable list to work through -- title, year, DOI
    link, why it failed, and the exact filename to save the PDF as.
  - _manifest.json: the same data as JSON, keyed by that same filename --
    import_manual_downloads.py reads this to know how to register whatever
    it finds in --out-dir, so there's no fragile filename-parsing involved
    on the way back in.

Filenames use the same slugify(title)-slugify(doi).pdf convention every
other download path in this project already uses, so a manually-downloaded
file lands with a name consistent with everything already in papers/.
"""

import argparse
import html
import json
import re
from pathlib import Path

import retrieve_papers as rp

STATUSES_NEEDING_MANUAL = ("no_oa", "error", "oa_url_not_pdf", "doi_unknown_to_unpaywall", "doi_not_found")
HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text):
    return " ".join(HTML_TAG_RE.sub("", text or "").split())


def load_pending(conn):
    placeholders = ",".join("?" * len(STATUSES_NEEDING_MANUAL))
    rows = conn.execute(
        f"SELECT key, title, authors, year, doi, status, error FROM papers "
        f"WHERE status IN ({placeholders}) ORDER BY status, title",
        STATUSES_NEEDING_MANUAL,
    ).fetchall()
    papers = [dict(zip(("key", "title", "authors", "year", "doi", "status", "error"), r)) for r in rows]
    for paper in papers:
        paper["title"] = strip_html(paper["title"])  # Crossref titles occasionally carry <i>/<sub> markup
    return papers


def suggested_filename(paper):
    doi_part = rp.slugify(paper["doi"]) if paper["doi"] else "manual"
    return f"{rp.slugify(paper['title'])}-{doi_part}.pdf"


def build_manifest(papers):
    manifest = {}
    for paper in papers:
        filename = suggested_filename(paper)
        manifest[filename] = {
            "key": paper["key"], "title": paper["title"], "authors": paper["authors"],
            "year": paper["year"], "doi": paper["doi"], "status": paper["status"], "error": paper["error"],
        }
    return manifest


STATUS_LABEL = {
    "no_oa": "No open-access copy found by Unpaywall -- try your institution's access, the publisher site, or the author's homepage.",
    "error": "A download attempt failed (see error below) -- the link may still be worth trying by hand in a browser.",
    "oa_url_not_pdf": "Unpaywall's link didn't serve an actual PDF (often a landing/paywall page) -- try the DOI link directly.",
    "doi_unknown_to_unpaywall": "Unpaywall has no record of this DOI at all.",
    "doi_not_found": "No DOI could be resolved for this title -- try a web search.",
}


def group_by_status(papers):
    by_status = {}
    for paper in papers:
        by_status.setdefault(paper["status"], []).append(paper)
    return by_status


def render_markdown(papers):
    lines = [
        "# Papers needing a manual download",
        "",
        f"{len(papers)} paper(s) -- a real DOI is known for each (so this is a specific, identified paper,",
        "not a guess), but automated download didn't get a PDF. Save each one as the exact filename shown",
        "into this directory, then run `python3 import_manual_downloads.py` to fold them into the pipeline.",
        "",
    ]
    by_status = group_by_status(papers)

    for status, group in by_status.items():
        lines.append(f"## {status} ({len(group)}) -- {STATUS_LABEL.get(status, '')}")
        lines.append("")
        for paper in group:
            if paper["doi"]:
                doi_url = f"https://doi.org/{paper['doi']}"
                doi_link = f"[{doi_url}]({doi_url})"  # markdown link, not a bare URL -- clickable when viewed in a browser
            else:
                doi_link = "(no DOI known)"
            lines.append(f"- **{paper['title']}** ({paper['year'] or 'year unknown'})")
            lines.append(f"  - Link: {doi_link}")
            if paper["error"]:
                lines.append(f"  - Error: {paper['error']}")
            lines.append(f"  - Save as: `{suggested_filename(paper)}`")
        lines.append("")
    return "\n".join(lines)


def render_html(papers):
    """Same content as render_markdown(), as a self-contained local HTML file --
    open it directly in a browser (file://) for clickable links without needing
    a markdown viewer/renderer. Not published anywhere; manual_downloads/ is
    gitignored and this is a private working list, so no external hosting."""
    by_status = group_by_status(papers)
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Papers needing a manual download</title>",
        "<style>",
        "  :root { color-scheme: light dark; }",
        "  body { font: 15px/1.5 -apple-system, system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }",
        "  h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; border-bottom: 1px solid #8884; padding-bottom: .3rem; }",
        "  .intro, .status-note { opacity: .75; }",
        "  ul { padding-left: 1.2rem; } li { margin-bottom: .9rem; }",
        "  .doi { font-family: ui-monospace, monospace; font-size: .9em; }",
        "  .error { color: #c0392b; } code { font-size: .9em; }",
        "</style></head><body>",
        "<h1>Papers needing a manual download</h1>",
        f"<p class='intro'>{len(papers)} paper(s) &mdash; a real DOI is known for each (so this is a specific, "
        "identified paper, not a guess), but automated download didn't get a PDF. Save each one as the exact "
        "filename shown into this directory, then run <code>python3 import_manual_downloads.py</code> to fold "
        "them into the pipeline.</p>",
    ]
    for status, group in by_status.items():
        parts.append(f"<h2>{html.escape(status)} ({len(group)})</h2>")
        if STATUS_LABEL.get(status):
            parts.append(f"<p class='status-note'>{html.escape(STATUS_LABEL[status])}</p>")
        parts.append("<ul>")
        for paper in group:
            title = html.escape(paper["title"])
            year = paper["year"] or "year unknown"
            parts.append(f"<li><strong>{title}</strong> ({year})<br>")
            if paper["doi"]:
                doi_url = f"https://doi.org/{paper['doi']}"
                parts.append(f"Link: <a class='doi' href='{html.escape(doi_url)}' target='_blank' rel='noopener'>{html.escape(doi_url)}</a><br>")
            else:
                parts.append("Link: (no DOI known)<br>")
            if paper["error"]:
                parts.append(f"<span class='error'>Error: {html.escape(paper['error'])}</span><br>")
            parts.append(f"Save as: <code>{html.escape(suggested_filename(paper))}</code></li>")
        parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)


def parse_args():
    parser = argparse.ArgumentParser(description="List papers needing a manual download.")
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--out-dir", type=Path, default=Path("manual_downloads"))
    return parser.parse_args()


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    conn = rp.PaperStore(args.db).conn
    papers = load_pending(conn)
    conn.close()

    (args.out_dir / "_pending.md").write_text(render_markdown(papers), encoding="utf-8")
    (args.out_dir / "_pending.html").write_text(render_html(papers), encoding="utf-8")
    (args.out_dir / "_manifest.json").write_text(json.dumps(build_manifest(papers), indent=2), encoding="utf-8")

    print(f"{len(papers)} paper(s) need a manual download -- see {args.out_dir / '_pending.md'}")
    print(f"  or open {(args.out_dir / '_pending.html').resolve()} directly in a browser for clickable links")
    print(f"drop PDFs into {args.out_dir}/ using the filenames from that list, then run import_manual_downloads.py")


if __name__ == "__main__":
    main()

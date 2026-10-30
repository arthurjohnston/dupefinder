"""The General Index tables of the project site, from example_output/general_index/cases.json.

Used by gi_report_case.py (general_index/index.html, rewritten whenever a case is recorded) and by
.github/scripts/build_pages_site.py (the site's front page and general_index/index.html again, on every
deploy), so both show the same tables. Standard library only: the Pages workflow installs nothing but
`markdown`.

Three tables (INDEX_GROUPS): copied data, then the reported cases, then those not reported, each sorted by
the larger of the two shared-text shares.
"""
import html
import json
from pathlib import Path

INDEX_GROUPS = (
    ("data", "Copied data", "Results data shared (table values, patient numbers), not just text."),
    ("reported", "Reported: copied text", "Reported to the journal."),
    ("unreported", "Not reported", "Public, but not reported to the journal."),
)

INTRO_HTML = """<p>Found by scanning the <a href="https://archive.org/details/GeneralIndex">General Index</a>
(word n-grams from over 100 million articles), then checked against both articles' full text. Each page shows
the shared passages as printed in both articles. "Shared text" is the share of each article's words inside a
run of 10+ matching words. Measured overlap only: not a finding of misconduct.</p>
<p>The later article is listed first; where the years are the same, the order means nothing. Tables are sorted
by the larger shared-text figure.</p>"""

INDEX_STYLE = """
:root {
  --bg: #f2f4ef; --surface: #ffffff; --ink: #1a1f1a; --ink-muted: #565f57;
  --border: #d7dbd0; --accent-strong: #7c4a15;
  --font-display: "Source Serif 4", Georgia, "Times New Roman", serif;
  --font-body: "Source Sans 3", -apple-system, "Segoe UI", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14170f; --surface: #1c2016; --ink: #e9ede4; --ink-muted: #a2ab98;
    --border: #333a29; --accent-strong: #f0b46a;
  }
}
:root[data-theme="dark"] {
  --bg: #14170f; --surface: #1c2016; --ink: #e9ede4; --ink-muted: #a2ab98;
  --border: #333a29; --accent-strong: #f0b46a;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 var(--font-body); }
main { max-width: 1300px; margin: 0 auto; padding: 2.5rem 16px 5rem; }
main > p { max-width: 78ch; }
h1, h2 { font-family: var(--font-display); line-height: 1.2; }
h1 { font-size: 2.2rem; margin: 0 0 1rem; }
h2 { font-size: 1.4rem; margin: 2.5rem 0 0.5rem; border-bottom: 1px solid var(--border); padding-bottom: .3rem; }
a { color: var(--accent-strong); }
.table-wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .45rem .7rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-muted); font-weight: 600; white-space: nowrap; }
td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
td.art { min-width: 18rem; }
"""


def case_key(case):
    """GI-235 sorts as 235, after GI-80."""
    tail = case.rsplit("-", 1)[-1]
    return (0, int(tail), case) if tail.isdigit() else (1, 0, case)


def pct(v):
    return "?" if v is None else f"{v:.0f}%"


def doi_url(doi):
    return f"https://doi.org/{doi}" if doi and doi.startswith("10.") else None


def load_cases(site_dir):
    path = Path(site_dir) / "cases.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def index_group(c):
    """Which INDEX_GROUPS table a cases.json entry goes in."""
    return "data" if c.get("data") else "reported" if c.get("reported") else "unreported"


def share_key(item):
    """Largest shared-text share (of either article) first; ties by case number."""
    case, c = item
    return (-max(c["later"].get("pct") or 0, c["earlier"].get("pct") or 0), case_key(case))


def _article(p):
    title = html.escape((p["title"] or p["doi"] or "").replace("*", ""))
    doi = p["doi"] or ""
    link = doi_url(doi)
    year = f" ({p['year']})" if p["year"] else ""
    if p.get("journal"):
        year += f", <i>{html.escape(p['journal'])}</i>"
    return f'{title}{year}<br><a href="{html.escape(link)}">doi:{html.escape(doi)}</a>' if link else title + year


def _union(c):
    """The note for a case with other sources (gi_sources): the share found in all of them."""
    if not c.get("union_pct"):
        return ""
    return f"<br><small>{c['union_pct']:.0f}% in {c['sources']} earlier works</small>"


def _row(case, c, prefix=""):
    later, earlier = c["later"], c["earlier"]
    return f"""<tr><td class="num"><a href="{html.escape(prefix + c['file'])}">{html.escape(case)}</a></td>
<td class="art">{_article(later)}</td><td class="art">{_article(earlier)}</td>
<td class="num">{pct(later['pct'])} / {pct(earlier['pct'])}{_union(c)}</td>
<td class="num">{c['longest'] or '?'} words</td><td class="num">{html.escape(c['reported'] or 'not reported')}</td></tr>"""


def tables_html(cases, prefix="", level=2):
    """The INDEX_GROUPS tables, each under an <h{level}> with its count. prefix goes before each page's file name
    (the front page links into general_index/)."""
    out = []
    for key, heading, about in INDEX_GROUPS:
        group = sorted(((k, c) for k, c in cases.items() if index_group(c) == key), key=share_key)
        if not group:
            continue
        rows = "\n".join(_row(k, c, prefix) for k, c in group)
        out.append(f"""<h{level} id="{key}">{html.escape(heading)} ({len(group)})</h{level}>
<p>{html.escape(about)}</p>
<div class="table-wrap"><table>
<thead><tr><th>Case</th><th>Later article</th><th>Earlier article</th><th>Shared text (later / earlier)</th>
<th>Longest passage</th><th>Reported</th></tr></thead>
<tbody>
{rows}
</tbody>
</table></div>""")
    return "\n".join(out)


def index_page(cases):
    """general_index/index.html: the intro and the tables, linking back to the front page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>General Index: checked pairs</title>
<style>{INDEX_STYLE}</style>
</head>
<body>
<main>
<h1>General Index: checked pairs</h1>
{INTRO_HTML}
{tables_html(cases)}
<p><a href="../">All results</a></p>
</main>
</body>
</html>
"""


def write_index(site_dir):
    """Rewrite site_dir/index.html from site_dir/cases.json; returns its path."""
    path = Path(site_dir) / "index.html"
    path.write_text(index_page(load_cases(site_dir)), encoding="utf-8")
    return path

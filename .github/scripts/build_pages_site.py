#!/usr/bin/env python3
"""Build the GitHub Pages site for example_output/: copy the directory and write its front page, so the
site always shows every published pair as of this deploy.

The front page (index.html) has, in order:
  - the total count: the General Index pairs (example_output/general_index/cases.json) plus the prototype's
    (the "(N pairs)" in README.md's publisher headings, which partition its pairs);
  - the General Index tables (general_index/gi_site_index.py, the same as general_index/index.html, which is
    rewritten here too);
  - "Results from Prototype": README.md rendered as before, its title dropped, its headings one level down,
    and its section pointing to the General Index pairs left out (they're the tables above).

Run by .github/workflows/pages.yml; works locally too (needs `pip install markdown`):
    python3 .github/scripts/build_pages_site.py --out _site
"""
import argparse
import html
import re
import shutil
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))
import gi_site_index  # noqa: E402

TITLE = "Text-overlap results"   # the page's heading and <title> add the total: "Text-overlap results: 173 pairs"
GI_SECTION = "## General Index pairs reported"   # README.md's pointer to general_index/: the tables replace it

STYLE = """
:root {
  --bg: #f2f4ef; --surface: #ffffff; --ink: #1a1f1a; --ink-muted: #565f57;
  --border: #d7dbd0; --accent: #a8641f; --accent-strong: #7c4a15;
  --font-display: "Source Serif 4", Georgia, "Times New Roman", serif;
  --font-body: "Source Sans 3", -apple-system, "Segoe UI", sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14170f; --surface: #1c2016; --ink: #e9ede4; --ink-muted: #a2ab98;
    --border: #333a29; --accent: #d99a4e; --accent-strong: #f0b46a;
  }
}
:root[data-theme="dark"] {
  --bg: #14170f; --surface: #1c2016; --ink: #e9ede4; --ink-muted: #a2ab98;
  --border: #333a29; --accent: #d99a4e; --accent-strong: #f0b46a;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 var(--font-body); }
main { max-width: 1500px; margin: 0 auto; padding: 2.5rem 16px 5rem; }
main p, main ul { max-width: 78ch; }
h1, h2, h3, h4 { font-family: var(--font-display); line-height: 1.2; }
h1 { font-size: 2.2rem; margin: 0 0 1rem; }
h2 { font-size: 1.7rem; margin: 3.5rem 0 1rem; border-bottom: 2px solid var(--border); padding-bottom: .4rem; }
h3 { font-size: 1.35rem; margin: 2.5rem 0 0.75rem; border-bottom: 1px solid var(--border); padding-bottom: .3rem; }
h4 { font-size: 1.1rem; margin: 2rem 0 0.5rem; }
a { color: var(--accent-strong); }
code { font-family: var(--font-mono); font-size: .88em; }
.counts { font-size: 1.15rem; }
.counts strong { font-variant-numeric: tabular-nums; }
.table-wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .45rem .7rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-muted); font-weight: 600; white-space: nowrap; }
td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
td.art { min-width: 18rem; }
.prototype td:nth-child(3), .prototype td:nth-last-child(-n+2) { white-space: nowrap; font-variant-numeric: tabular-nums; }
.prototype td:nth-child(2) { min-width: 34rem; }
.prototype td:nth-child(2) a { overflow-wrap: anywhere; }
"""


def prototype_count(readme):
    """The prototype's pairs: the sum of README.md's "### ... (N pairs)" publisher headings."""
    return sum(int(n) for n in re.findall(r"^###\s.*\((\d+) pairs?\)\s*$", readme, re.M))


def prototype_html(readme):
    """README.md as HTML, without its title or its General Index section, every heading one level down."""
    text = re.sub(r"\A# .+\n", "", readme)
    if GI_SECTION in text:
        start = text.index(GI_SECTION)
        nxt = re.search(r"^## ", text[start + len(GI_SECTION):], re.M)
        text = text[:start] + (text[start + len(GI_SECTION) + nxt.start():] if nxt else "")
    text = re.sub(r"^(#{2,5}) ", lambda m: "#" + m.group(1) + " ", text, flags=re.M)
    body = markdown.markdown(text, extensions=["tables"])
    return body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")


def front_page(readme, cases):
    gi = len(cases)
    reported = sum(1 for c in cases.values() if c.get("reported"))
    proto = prototype_count(readme)
    title = f"{TITLE}: {gi + proto} pairs"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<h1>{html.escape(title)}</h1>
<p class="counts"><strong>{gi}</strong> from the General Index ({reported} reported) &middot;
<strong>{proto}</strong> from the prototype</p>
<h2 id="general-index">General Index ({gi})</h2>
{gi_site_index.INTRO_HTML}
{gi_site_index.tables_html(cases, prefix="general_index/", level=3)}
<h2 id="prototype">Results from Prototype ({proto})</h2>
<section class="prototype">
{prototype_html(readme)}
</section>
</main>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, default=Path("example_output"))
    parser.add_argument("--out", type=Path, default=Path("_site"))
    args = parser.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    shutil.copytree(args.src, args.out)

    readme = (args.src / "README.md").read_text(encoding="utf-8")
    cases = gi_site_index.load_cases(args.src / "general_index")
    (args.out / "index.html").write_text(front_page(readme, cases), encoding="utf-8")
    if cases:
        gi_site_index.write_index(args.out / "general_index")
    print(f"wrote {args.out}/index.html: {len(cases)} General Index pairs, {prototype_count(readme)} prototype pairs")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the GitHub Pages site for example_output/: copy the directory and render its README.md
as index.html, so the site's front page is always the README's own table of pages.

Run by .github/workflows/pages.yml; works locally too (needs `pip install markdown`):
    python3 .github/scripts/build_pages_site.py --out _site
"""
import argparse
import html
import re
import shutil
from pathlib import Path

import markdown

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
main > p, main > ul { max-width: 78ch; }
h1, h2 { font-family: var(--font-display); line-height: 1.2; }
h1 { font-size: 2.2rem; margin: 0 0 1rem; }
h2 { font-size: 1.4rem; margin: 2.5rem 0 0.75rem; border-bottom: 1px solid var(--border); padding-bottom: .3rem; }
a { color: var(--accent-strong); }
code { font-family: var(--font-mono); font-size: .88em; }
.table-wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .45rem .7rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-muted); font-weight: 600; white-space: nowrap; }
td:nth-child(3), td:nth-last-child(-n+2) { white-space: nowrap; font-variant-numeric: tabular-nums; }
td:nth-child(2) { min-width: 34rem; }
td:nth-child(2) a { overflow-wrap: anywhere; }
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, default=Path("example_output"))
    parser.add_argument("--out", type=Path, default=Path("_site"))
    args = parser.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    shutil.copytree(args.src, args.out)

    text = (args.src / "README.md").read_text(encoding="utf-8")
    title = re.search(r"^# (.+)$", text, re.M).group(1)
    body = markdown.markdown(text, extensions=["tables"])
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    (args.out / "index.html").write_text(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
""", encoding="utf-8")
    print(f"wrote {args.out}/index.html and {sum(1 for _ in args.out.rglob('*.html')) - 1} page(s)")


if __name__ == "__main__":
    main()

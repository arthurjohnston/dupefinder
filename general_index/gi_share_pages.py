"""Make result pages shareable: one self-contained HTML file per page, the screenshots embedded in it.

The pages in data/fulltext_pages_s16/ keep their screenshots as separate JPEGs (img/GI-NN/) and link the
local PDFs by relative path, so a page sent on its own shows broken images and dead links. This writes a
copy that stands alone:
  - every screenshot inlined as a data: URI (the click-to-open-full-size link around each one dropped,
    since it would carry the same image a second time; lazy loading off, so printing shows them all);
  - the local PDF links replaced by the file name and "(not included)", or with --embed-pdfs, the PDFs
    themselves as download links (data: URIs with a download attribute; adds each PDF's size, x1.33);
  - a full document skeleton (doctype, charset, viewport) around the page, which is written as a
    fragment for publishing as a Claude Artifact.
The originals are not touched. DOI links are kept as they are.

Usage: gi_share_pages.py [GI_NUMBER | PAGE.html ...] [--all] [--pages-dir data/fulltext_pages_s16]
                         [--out-dir data/shared_pages] [--embed-pdfs]
e.g.   gi_share_pages.py 80 76        ->  data/shared_pages/GI-80-....html, GI-76-....html
"""
import argparse
import base64
import html
import mimetypes
import re
import sys
from pathlib import Path

import gi_db

IMG_LINK = re.compile(r'<a href="([^"]+)">(<img [^>]*>)</a>')
IMG_SRC = re.compile(r'(<img [^>]*?src=")([^"]+)(")')
PDF_LINK = re.compile(r'<a href="([^"]+\.pdf)">([^<]*)</a>', re.IGNORECASE)

SKELETON = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{head}
</head>
<body>
{body}
</body>
</html>
"""


def is_local(ref):
    return not re.match(r"^[a-z][a-z0-9+.-]*:", ref, re.IGNORECASE) and not ref.startswith(("#", "//"))


def data_uri(path, default_type="application/octet-stream"):
    kind = mimetypes.guess_type(path.name)[0] or default_type
    return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def shareable(page_html, base_dir, embed_pdfs=False):
    """(standalone html, notes): page_html with local images inlined and local PDF links resolved."""
    notes = []

    def unwrap(m):  # <a href=X><img src=X></a>: the link only opens the same image full size
        return m.group(2) if is_local(html.unescape(m.group(1))) else m.group(0)

    page_html = IMG_LINK.sub(unwrap, page_html)

    def inline(m):
        ref = html.unescape(m.group(2))
        if not is_local(ref):
            return m.group(0)
        path = base_dir / ref
        if not path.exists():
            notes.append(f"missing image {ref}")
            return m.group(0)
        return m.group(1) + data_uri(path, "image/jpeg") + m.group(3)

    page_html = IMG_SRC.sub(inline, page_html)
    # the images are in the file already: lazy loading only leaves blanks when printed or captured
    page_html = page_html.replace(' loading="lazy"', "")

    def pdf(m):
        ref, text = html.unescape(m.group(1)), m.group(2)
        if not is_local(ref):
            return m.group(0)
        path = (base_dir / ref).resolve()
        if embed_pdfs and path.exists():
            return (f'<a download="{html.escape(path.name, quote=True)}" '
                    f'href="{data_uri(path, "application/pdf")}">{text}</a> (embedded, click to save)')
        if embed_pdfs:
            notes.append(f"missing PDF {ref}")
        return f"{text} (not included)"

    page_html = PDF_LINK.sub(pdf, page_html)

    if re.match(r"\s*<!doctype", page_html, re.IGNORECASE):
        return page_html, notes
    # the fragment starts with <title> and <style>: those go in <head>, the rest in <body>
    m = re.match(r"\s*((?:<title>.*?</title>\s*)?(?:<style>.*?</style>\s*)*)", page_html, re.DOTALL)
    head, body = m.group(1).strip(), page_html[m.end():]
    return SKELETON.format(head=head, body=body), notes


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pages", nargs="*", help="GI numbers or page files")
    ap.add_argument("--all", action="store_true", help="every GI-*.html page in --pages-dir")
    ap.add_argument("--pages-dir", default=str(gi_db.DATA / "fulltext_pages_s16"))
    ap.add_argument("--out-dir", default=str(gi_db.DATA / "shared_pages"))
    ap.add_argument("--embed-pdfs", action="store_true", help="embed both PDFs as download links")
    args = ap.parse_args()
    pages_dir, out_dir = Path(args.pages_dir), Path(args.out_dir)

    files = sorted(pages_dir.glob("GI-*.html")) if args.all else []
    for p in args.pages:
        if p.isdigit():
            found = sorted(pages_dir.glob(f"GI-{int(p):02d}-*.html"))
            if not found:
                print(f"no page for GI-{p} in {pages_dir}", file=sys.stderr)
            files += found
        else:
            files.append(Path(p))
    if not files:
        ap.error("name some pages (GI numbers or files), or --all")
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        page, notes = shareable(f.read_text(encoding="utf-8"), f.parent, args.embed_pdfs)
        out = out_dir / f.name
        out.write_text(page, encoding="utf-8")
        print(f"{out}  {out.stat().st_size / 1e6:.1f} MB" + (f"  ({'; '.join(notes)})" if notes else ""))


if __name__ == "__main__":
    main()

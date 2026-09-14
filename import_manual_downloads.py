#!/usr/bin/env python3
"""Fold hand-downloaded PDFs (from list_manual_downloads.py's --out-dir) into
the pipeline -- moves each into papers/ and records it in state.sqlite3
exactly the way CLAUDE.md's retrieve_papers.py "Known gap" describes doing
by hand: verify it's a real PDF, then a state.sqlite3 row with
status='downloaded' and the real file_path, keyed the same way
make_key()/list_manual_downloads.py would compute it -- so extract_papers.py
picks it up with zero changes, same as any other downloaded paper.

Reads _manifest.json (written by list_manual_downloads.py) rather than
trying to reverse-engineer a key from whatever filename shows up -- the
manifest is the source of truth for "this exact file goes with this exact
DB row". Only files that are (a) listed in the manifest and (b) actually
present get processed; anything else in --out-dir is left alone and
reported, not guessed at.

Idempotent: a manifest entry whose key is already status='downloaded' in
state.sqlite3 is skipped (matching every other stage's non-clobbering
discipline) unless --overwrite. Safe to run repeatedly as more files trickle
into --out-dir over time.
"""

import argparse
import json
import shutil
from pathlib import Path

import retrieve_papers as rp

PDF_MAGIC = b"%PDF"


def is_real_pdf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == PDF_MAGIC
    except OSError:
        return False


def parse_args():
    parser = argparse.ArgumentParser(description="Import hand-downloaded PDFs into the pipeline.")
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    parser.add_argument("--manual-dir", type=Path, default=Path("manual_downloads"))
    parser.add_argument("--papers-dir", type=Path, default=Path("papers"))
    parser.add_argument("--overwrite", action="store_true",
                         help="re-import even if state.sqlite3 already has this key as status='downloaded'")
    return parser.parse_args()


def main():
    args = parse_args()
    manifest_path = args.manual_dir / "_manifest.json"
    if not manifest_path.exists():
        print(f"no {manifest_path} -- run list_manual_downloads.py first")
        return

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    args.papers_dir.mkdir(parents=True, exist_ok=True)
    store = rp.PaperStore(args.db)

    imported = skipped_already_done = missing = not_a_pdf = 0
    remaining_manifest = {}

    for filename, paper in manifest.items():
        src = args.manual_dir / filename
        record = store.get(paper["key"])

        if record and record.get("status") == "downloaded" and not args.overwrite:
            skipped_already_done += 1
            continue  # already imported (by a previous run, or auto-downloaded some other way since)

        if not src.exists():
            missing += 1
            remaining_manifest[filename] = paper
            continue

        if not is_real_pdf(src):
            print(f"  NOT A PDF (skipping, left in place): {filename}")
            not_a_pdf += 1
            remaining_manifest[filename] = paper
            continue

        dest = args.papers_dir / filename
        shutil.move(str(src), str(dest))
        store.upsert(
            paper["key"], title=paper["title"], authors=paper["authors"], year=paper["year"], doi=paper["doi"],
            status="downloaded", oa_status="manual", pdf_url=None, file_path=str(dest), error=None,
        )
        print(f"  imported: {filename} -> {dest}")
        imported += 1

    manifest_path.write_text(json.dumps(remaining_manifest, indent=2), encoding="utf-8")
    store.conn.close()

    print()
    print(f"imported {imported}, already done {skipped_already_done}, "
          f"still missing {missing}, rejected (not a PDF) {not_a_pdf}")
    if missing:
        print(f"{missing} paper(s) still waiting in {manifest_path} -- not yet found in {args.manual_dir}/")


if __name__ == "__main__":
    main()

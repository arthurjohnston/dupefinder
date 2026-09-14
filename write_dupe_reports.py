#!/usr/bin/env python3
"""Write one text report per suspected-duplicator paper, ranked by count.

Consumes the ai_check pass build_dupe_candidates.py's `ensure_columns()` adds
columns for (todo.md's "AI pre-filter pass" section has the full story and
the false-positive patterns found doing that pass by hand). This script is
the last step: for every paper that is the chronologically LATER side
(later_paper_id) of at least --min-count ai_check='yes' cross-paper
candidates, write dupe_reports/<paper-id>-<slug>.txt listing every match --
the earlier/source paper, similarity/lcs_ratio/ngram_jaccard, same_author,
the ai_check_reason, and both sides' full paragraph text.

A paper below --min-count is still included if any of its candidates are
same_author=0 (cross-author) -- those are the rare, high-value signal (see
todo.md: only 3 of 201 ai_check='yes' cross-paper candidates were
cross-author in the first pass) and shouldn't be dropped just for having a
low count.

Links use each paper's DOI (https://doi.org/<doi>) when known, which
resolves for arXiv-sourced DOIs too; falls back to the local file_path.

Each displayed paragraph is stitched with adjacent paragraphs (same
paper_id, para_index +/- 1, +/- 2...) when it looks cut off -- extract_papers.py's
paragraph splitter sometimes breaks one continuous sentence across two
paragraph records (a PyMuPDF block-boundary/page-boundary artifact), and a
report showing only the fragment on one side of that break can misread as a
weaker or stronger match than the real, complete text actually is -- this
happened for real (see todo.md's "AI pre-filter pass" -- a "worth a second
look" verdict on a fragment turned out to be two papers making an
unremarkable, complete, near-identical-sounding statement once the
continuation was read). Detection is a plain heuristic (does the text end
with terminal punctuation / start with a capital letter), not a guarantee.
"""

import argparse
import re
from pathlib import Path

import db

DEFAULT_MIN_COUNT = 2
MAX_STITCH_EXTRA = 3  # cap on how many neighboring paragraphs to pull in per direction


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def _looks_complete_end(text):
    return bool(re.search(r'[.!?][")”]?\s*$', text.strip()))


def _looks_complete_start(text):
    stripped = text.strip()
    return not stripped or not stripped[0].islower()


def expand_paragraph(conn, paper_id, para_index, text):
    """Stitch in neighboring paragraphs (same paper_id) when `text` looks cut
    off mid-sentence at either end -- see module docstring. Returns
    (display_index_range, stitched_text)."""
    lo = hi = para_index
    pieces = [text]

    extra = 0
    while not _looks_complete_end(pieces[-1]) and extra < MAX_STITCH_EXTRA:
        row = conn.execute(
            "SELECT text FROM paragraphs WHERE paper_id = ? AND para_index = ?", (paper_id, hi + 1)
        ).fetchone()
        if not row:
            break
        pieces.append(row[0])
        hi += 1
        extra += 1

    extra = 0
    while not _looks_complete_start(pieces[0]) and extra < MAX_STITCH_EXTRA:
        row = conn.execute(
            "SELECT text FROM paragraphs WHERE paper_id = ? AND para_index = ?", (paper_id, lo - 1)
        ).fetchone()
        if not row:
            break
        pieces.insert(0, row[0])
        lo -= 1
        extra += 1

    index_range = f"{lo}" if lo == hi else f"{lo}-{hi}"
    return index_range, " ".join(pieces)


def paper_link(conn, paper_id):
    doi, file_path = conn.execute("SELECT doi, file_path FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if doi:
        return f"https://doi.org/{doi}"
    return file_path


def target_papers(conn, min_count):
    rows = conn.execute(
        """
        SELECT pd.later_paper_id, COUNT(*) as n
        FROM potential_dupes pd
        WHERE pd.same_paper = 0 AND pd.ai_check = 'yes' AND pd.later_paper_id IS NOT NULL
        GROUP BY pd.later_paper_id
        """
    ).fetchall()
    by_count = {later_id: n for later_id, n in rows}

    cross_author_papers = {
        r[0] for r in conn.execute(
            """
            SELECT DISTINCT later_paper_id FROM potential_dupes
            WHERE same_paper = 0 AND ai_check = 'yes' AND same_author = 0 AND later_paper_id IS NOT NULL
            """
        ).fetchall()
    }

    targets = {pid for pid, n in by_count.items() if n >= min_count} | cross_author_papers
    return sorted(targets, key=lambda pid: (-by_count.get(pid, 0), pid))


def write_report(conn, later_paper_id, out_dir):
    title, year = conn.execute("SELECT title, year FROM papers WHERE id = ?", (later_paper_id,)).fetchone()
    link = paper_link(conn, later_paper_id)

    matches = conn.execute(
        """
        SELECT pd.id, pd.paper_id_1, pd.paper_id_2, pd.paragraph_id_1, pd.paragraph_id_2,
               pd.earlier_paper_id, pd.later_paper_id, pd.similarity, pd.lcs_ratio, pd.ngram_jaccard,
               pd.same_author, pd.later_cites_earlier, pd.ai_check_reason
        FROM potential_dupes pd
        WHERE pd.same_paper = 0 AND pd.ai_check = 'yes' AND pd.later_paper_id = ?
        ORDER BY pd.similarity DESC
        """,
        (later_paper_id,),
    ).fetchall()

    lines = []
    lines.append(f"Suspected duplication report: {title}")
    lines.append(f"Year: {year}")
    lines.append(f"Link: {link}")
    lines.append("")
    n_cross = sum(1 for m in matches if m[10] == 0)
    n_sources = len({m[5] for m in matches})
    lines.append(
        f"{len(matches)} match(es) where this paper is the chronologically later side, "
        f"from {n_sources} distinct earlier paper(s) ({n_cross} cross-author, {len(matches) - n_cross} same-author)."
    )
    lines.append("=" * 100)

    for i, m in enumerate(matches, 1):
        (dupe_id, paper1, paper2, para1, para2, earlier_id, later_id,
         sim, lcs, ngj, same_author, cites, reason) = m
        earlier_title, earlier_year = conn.execute("SELECT title, year FROM papers WHERE id = ?", (earlier_id,)).fetchone()
        earlier_link = paper_link(conn, earlier_id)

        # figure out which side (paragraph_id_1/2) belongs to the earlier vs later paper
        if paper1 == earlier_id:
            earlier_para_id, later_para_id = para1, para2
        else:
            earlier_para_id, later_para_id = para2, para1
        earlier_para_id_paper, earlier_idx, earlier_text = conn.execute(
            "SELECT paper_id, para_index, text FROM paragraphs WHERE id = ?", (earlier_para_id,)
        ).fetchone()
        later_para_id_paper, later_idx, later_text = conn.execute(
            "SELECT paper_id, para_index, text FROM paragraphs WHERE id = ?", (later_para_id,)
        ).fetchone()
        earlier_range, earlier_full = expand_paragraph(conn, earlier_para_id_paper, earlier_idx, earlier_text)
        later_range, later_full = expand_paragraph(conn, later_para_id_paper, later_idx, later_text)

        lines.append("")
        lines.append(f"--- Match {i}/{len(matches)} (potential_dupes id={dupe_id}) ---")
        lines.append(f"Earlier/source paper: {earlier_title} ({earlier_year})")
        lines.append(f"Link: {earlier_link}")
        lines.append(
            f"similarity={sim:.3f}  lcs_ratio={lcs if lcs is not None else 'n/a'}  "
            f"ngram_jaccard={ngj if ngj is not None else 'n/a'}  same_author={bool(same_author)}  "
            f"later_cites_earlier={cites}"
        )
        lines.append(f"AI note: {reason}")
        lines.append("")
        lines.append(f"  [EARLIER, ¶{earlier_range}] (matched paragraph was ¶{earlier_idx}): {earlier_full}")
        lines.append("")
        lines.append(f"  [LATER,   ¶{later_range}] (matched paragraph was ¶{later_idx}): {later_full}")
        lines.append("-" * 100)

    out_path = out_dir / f"{later_paper_id}-{slugify(title)}.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path, len(matches), n_cross


def parse_args():
    parser = argparse.ArgumentParser(description="Write per-paper suspected-duplication text reports.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--out-dir", type=Path, default=Path("dupe_reports"))
    parser.add_argument("--min-count", type=int, default=DEFAULT_MIN_COUNT,
                         help="minimum ai_check=yes candidate count to get a report, "
                              "regardless of count for any paper with a cross-author candidate "
                              f"(default {DEFAULT_MIN_COUNT})")
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    targets = target_papers(conn, args.min_count)
    print(f"{len(targets)} paper(s) qualify for a report (>= {args.min_count} candidates, or any cross-author)")

    index_lines = ["Suspected-duplication reports, ranked by candidate count", "=" * 60, ""]
    for later_id in targets:
        out_path, n, n_cross = write_report(conn, later_id, args.out_dir)
        flag = f"  <-- {n_cross} CROSS-AUTHOR" if n_cross else ""
        print(f"  wrote {out_path} (n={n}{flag})")
        index_lines.append(f"n={n:3d}{' [CROSS-AUTHOR]' if n_cross else '':16s} {out_path.name}")

    (args.out_dir / "_index.txt").write_text("\n".join(index_lines), encoding="utf-8")
    conn.close()


if __name__ == "__main__":
    main()

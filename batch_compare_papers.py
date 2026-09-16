#!/usr/bin/env python3
"""Batch sibling of compare_two_papers.py / write_dupe_reports_html.py: runs the full
two-method comparison (exact word-shingle matching + sentence-level semantic/paraphrase
matching -- see compare_two_papers.py's own module docstring for why both, not just one)
across every paper pair a potential_dupes selection resolves to, writing one plain-text
report per pair -- for a subagent (or a human at a terminal) to read directly with
cat/Read, no HTML/CSS to parse.

Exists because REVIEWING.md already makes running compare_two_papers.py on a pair
mandatory before any `d` verdict, and dispatching many review subagents who each
re-run it per candidate they narrow down to -- each separately loading the
sentence-transformers model and re-deriving the same comparison -- is the expensive,
repeated part of that workflow. This script does it once, up front, loading the model a
single time, so a review subagent reads a pre-computed report instead of re-deriving it.
Not a replacement for compare_two_papers.py itself (still the right tool for a single
ad-hoc pair, or for --append-to-writeup once a case is confirmed) -- this is for working
through a large backlog of pairs at once.

Shares its pair-selection/grouping and three noise filters with write_dupe_reports_html.py
(same --status/--ids/--paper-ids, --min-shingle-matches/--skip-identical-titles/
--min-year-gap semantics and defaults, same --shingle-size/--max-shingle-exhibits/--x-drop/
--mismatch-penalty naming) so the two scripts stay predictable siblings -- see that
module's docstring for why each filter defaults the way it does. The sentence-level
method's own flags (--model/--min-similarity/--min-lcs-ratio/--top-n/--skip-sentences)
match compare_two_papers.py's naming instead, for the same reason.

Each output file's header lists every potential_dupes id between the pair, the two papers'
titles/years/authors/DOIs, and the exact `review_dupes.py --agent-verdict <id> <key>`
command shape -- so a reader can commit a verdict without a separate database query. A
`_index.txt` alongside the per-pair files ranks every pair by shingle-match count (the
strongest single "worth a close look" signal, per text_overlap.py's own docstring) so a
lead or a subagent knows where to start, and makes it easy to split the file list across
several review subagents by chunking that ranked list rather than partitioning
potential_dupes ids directly.

Usage:
    python3 batch_compare_papers.py --library-db computer-ethics/library.sqlite3 \\
        --status confirmed --out-dir computer-ethics/dupe_comparisons
    python3 batch_compare_papers.py --library-db computer-ethics/library.sqlite3 \\
        --ids 6463,6464,6466,6467 --out-dir computer-ethics/dupe_comparisons --skip-sentences
"""

import argparse
import sqlite3
from pathlib import Path

import db
import text_overlap as to
from compare_two_papers import (
    DEFAULT_MODEL,
    embed_sentences,
    find_matches,
    find_shingle_matches,
    load_model,
    load_paper_sentences,
    load_paper_words,
)
from write_dupe_reports import slugify

# Matches write_dupe_reports_html.py's own default -- NOT compare_two_papers.py's default of
# 6, kept deliberately consistent with this script's HTML sibling rather than its single-pair
# ancestor, since --min-shingle-matches (below) is tuned against this size.
DEFAULT_SHINGLE_SIZE = 10
DEFAULT_MIN_SHINGLE_MATCHES = 10
DEFAULT_MAX_SHINGLE_EXHIBITS = 300  # generous -- a plain-text line costs nothing like an HTML
# exhibit's styling/highlighting markup does, so there's less reason to cap this tightly.
DEFAULT_MIN_SENTENCE_SIMILARITY = 0.6  # matches compare_two_papers.py's own default
DEFAULT_TOP_N_SENTENCES = 200  # matches compare_two_papers.py's own default


def get_words(conn, paper_id, cache):
    """Cached load_paper_words() -- a paper that recurs across many pairs in one batch run
    (a template/boilerplate document, or a large anthology matched against several of its
    own chapters -- both real, common patterns in this corpus) would otherwise have its
    words re-extracted from paragraphs on every pair. `cache` is a plain dict the caller
    owns and passes in, so it persists across the whole batch run, not just one pair."""
    if paper_id not in cache:
        cache[paper_id] = load_paper_words(conn, paper_id)
    return cache[paper_id]


def get_sentence_vectors(conn, paper_id, model, cache):
    """Cached (load_paper_sentences() + embed_sentences()) pair -- same rationale as
    get_words() above, but this one matters much more: embedding is the expensive step, and
    a recurring paper (one appeared in 20 different pairs in this corpus's own
    agent_decided_unsure backlog) would otherwise get its full sentence set re-embedded once
    per pair it's part of."""
    if paper_id not in cache:
        sentences = load_paper_sentences(conn, paper_id)
        cache[paper_id] = (sentences, embed_sentences(model, sentences))
    return cache[paper_id]


def paper_meta(conn, paper_id):
    row = conn.execute("SELECT title, year, doi FROM papers WHERE id=?", (paper_id,)).fetchone()
    authors = [r[0] for r in conn.execute(
        """SELECT a.name FROM authors a JOIN paper_authors pa ON pa.author_id = a.id
           WHERE pa.paper_id = ? ORDER BY pa.author_order""",
        (paper_id,),
    ).fetchall()]
    return {"title": row[0], "year": row[1], "doi": row[2], "authors": authors}


def render_pair_report(conn, pid1, pid2, dupe_rows, model, shingle_size, max_shingle_exhibits,
                        x_drop, mismatch_penalty, skip_sentences, min_sentence_similarity,
                        min_lcs_ratio, top_n_sentences, word_cache, sentence_cache,
                        min_shingle_matches):
    """Returns (slug, report_text, shingle_total), or (None, None, shingle_total) if the pair
    doesn't clear `min_shingle_matches` -- checked immediately after the (cheap) shingle scan
    and BEFORE the (expensive, one sentence-transformers encode() call per paper) sentence-
    level pass, so a filtered-out pair never pays for work its report will never use. `dupe_rows`
    (may be empty -- a pair with no potential_dupes row at all is still a valid --paper-ids
    request) is used only for the header's candidate-passage summary and to recover
    same_author/chronology, exactly like write_dupe_reports_html.py's render_case() -- the
    actual exhibited evidence always comes fresh from the two independent whole-document
    methods below, not from these rows."""
    words_1 = get_words(conn, pid1, word_cache)
    words_2 = get_words(conn, pid2, word_cache)
    shingle_runs = find_shingle_matches(words_1, words_2, shingle_size=shingle_size,
                                        mismatch_penalty=mismatch_penalty, x_drop=x_drop)
    shingle_total = len(shingle_runs)
    if shingle_total < min_shingle_matches:
        return None, None, shingle_total
    shown_shingles = shingle_runs[:max_shingle_exhibits]

    m1, m2 = paper_meta(conn, pid1), paper_meta(conn, pid2)
    earlier_id = next((r["earlier_paper_id"] for r in dupe_rows if r["earlier_paper_id"]), None)
    if earlier_id is None and m1["year"] and m2["year"] and m1["year"] != m2["year"]:
        earlier_id = pid1 if m1["year"] < m2["year"] else pid2
    same_author = next((r["same_author"] for r in dupe_rows if r["same_author"] is not None), None)

    lines = [
        f"PAPER PAIR: {pid1} vs {pid2}",
        f"  ({pid1}) {m1['title']}  [{m1['year'] or 'n.d.'}]",
        f"        authors: {', '.join(m1['authors']) or '(none on file)'}",
        f"        doi: {m1['doi'] or '(none on file)'}",
        f"  ({pid2}) {m2['title']}  [{m2['year'] or 'n.d.'}]",
        f"        authors: {', '.join(m2['authors']) or '(none on file)'}",
        f"        doi: {m2['doi'] or '(none on file)'}",
        f"  chronology: {f'{earlier_id} is earlier/source' if earlier_id else 'unknown (years equal or missing)'}",
        f"  same_author (per DB -- verify against the actual PDFs before trusting this, see below): {same_author}",
    ]
    ids = sorted(r["id"] for r in dupe_rows)
    if ids:
        sims = [r["similarity"] for r in dupe_rows]
        lines.append(f"  potential_dupes ids ({len(ids)}): {', '.join(str(i) for i in ids)}")
        lines.append(f"  candidate passages: {len(dupe_rows)}  similarity range: "
                      f"{min(sims):.3f}-{max(sims):.3f}")
    else:
        lines.append("  potential_dupes ids: none (found by title-bucket matching, not the "
                      "embedding pipeline -- see find_title_bucket_dupes.py)")
    lines += [
        "",
        "Apply a verdict once you've decided (see REVIEWING.md for the full judgment framework):",
        "  .venv/bin/python3 review_dupes.py --library-db <corpus>/library.sqlite3 "
        "--agent-verdict <id-from-the-list-above> <d|f|b|c|p|a|u>",
        "",
        "Non-negotiable before calling d (REVIEWING.md's \"Agent review mode\"):",
        "  1. pdftotext BOTH source PDFs and read the actual bylines yourself -- never trust",
        "     same_author from the DB alone (name-format/order mismatches are a known, common gap).",
        "  2. Check the citations table AND a full-text search of the later PDF for whether it",
        "     cites the earlier one.",
        "  3. Base the verdict on the whole-document comparison below, not one paragraph.",
        "If either source PDF is missing on disk (common in this corpus, check papers.file_path),",
        "mark u immediately rather than guessing -- don't spend time hunting for a workaround.",
        "",
        "=" * 100,
        f"METHOD 1: exact {shingle_size}-word shingle matches"
        + (f", x-drop {x_drop} (mismatch penalty {mismatch_penalty})" if x_drop is not None else ""),
        f"{len(shown_shingles)} run(s) shown (of {shingle_total} total found), sorted by match "
        f"length descending",
        "=" * 100,
    ]
    for length, para_a, para_b, text_a, text_b, _start_a, _end_a, _start_b, _end_b, subs in shown_shingles:
        lines.append("")
        lines.append(f"near-exact match, {length} word(s), {subs} substitution(s)" if subs
                     else f"exact match, {length} word(s)")
        lines.append(f"  A (paper {pid1}) ¶{para_a}: {text_a}")
        lines.append(f"  B (paper {pid2}) ¶{para_b}: {text_b}")
        lines.append("-" * 100)

    if not skip_sentences:
        sentences_1, vectors_1 = get_sentence_vectors(conn, pid1, model, sentence_cache)
        sentences_2, vectors_2 = get_sentence_vectors(conn, pid2, model, sentence_cache)
        raw_matches = find_matches(sentences_1, vectors_1, sentences_2, vectors_2, min_sentence_similarity)
        scored = []
        for sim, sent_a, sent_b in raw_matches:
            lcs = to.longest_common_word_run(sent_a[2], sent_b[2])
            if lcs < min_lcs_ratio:
                continue
            ngram = to.ngram_jaccard(sent_a[2], sent_b[2])
            scored.append((lcs, ngram, sim, sent_a, sent_b))
        scored.sort(key=lambda r: r[0], reverse=True)
        scored = scored[:top_n_sentences]

        lines += [
            "",
            "=" * 100,
            "METHOD 2: sentence-level semantic/paraphrase matches",
            f"{len(scored)} match(es) shown (of {len(raw_matches)} above min-similarity="
            f"{min_sentence_similarity}), sorted by lcs_ratio descending",
            "=" * 100,
        ]
        for lcs, ngram, sim, sent_a, sent_b in scored:
            para_a, _sidx_a, text_a, _full_a = sent_a
            para_b, _sidx_b, text_b, _full_b = sent_b
            lines.append("")
            lines.append(f"lcs_ratio={lcs:.3f}  ngram_jaccard={ngram:.3f}  cosine={sim:.3f}")
            lines.append(f"  A (paper {pid1}) ¶{para_a}: {text_a}")
            lines.append(f"  B (paper {pid2}) ¶{para_b}: {text_b}")
            lines.append("-" * 100)

    slug = f"{slugify(m1['title'])}-vs-{slugify(m2['title'])}-{shingle_total}-shingle-match" \
           f"{'es' if shingle_total != 1 else ''}"
    return slug, "\n".join(lines), shingle_total


def parse_args():
    parser = argparse.ArgumentParser(description="Batch-generate plain-text two-paper comparison "
                                                  "reports, one per paper pair, for agent/human review.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--out-dir", type=Path, default=Path("dupe_comparisons"))
    parser.add_argument("--status", default="confirmed", help="potential_dupes.status to include (default confirmed)")
    parser.add_argument("--ids", help="comma-separated potential_dupes.id list, overrides --status")
    parser.add_argument("--paper-ids", help="'A,B' paper_id pair with no potential_dupes lookup at all; "
                                             "overrides --ids/--status")
    parser.add_argument("--shingle-size", type=int, default=DEFAULT_SHINGLE_SIZE,
                         help=f"word n-gram size for the exact-shingle scan (default {DEFAULT_SHINGLE_SIZE})")
    parser.add_argument("--max-shingle-exhibits", type=int, default=DEFAULT_MAX_SHINGLE_EXHIBITS,
                         help=f"cap the number of shingle-match exhibits written per pair, keeping "
                              f"the longest ones (default {DEFAULT_MAX_SHINGLE_EXHIBITS})")
    parser.add_argument("--x-drop", type=int, default=None,
                         help="enable seed-and-extend tolerance for isolated word substitutions -- "
                              "see compare_two_papers.py's find_shingle_matches() docstring. Default "
                              "(None) preserves exact-only matching.")
    parser.add_argument("--mismatch-penalty", type=int, default=1,
                         help="score subtracted per mismatched word during --x-drop extension; only "
                              "meaningful when --x-drop is set")
    parser.add_argument("--min-shingle-matches", type=int, default=DEFAULT_MIN_SHINGLE_MATCHES,
                         help=f"skip a pair entirely (no file, no sentence-level pass either) if its "
                              f"exact word-shingle scan found fewer than this many runs (default "
                              f"{DEFAULT_MIN_SHINGLE_MATCHES}); pass 0 to write every pair regardless")
    parser.add_argument("--skip-identical-titles", action="store_true",
                         help="skip a pair entirely when the two papers' titles are exactly identical "
                              "after stripping whitespace -- see write_dupe_reports_html.py's flag of "
                              "the same name for the rationale. Off by default.")
    parser.add_argument("--min-year-gap", type=int, default=0,
                         help="skip a pair unless the two papers' years differ by at least this many "
                              "years (default 0 -- no effect). See write_dupe_reports_html.py's flag "
                              "of the same name for why this does NOT default to skipping same-year "
                              "pairs.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="sentence-transformers model for METHOD 2")
    parser.add_argument("--min-similarity", type=float, default=DEFAULT_MIN_SENTENCE_SIMILARITY,
                         help=f"cosine similarity floor for METHOD 2's first-pass filter (default "
                              f"{DEFAULT_MIN_SENTENCE_SIMILARITY} -- see compare_two_papers.py's own "
                              f"flag of the same name)")
    parser.add_argument("--min-lcs-ratio", type=float, default=0.0,
                         help="only show METHOD 2 results at or above this lcs_ratio (default 0)")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N_SENTENCES,
                         help=f"cap the number of METHOD 2 results written per pair (default "
                              f"{DEFAULT_TOP_N_SENTENCES})")
    parser.add_argument("--skip-sentences", action="store_true",
                         help="run only METHOD 1 (shingles) -- skips loading the sentence-transformers "
                              "model entirely, much faster across a large batch at the cost of missing "
                              "paraphrase-only cases (e.g. this corpus's own Taro/Saxby case, which has "
                              "zero exact shingle matches -- see todo.md/CLAUDE.md)")
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)
    conn.row_factory = sqlite3.Row
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.paper_ids:
        pid1, pid2 = (int(x) for x in args.paper_ids.split(","))
        groups = {tuple(sorted((pid1, pid2))): []}
    else:
        if args.ids:
            ids = [int(x) for x in args.ids.split(",")]
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(f"SELECT * FROM potential_dupes WHERE id IN ({placeholders})", ids).fetchall()
        else:
            rows = conn.execute("SELECT * FROM potential_dupes WHERE status=?", (args.status,)).fetchall()
        groups = {}
        for r in rows:
            key = tuple(sorted((r["paper_id_1"], r["paper_id_2"])))
            groups.setdefault(key, []).append(r)

    print(f"{len(groups)} paper pair(s) to consider")

    model = None
    if not args.skip_sentences:
        print(f"loading {args.model}...")
        model = load_model(args.model)

    skipped_titles = skipped_years = skipped_shingles = 0
    word_cache = {}
    sentence_cache = {}
    index_rows = []  # (shingle_total, slug, pid1, pid2, n_ids)
    for (pid1, pid2), group_rows in groups.items():
        if args.skip_identical_titles:
            t1, t2 = (conn.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone()[0]
                      for pid in (pid1, pid2))
            if t1.strip() == t2.strip():
                skipped_titles += 1
                print(f"  skipped {pid1}/{pid2} (identical titles: {t1!r})")
                continue
        if args.min_year_gap:
            y1, y2 = (conn.execute("SELECT year FROM papers WHERE id=?", (pid,)).fetchone()[0]
                      for pid in (pid1, pid2))
            if y1 is not None and y2 is not None and abs(y1 - y2) < args.min_year_gap:
                skipped_years += 1
                print(f"  skipped {pid1}/{pid2} (years {y1}/{y2}, below --min-year-gap {args.min_year_gap})")
                continue

        slug, report, shingle_total = render_pair_report(
            conn, pid1, pid2, group_rows, model,
            shingle_size=args.shingle_size, max_shingle_exhibits=args.max_shingle_exhibits,
            x_drop=args.x_drop, mismatch_penalty=args.mismatch_penalty,
            skip_sentences=args.skip_sentences, min_sentence_similarity=args.min_similarity,
            min_lcs_ratio=args.min_lcs_ratio, top_n_sentences=args.top_n,
            word_cache=word_cache, sentence_cache=sentence_cache,
            min_shingle_matches=args.min_shingle_matches,
        )
        if slug is None:
            skipped_shingles += 1
            print(f"  skipped {pid1}/{pid2} ({shingle_total} shingle matches, below "
                  f"--min-shingle-matches {args.min_shingle_matches})")
            continue

        out_path = args.out_dir / f"{slug}.txt"
        out_path.write_text(report, encoding="utf-8")
        print(f"  wrote {out_path} ({len(group_rows)} passage(s), {shingle_total} shingle match(es))")
        index_rows.append((shingle_total, out_path.name, pid1, pid2, len(group_rows)))

    index_rows.sort(key=lambda r: r[0], reverse=True)
    index_lines = [f"{len(index_rows)} pair(s) written, ranked by exact-shingle-match count "
                   f"(strongest single 'worth a close look' signal -- see text_overlap.py):", ""]
    for shingle_total, filename, pid1, pid2, n_ids in index_rows:
        index_lines.append(f"n_shingles={shingle_total:<6} candidates={n_ids:<4} "
                            f"{pid1}/{pid2}  {filename}")
    (args.out_dir / "_index.txt").write_text("\n".join(index_lines), encoding="utf-8")
    print(f"\nwrote {args.out_dir / '_index.txt'}")

    if skipped_titles:
        print(f"{skipped_titles} pair(s) skipped (identical titles)")
    if skipped_years:
        print(f"{skipped_years} pair(s) skipped (below --min-year-gap {args.min_year_gap})")
    if skipped_shingles:
        print(f"{skipped_shingles} pair(s) skipped (below --min-shingle-matches {args.min_shingle_matches})")

    conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Write one self-contained HTML evidence page per confirmed-duplicate paper
pair, ready to publish (e.g. as a Claude Artifact).

Sibling to write_dupe_reports.py (which writes plain-text reports off the
ai_check='yes' pre-filter pass) -- this one is aimed at hand-reviewed
candidates instead: by default every potential_dupes row with
status='confirmed', grouped by paper pair, so a paper pair with several
matched passages becomes one page instead of several. Reuses
write_dupe_reports.py's expand_paragraph()/slugify() (same paragraph-
stitching heuristic) and review_dupes.py's WORD_RE tokenizer (same
word-diff boundaries used to render a shingle match's highlighted span).

The potential_dupes rows themselves are used only to find and group paper pairs and to summarize
scale (passage count, similarity range, median LCS) in the header -- they are NOT rendered as their
own "Passage N of M" exhibits (2026-09-05: dropped after review found the raw embedding-similarity
passage pairs useful for grouping candidates but not convincing as evidence on their own -- a cosine-
similar paragraph pair can still just be similar wording, not a copy). The actual exhibits are all
sourced from the second, independent method below.

Runs (2026-09-04, on by default, see --shingle-size/--no-shingles) an exact word-shingle scan
across each pair's complete text via compare_two_papers.py's find_shingle_matches(), and renders
those as this page's exhibits -- it finds verbatim spans the LSH+cosine candidate pipeline never
generated a row for at all (too short, or split across a boundary the embedding-level check never
saw as one paragraph), the same way compare_two_papers.py's own combined report does for an ad-hoc
two-paper comparison, and an exact multi-word run is a far more convincing "this was copied" signal
than embedding similarity alone. With --no-shingles, a page has no exhibits at all (just the header/
summary stats) -- there is no other exhibit source left to fall back to.

Output is deliberately a HTML *fragment*, not a standalone document: no
<!DOCTYPE>/<html>/<head>/<body> wrapper, just a <title>, an inline <style>,
and the body content -- Claude Artifacts wraps a page in that skeleton at
publish time and errors if the file supplies its own, and this was written
to be published that way. Opening a fragment file directly in a browser
still renders it (browsers tolerate a missing wrapper), just without the
<head>'s meta/viewport tags an Artifact publish adds for you.

    python3 write_dupe_reports_html.py --status confirmed --out-dir dupe_reports_html
    python3 write_dupe_reports_html.py --ids 6463,6464,6466,6467 --out-dir dupe_reports_html
"""

import argparse
import html
import sqlite3
from pathlib import Path

import db
import text_overlap as to
from compare_two_papers import find_shingle_matches, load_paper_words
from review_dupes import WORD_RE
from write_dupe_reports import slugify

DEFAULT_SHINGLE_SIZE = 10  # see compare_two_papers.py's find_shingle_matches() docstring for why
# this is a genuinely different, complementary method to the potential_dupes-sourced exhibits
# above -- it can't be fooled by a sentence-split error and catches sub-sentence verbatim spans,
# but it also finds nothing this table doesn't already contain if every real match was already
# above the LSH+cosine candidate-generation threshold; run alongside, not instead of.
DEFAULT_MAX_SHINGLE_EXHIBITS = 50
DEFAULT_MIN_SHINGLE_MATCHES = 10  # below this, a pair is skipped entirely (no file written) --
# see --min-shingle-matches. Cheap noise filter: a pair whose only evidence is embedding
# candidates plus a couple of short/no exact shingle runs isn't worth its own page by default;
# --min-shingle-matches 0 (or any --ids/--paper-ids single-pair run you care about regardless,
# e.g. a case like Taro/Saxby that was confirmed on lcs_ratio rather than shingles) overrides.


def _find_token_span(tokens, needle_words):
    """Locate a contiguous run within `tokens` (as produced by WORD_RE.findall() -- each token
    may carry trailing whitespace) whose stripped/lowercased content matches `needle_words` (a
    plain word list, e.g. a known exact match's own text.split()). Returns (start, end) [end
    exclusive] token-index span, or None if not found. None is a real, expected outcome (not
    just a defensive fallback): this compares across two different tokenizers --
    compare_two_papers.py's bare \\S+ (what found the match in the first place) vs. this
    module's WORD_RE (\\S+\\s*|\\s+, used for the diff itself) -- which could in principle
    disagree on an edge case neither has been tested against; a caller should treat None as
    "can't confirm exactly where this sits, skip the extra highlight" rather than guess."""
    if not needle_words:
        return None
    stripped = [t.strip().lower() for t in tokens]
    needle = [w.lower() for w in needle_words]
    n = len(needle)
    for i in range(len(stripped) - n + 1):
        if stripped[i:i + n] == needle:
            return i, i + n
    return None


def render_context_with_match(text, match_words, diff_indices=None):
    """Render `text` plainly, except for the exact span matching `match_words` (if found), which
    gets wrapped in <mark class="match"> (yellow). Deliberately does NOT diff against a second
    text: a shingle exhibit's surrounding context is each side's OWN independent paragraph
    neighborhood, not a second aligned passage worth comparing word-for-word -- the two sides'
    context has no reason to correspond to each other outside the exact-match span itself, so
    running a real diff across all of it just colors most of the unrelated surrounding text
    red/green (confirmed for real, back when this page still rendered potential_dupes-sourced
    passages that way -- see the module docstring) instead of showing the one thing this exhibit
    is about.

    `diff_indices` (added alongside X-drop substitution support): a set of positions *within the
    matched span* (0-based, aligned the same way on both sides since X-drop only bridges
    same-position substitutions, never an indel -- see find_shingle_matches()'s docstring) whose
    word differs from the other side's word at that position. Those specific words get
    <mark class="match-diff"> (red) instead of the ordinary <mark class="match"> (yellow) the
    rest of the span gets, so a near-exact run visibly shows exactly which word was swapped
    rather than just reporting a "substitutions: N" count in the metrics line. None/empty means
    every matched word renders as an ordinary (non-diff) match, same as before this existed."""
    tokens = WORD_RE.findall(text)
    span = _find_token_span(tokens, match_words) if match_words else None
    if not span:
        return html.escape("".join(tokens))
    start, end = span
    before = html.escape("".join(tokens[:start]))
    after = html.escape("".join(tokens[end:]))
    diff_indices = diff_indices or set()

    # Group the matched span into consecutive same-class runs (ordinary match vs. diff word)
    # rather than wrapping every single word in its own <mark> -- purely cosmetic (fewer,
    # larger highlighted chunks read better than a word-by-word checkerboard).
    matched_tokens = tokens[start:end]
    runs = []
    current_class = None
    current_chunk = []
    for i, tok in enumerate(matched_tokens):
        cls = "match-diff" if i in diff_indices else "match"
        if cls != current_class:
            if current_chunk:
                runs.append((current_class, current_chunk))
            current_class, current_chunk = cls, []
        current_chunk.append(tok)
    if current_chunk:
        runs.append((current_class, current_chunk))
    matched_html = "".join(
        f'<mark class="{cls}">{html.escape("".join(chunk))}</mark>' for cls, chunk in runs
    )
    return f'{before}{matched_html}{after}'


STYLE = """
:root {
  --bg: #f2f4ef; --surface: #ffffff; --surface-2: #eceee7;
  --ink: #1a1f1a; --ink-muted: #565f57; --border: #d7dbd0;
  --accent: #a8641f; --accent-strong: #7c4a15;
  --diff-a: #b6472f; --diff-a-bg: #fbe9e4;
  --diff-b: #2f7a4a; --diff-b-bg: #e6f3e9;
  --match-ink: #6b5600; --match-bg: #fdeaa0;
  --match-diff-ink: #7a1710; --match-diff-bg: #f6c9c0;
  --font-display: "Source Serif 4", Georgia, "Times New Roman", serif;
  --font-body: "Source Sans 3", -apple-system, "Segoe UI", sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14170f; --surface: #1c2016; --surface-2: #242a1c;
    --ink: #e9ede4; --ink-muted: #a2ab98; --border: #333a29;
    --accent: #d99a4e; --accent-strong: #f0b46a;
    --diff-a: #e2897a; --diff-a-bg: #3a221e;
    --diff-b: #83c99a; --diff-b-bg: #1c3323;
    --match-ink: #f0d878; --match-bg: #4a3d10;
    --match-diff-ink: #f5b3a4; --match-diff-bg: #4a1e17;
  }
}
:root[data-theme="dark"] {
  --bg: #14170f; --surface: #1c2016; --surface-2: #242a1c;
  --ink: #e9ede4; --ink-muted: #a2ab98; --border: #333a29;
  --accent: #d99a4e; --accent-strong: #f0b46a;
  --diff-a: #e2897a; --diff-a-bg: #3a221e;
  --diff-b: #83c99a; --diff-b-bg: #1c3323;
  --match-ink: #f0d878; --match-bg: #4a3d10;
  --match-diff-ink: #f5b3a4; --match-diff-bg: #4a1e17;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--font-body); line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.page { max-width: 1180px; margin: 0 auto; padding: 4rem 1.5rem 6rem; }
.lede { max-width: 760px; margin: 0 auto; }
.eyebrow {
  font-family: var(--font-mono); font-size: 0.72rem; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--accent-strong); margin: 0 0 0.9rem;
}
.classification {
  display: inline-block; font-family: var(--font-mono); font-size: 0.72rem;
  letter-spacing: 0.1em; text-transform: uppercase; color: var(--accent);
  background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px;
  padding: 0.3rem 0.85rem; margin: 0 0 1.1rem;
}
h1 {
  font-family: var(--font-display); font-weight: 600; font-size: clamp(1.9rem, 4vw, 2.6rem);
  line-height: 1.15; margin: 0 0 1rem; text-wrap: balance;
}
.dek {
  font-size: 1.05rem; color: var(--ink-muted); max-width: 62ch; margin: 0 0 2.5rem;
}
.dek a { color: var(--accent-strong); }
.source-note {
  font-size: 0.85rem; color: var(--ink-muted); margin: 0.9rem 0 2.5rem;
  border-left: 2px solid var(--border); padding-left: 0.9rem;
}
.source-note a { color: var(--accent-strong); }

.comparison {
  display: grid; grid-template-columns: 1fr auto 1fr; gap: 1.1rem; align-items: stretch;
  margin-bottom: 2.75rem;
}
@media (max-width: 640px) { .comparison { grid-template-columns: 1fr; } .connector { display: none; } }
.paper-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 3px;
  padding: 1.25rem 1.35rem;
}
.paper-card .role {
  font-family: var(--font-mono); font-size: 0.68rem; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--ink-muted); margin: 0 0 0.55rem;
}
.paper-card .role.later { color: var(--diff-a); }
.paper-card h2 {
  font-family: var(--font-display); font-weight: 600; font-size: 1.08rem;
  line-height: 1.3; margin: 0 0 0.5rem; text-wrap: balance;
}
.paper-card .meta {
  font-size: 0.85rem; color: var(--ink-muted); margin: 0 0 0.35rem;
}
.paper-card a.doi {
  font-family: var(--font-mono); font-size: 0.78rem; color: var(--accent-strong);
  word-break: break-all;
}
.connector {
  display: flex; align-items: center; justify-content: center;
  font-family: var(--font-mono); color: var(--ink-muted); font-size: 1.3rem;
}

.summary-bar {
  display: flex; flex-wrap: wrap; gap: 0.5rem 1.6rem; align-items: baseline;
  font-family: var(--font-mono); font-size: 0.85rem; color: var(--ink-muted);
  border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
  padding: 0.9rem 0; margin-bottom: 2.75rem;
}
.summary-bar strong { color: var(--ink); font-variant-numeric: tabular-nums; font-weight: 600; }

.actions {
  margin-bottom: 2.75rem;
}
.actions .actions-label {
  font-family: var(--font-mono); font-size: 0.68rem; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--ink-muted); margin: 0 0 0.6rem;
}
.actions pre {
  background: var(--surface-2); border: 1px solid var(--border); border-radius: 3px;
  padding: 0.7rem 0.9rem; margin: 0 0 0.6rem; overflow-x: auto;
  font-family: var(--font-mono); font-size: 0.8rem; line-height: 1.5;
}
.actions pre .comment { color: var(--ink-muted); }

.exhibit { margin-bottom: 2.75rem; }
.exhibit-head {
  display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap;
  gap: 0.4rem 1.2rem; margin-bottom: 0.9rem; max-width: 760px; margin-inline: auto;
}
.exhibit-head h3 {
  font-family: var(--font-display); font-size: 1.05rem; font-weight: 600; margin: 0;
}
.metrics {
  font-family: var(--font-mono); font-size: 0.78rem; color: var(--ink-muted);
  display: flex; gap: 1rem; font-variant-numeric: tabular-nums;
}
.metrics b { color: var(--ink); font-weight: 600; }

.passage-pair {
  display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; align-items: start;
}
@media (max-width: 760px) { .passage-pair { grid-template-columns: 1fr; } }
.passage {
  background: var(--surface); border: 1px solid var(--border); border-radius: 3px;
  padding: 1.1rem 1.25rem; min-width: 0;
}
.passage .label {
  font-family: var(--font-mono); font-size: 0.7rem; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--ink-muted); margin: 0 0 0.5rem;
  display: flex; justify-content: space-between; gap: 1rem;
}
.passage .label .idx { font-variant-numeric: tabular-nums; }
.passage p { margin: 0; font-size: 0.97rem; overflow-wrap: break-word; }
.passage.earlier { border-top: 3px solid var(--diff-a); }
.passage.later { border-top: 3px solid var(--diff-b); }
mark.match { background: var(--match-bg); color: var(--match-ink); padding: 0.03em 0.1em; border-radius: 2px; font-weight: 600; }
mark.match-diff { background: var(--match-diff-bg); color: var(--match-diff-ink); padding: 0.03em 0.1em; border-radius: 2px; font-weight: 600; text-decoration: underline; text-decoration-thickness: 0.08em; }

footer {
  margin-top: 3.5rem; padding-top: 1.5rem; border-top: 1px solid var(--border);
  font-size: 0.78rem; color: var(--ink-muted); font-family: var(--font-mono);
}
"""


CONTEXT_PARAGRAPHS = 2  # paragraphs of extra context pulled in on each side of a shingle match,
# unconditionally -- unlike expand_paragraph()'s conditional stitching above (which only pulls in
# a neighbor when the text looks cut off mid-sentence), a shingle match's own reported boundaries
# are already exact and complete; this is purely so a human sees more of the surrounding
# paragraph(s) to judge the match in context, per the direct ask that led to this function.


def expand_context(conn, paper_id, start_para, end_para, context=CONTEXT_PARAGRAPHS):
    """Fetch every paragraph of `paper_id` from start_para-context through end_para+context
    (clamped to whatever actually exists -- a BETWEEN range tolerates gaps, e.g. from
    extract_papers.py's own exact-repeat dedup, without special-casing them), joined the same
    way expand_paragraph() joins its own stitched pieces. Returns (index_range_str, joined_text),
    or (None, None) if nothing in that range exists at all."""
    rows = conn.execute(
        "SELECT para_index, text FROM paragraphs WHERE paper_id=? AND para_index BETWEEN ? AND ? "
        "ORDER BY para_index",
        (paper_id, start_para - context, end_para + context),
    ).fetchall()
    if not rows:
        return None, None
    lo, hi = rows[0][0], rows[-1][0]
    index_range = f"{lo}" if lo == hi else f"{lo}-{hi}"
    return index_range, " ".join(r[1] for r in rows)


def render_shingle_exhibits(conn, paper_id_1, paper_id_2, earlier_id, shingle_size, max_exhibits,
                             mismatch_penalty=1, x_drop=None):
    """This page's sole exhibit source (see the module docstring for why the potential_dupes-
    sourced candidates aren't rendered as their own exhibits any more): runs
    compare_two_papers.py's exact word-shingle matcher directly across the two papers' full text,
    rather than relying only on candidate rows that already exist. Catches verbatim spans the
    LSH+cosine candidate pipeline missed entirely (too short, or split across a sentence/paragraph
    boundary the embedding-level check never saw as one unit) -- see that module's own docstring
    for the full rationale. Returns (exhibit_html_list, total_runs_found).

    Each exhibit shows CONTEXT_PARAGRAPHS of extra surrounding text on both sides (not just the
    paragraph(s) the match itself falls in) with the exact matched span additionally marked
    (<mark class="match">, via render_context_with_match()) so it's visually distinct from the
    rest of a paragraph's ordinary shared wording -- added after the plain per-paragraph rendering
    made it hard to tell, in a longer passage, which specific run was the actual finding this
    exhibit exists to point at. `x_drop` (None disables, matching find_shingle_matches()'s own
    default) enables seed-and-extend tolerance for isolated word substitutions past each exact
    seed's boundary -- see that function's and _extend_xdrop()'s docstrings; a bridged exhibit's
    metrics line shows how many substitutions it contains."""
    words_1 = load_paper_words(conn, paper_id_1)
    words_2 = load_paper_words(conn, paper_id_2)
    runs = find_shingle_matches(words_1, words_2, shingle_size=shingle_size,
                                 mismatch_penalty=mismatch_penalty, x_drop=x_drop)
    shown = runs[:max_exhibits]

    # Selection above keeps the longest N runs (find_shingle_matches()'s own length-descending
    # order -- unchanged, and still what compare_two_papers.py's console/appendix output uses).
    # DISPLAY order is different: re-sorted into the LATER/flagged paper's own reading order
    # (start-of-match word position) rather than length-descending, so a multi-exhibit page reads
    # top-to-bottom the way the flagged paper does instead of jumping around between unrelated
    # passages. Falls back to paper_id_2's position when chronology is unknown (earlier_id is
    # None), matching the no-swap PARAGRAPH A/PARAGRAPH B convention below.
    later_is_paper_2 = (earlier_id != paper_id_2)
    shown = sorted(shown, key=lambda r: r[7] if later_is_paper_2 else r[5])

    exhibits = []
    for i, (length, para_1, para_2, text_1, text_2, start_1, end_1, start_2, end_2, subs) in enumerate(shown, 1):
        end_para_1 = words_1[end_1 - 1][1]
        end_para_2 = words_2[end_2 - 1][1]
        range1, full1 = expand_context(conn, paper_id_1, para_1, end_para_1)
        range2, full2 = expand_context(conn, paper_id_2, para_2, end_para_2)
        if full1 is None or full2 is None:
            continue
        pid_a, ra, fa, match_a = paper_id_1, range1, full1, text_1.split()
        pid_b, rb, fb, match_b = paper_id_2, range2, full2, text_2.split()
        if earlier_id == paper_id_2:
            (pid_a, ra, fa, match_a), (pid_b, rb, fb, match_b) = (pid_b, rb, fb, match_b), (pid_a, ra, fa, match_a)
        # Position-aligned regardless of the earlier/later swap above (swapping the pair doesn't
        # change which positions differ from each other) -- computed once, shared by both sides.
        diff_indices = {i for i, (wa, wb) in enumerate(zip(match_a, match_b)) if wa.lower() != wb.lower()}
        rendered_a = render_context_with_match(fa, match_a, diff_indices)
        rendered_b = render_context_with_match(fb, match_b, diff_indices)
        lcs = to.longest_common_word_run(fa, fb)
        ngj = to.ngram_jaccard(fa, fb)
        earlier_label = "EARLIER" if earlier_id else "PARAGRAPH A"
        later_label = "LATER" if earlier_id else "PARAGRAPH B"
        exhibits.append(f"""
<section class="exhibit">
  <div class="exhibit-head">
    <h3>Shingle match {i} of {len(shown)}</h3>
    <div class="metrics"><span>{"near-exact run" if subs else "exact run"} <b>{length} words</b></span>
      {f'<span>substitutions <b>{subs}</b></span>' if subs else ""}
      <span>LCS <b>{lcs:.2f}</b></span><span>5-gram Jaccard <b>{ngj:.2f}</b></span></div>
  </div>
  <div class="passage-pair">
    <div class="passage earlier"><p class="label"><span>{earlier_label}</span><span class="idx">&para;{ra}</span></p>
      <p>{rendered_a}</p></div>
    <div class="passage later"><p class="label"><span>{later_label}</span><span class="idx">&para;{rb}</span></p>
      <p>{rendered_b}</p></div>
  </div>
</section>""")
    return exhibits, len(runs)


def paper_authors(conn, paper_id):
    rows = conn.execute(
        """SELECT a.name FROM authors a
           JOIN paper_authors pa ON pa.author_id = a.id
           WHERE pa.paper_id = ? ORDER BY pa.author_order""",
        (paper_id,),
    ).fetchall()
    return [r[0] for r in rows]


def render_case(conn, paper_id_1, paper_id_2, dupe_rows, source_note=None, short_title=None,
                 shingle_size=DEFAULT_SHINGLE_SIZE, max_shingle_exhibits=DEFAULT_MAX_SHINGLE_EXHIBITS,
                 classification=None, mismatch_penalty=1, x_drop=None,
                 include_shingle_count_in_title=True, library_db_path=None):
    """dupe_rows: list of potential_dupes rows (sqlite3.Row) for this pair,
    already sorted the way they should display -- may be EMPTY: a pair found by
    find_title_bucket_dupes.py rather than the embedding-similarity pipeline can have zero
    potential_dupes rows at any threshold (the whole point of that tool -- see its own module
    docstring), and the header renders a different, honest dek/summary line for that case rather
    than crashing on an empty sims/lcs_vals list or claiming "0 candidate passages" as if the pair
    were weak. `short_title` is a 2-4 word product-style name for the <title> tag (a full "Paper A
    vs. Paper B" string is the right H1 but a bad browser-tab/gallery name) -- falls back to the
    earlier paper's own title, truncated, when not given. Either way, the shingle-match count gets
    appended both to the <title> tag (e.g. "Foo Bar (34 shingle matches)") and to the output
    filename/slug (e.g. "foo-vs-bar-34-shingle-matches.html") when `shingle_size` is set, so a
    directory or gallery of many pages can be triaged by scale without opening each one -- see
    `include_shingle_count_in_title` to turn both off. `dupe_rows` itself is used only to
    find/group this pair and to summarize scale (passage count, similarity range, median LCS) in
    the header -- see the module docstring for why those rows aren't rendered as exhibits.
    `shingle_size` (None disables) runs an exact word-shingle scan across the two papers' complete
    text and renders those as this page's exhibits -- see render_shingle_exhibits()'s docstring.
    `classification` (plain text, e.g. "Paper-mill pattern &middot; 1 of 22 in this set") renders as
    a badge above the eyebrow, first thing on the page -- an editorial judgment about what KIND of
    finding this is (e.g. computer-ethics/flagged_cases/README.md's paper-mill-vs-different-in-kind
    split), not something derivable from potential_dupes/the shingle scan, so it's passed in rather
    than computed here. None (default) renders no badge, unchanged from before this parameter existed.
    Returns (slug, body, shingle_total) -- `shingle_total` (0 when `shingle_size` is None) lets a
    caller decide whether the pair clears --min-shingle-matches before writing anything to disk.
    `library_db_path` (a path string, not the open `conn`) renders a "mark this pair" block with the
    actual `review_dupes.py --agent-verdict` commands a reviewer looking at this page can copy-paste
    to record their verdict on the underlying database -- `p` (same paper, cataloged twice) and `a`
    (same author, self-reuse metadata missed -- see REVIEWING.md point 6 and mark_same_author()) both
    need a real `potential_dupes.id` to operate on (that's all `--agent-verdict` takes; the id is only
    a handle since both cascade to every row between the pair), so the block is only rendered when
    `dupe_rows` is non-empty AND `library_db_path` is given -- None (default) renders no block,
    matching this page's behavior before this parameter existed.
    Returns (slug, html_str)."""
    p1 = conn.execute("SELECT title, year, doi FROM papers WHERE id=?", (paper_id_1,)).fetchone()
    p2 = conn.execute("SELECT title, year, doi FROM papers WHERE id=?", (paper_id_2,)).fetchone()
    title1, year1, doi1 = p1
    title2, year2, doi2 = p2
    authors1, authors2 = paper_authors(conn, paper_id_1), paper_authors(conn, paper_id_2)

    # Chronology: prefer the rows' own earlier/later_paper_id (matches build_dupe_candidates.py's
    # NULL-when-unknown convention); fall back to raw year if every row leaves it NULL.
    earlier_id = next((r["earlier_paper_id"] for r in dupe_rows if r["earlier_paper_id"]), None)
    if earlier_id is None:
        if year1 and year2 and year1 != year2:
            earlier_id = paper_id_1 if year1 < year2 else paper_id_2

    def card(pid, title, year, doi, authors, role):
        by = ", ".join(authors) if authors else "(authors unknown)"
        doi_html = f'<a class="doi" href="https://doi.org/{html.escape(doi)}">doi.org/{html.escape(doi)}</a>' if doi else ""
        role_class = "later" if role.startswith("LATER") else ""
        return (
            f'<div class="paper-card"><p class="role {role_class}">{html.escape(role)}</p>'
            f'<h2>{html.escape(title)}</h2>'
            f'<p class="meta">{html.escape(by)} &middot; {year or "n.d."}</p>'
            f'{doi_html}</div>'
        )

    if earlier_id == paper_id_1:
        left = card(paper_id_1, title1, year1, doi1, authors1, "EARLIER / SOURCE")
        right = card(paper_id_2, title2, year2, doi2, authors2, "LATER / FLAGGED")
    elif earlier_id == paper_id_2:
        left = card(paper_id_2, title2, year2, doi2, authors2, "EARLIER / SOURCE")
        right = card(paper_id_1, title1, year1, doi1, authors1, "LATER / FLAGGED")
    else:
        left = card(paper_id_1, title1, year1, doi1, authors1, "PAPER A")
        right = card(paper_id_2, title2, year2, doi2, authors2, "PAPER B")

    sims = [r["similarity"] for r in dupe_rows]
    lcs_vals = [r["lcs_ratio"] for r in dupe_rows if r["lcs_ratio"] is not None]
    no_candidates = not dupe_rows

    shingle_exhibits, shingle_total = [], 0
    if shingle_size:
        shingle_exhibits, shingle_total = render_shingle_exhibits(
            conn, paper_id_1, paper_id_2, earlier_id, shingle_size, max_shingle_exhibits,
            mismatch_penalty=mismatch_penalty, x_drop=x_drop,
        )
    shingle_divider = ""
    if shingle_size:
        shown_note = (f" (top {len(shingle_exhibits)} by length shown)"
                      if shingle_total > len(shingle_exhibits) else "")
        shingle_divider = f"""
<div class="exhibit-head" style="margin-top:0.5rem;">
  <h3>Exact word-shingle matches ({shingle_size}+ consecutive words)</h3>
  <div class="metrics"><span><b>{shingle_total}</b> run{"s" if shingle_total != 1 else ""} found{shown_note}</span></div>
</div>"""

    source_html = f'<p class="source-note">{source_note}</p>' if source_note else ""
    tab_title = short_title or (title1 if earlier_id != paper_id_2 else title2)[:40]
    if shingle_size and include_shingle_count_in_title:
        tab_title = f"{tab_title} ({shingle_total} shingle match{'es' if shingle_total != 1 else ''})"

    shingle_summary = (
        f'<span>exact shingle runs ({shingle_size}+ words) <strong>{shingle_total}</strong></span>'
        if shingle_size else ""
    )
    if no_candidates:
        candidate_summary = '<span>candidate passages <strong>0 (title-match only)</strong></span>'
    else:
        candidate_summary = (
            f'<span>candidate passages <strong>{len(dupe_rows)}</strong></span>'
            f'<span>similarity range <strong>{min(sims):.3f}&ndash;{max(sims):.3f}</strong></span>'
            f'<span>median LCS ratio <strong>{sorted(lcs_vals)[len(lcs_vals)//2]:.2f}</strong></span>'
        )
    if no_candidates:
        found_by = (
            "This pair was found by matching on title alone (find_title_bucket_dupes.py) "
            "&mdash; the usual paragraph-embedding similarity search never generated a candidate "
            "for it at any threshold."
        )
    else:
        found_by = (
            f"{len(dupe_rows)} candidate passage{'s' if len(dupe_rows) != 1 else ''} "
            f"(embedding cosine similarity {min(sims):.2f}&ndash;{max(sims):.2f}) identified this "
            f"pair for review."
        )
    dek = (
        f"{found_by} The exhibits below are exact word-shingle matches instead &mdash; a "
        f"multi-word verbatim run is the more convincing "
        f"“this was copied” signal, with the matched span "
        f'<mark class="match" style="padding:0 .3em">highlighted</mark>.'
        if shingle_size else
        f"{found_by} --no-shingles was set, so no exhibits are rendered below."
    )
    classification_html = f'<p class="classification">{html.escape(classification)}</p>' if classification else ""

    actions_html = ""
    if dupe_rows and library_db_path:
        # Any row between the pair works -- both 'p' and 'a' cascade to every potential_dupes
        # row between paper_id_1/paper_id_2 (mark_same_paper()/mark_same_author()), so this id
        # is just a handle, not a claim that this specific row is the one being judged.
        rep_id = dupe_rows[0]["id"]
        db_arg = html.escape(str(library_db_path))
        actions_html = f"""
    <div class="actions">
      <p class="actions-label">Mark this pair (review_dupes.py)</p>
      <pre>python3 review_dupes.py --library-db {db_arg} --agent-verdict {rep_id} p
<span class="comment"># same underlying paper, cataloged twice -- corrects same_paper=1 pair-wide</span></pre>
      <pre>python3 review_dupes.py --library-db {db_arg} --agent-verdict {rep_id} a
<span class="comment"># same author, self-reuse -- corrects same_author=1 pair-wide (see REVIEWING.md point 6)</span></pre>
    </div>"""

    body = f"""<title>{html.escape(tab_title)}</title>
<style>{STYLE}</style>
<div class="page">
  <div class="lede">
    {classification_html}
    <p class="eyebrow">Duplicate-text finding</p>
    <h1>{html.escape(title1 if earlier_id != paper_id_2 else title2)} &rarr; {html.escape(title2 if earlier_id != paper_id_2 else title1)}</h1>
    <p class="dek">{dek}</p>
    {source_html}
    <div class="comparison">
      {left}
      <div class="connector">&rarr;</div>
      {right}
    </div>
    <div class="summary-bar">
      {candidate_summary}
      {shingle_summary}
    </div>
    {actions_html}
  </div>
  {shingle_divider}
  {"".join(shingle_exhibits)}
  <footer>Generated by write_dupe_reports_html.py from library.sqlite3&rsquo;s potential_dupes table
    (grouping/stats only) {f"and a {shingle_size}+-word exact word-shingle scan (exhibits)" if shingle_size else ""}.</footer>
</div>"""
    slug = f"{slugify(title1)}-vs-{slugify(title2)}"
    if shingle_size and include_shingle_count_in_title:
        slug = f"{slug}-{shingle_total}-shingle-match{'es' if shingle_total != 1 else ''}"
    return slug, body, shingle_total


def parse_args():
    parser = argparse.ArgumentParser(description="Write HTML evidence pages for confirmed duplicate-text findings.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--out-dir", type=Path, default=Path("dupe_reports_html"))
    parser.add_argument("--status", default="confirmed", help="potential_dupes.status to include (default confirmed)")
    parser.add_argument("--ids", help="comma-separated potential_dupes.id list, overrides --status")
    parser.add_argument("--paper-ids", help="'A,B' paper_id pair with no potential_dupes lookup at all -- "
                                             "for a pair find_title_bucket_dupes.py found that has zero "
                                             "potential_dupes rows at any threshold (see render_case()'s "
                                             "no_candidates handling); overrides --ids/--status")
    parser.add_argument("--title", help="short (2-4 word) <title> for the page; only applies when "
                                         "--ids/--status resolves to a single paper pair")
    parser.add_argument("--source-note", help="HTML snippet (e.g. an external citation/link) shown "
                                                "under the dek; only applies to a single paper pair")
    parser.add_argument("--classification", help="plain-text badge rendered first thing on the page, "
                                                    "above the eyebrow (e.g. 'Paper-mill pattern "
                                                    "· 1 of 22 in this set') -- an editorial "
                                                    "judgment call, not derived from the data; only "
                                                    "applies to a single paper pair")
    parser.add_argument("--case-number", help="prefixed onto the output filename as '<case-number>-' "
                                               "(e.g. '07' -> 07-<slug>.html) -- for matching a "
                                               "flagged_cases/NN-<slug>/ directory's own numbering; "
                                               "does not affect the <title> tag or anything else, "
                                               "only applies to a single paper pair")
    parser.add_argument("--shingle-size", type=int, default=DEFAULT_SHINGLE_SIZE,
                         help=f"also run an exact word-shingle scan across each pair's complete text "
                              f"and render those as additional exhibits (default {DEFAULT_SHINGLE_SIZE} -- "
                              f"see compare_two_papers.py's find_shingle_matches() for the method)")
    parser.add_argument("--no-shingles", action="store_true",
                         help="skip the word-shingle scan -- since it's the only exhibit source, "
                              "the resulting page has no exhibits, just the header/summary stats")
    parser.add_argument("--max-shingle-exhibits", type=int, default=DEFAULT_MAX_SHINGLE_EXHIBITS,
                         help=f"cap the number of shingle-match exhibits rendered per pair, keeping "
                              f"the longest ones (default {DEFAULT_MAX_SHINGLE_EXHIBITS}) -- but "
                              f"then DISPLAYED in the later/flagged paper's own reading order, not "
                              f"longest-first; see render_shingle_exhibits()'s docstring")
    parser.add_argument("--x-drop", type=int, default=None,
                         help="enable seed-and-extend tolerance for isolated word substitutions past "
                              "each exact shingle match's boundary -- see compare_two_papers.py's "
                              "find_shingle_matches()/_extend_xdrop() docstrings. Default (None) "
                              "preserves exact-only matching.")
    parser.add_argument("--mismatch-penalty", type=int, default=1,
                         help="score subtracted per mismatched word during --x-drop extension; only "
                              "meaningful when --x-drop is set")
    parser.add_argument("--min-shingle-matches", type=int, default=DEFAULT_MIN_SHINGLE_MATCHES,
                         help=f"skip writing a pair's page entirely (no file) if its exact "
                              f"word-shingle scan found fewer than this many runs (default "
                              f"{DEFAULT_MIN_SHINGLE_MATCHES}) -- a noise filter, since embedding "
                              f"candidates alone can surface pairs with no real verbatim overlap; "
                              f"pass 0 to write every pair regardless (e.g. a case confirmed on "
                              f"lcs_ratio rather than exact shingles, like Taro/Saxby). Has no "
                              f"effect with --no-shingles, since there's no count to check then.")
    parser.add_argument("--no-shingle-count-in-title", action="store_true",
                         help="don't append the shingle-match count to the <title> tag or to the "
                              "output filename/slug (e.g. 'foo-vs-bar-34-shingle-matches.html') -- "
                              "on by default so a directory/gallery of many published pages can be "
                              "triaged by scale without opening each one; has no effect with "
                              "--no-shingles, since there's no count to append then")
    parser.add_argument("--skip-identical-titles", action="store_true",
                         help="skip a pair entirely (no file, no shingle scan run at all) when the "
                              "two papers' titles are exactly identical after stripping whitespace "
                              "-- these are almost always the 'same paper cataloged twice' case "
                              "(see review_dupes.py's `p`/mark_same_paper()) rather than a genuine "
                              "different-identity republication with its own evidence worth a page. "
                              "Off by default.")
    parser.add_argument("--min-year-gap", type=int, default=0,
                         help="skip a pair (no file, no shingle scan run at all) unless the two "
                              "papers' publication years differ by at least this many years "
                              "(default 0 -- no effect, since a gap is always >= 0). Deliberately "
                              "NOT a 'skip same-year pairs' filter defaulting on: same-year pairs "
                              "include genuine confirmed findings in this corpus (e.g. a same-author "
                              "thesis-and-paper pair published the same year), so skipping them by "
                              "default would silently hide real evidence. A pair where either paper's "
                              "year is unknown (NULL) is never skipped by this filter -- there's "
                              "nothing to compare, so it errs toward writing the page.")
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)
    conn.row_factory = sqlite3.Row
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.paper_ids:
        pid1, pid2 = (int(x) for x in args.paper_ids.split(","))
        groups = {tuple(sorted((pid1, pid2))): []}
        rows = []
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

    print(f"{len(rows)} row(s) across {len(groups)} paper pair(s)")
    single = len(groups) == 1
    shingle_size = None if args.no_shingles else args.shingle_size
    skipped = 0
    skipped_identical_titles = 0
    skipped_year_gap = 0
    for (pid1, pid2), group_rows in groups.items():
        if args.skip_identical_titles:
            title1, title2 = (conn.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone()[0]
                               for pid in (pid1, pid2))
            if title1.strip() == title2.strip():
                skipped_identical_titles += 1
                print(f"  skipped {pid1}/{pid2} (identical titles: {title1!r})")
                continue
        if args.min_year_gap:
            year1, year2 = (conn.execute("SELECT year FROM papers WHERE id=?", (pid,)).fetchone()[0]
                             for pid in (pid1, pid2))
            if year1 is not None and year2 is not None and abs(year1 - year2) < args.min_year_gap:
                skipped_year_gap += 1
                print(f"  skipped {pid1}/{pid2} (years {year1}/{year2}, below --min-year-gap "
                      f"{args.min_year_gap})")
                continue
        slug, body, shingle_total = render_case(
            conn, pid1, pid2, group_rows,
            source_note=args.source_note if single else None,
            short_title=args.title if single else None,
            shingle_size=shingle_size,
            max_shingle_exhibits=args.max_shingle_exhibits,
            classification=args.classification if single else None,
            mismatch_penalty=args.mismatch_penalty,
            x_drop=args.x_drop,
            include_shingle_count_in_title=not args.no_shingle_count_in_title,
            library_db_path=args.library_db,
        )
        if shingle_size and shingle_total < args.min_shingle_matches:
            skipped += 1
            print(f"  skipped {pid1}/{pid2} ({shingle_total} shingle match"
                  f"{'es' if shingle_total != 1 else ''}, below --min-shingle-matches "
                  f"{args.min_shingle_matches})")
            continue
        filename = f"{args.case_number}-{slug}.html" if (single and args.case_number) else f"{slug}.html"
        out_path = args.out_dir / filename
        out_path.write_text(body, encoding="utf-8")
        print(f"  wrote {out_path} ({len(group_rows)} passage(s))")

    if skipped:
        print(f"{skipped} pair(s) skipped entirely (below --min-shingle-matches "
              f"{args.min_shingle_matches}) -- pass --min-shingle-matches 0 to write them anyway")
    if skipped_identical_titles:
        print(f"{skipped_identical_titles} pair(s) skipped entirely (identical titles) -- "
              f"drop --skip-identical-titles to write them anyway")
    if skipped_year_gap:
        print(f"{skipped_year_gap} pair(s) skipped entirely (below --min-year-gap "
              f"{args.min_year_gap}) -- lower --min-year-gap to write them anyway")

    conn.close()


if __name__ == "__main__":
    main()

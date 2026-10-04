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
import difflib
import html
import sqlite3
from pathlib import Path

import db
import text_overlap as to
from compare_two_papers import (DEFAULT_GAP_EXTEND, DEFAULT_GAP_OPEN, DEFAULT_MAX_GAP,
                                 DegenerateGappedPair, find_shingle_matches, load_paper_words)
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

/* --whole-document: one continuous diff of both papers, not N per-run exhibits. */
.wdd {
  background: var(--surface); border: 1px solid var(--border); border-radius: 3px;
  padding: 1.5rem 1.7rem; font-size: 0.94rem; line-height: 1.75;
  overflow-wrap: break-word;
}
.wdd p { margin: 0 0 0.95rem; }
.wdd p:last-child { margin-bottom: 0; }
.wdd .same { background: var(--match-bg); color: var(--match-ink); }
.wdd .shared-weak { background: var(--surface-2); color: var(--ink-muted); }
.wdd del { background: var(--diff-a-bg); color: var(--diff-a); text-decoration: line-through; }
.wdd ins { background: var(--diff-b-bg); color: var(--diff-b); text-decoration: none; }
.wdd details { margin: 0 0 0.95rem; }
.wdd summary {
  cursor: pointer; font-family: var(--font-mono); font-size: 0.78rem;
  color: var(--ink-muted); padding: 0.2rem 0;
}
.wdd-legend {
  display: flex; flex-wrap: wrap; gap: 0.5rem 1.4rem; margin: 0 0 1.1rem;
  font-family: var(--font-mono); font-size: 0.74rem; color: var(--ink-muted);
}
.wdd-legend b { font-weight: 400; padding: 0 0.35em; }
.wdd-legend .same-key { background: var(--match-bg); color: var(--match-ink); }
.wdd-legend .weak-key { background: var(--surface-2); color: var(--ink-muted); }
.wdd-legend .del-key { background: var(--diff-a-bg); color: var(--diff-a); text-decoration: line-through; }
.wdd-legend .ins-key { background: var(--diff-b-bg); color: var(--diff-b); }
.wdd-caveat {
  font-size: 0.85rem; color: var(--ink-muted); margin: 1.1rem 0 0;
  border-left: 2px solid var(--border); padding-left: 0.9rem;
}

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
                             mismatch_penalty=1, x_drop=None, gap_open=None,
                             gap_extend=DEFAULT_GAP_EXTEND, max_gap=DEFAULT_MAX_GAP):
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
                                 mismatch_penalty=mismatch_penalty, x_drop=x_drop,
                                 gap_open=gap_open, gap_extend=gap_extend, max_gap=max_gap)
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
    for i, run in enumerate(shown, 1):
        (length, para_1, para_2, text_1, text_2, start_1, end_1, start_2, end_2, subs) = (
            run.length, run.para_a, run.para_b, run.text_a, run.text_b,
            run.start_a, run.end_a, run.start_b, run.end_b, run.substitutions)
        indels = run.indels
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
        # That only holds while the two sides have the SAME number of words, i.e. while extension
        # is lockstep. A gapped run (--gap-open) has an insertion or deletion in it, so position i
        # on one side is not position i on the other past the first gap, and one shared index set
        # would mark the wrong words from there on. In that case each side gets its own set,
        # derived from the alignment difflib reports -- the same alignment the whole-document view
        # renders, so the two views agree on which words differ.
        if len(match_a) == len(match_b):
            diff_a = diff_b = {k for k, (wa, wb) in enumerate(zip(match_a, match_b))
                               if wa.lower() != wb.lower()}
        else:
            diff_a, diff_b = set(), set()
            lo_a, lo_b = [w.lower() for w in match_a], [w.lower() for w in match_b]
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, lo_a, lo_b,
                                                                autojunk=False).get_opcodes():
                if tag != "equal":
                    diff_a.update(range(i1, i2))
                    diff_b.update(range(j1, j2))
        rendered_a = render_context_with_match(fa, match_a, diff_a)
        rendered_b = render_context_with_match(fb, match_b, diff_b)
        lcs = to.longest_common_word_run(fa, fb)
        ngj = to.ngram_jaccard(fa, fb)
        earlier_label = "EARLIER" if earlier_id else "PARAGRAPH A"
        later_label = "LATER" if earlier_id else "PARAGRAPH B"
        exhibits.append(f"""
<section class="exhibit">
  <div class="exhibit-head">
    <h3>Shingle match {i} of {len(shown)}</h3>
    <div class="metrics"><span>{"near-exact run" if subs or indels else "exact run"} <b>{length} words</b></span>
      {f'<span>substitutions <b>{subs}</b></span>' if subs else ""}{f'<span>inserted/deleted <b>{indels}</b> ({end_1 - start_1}w A vs {end_2 - start_2}w B)</span>' if indels else ""}
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



DEFAULT_MAX_GAP_WORDS = 120  # --max-gap-words: a one-sided stretch longer than this is rendered
# inside a collapsed <details> rather than inline. Nothing is dropped -- this only stops one
# paper's genuinely original 3,000-word section from burying the diff it sits inside. The
# threshold is per gap side, and 0 disables collapsing entirely.

_WDD_SENTENCE_END = (".", "!", "?", '"', "\u201d", ")", "]", ":", ";")

_WDD_TAGS = {
    "same": ('<span class="same">', "</span>"),
    "shared-weak": ('<span class="shared-weak">', "</span>"),
    "del": ("<del>", "</del>"),
    "ins": ("<ins>", "</ins>"),
}


def _wdd_emit(items):
    """Render [(tag_key, word, para_break), ...] as paragraphed HTML.

    Consecutive items sharing a tag are wrapped in one tag rather than one per word (same
    cosmetic reason as render_context_with_match()'s run grouping). `para_break` is the source
    paragraph index a word came from, or None to mean "never break here": paragraph structure is
    taken from ONE side only (document A wherever A has words at all), because the two documents'
    paragraph boundaries don't correspond outside the aligned runs and honoring both produces
    paragraph breaks in the middle of a sentence.

    A paragraph change is honored only when the preceding word actually ended a sentence. The same
    PyMuPDF block/page-boundary artifact write_dupe_reports.py's expand_paragraph() exists to undo
    also splits one continuous sentence across two paragraph records here, and taking those splits
    literally puts a paragraph break mid-clause -- seen for real on this project's own
    hospital/women's-safety pair, which breaks right after the swapped title noun. Falling through
    keeps the sentence whole; the cost is that a genuine paragraph break after an abbreviation or a
    heading with no terminal punctuation is missed, which is the cheaper error."""
    out = []
    open_tag = None
    cur_para = None
    started = False
    last_word = ""
    for tag, word, para in items:
        if (para is not None and started and para != cur_para
                and last_word.rstrip().endswith(_WDD_SENTENCE_END)):
            if open_tag:
                out.append(_WDD_TAGS[open_tag][1])
                open_tag = None
            out.append("</p>\n<p>")
        if para is not None:
            cur_para = para
        started = True
        last_word = word
        if tag != open_tag:
            if open_tag:
                out.append(_WDD_TAGS[open_tag][1])
            out.append(_WDD_TAGS[tag][0])
            open_tag = tag
        out.append(html.escape(word) + " ")
    if open_tag:
        out.append(_WDD_TAGS[open_tag][1])
    return f"<p>{''.join(out)}</p>" if started else ""


def _wdd_spine(runs):
    """Pick the heaviest chain of shingle runs that is non-overlapping and strictly forward-moving
    in BOTH documents -- the alignment a single continuous diff has to follow.

    find_shingle_matches() reports every maximal run it finds, independently: two runs may overlap
    on one side, or cross (run X earlier than Y in document A but later in B, i.e. the material was
    reordered). A linear walk can use neither. This is the standard weighted-longest-increasing-
    subsequence pick, maximizing total aligned words, so the spine keeps the most text it can.

    Returns (spine, dropped_runs). A dropped run is NOT the same thing as lost coverage, and the
    difference matters: find_shingle_matches() reports one run per (position_a, position_b)
    occurrence pair, so a phrase appearing three times in each document yields up to nine runs over
    the same words, and a spine keeping one of them loses nothing at all. On this project's own
    99%/99% pair, 72 of 80 runs fall outside the spine while spine coverage stays at 99%. Only a
    drop in the covered-WORD count means the view is actually understating the overlap, which is
    what render_whole_document_diff() measures and reports.

    O(n^2) in the number of runs, with a greedy fallback past _WDD_SPINE_EXACT_MAX (a boilerplate-
    heavy pair can report thousands of runs; the exact pick is not worth minutes of CPU there, and
    the greedy chain is the same answer whenever the runs don't actually cross)."""
    items = sorted(runs, key=lambda r: (r[5], r[7]))
    n = len(items)
    if n == 0:
        return [], []
    if n > _WDD_SPINE_EXACT_MAX:
        spine, ca, cb = [], 0, 0
        for r in items:
            if r[5] >= ca and r[7] >= cb:
                spine.append(r)
                ca, cb = r[6], r[8]
        chosen = set(id(r) for r in spine)
        return spine, [r for r in items if id(r) not in chosen]
    best = [0] * n
    prev = [-1] * n
    for i, ri in enumerate(items):
        best[i] = ri[6] - ri[5]
        for j in range(i):
            if items[j][6] <= ri[5] and items[j][8] <= ri[7] and best[j] + (ri[6] - ri[5]) > best[i]:
                best[i] = best[j] + (ri[6] - ri[5])
                prev[i] = j
    k = max(range(n), key=lambda i: best[i])
    chain = []
    while k != -1:
        chain.append(items[k])
        k = prev[k]
    chain.reverse()
    chosen = set(id(r) for r in chain)
    return chain, [r for r in items if id(r) not in chosen]


_WDD_SPINE_EXACT_MAX = 2500

WDD_UNPLACED_NOTE_WORDS = 100  # below this many matched-but-unplaceable words, the reordering
# caveat is noise: a handful of words land outside the spine on almost every pair, and a warning
# that fires every time teaches a reader to skip it.


def _wdd_gap_items(words_a, words_b, equal_tag="shared-weak"):
    """Items for the unaligned stretch between two spine runs.

    `equal_tag` is what a stretch the two sides agree on renders as. It defaults to `shared-weak`
    (shared, but too short a run to be evidence on its own), which is right for a gap. A gapped
    matched run (--gap-open) is also rendered through here, because an insertion or deletion inside
    it means the two sides no longer line up position-for-position -- but there the agreeing
    stretches ARE the evidence, so the caller passes `same`.

    Not simply "everything here is different": a gap routinely contains text both papers share
    that merely never reached `shingle_size` consecutive identical words (one substituted word in
    an eight-word sentence). Running difflib across the gap separates the two, so genuinely shared
    short wording renders as `shared-weak` rather than being colored as a difference -- the page
    would otherwise overstate how much of a near-identical pair actually differs. A gap with text
    on only one side skips the diff and is a plain deletion/insertion."""
    if not words_a:
        return [("ins", w, None) for w, _ in words_b]
    if not words_b:
        return [("del", w, p) for w, p in words_a]
    la = [w.lower() for w, _ in words_a]
    lb = [w.lower() for w, _ in words_b]
    items = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
        if tag == "equal":
            items += [(equal_tag, w, p) for w, p in words_a[i1:i2]]
        else:
            items += [("del", w, p) for w, p in words_a[i1:i2]]
            items += [("ins", w, None) for w, _ in words_b[j1:j2]]
    return items


def render_whole_document_diff(conn, paper_id_1, paper_id_2, earlier_id, shingle_size,
                                mismatch_penalty=1, x_drop=None,
                                max_gap_words=DEFAULT_MAX_GAP_WORDS, gap_open=None,
                                gap_extend=DEFAULT_GAP_EXTEND, max_gap=DEFAULT_MAX_GAP):
    """Render both complete documents as ONE continuous word-level diff, in reading order, instead
    of N separate per-run exhibits. Returns (html, stats, total_runs).

    For the case these pages keep running into -- two documents that are substantially the same
    document, with a swapped title noun and a new byline -- the per-exhibit view is the wrong
    shape: "Shingle match 1 of 82" invites the reader to assess 82 separate passages, when the
    finding is that there is one document here, published twice. This view shows that in a single
    glance: an almost entirely highlighted page with a few red/green spots at the title, the
    authors and the masthead.

    Mechanism: take the same find_shingle_matches() runs the exhibit view uses, pick the
    forward-moving non-overlapping chain of them (_wdd_spine()), then walk both documents once --
    aligned runs as shared text (with X-drop's substituted words as an inline <del>/<ins> pair),
    the stretches between them diffed against each other (_wdd_gap_items()).

    Deliberately NOT a plain difflib diff of the two word streams, which is the obvious
    implementation and the wrong one: difflib would happily align two unrelated occurrences of
    "the system" thousands of words apart and report a matching ratio built mostly from function
    words. The shingle runs are the evidentiary claim (N consecutive identical words, which
    doesn't happen by chance); difflib is used only WITHIN a gap the runs already bracket, where
    both stretches are short and their correspondence is no longer in question.

    Known limitation, reported on the page rather than hidden: the walk is monotone, so material
    that appears in a different ORDER in the two documents cannot all be placed. Those runs come
    back from _wdd_spine() as dropped, their words render as unique-to-each-side, and the stats
    carry both the spine coverage (what this view shows) and the total run coverage (what the
    scan actually found) so the two can be compared."""
    words_a, words_b = load_paper_words(conn, paper_id_1), load_paper_words(conn, paper_id_2)
    pid_a, pid_b = paper_id_1, paper_id_2
    if earlier_id == paper_id_2:
        words_a, words_b = words_b, words_a
        pid_a, pid_b = paper_id_2, paper_id_1
    runs = find_shingle_matches(words_a, words_b, shingle_size=shingle_size,
                                 mismatch_penalty=mismatch_penalty, x_drop=x_drop,
                                 gap_open=gap_open, gap_extend=gap_extend, max_gap=max_gap)
    spine, dropped = _wdd_spine(runs)

    covered_a, covered_b = set(), set()
    for r in runs:
        covered_a.update(range(r[5], r[6]))
        covered_b.update(range(r[7], r[8]))

    # One flat item stream for the whole document, rendered by a single _wdd_emit() call, so
    # paragraphs run continuously across the boundary between an aligned run and the gap next to
    # it. Emitting each block separately (the first version) wrapped every block in its own <p>,
    # which put a paragraph break at every single difference -- including mid-sentence, right
    # after a two-word title swap. `parts` therefore only ever gains an entry when a collapsed
    # <details> block has to interrupt the flow, since that cannot sit inside a <p>.
    parts = []
    pending = []
    subs = 0
    indels = 0
    spine_a, spine_b = set(), set()
    cursor_a = cursor_b = 0

    def add_gap(gap_a, gap_b):
        items = _wdd_gap_items(gap_a, gap_b)
        one_sided = not gap_a or not gap_b
        if one_sided and max_gap_words and len(items) > max_gap_words:
            label = "only in B" if not gap_a else "only in A"
            if pending:
                parts.append(_wdd_emit(pending))
                pending.clear()
            parts.append(f'<details><summary>{len(items):,} words {html.escape(label)} '
                         f'&mdash; click to expand</summary>{_wdd_emit(items)}</details>')
        else:
            pending.extend(items)

    for r in spine:
        start_a, end_a, start_b, end_b = r[5], r[6], r[7], r[8]
        gap_a, gap_b = words_a[cursor_a:start_a], words_b[cursor_b:start_b]
        if gap_a or gap_b:
            add_gap(gap_a, gap_b)
        run_a, run_b = words_a[start_a:end_a], words_b[start_b:end_b]
        if len(run_a) == len(run_b):
            for (wa, pa), (wb, _) in zip(run_a, run_b):
                if wa.lower() == wb.lower():
                    pending.append(("same", wa, pa))
                else:
                    subs += 1
                    pending.append(("del", wa, pa))
                    pending.append(("ins", wb, None))
        else:
            # A gapped run (--gap-open): an indel inside it means zip() would pair word i of one
            # side with word i of the other past the gap and silently drop the tail of the longer
            # side. Align it the way a gap is aligned instead, but with the agreeing stretches
            # marked `same` -- inside a matched run they are the evidence, not incidental overlap.
            # Counts come from the run's own reported figures rather than from the rendered items:
            # a del/ins pair in the stream can be either a substitution or one half of an indel, and
            # halving the total (what this did first) silently reports an indel as half a
            # substitution. find_shingle_matches() already distinguishes them.
            pending.extend(_wdd_gap_items(run_a, run_b, equal_tag="same"))
            subs += r.substitutions
            indels += r.indels
        spine_a.update(range(start_a, end_a))
        spine_b.update(range(start_b, end_b))
        cursor_a, cursor_b = end_a, end_b
    tail_a, tail_b = words_a[cursor_a:], words_b[cursor_b:]
    if tail_a or tail_b:
        add_gap(tail_a, tail_b)
    if pending:
        parts.append(_wdd_emit(pending))

    # Words the scan matched that this ordered view could not place. Compared as word sets, not
    # as run counts -- see _wdd_spine()'s docstring for why the two are nowhere near the same
    # number, and why only this one is worth telling the reader about.
    unplaced_a, unplaced_b = len(covered_a - spine_a), len(covered_b - spine_b)
    stats = {
        "paper_a": pid_a, "paper_b": pid_b,
        "words_a": len(words_a), "words_b": len(words_b),
        "runs": len(runs), "spine_runs": len(spine), "dropped_runs": len(dropped),
        "substitutions": subs, "indels": indels, "gapped": gap_open is not None,
        "covered_a": len(covered_a), "covered_b": len(covered_b),
        "unplaced_a": unplaced_a, "unplaced_b": unplaced_b,
        "pct_a": 100 * len(covered_a) / len(words_a) if words_a else 0,
        "pct_b": 100 * len(covered_b) / len(words_b) if words_b else 0,
        "spine_pct_a": 100 * len(spine_a) / len(words_a) if words_a else 0,
        "spine_pct_b": 100 * len(spine_b) / len(words_b) if words_b else 0,
    }
    return f'<div class="wdd">{"".join(parts)}</div>', stats, len(runs)

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
                 include_shingle_count_in_title=True, library_db_path=None,
                 whole_document=False, max_gap_words=DEFAULT_MAX_GAP_WORDS, gap_open=None,
                 gap_extend=DEFAULT_GAP_EXTEND, max_gap=DEFAULT_MAX_GAP, neutral=False):
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
    `whole_document` renders the two papers as one continuous word-level diff instead of the
    per-run exhibit list -- see render_whole_document_diff()'s docstring for when that's the right
    shape (two documents that are substantially one document) and what it can't show. It replaces
    the exhibits rather than adding to them, and needs `shingle_size` (it is built from the same
    runs); `max_gap_words` is passed through to it.
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

    # --neutral pages keep the year ordering for layout but never label a side as source or flagged:
    # printed years are not always reliable (CE-36, LEAD-08), and the page asserts no direction.
    earlier_role, later_role = ("PAPER A", "PAPER B") if neutral else ("EARLIER / SOURCE", "LATER / FLAGGED")
    if earlier_id == paper_id_1:
        left = card(paper_id_1, title1, year1, doi1, authors1, earlier_role)
        right = card(paper_id_2, title2, year2, doi2, authors2, later_role)
    elif earlier_id == paper_id_2:
        left = card(paper_id_2, title2, year2, doi2, authors2, earlier_role)
        right = card(paper_id_1, title1, year1, doi1, authors1, later_role)
    else:
        left = card(paper_id_1, title1, year1, doi1, authors1, "PAPER A")
        right = card(paper_id_2, title2, year2, doi2, authors2, "PAPER B")

    sims = [r["similarity"] for r in dupe_rows]
    lcs_vals = [r["lcs_ratio"] for r in dupe_rows if r["lcs_ratio"] is not None]
    no_candidates = not dupe_rows

    shingle_exhibits, shingle_total = [], 0
    wdd_stats = None
    if shingle_size and whole_document:
        wdd_html, wdd_stats, shingle_total = render_whole_document_diff(
            conn, paper_id_1, paper_id_2, earlier_id, shingle_size,
            mismatch_penalty=mismatch_penalty, x_drop=x_drop, max_gap_words=max_gap_words,
            gap_open=gap_open, gap_extend=gap_extend, max_gap=max_gap,
        )
        shingle_exhibits = [wdd_html]
    elif shingle_size:
        shingle_exhibits, shingle_total = render_shingle_exhibits(
            conn, paper_id_1, paper_id_2, earlier_id, shingle_size, max_shingle_exhibits,
            mismatch_penalty=mismatch_penalty, x_drop=x_drop,
            gap_open=gap_open, gap_extend=gap_extend, max_gap=max_gap,
        )
    # Under --gap-open a reported run is no longer a purely exact match: it can contain substituted
    # words AND inserted/deleted ones. Every heading, legend and footer that says "exact" has to stop
    # saying it, or the page overstates its own evidence to whoever it gets sent to.
    # Three tiers, because the extension mode really does change what a "run" is and the page should
    # not claim more than the scan established. Note --x-drop ALONE already permits substituted
    # words, so the old blanket "exact word-shingle matches" heading was loose before --gap-open
    # existed; this corrects that too rather than only labelling the new mode.
    gapped = gap_open is not None
    if gapped:
        run_kind = "near-verbatim"
        match_rule = (f"{shingle_size}+ consecutive words, allowing substituted and "
                      f"inserted/deleted words within a run")
    elif x_drop is not None:
        run_kind = "near-exact"
        match_rule = f"{shingle_size}+ consecutive words, allowing substituted words within a run"
    else:
        run_kind = "exact"
        match_rule = f"{shingle_size}+ consecutive identical words"
    shingle_divider = ""
    if wdd_stats:
        s = wdd_stats
        a_label, b_label = ("EARLIER", "LATER") if earlier_id else ("PAPER A", "PAPER B")
        reorder_note = ""
        # Only worth saying when the ordered view actually shows less than the scan found. A run
        # falling outside the spine usually costs nothing (see _wdd_spine()), so reporting dropped
        # runs here would cry reordering on pairs that are cleanly aligned end to end.
        if max(s["unplaced_a"], s["unplaced_b"]) >= WDD_UNPLACED_NOTE_WORDS:
            reorder_note = (
                f'<p class="wdd-caveat">This view reads both papers in order, so matched text that '
                f'sits at a different POSITION in each one cannot be placed: '
                f'{s["unplaced_a"]:,} words of {a_label.lower()} and {s["unplaced_b"]:,} of '
                f'{b_label.lower()} are matched by the scan but render below as unique to one side. '
                f'The overlap figure is therefore the scan\'s &mdash; <b>{s["pct_a"]:.0f}%</b> of '
                f'{a_label.lower()} and <b>{s["pct_b"]:.0f}%</b> of {b_label.lower()} &mdash; not the '
                f'{s["spine_pct_a"]:.0f}%/{s["spine_pct_b"]:.0f}% this diff shows in place. '
                f'The per-run exhibit view (drop --whole-document) shows every run regardless of '
                f'order.</p>'
            )
        shingle_divider = f"""
<div class="exhibit-head" style="margin-top:0.5rem;">
  <h3>Both documents, end to end, as one diff</h3>
  <div class="metrics">
    <span>shared <b>{s["pct_a"]:.0f}%</b> of {a_label.lower()} / <b>{s["pct_b"]:.0f}%</b> of {b_label.lower()}</span>
    <span>{s["words_a"]:,} vs {s["words_b"]:,} words</span>
    <span><b>{s["runs"]}</b> {run_kind} run{"s" if s["runs"] != 1 else ""}</span>
    {f'<span>substituted words <b>{s["substitutions"]}</b></span>' if s["substitutions"] else ""}
    {f'<span>inserted/deleted <b>{s["indels"]}</b></span>' if s.get("indels") else ""}
  </div>
</div>
<div class="wdd-legend">
  <span><b class="same-key">shared</b> ({match_rule})</span>
  <span><b class="weak-key">shared, below that length</b></span>
  <span><b class="del-key">only in {a_label.lower()}</b></span>
  <span><b class="ins-key">only in {b_label.lower()}</b></span>
</div>{f'<p class="wdd-caveat">Red and green mark two different things, both real: text present in only one paper, and single words substituted or inserted/deleted <em>inside</em> an otherwise-shared run. The run counts above separate them. Note also what the percentage counts: every word falling inside a matched run, <em>including</em> those edited words. Of the {s["covered_a"]:,} words it counts as shared on the {a_label.lower()} side, {s["substitutions"] + s["indels"]:,} ({100 * (s["substitutions"] + s["indels"]) / max(s["covered_a"], 1):.0f}%) are substituted or inserted/deleted rather than identical &mdash; so read the figure as "this much of the document is the same passage", not "this much is word-for-word".</p>' if gapped else ""}{reorder_note}"""
    elif shingle_size:
        shown_note = (f" (top {len(shingle_exhibits)} by length shown)"
                      if shingle_total > len(shingle_exhibits) else "")
        shingle_divider = f"""
<div class="exhibit-head" style="margin-top:0.5rem;">
  <h3>{run_kind.capitalize()} word-shingle matches ({match_rule})</h3>
  <div class="metrics"><span><b>{shingle_total}</b> run{"s" if shingle_total != 1 else ""} found{shown_note}</span></div>
</div>"""

    source_html = f'<p class="source-note">{source_note}</p>' if source_note else ""
    tab_title = short_title or (title1 if earlier_id != paper_id_2 else title2)[:40]
    if shingle_size and include_shingle_count_in_title:
        tab_title = f"{tab_title} ({shingle_total} shingle match{'es' if shingle_total != 1 else ''})"

    shingle_summary = (
        f'<span>{run_kind} shingle runs ({shingle_size}+ words) <strong>{shingle_total}</strong></span>'
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
    if wdd_stats:
        dek = (
            f"{found_by} Below, both papers are rendered end to end as a single word-level diff: "
            f"text the two share verbatim is "
            f'<mark class="match" style="padding:0 .3em">highlighted</mark>, and everything that '
            f"differs is marked where it falls. This is the view for a pair that is substantially "
            f"one document rather than a handful of reused passages &mdash; what it shows is how "
            f"little differs, and where."
        )
    elif shingle_size:
        dek = (
            f"{found_by} The exhibits below are {run_kind} word-shingle matches instead &mdash; a "
            f"multi-word verbatim run is the more convincing "
            + ("evidence of shared text than embedding similarity" if neutral
               else "“this was copied” signal")
            + f", with the matched span "
            f'<mark class="match" style="padding:0 .3em">highlighted</mark>.'
        )
    else:
        dek = f"{found_by} --no-shingles was set, so no exhibits are rendered below."

    # --neutral pages are for showing the tool's output to people outside a review, so they carry
    # no verdict: no classification badge, no directional arrow, no review_dupes.py commands.
    classification_html = (f'<p class="classification">{html.escape(classification)}</p>'
                           if classification and not neutral else "")
    eyebrow = "Text-overlap comparison" if neutral else "Duplicate-text finding"
    arrow = "&harr;" if neutral else "&rarr;"

    actions_html = ""
    if dupe_rows and library_db_path and not neutral:
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
    <p class="eyebrow">{eyebrow}</p>
    <h1>{html.escape(title1 if earlier_id != paper_id_2 else title2)} {arrow} {html.escape(title2 if earlier_id != paper_id_2 else title1)}</h1>
    <p class="dek">{dek}</p>
    {source_html}
    <div class="comparison">
      {left}
      <div class="connector">{arrow}</div>
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
    (grouping/stats only){f" and a {shingle_size}+-word word-shingle scan" if shingle_size else ""}{f", extended with X-drop {x_drop}" if shingle_size and x_drop is not None else ""}{f" and gapped alignment (gap open {gap_open}, extend {gap_extend}, max gap {max_gap}) &mdash; so a run may contain substituted and inserted/deleted words" if gapped else (" &mdash; so a run may contain substituted words, but no insertions or deletions" if shingle_size and x_drop is not None else (" &mdash; exact matches only" if shingle_size else ""))}.</footer>
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
    parser.add_argument("--neutral", action="store_true",
                        help="page for showing output outside a review: a 'Text-overlap comparison' "
                             "heading, a two-way arrow between the papers instead of a directional "
                             "one, no 'this was copied' wording, and no --classification badge or "
                             "review_dupes.py commands")
    parser.add_argument("--whole-document", action="store_true",
                         help="render both papers end to end as ONE continuous word-level diff "
                              "instead of a list of per-run exhibits -- for a pair that is "
                              "substantially the same document (a retitled republication), where "
                              "'Shingle match 1 of 82' invites reading 82 separate findings when "
                              "the finding is that there is one document here. Built from the same "
                              "shingle runs, so it needs --shingle-size; see "
                              "render_whole_document_diff() for what a monotone diff cannot show "
                              "when material is reordered (the page reports it).")
    parser.add_argument("--max-gap-words", type=int, default=DEFAULT_MAX_GAP_WORDS,
                         help=f"--whole-document only: collapse a one-sided stretch longer than "
                              f"this into a click-to-expand block (default {DEFAULT_MAX_GAP_WORDS}, "
                              f"0 disables). Nothing is omitted -- this keeps one paper's genuinely "
                              f"original long section from burying the diff around it.")
    parser.add_argument("--x-drop", type=int, default=None,
                         help="enable seed-and-extend tolerance for isolated word substitutions past "
                              "each exact shingle match's boundary -- see compare_two_papers.py's "
                              "find_shingle_matches()/_extend_xdrop() docstrings. Default (None) "
                              "preserves exact-only matching.")
    parser.add_argument("--mismatch-penalty", type=int, default=1,
                         help="score subtracted per mismatched word during --x-drop extension; only "
                              "meaningful when --x-drop is set")
    parser.add_argument("--gap-open", type=int, nargs="?", const=DEFAULT_GAP_OPEN, default=None,
                         help=f"upgrade extension from --x-drop's lockstep walk to a GAPPED one that "
                              f"bridges inserted/deleted words as well as substituted ones -- see "
                              f"compare_two_papers.py's _extend_gapped(). Needs --x-drop (that is "
                              f"the threshold it stops on); 8 suits a gapped run better than the 3 "
                              f"that suits lockstep. Bare flag = {DEFAULT_GAP_OPEN}. Runs get longer "
                              f"and fewer, and both views render the indels honestly.")
    parser.add_argument("--gap-extend", type=int, default=DEFAULT_GAP_EXTEND,
                         help=f"score per further word of an already-open gap (default "
                              f"{DEFAULT_GAP_EXTEND}); only meaningful with --gap-open")
    parser.add_argument("--max-gap", type=int, default=DEFAULT_MAX_GAP,
                         help=f"longest single insertion/deletion --gap-open bridges, in words "
                              f"(default {DEFAULT_MAX_GAP}); a longer one stays two runs")
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
    if args.gap_open is not None and args.x_drop is None:
        raise SystemExit("--gap-open needs --x-drop as well: x-drop is the score-drop threshold the "
                          "gapped extension stops on (see compare_two_papers.py's _extend_gapped()). "
                          "Try --x-drop 8.")
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
    skipped_degenerate = 0
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
        try:
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
                neutral=args.neutral,
                library_db_path=args.library_db,
                whole_document=args.whole_document,
                max_gap_words=args.max_gap_words,
                gap_open=args.gap_open,
                gap_extend=args.gap_extend,
                max_gap=args.max_gap,
            )
        except DegenerateGappedPair as exc:
            # Skip the pair rather than abort a multi-pair run; a single --ids/--paper-ids run
            # prints the same line and writes nothing, which is the honest outcome.
            skipped_degenerate += 1
            print(f"  skipped {pid1}/{pid2}: --gap-open refused -- {exc}")
            continue
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
    if skipped_degenerate:
        print(f"{skipped_degenerate} pair(s) skipped: too repetitive for --gap-open to finish "
              f"(compare_two_papers.MAX_GAPPED_SEED_HITS) -- drop --gap-open for those")
    if skipped_identical_titles:
        print(f"{skipped_identical_titles} pair(s) skipped entirely (identical titles) -- "
              f"drop --skip-identical-titles to write them anyway")
    if skipped_year_gap:
        print(f"{skipped_year_gap} pair(s) skipped entirely (below --min-year-gap "
              f"{args.min_year_gap}) -- lower --min-year-gap to write them anyway")

    conn.close()


if __name__ == "__main__":
    main()

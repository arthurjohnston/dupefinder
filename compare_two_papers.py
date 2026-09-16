#!/usr/bin/env python3
"""Exhaustive, sentence-level comparison of exactly two already-known papers -- for when a
pair has already been narrowed down by the normal pipeline (LSH candidate generation +
review) and is worth a much closer, much more expensive look than corpus-scale ever allows.

Why this is a genuinely different tool, not just build_dupe_candidates.py run smaller:
the corpus-wide pipeline is deliberately approximate in two ways that don't matter at corpus
scale but matter a great deal once you already know which two documents you care about:

1. **LSH bucketing is approximate by design** (see lsh_index.py) -- two paragraphs can fail
   to land in a shared bucket and simply never become a candidate pair at all, even if they'd
   score well above threshold on a direct comparison. Fine when the alternative is O(corpus^2)
   comparisons; not fine when the alternative is comparing two known documents directly, which
   is cheap.
2. **The whole pipeline compares at PARAGRAPH granularity.** Real patchwriting -- the thing
   this tool exists to catch -- often shows up as one or two sentences within an otherwise
   original paragraph, or a paragraph where every sentence has been individually reworded
   just enough that the paragraph's OWN embedding similarity drops under threshold even
   though several of its individual sentences, compared one at a time, would not have. A
   corpus-wide paragraph-level scan structurally cannot see this; a direct sentence-level
   comparison of two known documents can.

Given only two documents, brute-force sentence-by-sentence comparison is completely
affordable (a ~2000-sentence document against another ~2000-sentence document is a 2000x2000
cosine matrix -- milliseconds) even though it would never scale to a whole corpus.

Sentence splitting is regex-based (a small abbreviation guard list plus a split-on-boundary
regex), not a real NLP tokenizer -- consistent with this project's existing dependency-free
conventions (see review_dupes.py's english_score(), retrieve_papers.py's title_similarity()).
Known gaps: an abbreviation not in ABBREVIATIONS will still cause a false split; a sentence
that itself contains an in-text citation with a period-then-lowercase continuation
("...(Author, 2001). the finding...") won't be rejoined. Good enough for surfacing candidate
matches for a human to read in context (each result shows the full source paragraph, not just
the isolated sentence), not intended as ground truth for exact sentence boundaries.

Ranking: sorted by lcs_ratio (longest verbatim word run) DESCENDING, not by cosine
similarity -- per text_overlap.py's own docstring, lcs_ratio is "the strongest single signal
for 'this is copied, not just related'", which is exactly the question this tool exists to
answer. Cosine similarity is used only as the cheap first-pass filter (--min-similarity) to
avoid computing the more expensive lcs_ratio/ngram_jaccard on every one of the N*M pairs.

Usage:
    python3 compare_two_papers.py --library-db anthropology/library.sqlite3 \\
        --paper-a 16422 --paper-b 23413 [--min-similarity 0.6] [--min-lcs-ratio 0.0] \\
        [--top-n 100] [--out report.txt] [--append-to-writeup path/to/WRITEUP.md]

`--append-to-writeup` is the recommended way to run this for a case that's getting written up (see
REVIEWING.md's "Writing up a confirmed case"): rather than leaving the full findings in a separate
--out file that a writeup merely references (easy to forget, easy for the writeup and the underlying
evidence to drift apart), it appends the complete, unedited report -- source links included -- directly
onto the end of the writeup file itself, so the full evidence always travels with the document making
claims about it.
"""
import argparse
import re
from pathlib import Path

import numpy as np

import db
import text_overlap as to

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# A regex sentence splitter, not a real tokenizer -- see module docstring for known gaps.
# Splits on . ! ? followed by whitespace and a capital letter (or an opening quote/paren),
# but first protects a short list of common abbreviations from being treated as sentence
# boundaries by temporarily swapping their period for a placeholder token that can't appear
# in real text, then swapping it back after splitting.
ABBREVIATIONS = [
    "Dr", "Mr", "Mrs", "Ms", "Prof", "Fig", "figs", "pp", "vol", "vols", "eds", "ed",
    "et al", "e.g", "i.e", "cf", "vs", "no", "Vol", "etc", "Jr", "Sr", "St",
]
_ABBREV_RE = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in ABBREVIATIONS) + r")\.",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9("“])')
_PLACEHOLDER_TOKEN = "@@DUPEFINDER_ABBREV_PERIOD@@"


def split_sentences(text):
    """Best-effort sentence split -- see module docstring for the known gaps this doesn't
    handle. Returns a list of non-empty, whitespace-stripped sentences."""
    guarded = _ABBREV_RE.sub(lambda m: m.group(1) + _PLACEHOLDER_TOKEN, text)
    parts = _SENTENCE_SPLIT_RE.split(guarded)
    return [p.replace(_PLACEHOLDER_TOKEN, ".").strip() for p in parts if p.strip()]


def load_paper_sentences(conn, paper_id):
    """Returns [(para_index, sentence_index, sentence_text, full_paragraph_text), ...] for
    every sentence in every paragraph of one paper, in document order. Reads from the
    already-extracted `paragraphs` table (paragraph text, not embeddings -- this tool
    re-embeds at sentence granularity itself) rather than re-parsing the source PDF, so it
    reflects exactly what the rest of the pipeline already considers this paper's content to
    be."""
    rows = conn.execute(
        "SELECT para_index, text FROM paragraphs WHERE paper_id = ? ORDER BY para_index",
        (paper_id,),
    ).fetchall()
    sentences = []
    for para_index, para_text in rows:
        for sent_index, sentence in enumerate(split_sentences(para_text)):
            if len(sentence.split()) < 4:
                continue  # a fragment this short is never a meaningful patchwriting signal on its own
            sentences.append((para_index, sent_index, sentence, para_text))
    return sentences


_WORD_RE = re.compile(r"\S+")


def load_paper_words(conn, paper_id):
    """Returns [(word, para_index), ...] for every word in the whole document, in reading
    order, spanning paragraph boundaries -- unlike load_paper_sentences() above, this makes
    no attempt to find sentence boundaries at all. That's the point: find_shingle_matches()
    below needs a single flat token stream to slide a window across, and a wrong sentence
    split (this module's own known gap -- see its docstring) would otherwise silently cut a
    real verbatim run in half right at the split point. Word casing is preserved for display;
    matching itself is done case-insensitively by the caller."""
    rows = conn.execute(
        "SELECT para_index, text FROM paragraphs WHERE paper_id = ? ORDER BY para_index",
        (paper_id,),
    ).fetchall()
    words = []
    for para_index, para_text in rows:
        for w in _WORD_RE.findall(para_text):
            words.append((w, para_index))
    return words


def _extend_xdrop(lower_a, lower_b, pos_a, pos_b, direction, mismatch_penalty, x_drop):
    """Extend a match from (pos_a, pos_b) one word at a time in `direction` (+1 forward, -1
    backward), tolerating mismatches via BLAST's seed-and-extend X-drop heuristic: +1 for a
    matching word, -mismatch_penalty for a differing one, tracked as a running score against
    its own running maximum. Extension stops once the current score falls more than x_drop
    below that maximum -- then the result is trimmed back to wherever the maximum actually
    occurred, discarding the trailing mismatches that triggered the stop (they never "paid for
    themselves"). This is what lets a single differently-spelled word in the middle of an
    otherwise-verbatim passage get bridged instead of prematurely ending the match (found for
    real in case 29's fintech-review pair -- see todo.md), while a genuine divergence into
    unrelated text still stops the extension quickly: an isolated substitution costs
    mismatch_penalty and is immediately recovered by the next match, but a cluster of them
    compounds until the drop exceeds x_drop.

    Returns the new boundary position (exclusive going forward, exclusive-as-a-start going
    backward) at the best-score point -- identical to `pos_a`/`pos_b` if extension immediately
    made things worse, i.e. this never returns a WORSE boundary than what was passed in."""
    score = best_score = 0
    a = best_a = pos_a
    b = best_b = pos_b
    len_a, len_b = len(lower_a), len(lower_b)
    while True:
        if direction == 1:
            if a >= len_a or b >= len_b:
                break
            match = lower_a[a] == lower_b[b]
        else:
            if a <= 0 or b <= 0:
                break
            match = lower_a[a - 1] == lower_b[b - 1]
        score += 1 if match else -mismatch_penalty
        a += direction
        b += direction
        if score > best_score:
            best_score, best_a, best_b = score, a, b
        elif best_score - score > x_drop:
            break
    return best_a, best_b


def find_shingle_matches(words_a, words_b, shingle_size=6, mismatch_penalty=1, x_drop=None):
    """Word n-gram ("shingle") fingerprinting across two full documents -- a completely
    different, purely lexical detection mechanism from the embedding-based sentence
    comparison above, deliberately run alongside it rather than instead of it:

    - Doesn't need sentence boundaries at all (works on the flat word stream), so it can't be
      fooled by a wrong sentence split the way the embedding comparison's regex splitter can
      be -- a verbatim run that spans what this tool's own sentence splitter would have
      mis-cut is still found whole here.
    - Doesn't need an embedding model or a similarity threshold -- a shingle either matches
      exactly (case-insensitively) or it doesn't, so this catches verbatim copying with zero
      false negatives from "the embedding similarity happened to fall just under threshold."
    - Catches copying SHORTER than a full sentence (a clause lifted into an otherwise
      original sentence) that a sentence-level comparison structurally cannot isolate, since
      it only ever compares whole sentences to whole sentences.
    - With x_drop left at its default (None), does NOT catch paraphrase/reworded copying at
      all -- a single swapped word breaks every shingle that crosses it, by design (this is
      why it's meant to run ALONGSIDE the embedding-based comparison, not replace it: the two
      methods have complementary blind spots). Setting x_drop enables seed-and-extend
      tolerance for ISOLATED word-level differences specifically (a typo, a spelling variant,
      a synonym swap) within an otherwise-verbatim passage -- see _extend_xdrop()'s docstring
      for the mechanism. It still does not catch real paraphrase (reworded clauses, reordered
      sentences): x_drop bridges a few wrong words in a sea of exact matches, it doesn't align
      two differently-worded passages. It also only handles SUBSTITUTIONS, not insertions/
      deletions -- this is a position-for-position comparison with no gap alignment, so a
      genuinely inserted or deleted word (as opposed to one glued onto an adjacent token with
      no space, e.g. a citation marker like "ecosystem(22)" vs "ecosystem" -- still one token
      each, still a plain substitution) desyncs every word after it, which usually looks like a
      run of consecutive mismatches and stops the extension rather than bridging it. Confirmed
      against a real case (29 in computer-ethics/flagged_cases/) that motivated this feature:
      a US/UK spelling difference ("favorable" vs "favourable") was splitting one real 314-word
      verbatim passage into two separate ~150-word reported runs at shingle_size=10; x_drop=3
      correctly re-merges it into one run with 1 substitution flagged.

    Algorithm: index every shingle_size-word shingle in document A by its lowercased word
    tuple -> list of start positions. For each shingle in document B, look up matching start
    positions in A; for each (pos_a, pos_b) pair found, greedily extend word-by-word in both
    directions while words keep matching exactly (case-insensitive) -- this merges what would
    otherwise be dozens of overlapping shingle_size-word fragments (positions i, i+1, i+2, ...
    all "matching" for one real long run) into one maximal contiguous match per real
    occurrence, and naturally reports true match length rather than the fixed shingle_size.
    Already-covered (pos_a, pos_b) start pairs are skipped on subsequent shingles so the same
    real run is never reported twice. If x_drop is set, each exact run is then further
    extended past its own exact boundary via _extend_xdrop() in both directions, tolerating
    mismatches -- so the reported run can be genuinely longer (and non-identical between the
    two sides) than the purely-exact core that seeded it.

    Returns a list of (length_in_words, para_a, para_b, text_a, text_b, start_a, end_a, start_b,
    end_b, substitutions) tuples, one per maximal run found, sorted by length descending.
    para_a/para_b are the paragraph index the run STARTS in (a long run can span multiple
    paragraphs in either document, e.g. across pdftotext's own paragraph-splitting quirks --
    text_a/text_b show the actual matched words as originally cased, which is what a human
    reading the result wants -- note text_a and text_b are only guaranteed IDENTICAL when
    substitutions is 0; with x_drop set and substitutions > 0 they differ at exactly that many
    word positions). start_a/end_a/start_b/end_b are word indices into the flat
    words_a/words_b lists this function was called with (end exclusive) -- added for a caller
    that needs to render more surrounding context than just the starting paragraph and
    highlight exactly where the match sits within it (write_dupe_reports_html.py's
    render_shingle_exhibits()); words_a[end_a - 1][1] gives the paragraph the run ENDS in, the
    same way words_a[start_a][1] gives para_a. substitutions is always 0 when x_drop is None
    (the pure-exact behavior this function had before x_drop existed, unchanged)."""
    tokens_a = [w for w, _ in words_a]
    tokens_b = [w for w, _ in words_b]
    lower_a = [w.lower() for w in tokens_a]
    lower_b = [w.lower() for w in tokens_b]

    index_a = {}
    for i in range(len(lower_a) - shingle_size + 1):
        shingle = tuple(lower_a[i:i + shingle_size])
        index_a.setdefault(shingle, []).append(i)

    covered_starts = set()  # (pos_a, pos_b) pairs already absorbed into a reported run
    runs = []
    for j in range(len(lower_b) - shingle_size + 1):
        shingle = tuple(lower_b[j:j + shingle_size])
        for i in index_a.get(shingle, ()):
            if (i, j) in covered_starts:
                continue
            # extend forward
            end_a, end_b = i, j
            while (end_a + shingle_size < len(lower_a) and end_b + shingle_size < len(lower_b)
                   and lower_a[end_a + shingle_size] == lower_b[end_b + shingle_size]):
                end_a += 1
                end_b += 1
            end_a += shingle_size
            end_b += shingle_size
            # extend backward
            start_a, start_b = i, j
            while start_a > 0 and start_b > 0 and lower_a[start_a - 1] == lower_b[start_b - 1]:
                start_a -= 1
                start_b -= 1
            exact_length = end_a - start_a
            for k in range(exact_length - shingle_size + 1):
                covered_starts.add((start_a + k, start_b + k))

            substitutions = 0
            if x_drop is not None:
                end_a, end_b = _extend_xdrop(lower_a, lower_b, end_a, end_b, 1, mismatch_penalty, x_drop)
                start_a, start_b = _extend_xdrop(lower_a, lower_b, start_a, start_b, -1, mismatch_penalty, x_drop)
                substitutions = sum(1 for k in range(end_a - start_a) if lower_a[start_a + k] != lower_b[start_b + k])
                # Mark the fuzzy-extended range covered too, not just the exact core -- otherwise
                # every OTHER exact seed within this same bridged run (there will be several,
                # since only the shingle_size-word windows actually touching a substitution fail
                # to match) rediscovers and re-reports the same run from scratch.
                for k in range(end_a - start_a - shingle_size + 1):
                    covered_starts.add((start_a + k, start_b + k))

            length = end_a - start_a
            para_a = words_a[start_a][1]
            para_b = words_b[start_b][1]
            text_a = " ".join(tokens_a[start_a:end_a])
            text_b = " ".join(tokens_b[start_b:end_b])
            runs.append((length, para_a, para_b, text_a, text_b, start_a, end_a, start_b, end_b, substitutions))
    runs.sort(key=lambda r: r[0], reverse=True)
    return runs


def load_model(model_name, logger_print=print):
    from sentence_transformers import SentenceTransformer
    try:
        model = SentenceTransformer(model_name, local_files_only=True)
        logger_print(f"loaded model {model_name} from local cache (offline, no network)")
    except Exception:
        logger_print(f"model {model_name} not fully cached locally -- downloading (first run only, ~90MB)")
        model = SentenceTransformer(model_name, local_files_only=False)
    return model


def embed_sentences(model, sentences, batch_size=64):
    texts = [s[2] for s in sentences]
    if not texts:
        return np.zeros((0, model.get_sentence_embedding_dimension()), dtype=np.float32)
    return model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                         convert_to_numpy=True, show_progress_bar=False).astype(np.float32)


def find_matches(sentences_a, vectors_a, sentences_b, vectors_b, min_similarity):
    """Full brute-force N x M cosine similarity (vectors are unit-normalized, so this is a
    plain matmul -- see embed_paragraphs.py's own docstring for why normalize_embeddings=True
    makes cosine similarity reduce to a dot product). Affordable here specifically because
    N and M are one paper's sentence count each, not a whole corpus -- see module docstring."""
    if len(sentences_a) == 0 or len(sentences_b) == 0:
        return []
    sims = vectors_a @ vectors_b.T
    idx_a, idx_b = np.where(sims >= min_similarity)
    results = []
    for i, j in zip(idx_a, idx_b):
        results.append((float(sims[i, j]), sentences_a[i], sentences_b[j]))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library-db", type=Path, required=True)
    parser.add_argument("--paper-a", type=int, required=True, help="paper_id of the first paper")
    parser.add_argument("--paper-b", type=int, required=True, help="paper_id of the second paper")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--min-similarity", type=float, default=0.6,
                         help="cosine similarity floor for the cheap first-pass filter (default 0.6, "
                              "deliberately more permissive than the corpus-wide pipeline's 0.85 -- "
                              "the whole point of this tool is to catch heavier paraphrasing that a "
                              "stricter bar would exclude; lcs_ratio/ngram_jaccard and your own "
                              "reading are the real filter, not this number)")
    parser.add_argument("--min-lcs-ratio", type=float, default=0.0,
                         help="only show sentence-level results at or above this lcs_ratio (default 0: "
                              "show everything that cleared --min-similarity, sorted so the highest-"
                              "lcs_ratio results are first regardless)")
    parser.add_argument("--top-n", type=int, default=200,
                         help="cap the number of sentence-level results shown/written")
    parser.add_argument("--shingle-size", type=int, default=6,
                         help="word n-gram size for the exact-verbatim shingle scan (default 6 -- see "
                              "find_shingle_matches()'s docstring for why this runs as a second, "
                              "independent method alongside the sentence-level one, not instead of it)")
    parser.add_argument("--top-n-shingles", type=int, default=300,
                         help="cap the number of shingle-match results shown/written")
    parser.add_argument("--x-drop", type=int, default=None,
                         help="enable seed-and-extend tolerance past each exact shingle match's own "
                              "boundary (BLAST-style X-drop -- see find_shingle_matches()'s and "
                              "_extend_xdrop()'s docstrings): bridges an isolated differently-spelled/"
                              "swapped word instead of letting it prematurely cut the run short, "
                              "without turning this into a real paraphrase-tolerant aligner. Default "
                              "(None/omitted) preserves the exact-only behavior this tool always had. "
                              "3 is a reasonable starting point with the default --mismatch-penalty.")
    parser.add_argument("--mismatch-penalty", type=int, default=1,
                         help="score subtracted per mismatched word during --x-drop extension "
                              "(a match always scores +1); only meaningful when --x-drop is set")
    parser.add_argument("--sort-by-position", choices=["a", "b"], default=None,
                         help="show shingle matches (Method 1 only) in reading order of the named "
                              "side's own document (start-of-match word position) instead of the "
                              "default longest-run-first -- useful when one side is known to be the "
                              "later/plagiarizing document and you want to read the evidence the way "
                              "it appears in that document, top to bottom. Applied AFTER --top-n-"
                              "shingles selects which runs to show, same order write_dupe_reports_html.py's "
                              "render_shingle_exhibits() already uses for its HTML exhibits (see its "
                              "own docstring) -- this just exposes the same behavior here. Does not "
                              "affect Method 2 (sentence-level) output, which has no equivalent flag.")
    parser.add_argument("--skip-sentences", action="store_true", help="run only the shingle scan")
    parser.add_argument("--skip-shingles", action="store_true", help="run only the sentence-level scan")
    parser.add_argument("--out", type=Path, default=None, help="also write the full combined report to this file")
    parser.add_argument("--append-to-writeup", type=Path, default=None,
                         help="append the complete report (source links + every match found) to the end of "
                              "this markdown file -- see the module docstring above for why this is the "
                              "recommended way to run the tool for a case being written up, instead of "
                              "leaving the findings in a separate --out file the writeup merely references")
    args = parser.parse_args()

    conn = db.connect(args.library_db)
    paper_rows = dict(conn.execute(
        "SELECT id, title || '|' || COALESCE(doi, '') FROM papers WHERE id IN (?, ?)",
        (args.paper_a, args.paper_b),
    ).fetchall())

    def title_of(pid):
        return paper_rows.get(pid, "?|").split("|", 1)[0]

    def doi_of(pid):
        doi = paper_rows.get(pid, "?|").split("|", 1)[1]
        return doi or None

    def link_line(label, pid):
        title = title_of(pid)
        doi = doi_of(pid)
        if doi:
            return f"{label} ({pid}): {title}\n  https://doi.org/{doi}"
        return f"{label} ({pid}): {title}\n  (no DOI on file for this paper_id)"

    print(f"Paper A ({args.paper_a}): {title_of(args.paper_a)}")
    print(f"Paper B ({args.paper_b}): {title_of(args.paper_b)}")

    lines = []
    lines.append(f"Combined two-paper comparison: paper {args.paper_a} vs paper {args.paper_b}")
    lines.append(link_line("A", args.paper_a))
    lines.append(link_line("B", args.paper_b))
    lines.append("Two independent methods, run separately and reported together (see compare_two_papers.py's "
                 "module docstring and find_shingle_matches()'s docstring for why both, not just one):")
    lines.append("  1. Exact word-shingle matching -- catches verbatim copying regardless of sentence "
                 "boundaries; misses anything paraphrased.")
    lines.append("  2. Sentence-embedding similarity + word-overlap scoring -- catches paraphrase/patchwriting; "
                 "misses verbatim spans shorter than a full sentence or that cross a sentence-split error.")

    if not args.skip_shingles:
        words_a = load_paper_words(conn, args.paper_a)
        words_b = load_paper_words(conn, args.paper_b)
        xdrop_note = f", x-drop {args.x_drop} (mismatch penalty {args.mismatch_penalty})" if args.x_drop is not None else ""
        print(f"{len(words_a)} word(s) in Paper A, {len(words_b)} word(s) in Paper B -- scanning for "
              f"{args.shingle_size}-word exact matches{xdrop_note}...")
        shingle_runs = find_shingle_matches(words_a, words_b, shingle_size=args.shingle_size,
                                             mismatch_penalty=args.mismatch_penalty, x_drop=args.x_drop)
        shown_shingles = shingle_runs[:args.top_n_shingles]
        print(f"{len(shingle_runs)} maximal run(s) found (length >= {args.shingle_size} words), "
              f"showing top {len(shown_shingles)}")

        sort_note = "match length descending"
        if args.sort_by_position:
            # start_a is shown_shingles[i][5], start_b is [i][7] -- see find_shingle_matches()'s
            # return tuple order, unpacked below.
            pos_index = 5 if args.sort_by_position == "a" else 7
            shown_shingles = sorted(shown_shingles, key=lambda r: r[pos_index])
            sort_note = f"reading order of Document {args.sort_by_position.upper()} (start-of-match position)"

        lines.append("")
        lines.append("=" * 100)
        lines.append(f"METHOD 1: exact {args.shingle_size}-word shingle matches{xdrop_note}")
        lines.append(f"{len(shown_shingles)} run(s) shown (of {len(shingle_runs)} total found), sorted by "
                     f"{sort_note}")
        lines.append("=" * 100)
        for length, para_a, para_b, text_a, text_b, _start_a, _end_a, _start_b, _end_b, substitutions in shown_shingles:
            lines.append("")
            if substitutions:
                lines.append(f"near-exact match, {length} word(s), {substitutions} substitution(s)")
            else:
                lines.append(f"exact match, {length} word(s)")
            lines.append(f"  A ¶{para_a}: {text_a}")
            lines.append(f"  B ¶{para_b}: {text_b}")
            lines.append("-" * 100)

    if not args.skip_sentences:
        sentences_a = load_paper_sentences(conn, args.paper_a)
        sentences_b = load_paper_sentences(conn, args.paper_b)
        print(f"{len(sentences_a)} sentence(s) in Paper A, {len(sentences_b)} sentence(s) in Paper B "
              f"-> {len(sentences_a) * len(sentences_b):,} pair(s) to compare")

        model = load_model(args.model)
        vectors_a = embed_sentences(model, sentences_a)
        vectors_b = embed_sentences(model, sentences_b)

        raw_matches = find_matches(sentences_a, vectors_a, sentences_b, vectors_b, args.min_similarity)
        print(f"{len(raw_matches)} pair(s) >= {args.min_similarity} cosine similarity -- scoring text overlap...")

        scored = []
        for sim, sent_a, sent_b in raw_matches:
            lcs = to.longest_common_word_run(sent_a[2], sent_b[2])
            if lcs < args.min_lcs_ratio:
                continue
            ngram = to.ngram_jaccard(sent_a[2], sent_b[2])
            scored.append((lcs, ngram, sim, sent_a, sent_b))
        scored.sort(key=lambda r: r[0], reverse=True)
        scored = scored[:args.top_n]

        lines.append("")
        lines.append("=" * 100)
        lines.append("METHOD 2: sentence-level semantic/paraphrase matches")
        lines.append(f"{len(scored)} match(es) shown (of {len(raw_matches)} above min-similarity="
                     f"{args.min_similarity}), sorted by lcs_ratio descending")
        lines.append("=" * 100)
        for lcs, ngram, sim, sent_a, sent_b in scored:
            para_a, sidx_a, text_a, full_para_a = sent_a
            para_b, sidx_b, text_b, full_para_b = sent_b
            lines.append("")
            lines.append(f"lcs_ratio={lcs:.3f}  ngram_jaccard={ngram:.3f}  cosine={sim:.3f}")
            lines.append(f"  A ¶{para_a}: {text_a}")
            lines.append(f"  B ¶{para_b}: {text_b}")
            lines.append("-" * 100)

    report = "\n".join(lines)
    print(report)
    if args.out:
        args.out.write_text(report)
        print(f"\nwrote {args.out}")
    if args.append_to_writeup:
        # Appended, never overwritten -- a writeup's prose above this section is hand-authored
        # and must survive a re-run. No de-duplication against a prior append of the same pair:
        # this is meant to be run once per pair as the last step before publishing a writeup (see
        # REVIEWING.md's "Writing up a confirmed case"), not repeatedly against the same file --
        # if you do need to re-run it, remove the previous "Full comparison output" section by hand
        # first rather than letting two copies accumulate.
        heading = (
            "\n\n---\n\n## Full comparison output (compare_two_papers.py)\n\n"
            "*Complete, unedited output of both methods below -- every match found, not just the "
            "highlights discussed above. Regenerate with the command in this section's first line "
            "if the underlying papers or thresholds change.*\n\n"
            f"`python3 compare_two_papers.py --library-db <corpus>/library.sqlite3 "
            f"--paper-a {args.paper_a} --paper-b {args.paper_b}"
            f"{f' --shingle-size {args.shingle_size}' if args.shingle_size != 6 else ''}"
            f"{f' --x-drop {args.x_drop}' if args.x_drop is not None else ''}"
            f"{f' --mismatch-penalty {args.mismatch_penalty}' if args.x_drop is not None and args.mismatch_penalty != 1 else ''}"
            f"{f' --min-similarity {args.min_similarity}' if args.min_similarity != 0.6 else ''}`\n\n"
            "```text\n" + report + "\n```\n"
        )
        with args.append_to_writeup.open("a") as f:
            f.write(heading)
        print(f"\nappended full findings to {args.append_to_writeup}")


if __name__ == "__main__":
    main()

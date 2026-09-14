#!/usr/bin/env python3
"""Find and register papers that are the same underlying work retrieved/
cataloged twice under two different `papers` rows -- the "duplicate
retrieval" gap the 2026-08-21 full-corpus plagiarism audit found accounts
for a real chunk of every cross-paper review queue (705 potential_dupes
rows that session alone: title-case variants, a typo, an HTML-entity-
corrupted title, a `Correction:` notice vs. the original, book-vs-own-
chapter DOIs, journal-issue-PDF-vs-its-own-article). See todo.md's "Full-
corpus plagiarism audit" section for the numbers and reasoning.

Three tiers:
  1. **Exact DOI match** (case-insensitive, both non-NULL) -- the two rows
     ARE the same registered work, full stop.
  2. **Exact match after title normalization** (HTML-entity-unescaped,
     lowercased, punctuation stripped, whitespace collapsed) **and a shared
     author** (fuzzy-matched, see `authors_overlap()`) -- catches title-case
     variants, stray HTML entities (`&amp;`), and punctuation/quote-mark
     differences, which is what most of this session's 705 found duplicates
     actually were. The author requirement was added 2026-09-06 (see "Real
     bug found" section below) after this tier's original title-only version
     was caught silently merging genuine cross-author paper-mill duplicates
     as "same paper," permanently erasing them from the review queue.
  3. **Exact-content overlap** (added 2026-08-24, todo.md's "review 200 at
     random" investigation): >=50% of the smaller paper's paragraphs have a
     byte-identical text match in the other paper. Catches two real,
     surprisingly common cases DOI/title matching structurally cannot --
     both have legitimately DIFFERENT DOIs and titles, so tiers 1-2 never
     see them: (a) the same multi-article PDF (a conference abstract
     supplement, a merged journal issue) served as the "open-access copy"
     for two or more different individual-article DOIs, so each ends up
     with the *entire* multi-article document's paragraphs, not just its
     own article's (near-100% overlap, both directions); (b) a book/
     handbook retrieved as one `papers` row and one of its own chapters
     retrieved separately under the chapter's own DOI (50-80% overlap,
     since the chapter's paragraphs are a genuine subset of the book's, not
     the reverse). Validated against this corpus's real data before
     trusting the threshold: of 266 candidate pairs checked (>=5 shared
     `potential_dupes` rows each, i.e. pairs the LSH/cosine pipeline had
     already flagged as candidates anyway), 174 cleared 50% overlap, split
     19/20/135 across 50-80%/80-95%/95-100% buckets -- every one spot-
     checked was case (a) or (b) above, zero false positives at >=50%.

     **Asymmetric-containment guard** (added 2026-09-06, see "Real bug
     found" section below): a pair is NOT merged here -- just logged as a
     "needs a manual look" warning -- when the overlap is asymmetric (the
     LARGER paper's own coverage, exact-matched paragraphs / larger paper's
     total, stays below `ASYMMETRIC_CONTAINMENT_MAX_LARGER_FRACTION`) *and*
     the two share no author. This is the signature of one paper's PDF
     actually being a bundled multi-article page (e.g. a journal's combined
     "Letters to the Editor" page holding three separately-authored,
     separately-DOI'd letters) rather than genuinely the same work retrieved
     twice -- found via a real case in this corpus, see below. Deliberately
     does NOT apply when the two DO share an author, since this tier's own
     validated book-vs-its-own-chapter case has exactly this asymmetric
     shape (a whole book is not "covered" by one chapter) and should keep
     merging -- the guard is specifically for the no-shared-author case the
     original 266-pair validation didn't separately break out, so some
     genuinely-legitimate book/chapter pairs with disjoint authorship
     (e.g. an edited volume where the book record lists only the editor)
     may now go unmerged too. That's an accepted, deliberate trade-off: an
     unmerged pair just sits in the ordinary review queue instead of being
     silently resolved, which is the cheap failure mode here, not a costly
     one -- re-validate this guard's false-negative rate on the book/chapter
     population specifically if it starts generating noticeable noise.

This tier is new precision this file's own earlier design deliberately
declined to attempt via DOI-suffix pattern matching or fuzzy title
comparison ("book-vs-chapter DOI-suffix detection... needs real judgment").
Exact-content-overlap fraction turned out to be a different, much more
reliable signal for the same underlying question -- it doesn't infer "these
might be the same work" from *metadata* pattern-matching (which really does
need judgment, since DOI suffixes and titles vary unpredictably by
publisher), it directly measures whether the *actual retrieved text* is
one document's content nested inside another's, which two independently-
written papers essentially never exhibit by coincidence at this magnitude.

Deliberately still does NOT do fuzzy/semantic *title* matching (typo
tolerance, reworded-title preprint-vs-published pairs) -- that's a
genuinely different case (different text, not overlapping content) where a
specific instance really can go either way, and is exactly what
review_dupes.py's (p)apers-are-the-same key is for.

For every pair found: registers it in a new `duplicate_papers` table
(paper_id_1 < paper_id_2, reason, detected_at) and applies the same
same_paper=1 correction review_dupes.py's mark_same_paper() applies by
hand (same_author/earlier_paper_id/later_paper_id/later_cites_earlier
nulled) to every EXISTING potential_dupes row between the pair --
deliberately leaves status/reviewed_at alone, same convention
mark_same_paper() documents (a paper-identity correction, not a verdict on
text).

`duplicate_papers` also gets consulted going forward: build_dupe_candidates.py
skips generating new candidates between a pair already registered here (see
its own load_duplicate_paper_pairs() import of this module) -- this is the
actual preventive half, so future extract_papers.py runs against a
duplicate-retrieved paper don't repopulate the review queue with the same
"cross-paper" noise all over again. Re-run this script itself periodically
(safe/idempotent -- INSERT OR IGNORE on the pair) as new papers are added.

## Real bug found in the process: `find_duplicate_papers.py`'s title tier blindly merged as `same_paper`

Tier 2's original version added every group of 2-3 papers sharing a normalized title to the same
unconditional `mark_same_paper()` path as tier 1's exact-DOI matches -- with no check that the two
papers were actually the same work, just that the title string matched. This conflated three very
different real situations under one identical-looking DB row:
1. **Genuinely the same paper, retrieved twice** (the tier's actual intended purpose) -- correct to merge.
2. **Coincidentally identical generic titles, genuinely different documents** -- e.g. three different
   people's "Review of Zuboff's *The Age of Surveillance Capitalism*" in the same journal issue.
3. **Identical title, different named authors, substantially the same text** -- exactly this project's
   own confirmed paper-mill signature -- and this is the dangerous one: `mark_same_paper()` nulls
   `same_author`/`earlier_paper_id`/etc. on every `potential_dupes` row for the pair and
   `build_dupe_candidates.py` treats a `duplicate_papers`-registered pair as settled "from
   candidate-generation time forward" per its own docstring -- so a real plagiarism finding gets
   silently and permanently removed from the human/AI review pool, with nothing in the data to
   indicate anything suspicious happened. This is exactly how case 07 (computer-ethics's
   `flagged_cases/07-.../WRITEUP.md`) got its own `potential_dupes` rows quietly re-merged as
   `same_paper=1` by a `find_duplicate_papers.py` re-run, immediately after being confirmed as a real
   cross-author duplicate.

**Fixed 2026-09-06** by requiring `authors_overlap()` (fuzzy, normalized author-name matching, same
0.82 `difflib` ratio bar `retrieve_papers.py`/`resolve_author_openalex_ids.py` use elsewhere in this
project) between the two papers before tier 2 merges them -- case 3 above (disjoint authorship) no
longer merges; case 1 (same authors, reformatted title) still does; case 2 mostly has no linked authors
at all on generic publisher-furniture titles and was already excluded by the group-size/title-length
guards below. An accompanying reconciliation pass (`reconcile_existing_pairs()`, run automatically as
part of `run()`) re-validates every *already-registered* `duplicate_papers` row against this new
criterion and reverts any that no longer qualify -- both `duplicate_papers` and the `potential_dupes`
rows it had touched (same_paper/same_author/ai_check reset for a fresh look, status/reviewed_at
untouched, matching this file's own revert convention already used once by hand for 16 computer-ethics
pairs -- see todo.md).

**Real motivating case found while validating the fix, anthropology corpus**: three papers all titled
"The future cost of cancer in South Africa: An interdisciplinary cost management strategy" (paper_ids
53388, 55824, 60978) were ALL merged together as one `same_paper` group by the old tier 2, because they
share the identical title. In fact only two of the three (55824, 60978) are genuinely the same short
106-word erratum notice, registered under two DOI-formatting variants of the same registration
(`10.7196/samj.2016.v106.i12.12182` vs `10.7196/samj.2017.v106i12.12182`) and both credited to the same
single author ("C Naidu") -- correctly still merges under the new author check. The third (53388) is
the original guest editorial *that the erratum corrects*, by five entirely different authors (Sartorius
et al.) -- correctly no longer merges with either of the other two under the new check.

## Second real bug found: PDF-bundling artifacts wrongly merged by tier 3

Investigating a separate weak/rejected candidate turned up `paper_id` 59421's PDF: nominally the
single-DOI record for a Siegel & Sacks "Letters to the Editor" reply, but the actual downloaded PDF is
the publisher's *combined* Letters page for that journal issue, holding THREE separately-authored,
separately-DOI'd letters back to back (Carol Marcus's original letter, Siegel & Sacks's own letter, and
Weber & Zanzonico's reply to both). Before this fix, tier 2 had merged 59421 with Marcus's paper (59420,
via matching titles -- both are titled "Eliminating Use of the Linear No-Threshold Assumption in Medical
Imaging") and tier 3 had separately merged it with Weber & Zanzonico's paper (59970, via 83% content
overlap) -- both wrongly, since these are three genuinely different, disjointly-authored documents that
happen to sit in one bloated PDF, not the same paper retrieved twice. The author-overlap fix above
already resolves the tier-2 half (Marcus != Siegel & Sacks); the asymmetric-containment guard documented
above (in tier 3's own section) resolves the tier-3 half (Weber & Zanzonico's short reply is ~83%
contained in 59421's 18-paragraph bundle, but 59421 is only ~28% covered by Weber & Zanzonico's 6
paragraphs -- an asymmetry the old symmetric-only check never looked for). Neither fix repairs the
underlying bad PDF itself (paper 59421's extracted paragraphs still include all three letters' text,
which will keep generating spurious cross-paper candidates against whichever of the other two a future
embedding-similarity pass happens to catch) -- that would need a targeted re-retrieval or a manual
paragraph-range trim, not attempted here. Same failure category as the already-documented paper_id 11721
case (a DOI resolving to a whole journal issue rather than one article) -- not a one-off.
"""

import argparse
import html
import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("find_duplicate_papers")

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title(title):
    """HTML-unescape, lowercase, strip everything but letters/digits/space,
    collapse whitespace -- deliberately aggressive (loses real information
    like hyphenation) because this is only ever used to ask "are these two
    titles the same string modulo formatting noise", not to compare
    different titles for similarity."""
    unescaped = html.unescape(title)
    stripped = _NORMALIZE_RE.sub("", unescaped.lower())
    return _WHITESPACE_RE.sub(" ", stripped).strip()


_COMMA_SPLIT_RE = re.compile(r"\s*,\s*")
_AUTHOR_PUNCT_RE = re.compile(r"[^\w\s]")
AUTHOR_MATCH_THRESHOLD = 0.82  # same bar as retrieve_papers.py's Crossref title-match acceptance (see its own docstring)


def normalize_author_name(name):
    """Lowercase, strip accents/punctuation, and flip "Last, First[, ...]"
    to "First Last" -- this corpus mixes both orderings depending on which
    upstream source (Crossref/DataCite/CORE) supplied the author list. Only
    flips on the FIRST comma (a "Last, First, Jr." suffix stays attached to
    First rather than becoming its own token). Kept as a local copy rather
    than importing resolve_author_openalex_ids.py's near-identical function,
    to keep this lightweight maintenance script free of that module's
    requests/retrieve_papers dependency chain for what is otherwise a
    pure-string helper."""
    if not name:
        return ""
    parts = _COMMA_SPLIT_RE.split(name.strip(), maxsplit=1)
    if len(parts) == 2:
        name = f"{parts[1]} {parts[0]}"
    unescaped = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    stripped = _AUTHOR_PUNCT_RE.sub(" ", unescaped.lower())
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def authors_overlap(names_a, names_b):
    """True if any author on one side fuzzy-matches (>=AUTHOR_MATCH_THRESHOLD,
    difflib ratio, after normalize_author_name()) any author on the other --
    deliberately fuzzy, not exact string equality, since this corpus's
    author-name formatting is inconsistent across sources ("Deepak P" vs
    "Deepak P.", "Rebecca Salganik" vs "salganik, rebecca" -- see todo.md's
    documented same_author name-format gap). Returns False, not "unknown",
    when either side has zero linked authors -- an empty author list is a
    real possibility (extraction failure, or genuinely author-less front
    matter) but is never treated as a wildcard match here: a missed merge
    just leaves the pair in the ordinary review queue, which is the safe
    failure mode for the title/content-overlap tiers this feeds."""
    if not names_a or not names_b:
        return False
    normed_a = [normalize_author_name(n) for n in names_a]
    normed_b = [normalize_author_name(n) for n in names_b]
    return any(
        SequenceMatcher(None, na, nb).ratio() >= AUTHOR_MATCH_THRESHOLD
        for na in normed_a if na
        for nb in normed_b if nb
    )


def load_author_names(conn):
    """{paper_id: [author name, ...]} for every paper with at least one
    linked author row. A paper absent from this dict has zero linked
    authors (extraction failure or genuinely author-less)."""
    rows = conn.execute(
        """
        SELECT pa.paper_id, a.name
        FROM paper_authors pa JOIN authors a ON a.id = pa.author_id
        """
    ).fetchall()
    names = {}
    for paper_id, name in rows:
        names.setdefault(paper_id, []).append(name)
    return names


ASYMMETRIC_CONTAINMENT_MAX_LARGER_FRACTION = 0.5  # see tier 3's docstring section above


def _is_likely_bundling_artifact(frac_larger, names_a, names_b):
    """True when the overlap is asymmetric (the larger paper's own coverage
    stays below ASYMMETRIC_CONTAINMENT_MAX_LARGER_FRACTION even though the
    smaller side already cleared min_fraction) AND the two papers share no
    author -- the signature of one paper's PDF being a bundled multi-article
    page rather than genuinely the same underlying work. See module
    docstring's "Second real bug found" section for the motivating case."""
    return frac_larger < ASYMMETRIC_CONTAINMENT_MAX_LARGER_FRACTION and not authors_overlap(names_a, names_b)


def init_table(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS duplicate_papers (
            paper_id_1 INTEGER NOT NULL REFERENCES papers(id),
            paper_id_2 INTEGER NOT NULL REFERENCES papers(id),
            reason TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            PRIMARY KEY (paper_id_1, paper_id_2)
        );
        """
    )
    conn.commit()


def find_pairs(conn, logger_=None):
    """Returns a list of (paper_id_1, paper_id_2, reason) with
    paper_id_1 < paper_id_2, deduplicated across both detection tiers.

    Both tiers require the matching group to have MAX_GROUP_SIZE (3) or
    fewer members, and the title tier additionally requires the normalized
    title to be at least MIN_TITLE_WORDS (4) significant (4+ letter) words
    long AND (as of 2026-09-06) at least one shared author between the two
    specific papers being paired -- see module docstring's "Real bug found"
    section. Group-size/length guards are necessary, not just cautious: the
    first real run of this function against this project's actual
    70,041-paper corpus, without these guards, produced 3,545 "duplicate"
    pairs from title-normalization alone -- almost all of them wrong.
    Generic recurring publisher furniture ("Front Cover" 561 papers,
    "Masthead" 528, "Reliability Society" 435, "Abstract" 231, "Table of
    Contents" 66, "Introduction" 45, "Editorial" 28) collapsed into one
    giant group per short title, each group actually hundreds of genuinely
    different magazine issues, not one document retrieved twice. A
    structurally-generated title pattern did the same at longer lengths --
    171 papers all titled "Review of <the same submission title>" (an
    OpenReview-style per-reviewer report title, one genuinely different
    document per reviewer) -- which is exactly why a length floor alone
    isn't enough and the group-size cap is the real fix: an accidental
    duplicate retrieval produces a *pair*, not a cluster of hundreds."""
    MAX_GROUP_SIZE = 3
    MIN_TITLE_WORDS = 4
    SIGNIFICANT_WORD_RE = re.compile(r"[a-z0-9]{4,}")

    rows = conn.execute("SELECT id, doi, title FROM papers").fetchall()
    if logger_:
        logger_.info("tier 1/2: checking %d papers for exact DOI/title matches...", len(rows))

    author_names = load_author_names(conn)
    pairs = {}  # (id_1, id_2) -> reason, first reason found wins

    def add_pairs_from_group(ids, reason):
        if not (2 <= len(ids) <= MAX_GROUP_SIZE):
            return
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.setdefault((ids[i], ids[j]), reason)

    by_doi = {}
    for paper_id, doi, _ in rows:
        if not doi:
            continue
        by_doi.setdefault(doi.strip().lower(), []).append(paper_id)
    for doi, ids in by_doi.items():
        add_pairs_from_group(ids, f"same DOI ({doi})")

    by_norm_title = {}
    for paper_id, _, title in rows:
        norm = normalize_title(title)
        if len(SIGNIFICANT_WORD_RE.findall(norm)) < MIN_TITLE_WORDS:
            continue
        by_norm_title.setdefault(norm, []).append(paper_id)
    for norm, ids in by_norm_title.items():
        if not (2 <= len(ids) <= MAX_GROUP_SIZE):
            continue
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                id_i, id_j = ids[i], ids[j]
                if authors_overlap(author_names.get(id_i, []), author_names.get(id_j, [])):
                    pairs.setdefault((id_i, id_j), f"same title after normalization, shared author ({norm[:80]!r})")

    if logger_:
        logger_.info("tier 1/2: %d pair(s) found. starting tier 3 (exact-content-overlap)...", len(pairs))

    for a, b, reason in find_content_overlap_pairs(conn, author_names=author_names, logger_=logger_):
        pairs.setdefault((a, b), reason)

    return [(a, b, reason) for (a, b), reason in pairs.items()]


DEFAULT_CONTENT_OVERLAP_MIN_FRACTION = 0.5
DEFAULT_CONTENT_OVERLAP_MIN_PARAGRAPHS = 5


def find_content_overlap_pairs(conn, min_fraction=DEFAULT_CONTENT_OVERLAP_MIN_FRACTION,
                                min_paragraphs=DEFAULT_CONTENT_OVERLAP_MIN_PARAGRAPHS,
                                author_names=None, logger_=None):
    """Tier 3 -- see module docstring for what this catches, the validation
    behind the default threshold, and the asymmetric-containment guard
    added 2026-09-06. Returns (paper_id_1 < paper_id_2, reason) tuples for
    pairs to merge; pairs that look like a bundled-PDF containment artifact
    instead are logged (not returned, not merged) -- see
    _is_likely_bundling_artifact().

    Scoped to distinct paper pairs already present in `potential_dupes`
    (`same_paper=0`), not a fresh O(n^2) scan of the whole corpus -- this
    tier only matters for pairs the embedding-similarity pipeline already
    surfaced as candidates anyway, and restricting to that set keeps this
    cheap (originally "a few dozen seconds" at 266 pairs; grows with the
    corpus though -- logs progress every PROGRESS_INTERVAL pairs below
    specifically because a silent multi-minute run with no incremental
    output was mistaken for a hung process in practice, see todo.md) instead
    of quadratic in the corpus size. `min_paragraphs` guards against an
    unreliable fraction on very short papers (a 2-paragraph paper sharing
    both paragraphs with something else is a coin flip, not a signal)."""
    PROGRESS_INTERVAL = 500
    if author_names is None:
        author_names = load_author_names(conn)
    pairs = conn.execute(
        "SELECT DISTINCT paper_id_1, paper_id_2 FROM potential_dupes WHERE same_paper = 0"
    ).fetchall()
    if logger_:
        logger_.info("tier 3: checking %d candidate pair(s) for exact-content overlap...", len(pairs))

    para_count = {}

    def pcount(pid):
        if pid not in para_count:
            para_count[pid] = conn.execute(
                "SELECT COUNT(*) FROM paragraphs WHERE paper_id=?", (pid,)
            ).fetchone()[0]
        return para_count[pid]

    results = []
    flagged = []
    for i, (a, b) in enumerate(pairs):
        if logger_ and i and i % PROGRESS_INTERVAL == 0:
            logger_.info("tier 3: %d/%d pair(s) checked, %d overlap match(es) so far", i, len(pairs), len(results))
        na, nb = pcount(a), pcount(b)
        smaller = min(na, nb)
        if smaller < min_paragraphs:
            continue
        exact = conn.execute(
            """
            SELECT COUNT(*) FROM paragraphs p1 JOIN paragraphs p2 ON p1.text = p2.text
            WHERE p1.paper_id = ? AND p2.paper_id = ?
            """,
            (a, b),
        ).fetchone()[0]
        frac = exact / smaller
        if frac < min_fraction:
            continue
        larger = max(na, nb)
        frac_larger = exact / larger
        lo, hi = (a, b) if a < b else (b, a)
        if _is_likely_bundling_artifact(frac_larger, author_names.get(a, []), author_names.get(b, [])):
            flagged.append((lo, hi, frac, frac_larger, smaller, larger))
            continue
        results.append((lo, hi, f"content overlap ({frac:.0%} of {smaller} paragraph(s), exact text match)"))
    if logger_ and flagged:
        logger_.warning(
            "tier 3: %d pair(s) skipped as likely bundled-PDF containment artifacts, not merged as "
            "same-paper (high one-sided overlap, no shared author -- needs a manual look): %s",
            len(flagged),
            "; ".join(f"{a}/{b} ({fs:.0%} of {sm} vs {fl:.0%} of {lg})" for a, b, fs, fl, sm, lg in flagged),
        )
    return results


def mark_same_paper(conn, paper_id_1, paper_id_2):
    """Same correction review_dupes.py's mark_same_paper() applies by hand:
    same_paper=1 and the now-vacuous same_author/earlier_paper_id/
    later_paper_id/later_cites_earlier fields nulled, on every existing
    potential_dupes row between this pair. Deliberately does NOT touch
    status/reviewed_at -- see that function's own docstring for why."""
    rows = conn.execute(
        "SELECT id FROM potential_dupes WHERE (paper_id_1=? AND paper_id_2=?) OR (paper_id_1=? AND paper_id_2=?)",
        (paper_id_1, paper_id_2, paper_id_2, paper_id_1),
    ).fetchall()
    ids = [r[0] for r in rows]
    conn.executemany(
        """UPDATE potential_dupes SET same_paper=1, same_author=NULL,
           earlier_paper_id=NULL, later_paper_id=NULL, later_cites_earlier=NULL
           WHERE id=?""",
        [(i,) for i in ids],
    )
    return len(ids)


def _content_overlap_still_valid(conn, paper_id_1, paper_id_2, names_a, names_b):
    """Recomputes tier 3's asymmetric-containment check for an already-
    registered 'content overlap (...)' duplicate_papers row, using the
    paragraph counts/overlap as they stand today (not whatever they were at
    the time of the original merge). Returns True (leave it alone) when
    either paper's paragraph table is empty -- nothing to recompute from --
    rather than reverting on missing data."""
    na = conn.execute("SELECT COUNT(*) FROM paragraphs WHERE paper_id=?", (paper_id_1,)).fetchone()[0]
    nb = conn.execute("SELECT COUNT(*) FROM paragraphs WHERE paper_id=?", (paper_id_2,)).fetchone()[0]
    if na == 0 or nb == 0:
        return True
    exact = conn.execute(
        """
        SELECT COUNT(*) FROM paragraphs p1 JOIN paragraphs p2 ON p1.text = p2.text
        WHERE p1.paper_id = ? AND p2.paper_id = ?
        """,
        (paper_id_1, paper_id_2),
    ).fetchone()[0]
    larger = max(na, nb)
    frac_larger = exact / larger
    return not _is_likely_bundling_artifact(frac_larger, names_a, names_b)


def reconcile_existing_pairs(conn, logger_=None):
    """Re-validate every already-registered `duplicate_papers` row against
    the CURRENT matching logic and revert any that no longer qualify --
    added 2026-09-06 alongside the author-overlap (tier 2) and asymmetric-
    containment (tier 3) fixes documented in the module docstring, so rows
    merged under the old, looser logic get cleaned up automatically instead
    of needing another one-off manual audit like the 16-pair computer-ethics
    revert this same bug already needed once by hand (see todo.md).

    Exact-DOI-match rows (tier 1, reason starting "same DOI") are never
    reconsidered -- DOI equality is definitional, not a heuristic that can
    go stale. A row whose reason doesn't match either recognized prefix is
    left alone rather than guessed at.

    Reverting means: delete the `duplicate_papers` row, and for every
    `potential_dupes` row between that pair currently sitting at
    `same_paper=1`, reset `same_paper=0, same_author=0, ai_check=NULL` for a
    fresh look -- deliberately leaves status/reviewed_at untouched (same
    convention `mark_same_paper()` documents) and deliberately does NOT
    recompute earlier_paper_id/later_paper_id/later_cites_earlier here (a
    subsequent build_dupe_candidates.py run recomputes those fields from
    scratch for any row it revisits anyway, per that script's own upsert
    docstring, so an interim NULL is harmless)."""
    author_names = load_author_names(conn)
    rows = conn.execute("SELECT paper_id_1, paper_id_2, reason FROM duplicate_papers").fetchall()
    reverted = 0
    for paper_id_1, paper_id_2, reason in rows:
        if reason.startswith("same DOI"):
            continue
        names_a = author_names.get(paper_id_1, [])
        names_b = author_names.get(paper_id_2, [])
        if reason.startswith("same title after normalization"):
            still_valid = authors_overlap(names_a, names_b)
        elif reason.startswith("content overlap"):
            still_valid = _content_overlap_still_valid(conn, paper_id_1, paper_id_2, names_a, names_b)
        else:
            still_valid = True
        if still_valid:
            continue

        conn.execute(
            "DELETE FROM duplicate_papers WHERE paper_id_1=? AND paper_id_2=?",
            (paper_id_1, paper_id_2),
        )
        touched = conn.execute(
            """SELECT id FROM potential_dupes
               WHERE ((paper_id_1=? AND paper_id_2=?) OR (paper_id_1=? AND paper_id_2=?)) AND same_paper=1""",
            (paper_id_1, paper_id_2, paper_id_2, paper_id_1),
        ).fetchall()
        for (pd_id,) in touched:
            conn.execute(
                "UPDATE potential_dupes SET same_paper=0, same_author=0, ai_check=NULL WHERE id=?",
                (pd_id,),
            )
        reverted += 1
        if logger_:
            logger_.info(
                "reconcile: reverted stale pair %d/%d (%s) -- %d potential_dupes row(s) reset for a fresh look",
                paper_id_1, paper_id_2, reason, len(touched),
            )
    conn.commit()
    return reverted


def run(conn, logger_=None):
    init_table(conn)

    reverted = reconcile_existing_pairs(conn, logger_=logger_)
    if logger_:
        logger_.info("reconcile: %d stale duplicate_papers pair(s) reverted", reverted)

    pairs = find_pairs(conn, logger_=logger_)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    new_pairs = 0
    candidates_corrected = 0
    for paper_id_1, paper_id_2, reason in pairs:
        cur = conn.execute(
            "INSERT OR IGNORE INTO duplicate_papers (paper_id_1, paper_id_2, reason, detected_at) VALUES (?, ?, ?, ?)",
            (paper_id_1, paper_id_2, reason, now),
        )
        if cur.rowcount:
            new_pairs += 1
        candidates_corrected += mark_same_paper(conn, paper_id_1, paper_id_2)
    conn.commit()

    if logger_:
        logger_.info("%d duplicate-paper pair(s) found (%d new this run), %d existing potential_dupes row(s) corrected",
                      len(pairs), new_pairs, candidates_corrected)
    return len(pairs), new_pairs, candidates_corrected


def parse_args():
    parser = argparse.ArgumentParser(description="Find papers that are the same work retrieved twice.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)
    run(conn, logger_=logger)
    conn.close()


if __name__ == "__main__":
    main()

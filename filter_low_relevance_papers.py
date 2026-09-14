#!/usr/bin/env python3
"""Post-harvest relevance filter for state.sqlite3 -- excludes two kinds of
low-quality/irrelevant papers from the pipeline before extract_papers.py
ever spends a `pdftotext` call on them, let alone an embedding.

Two failure modes this addresses, both found live in the 2026-08-24 CORE +
DataCite theses batch:

1. **Crank self-publishing.** DataCite's `resource-type-id=dissertation`
   filter (bulk_retrieve_theses.py) is self-declared by the depositor, not
   verified by any institution -- Zenodo (DOI prefix 10.5281) lets anyone
   tag an upload "dissertation" with zero oversight. One depositor
   (pseudonymous authors "AIan, CIoud" / "Yamamoto, Takeo", among others)
   has been re-uploading the same handful of amateur "AI consciousness" /
   "unified theory of everything" manuscripts under a fresh DOI every few
   days -- 7 separate DOIs for "Masked Intelligence...", 6 for "The
   Three-Stage Origin of Self-Awareness...", 4 for "The F-Theory: A Unified
   Foundation of Science...", etc. -- inflating keyword-search result
   counts and polluting the corpus with non-academic content that was
   never going to be a real plagiarism source or target.

   Detected generically, not by hardcoding those specific titles/authors:
   any (normalized title, DOI prefix) pair appearing >= CRANK_MIN_COPIES
   (default 3) times in state.sqlite3 is flagged. A real dissertation
   essentially never shares its exact title with 2+ *other* DOIs from the
   same registrar -- this specific repeated-self-revision pattern does.
   Requiring the *same* DOI prefix (not just the same title) keeps this
   from ever colliding with find_duplicate_papers.py's own title-based
   duplicate-retrieval detection, which is a different question (is this
   ONE paper cataloged twice?) from this one (is this depositor spamming
   many DOIs for what was never a real dissertation to begin with?).

2. **Off-topic.** DataCite/CORE keyword search matches a query term
   anywhere in a record's metadata (abstract, subject tags, funder info),
   not just the title -- so a dissertation on emergency-department nursing
   that happens to mention "ethics" in its IRB-approval boilerplate matches
   a search for "digital ethics" even though the dissertation itself has
   nothing to do with computers, AI, or technology. Real numbers from that
   batch: of 265 downloaded theses, keyword-in-title match rate was only
   ~39%, and titles included things like Greek tragedy, Buddhist
   soteriology, food security, and sepsis diagnostics.

   Detected by title-only keyword match against a deliberately broad --
   not phrase-exact, unlike bulk_retrieve_crossref.py's harvest-time
   DEFAULT_KEYWORDS -- topical term list (ON_TOPIC_TITLE_TERMS). False
   negatives here (excluding something that actually was on-topic) are the
   cheaper failure than false positives (leaving off-topic content in a
   plagiarism corpus built specifically around computer/AI/tech ethics),
   so the term list is intentionally generous rather than precise.

Scope: only rows with status='downloaded' AND a non-NULL file_path, that
have NOT already been extracted into library.sqlite3 (checked by
file_path). A row already in library.sqlite3 is left completely alone and
logged, never moved/relabeled -- breaking a file_path an already-extracted
paragraph/citation set still depends on would be a straightforwardly worse
bug than the one this script exists to fix. In other words: this is meant
to run on a freshly downloaded batch *before* extract_papers.py sees it,
not as a retroactive cleanup of papers already mined for paragraphs (that
would need a real deletion/re-extraction pass, a bigger and more dangerous
decision this script deliberately does not make on its own).

Scope also excludes anything before --since (an ISO 8601 UTC timestamp,
matched against state.sqlite3's own `updated_at`) when given. This matters
in practice, not just in theory: bulk_retrieve_crossref.py's --whole-prefix
mode (see its own module docstring) deliberately crawls a low-scrutiny
publisher's ENTIRE catalog with no topical keyword restriction at all --
1,684 pending IAEME (10.34218) rows in this project's own state.sqlite3
would otherwise get swept up by the off-topic check above, undoing that
earlier, deliberate strategic decision. --since lets a run target only a
specific batch (e.g. "just today's CORE + DataCite theses run") without
touching an unrelated backlog that was never meant to be topic-filtered.

Non-destructive: flagged PDFs are *moved* (not deleted) from --papers-dir
into --excluded-dir/<reason>/, and their state.sqlite3 status changes from
'downloaded' to 'excluded_crank' / 'excluded_offtopic' (reason recorded in
the existing `error` column) so extract_papers.py's own
`WHERE status='downloaded'` query skips them from here on -- same
"reversible, auditable, minimal-footprint" discipline as every other
correction script in this codebase (find_duplicate_papers.py's
same_paper=1, review_dupes.py's (p) key). Idempotent: rows already in an
excluded_* status are skipped on re-run, so re-running after a new batch
only evaluates what's new.

Usage:
    python3 filter_low_relevance_papers.py --dry-run   # report counts, touch nothing
    python3 filter_low_relevance_papers.py              # apply
"""

import argparse
import logging
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import db
from find_duplicate_papers import normalize_title

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("filter_low_relevance_papers")

CRANK_MIN_COPIES = 3
_DOI_PREFIX_RE = re.compile(r"^(10\.\d+)/")

# Deliberately broad word/stem-level terms, NOT the exact phrases
# bulk_retrieve_crossref.py's DEFAULT_KEYWORDS searches with -- a title
# saying "Bias in Hiring Algorithms" should match even though it never
# spells out the exact phrase "algorithmic bias". See module docstring
# point 2 for why generous-inclusion is the right failure direction here.
ON_TOPIC_TITLE_TERMS = [
    "ethic", "artificial intelligence", r"\bai\b", "algorithm", "machine learning",
    "deep learning", "neural network", "large language model", r"\bllm\b", "chatbot",
    "privacy", "surveillance", r"\bbias\b", "fairness", "discriminat", "accountab",
    "governance", "automat", "digital right", "data protection", "misinformation",
    "disinformation", "deepfake", "content moderation", "explainab", "trustworthy",
    "robot", "autonomous system", "facial recognition", "predictive polic",
    "differential privacy", "cyberlaw", "cyber law", "computer science", "information system",
    "information technology", "digital divide", "algorithmic", "technology polic",
    "tech polic", "data scien", "big data", "social media", "internet of things",
    r"\biot\b", "cybersecurity", "cyber security", "software pira", "computer crime",
    "digital labour", "digital labor", "human-computer", "human computer interaction",
]
_ON_TOPIC_RE = re.compile("|".join(ON_TOPIC_TITLE_TERMS), re.IGNORECASE)


def is_on_topic_title(title):
    """Public wrapper around _ON_TOPIC_RE -- also used by
    bulk_retrieve_author_works.py to filter at HARVEST time (before
    ever downloading a stranger-to-this-project's off-topic paper),
    not just as this script's own post-download safety net. Same
    generous-inclusion-favored term list either way (see its own
    comment above)."""
    return bool(_ON_TOPIC_RE.search(title or ""))


def already_extracted_file_paths(library_db_path):
    """The set of file_path values already cataloged in library.sqlite3 --
    rows to never touch (see module docstring's Scope section). Returns an
    empty set if library.sqlite3 doesn't exist yet (nothing extracted at all)."""
    if not Path(library_db_path).exists():
        return set()
    conn = db.connect(library_db_path)
    try:
        return {row[0] for row in conn.execute("SELECT file_path FROM papers")}
    finally:
        conn.close()


def load_candidates(conn, already_extracted, since=None):
    """status='downloaded' rows with a real, not-yet-extracted file_path,
    optionally restricted to updated_at >= since (see module docstring's
    Scope section on why --since matters for not colliding with
    --whole-prefix's deliberately topic-agnostic catalog crawls)."""
    if since:
        rows = conn.execute(
            "SELECT key, title, doi, file_path FROM papers "
            "WHERE status='downloaded' AND file_path IS NOT NULL AND updated_at >= ?",
            (since,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT key, title, doi, file_path FROM papers WHERE status='downloaded' AND file_path IS NOT NULL"
        ).fetchall()
    skipped_extracted = [r for r in rows if r[3] in already_extracted]
    if skipped_extracted:
        logger.info("skipping %d already-extracted row(s) (present in library.sqlite3) -- never touched",
                     len(skipped_extracted))
    return [r for r in rows if r[3] not in already_extracted]


def find_crank_keys(candidates):
    """(normalized title, DOI prefix) groups with >= CRANK_MIN_COPIES members.
    Returns {key: (title, doi)} for every row in a flagged group."""
    groups = defaultdict(list)
    for key, title, doi, file_path in candidates:
        m = _DOI_PREFIX_RE.match(doi or "")
        if not m:
            continue
        groups[(normalize_title(title), m.group(1))].append((key, title, doi))
    flagged = {}
    for (norm_title, prefix), members in groups.items():
        if len(members) >= CRANK_MIN_COPIES:
            logger.info("crank: %r (%s) x%d", members[0][1][:70], prefix, len(members))
            for key, title, doi in members:
                flagged[key] = (title, doi)
    return flagged


def find_offtopic_keys(candidates, already_flagged):
    """Rows whose title matches none of ON_TOPIC_TITLE_TERMS -- skips
    anything already flagged crank (a title can only be excluded once,
    and crank is the more specific/confident signal of the two)."""
    flagged = {}
    for key, title, doi, file_path in candidates:
        if key in already_flagged:
            continue
        if not is_on_topic_title(title):
            flagged[key] = (title, doi)
    return flagged


def apply_exclusions(conn, flagged, reason, papers_dir, excluded_dir, dry_run):
    if not flagged:
        return 0
    dest_dir = excluded_dir / reason
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    moved = 0
    for key, (title, doi) in flagged.items():
        row = conn.execute("SELECT file_path FROM papers WHERE key=?", (key,)).fetchone()
        if row is None or row[0] is None:
            continue
        src = Path(row[0])
        if dry_run:
            moved += 1
            continue
        dest = dest_dir / src.name
        new_path = str(dest)
        try:
            if src.exists():
                shutil.move(str(src), dest)
            else:
                logger.warning("file already missing on disk for %r (%s), updating state anyway", title, key)
                new_path = row[0]  # nothing to move; keep the old (already-gone) path on record
        except OSError as exc:
            logger.error("failed to move %s -> %s: %s -- leaving state row untouched", src, dest, exc)
            continue
        conn.execute(
            "UPDATE papers SET status=?, error=?, file_path=?, updated_at=? WHERE key=?",
            (f"excluded_{reason}", f"filter_low_relevance_papers.py: {reason}", new_path, now, key),
        )
        moved += 1
    if not dry_run:
        conn.commit()
    return moved


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=Path("state.sqlite3"))
    p.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    p.add_argument("--papers-dir", type=Path, default=Path("papers"))
    p.add_argument("--excluded-dir", type=Path, default=Path("papers_excluded"))
    p.add_argument("--since", default=None,
                    help="ISO 8601 UTC timestamp (matches state.sqlite3's updated_at format, e.g. "
                         "2026-08-25T01:00:00Z) -- only evaluate rows updated at/after this. Omit to "
                         "evaluate the entire downloaded-but-unextracted backlog (see module docstring's "
                         "Scope section for why this matters with --whole-prefix batches in the mix).")
    p.add_argument("--dry-run", action="store_true", help="Report counts; move/update nothing")
    p.add_argument("--skip-offtopic", action="store_true",
                    help="Only run the crank-cluster check, skip ON_TOPIC_TITLE_TERMS entirely -- that term "
                         "list is specifically computer-ethics-flavored (see its own comment), so running it "
                         "unmodified against a deliberately different-field corpus (e.g. hindawi/, nursing/, "
                         "anthropology/ -- see todo.md's field-diversification writeup) would wrongly strip "
                         "nearly everything. Crank detection (repeated normalized-title + DOI-prefix, most "
                         "often self-published Zenodo spam) has no topic assumption baked in and stays useful "
                         "regardless of field.")
    return p.parse_args()


def main():
    args = parse_args()
    already_extracted = already_extracted_file_paths(args.library_db)
    conn = db.connect(args.db)
    try:
        candidates = load_candidates(conn, already_extracted, since=args.since)
        logger.info("%d candidate downloaded-but-unextracted row(s) to evaluate", len(candidates))

        crank = find_crank_keys(candidates)
        offtopic = {} if args.skip_offtopic else find_offtopic_keys(candidates, crank)

        logger.info("flagged %d crank, %d off-topic (of %d evaluated)", len(crank), len(offtopic), len(candidates))

        crank_moved = apply_exclusions(conn, crank, "crank", args.papers_dir, args.excluded_dir, args.dry_run)
        offtopic_moved = apply_exclusions(conn, offtopic, "offtopic", args.papers_dir, args.excluded_dir, args.dry_run)

        verb = "would move" if args.dry_run else "moved"
        logger.info("%s %d crank + %d off-topic file(s); %d remain status='downloaded'",
                     verb, crank_moved, offtopic_moved, len(candidates) - len(crank) - len(offtopic))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

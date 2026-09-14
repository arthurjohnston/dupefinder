#!/usr/bin/env python3
"""Extract structured metadata + paragraphs from downloaded paper PDFs.

For every paper in state.sqlite3 (retrieve_papers.py's manifest) that was
successfully downloaded, this script:

  1. Extracts the PDF's text via PyMuPDF's block-level layout analysis (see
     pdf_to_text()) -- not `pdftotext`: PyMuPDF groups text into real spatial
     paragraph blocks and gets multi-column reading order right, where
     `pdftotext`'s row-based streaming either merges a whole page's
     paragraphs into one oversized blob (plain mode) or garbles two-column
     text into nonsense (-layout mode).
  2. Takes title/authors/year/doi from state.sqlite3 (Crossref-verified —
     far more reliable than regexing them back out of PDF text).
  3. Heuristically locates the References/Bibliography section and splits
     it into individual raw citation strings.
  4. Splits the remaining body text into paragraphs (already close to
     paragraph-granular after step 1; see split_paragraphs()), then drops
     exact-repeat paragraphs within the same document (e.g. a running header
     or figure caption reprinted on multiple pages) -- these are never
     written to paragraphs.jsonl at all, per todo.md's "don't double store for
     same doc".

Everything except paragraph text is stored in normalized tables in
library.sqlite3 (papers, authors, paper_authors, citations). Paragraph text
is written to paragraphs.jsonl instead — embed_paragraphs.py reads that
file, generates embeddings, and is what actually persists paragraph text
(alongside its embedding) into the database.

Incremental and restartable: a paper is skipped once its file_path row
exists in library.sqlite3's `papers` table (written only after that paper's
paragraphs/citations are fully processed and flushed -- see main()), and
paragraphs.jsonl is *appended* to rather than overwritten. This is what
lets a run against a still-growing corpus (e.g. bulk_retrieve_arxiv.py
downloading in the background) be safely re-run, killed and resumed, or
processed in bounded chunks via --limit, without redoing already-extracted
PDFs or losing already-written paragraph lines. --recompute opts back into
the old from-scratch behavior (re-extract everything, rewrite
paragraphs.jsonl) when you actually want that -- e.g. after changing the
extraction/dedup logic itself.

Citation splitting is heuristic (regex-based), not a full bibliographic
parser: it recovers raw reference strings, not structured
author/title/venue fields. For higher-fidelity citation parsing, a tool
like GROBID would be a natural upgrade.

Parallel across --max-workers processes (default os.cpu_count()): extract_paper() is a pure,
stateless function of one PDF path (no shared state, no I/O beyond reading that one file), so
the CPU-bound PyMuPDF parsing for each pending paper runs in its own worker process via
ProcessPoolExecutor -- real parallelism, not just concurrency, since PyMuPDF's C-extension
parsing doesn't reliably release the GIL for threads to exploit. Only the actual DB/file writes
(upsert_paper/replace_authors/replace_citations, and paragraphs.jsonl's append) happen in the
main process, one paper's result at a time as workers finish -- same single-writer discipline
this project already uses for sqlite3 access elsewhere (see todo.md's "Cross-process retrieval
dedup race" for why more than one writer at a time against the same db/file is asking for
trouble). Results can complete out of (submission) order; nothing here depends on processing
`pending` in list order -- paragraphs.jsonl's lines are self-contained (file_path, para_index)
and embed_paragraphs.py matches them back to a paper by file_path, not by position in the file,
and each paper's own DB writes/paragraph lines only ever depend on that paper's own extraction
result. `--max-workers 1` restores the old fully-sequential behavior (e.g. for a clean
one-paper-at-a-time log to debug a specific extraction failure).
"""

import argparse
import json
import logging
import os
import re
import sqlite3  # only for sqlite3.Row below; connections themselves go through db.connect()
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import fitz  # PyMuPDF

import db

REFERENCES_HEADING_RE = re.compile(
    r"^\s*(references|bibliography|works cited|reference list)\s*$", re.IGNORECASE
)
NUMBERED_CITATION_RE = re.compile(r"\n(?=\s*(\[\d+\]|\d{1,3}[.)]\s))")
LABEL_ONLY_RE = re.compile(r"^\[?\d{1,3}\]?[.)]?\s*$")
MIN_PARAGRAPH_LEN = 150

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("extract_papers")


def pdf_to_text(pdf_path: Path) -> str:
    """Extract text via PyMuPDF's block-level layout analysis, not
    `pdftotext`. PyMuPDF groups each page's text into real spatial blocks
    (paragraphs, headings, captions) from glyph positions, and -- for
    normally-generated academic PDFs, single- or multi-column alike --
    emits those blocks in the PDF content stream's natural order, which in
    practice already is correct reading order (confirmed empirically,
    including on two-column ACL/IEEE-style layouts).

    This replaces two separate problems plain `pdftotext` had: (1) it
    frequently omits a blank line between consecutive body paragraphs on the
    same page, so blank-line-based splitting merged whole pages into
    oversized "paragraphs"; and (2) `pdftotext -layout` -- tried as a fix,
    since it preserves the first-line indentation paragraph starts could be
    detected from -- instead badly garbles multi-column PDFs, interleaving
    left/right column text line-by-line into nonsense (caught by
    tests/run_tests.py's back-test against a known real plagiarism case,
    where it silently dropped a match below the similarity threshold).
    PyMuPDF's blocks are already close to true paragraph granularity for
    both column layouts, so no indentation heuristics are needed on top.

    Blocks are joined with a blank line between them so the rest of the
    pipeline below (split_paragraphs/dehyphenate_and_join), originally
    written for pdftotext's blank-line-delimited output, keeps working
    unmodified -- each PyMuPDF block just takes the place of one
    blank-line-delimited chunk pdftotext would have produced."""
    doc = fitz.open(pdf_path)
    pages_out = []
    for page in doc:
        block_texts = [
            b[4] for b in page.get_text("blocks")
            if b[6] == 0 and b[4].strip()  # b[6] == 0: text block (skip images)
        ]
        pages_out.append("\n\n".join(block_texts))
    doc.close()
    return "\n\n".join(pages_out)


def normalize_whitespace(text: str) -> str:
    # PyMuPDF, unlike pdftotext, leaves typographic ligatures (ﬁ, ﬂ, ﬃ, ...) as
    # single ligature codepoints instead of decomposing them -- NFKC folds them
    # back to plain ASCII ("signiﬁcant" -> "significant") so embedding/matching
    # isn't quietly penalized for a word that only differs by ligature glyph.
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r", "\n")
    text = text.replace("\xa0", " ")  # non-breaking space
    text = text.replace("\t", " ")
    text = text.replace("­", "")  # soft hyphen (invisible formatting char)
    text = text.replace("‐", "-").replace("‑", "-")  # unicode hyphen variants
    text = re.sub(r"-{2,}", "-", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text


def find_references_split(lines):
    """Return the index of the line where the references section starts,
    searching from the end (so a stray "References" mention mid-body
    doesn't trigger a false split), or None if no heading is found."""
    for i in range(len(lines) - 1, -1, -1):
        if REFERENCES_HEADING_RE.match(lines[i]):
            return i
    return None


def split_citations(ref_text: str):
    ref_text = ref_text.strip()
    if not ref_text:
        return []

    parts = NUMBERED_CITATION_RE.split(ref_text)
    parts = [re.sub(r"\s{2,}", " ", p.strip()) for p in parts if p.strip()]
    # pdftotext's column/page reflow sometimes emits a bare "[2]" label with
    # no body just before the real "[2] Author... " entry -- the content
    # isn't lost (it's in the real entry), so these fragments are pure noise.
    parts = [p for p in parts if not LABEL_ONLY_RE.match(p)]
    if len(parts) > 1:
        return parts

    # Fall back to blank-line-delimited entries (hanging-indent /
    # author-year reference lists commonly separate entries this way).
    parts = [p.strip().replace("\n", " ") for p in re.split(r"\n\s*\n", ref_text)]
    parts = [re.sub(r"\s{2,}", " ", p) for p in parts if len(p.strip()) > 10]
    return parts


def looks_like_heading_or_noise(paragraph: str) -> bool:
    if len(paragraph) < MIN_PARAGRAPH_LEN:
        return True
    if "." not in paragraph and "?" not in paragraph and "!" not in paragraph:
        return True  # real prose paragraphs contain sentence-ending punctuation
    return False


def dehyphenate_and_join(block: str) -> str:
    lines = [l.strip() for l in block.split("\n")]
    out = []
    for line in lines:
        if not line:
            continue
        if out and out[-1].endswith("-") and out[-1][-2:-1].isalpha():
            out[-1] = out[-1][:-1] + line
        else:
            out.append(line)
    return re.sub(r"\s{2,}", " ", " ".join(out)).strip()


def split_paragraphs(body_text: str):
    # pdf_to_text() already joins PyMuPDF's paragraph-granular blocks with a
    # blank line each, so this blank-line split just recovers those blocks --
    # no further indentation heuristics needed on top (see pdf_to_text()).
    raw_blocks = re.split(r"\n\s*\n+", body_text)
    joined = [dehyphenate_and_join(b) for b in raw_blocks]
    joined = [b for b in joined if b]

    # Merge short fragments into a neighbor -- e.g. a footnote marker, a
    # figure-caption label, or a heading/subheading that PyMuPDF gave its own
    # block -- which would otherwise stand as noise-sized pieces on their own.
    merged = []
    for block in joined:
        if merged and len(merged[-1]) < MIN_PARAGRAPH_LEN:
            merged[-1] = merged[-1] + " " + block
        else:
            merged.append(block)

    return [p for p in merged if not looks_like_heading_or_noise(p)]


def dedupe_paragraphs(paragraphs):
    """Drop exact-repeat paragraphs within the same document -- e.g. a running
    header or figure caption reprinted verbatim on multiple pages. These
    aren't genuine distinct content, so per todo.md's "don't double store for
    same doc" they should never reach paragraphs.jsonl, let alone get
    embedded and flagged as a trivial same-paper "dupe" of themselves.
    Keeps the first occurrence's position; returns (deduped, num_dropped)."""
    seen = set()
    deduped = []
    for p in paragraphs:
        if p in seen:
            continue
        seen.add(p)
        deduped.append(p)
    return deduped, len(paragraphs) - len(deduped)


def extract_paper(pdf_path: Path):
    raw = normalize_whitespace(pdf_to_text(pdf_path))
    lines = raw.split("\n")
    split_idx = find_references_split(lines)

    if split_idx is None:
        body_text, ref_text = raw, ""
    else:
        body_text = "\n".join(lines[:split_idx])
        ref_text = "\n".join(lines[split_idx + 1:])

    paragraphs, num_deduped = dedupe_paragraphs(split_paragraphs(body_text))
    return paragraphs, split_citations(ref_text), num_deduped


def load_downloaded_papers(state_db: Path):
    # timeout=30: state.sqlite3 may be under active write from a concurrently
    # running retrieve_papers.py/bulk_retrieve_arxiv.py -- wait out a brief
    # lock rather than failing outright on a transient "database is locked".
    conn = db.connect(state_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT title, authors, year, doi, file_path FROM papers "
        "WHERE status = 'downloaded' AND file_path IS NOT NULL"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def already_extracted_paths(conn):
    """file_path -> True for every paper already in library.sqlite3 -- the
    ledger of "which files were run on" extract_papers.py needs to resume
    from any point without redoing already-extracted PDFs. A paper's row is
    only written after its paragraphs/citations are fully processed (see
    main()), so a row's presence here is a reliable "done" marker even if a
    previous run was interrupted mid-PDF."""
    return {file_path for (file_path,) in conn.execute("SELECT file_path FROM papers")}


def _papers_doi_has_unique_index(conn) -> bool:
    for _, name, is_unique, origin, _ in conn.execute("PRAGMA index_list(papers)").fetchall():
        if not is_unique or origin != "u":
            continue
        cols = [row[2] for row in conn.execute(f"PRAGMA index_info({name})").fetchall()]
        if cols == ["doi"]:
            return True
    return False


def migrate_drop_doi_unique(conn):
    """`papers.doi` was originally UNIQUE, on the assumption a DOI maps to exactly one physical file.
    False in practice: the same DOI can legitimately be downloaded twice as two separate files -- e.g.
    this project's library.sqlite3/paragraphs/potential_dupes were partly populated on a different
    computer whose original PDFs aren't present here (no local file, but real paragraphs/embeddings/
    dupe-matches worth keeping), and a fresh download of an already-known DOI under a new filename used
    to collide with that historical row's UNIQUE(doi). An earlier, wrong fix "resolved" the collision by
    updating the existing row's file_path in place -- which would then cascade-delete its paragraphs (via
    embed_paragraphs.py's reconcile_stale_paragraphs(), once a differently-paginated re-extraction landed
    under the same paper_id) and, in turn, any potential_dupes matches referencing those paragraphs. Two
    rows sharing the same doi are allowed from here on; nothing else in this codebase assumes doi is
    unique (only file_path is -- see upsert_paper()). SQLite can't just ALTER TABLE off a UNIQUE
    constraint, hence the rebuild-and-copy; ids are preserved explicitly since paper_authors/citations/
    paragraphs/potential_dupes all reference papers.id as a foreign key."""
    if not _papers_doi_has_unique_index(conn):
        return
    conn.executescript(
        """
        CREATE TABLE papers_new (
            id INTEGER PRIMARY KEY,
            doi TEXT,
            title TEXT NOT NULL,
            year INTEGER,
            file_path TEXT UNIQUE NOT NULL,
            extracted_at TEXT
        );
        INSERT INTO papers_new (id, doi, title, year, file_path, extracted_at)
            SELECT id, doi, title, year, file_path, extracted_at FROM papers;
        DROP TABLE papers;
        ALTER TABLE papers_new RENAME TO papers;
        """
    )
    conn.commit()


def init_library_db(db_path: Path):
    conn = db.connect(db_path)  # embed_paragraphs.py/build_dupe_candidates.py may connect concurrently (see db.py: WAL mode)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY,
            doi TEXT,
            title TEXT NOT NULL,
            year INTEGER,
            file_path TEXT UNIQUE NOT NULL,
            extracted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS authors (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_authors (
            paper_id INTEGER NOT NULL REFERENCES papers(id),
            author_id INTEGER NOT NULL REFERENCES authors(id),
            author_order INTEGER NOT NULL,
            PRIMARY KEY (paper_id, author_id)
        );
        CREATE TABLE IF NOT EXISTS citations (
            id INTEGER PRIMARY KEY,
            paper_id INTEGER NOT NULL REFERENCES papers(id),
            citation_order INTEGER NOT NULL,
            raw_text TEXT NOT NULL
        );
        """
    )
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    if "extracted_at" not in existing_cols:
        conn.execute("ALTER TABLE papers ADD COLUMN extracted_at TEXT")
    migrate_drop_doi_unique(conn)
    conn.commit()
    return conn


def upsert_paper(conn, title, doi, year, file_path):
    """Insert or update a papers row, keyed on file_path only -- the same DOI can legitimately show
    up under a second, different file_path (e.g. a paper re-downloaded after an original copy from a
    different computer/session is no longer present locally: see migrate_drop_doi_unique()'s docstring
    for why doi is deliberately NOT unique here). That must become its own new row, not merge into the
    existing one -- merging would update the existing row's file_path in place, which would then
    cascade-delete that row's real paragraphs/potential_dupes matches once a differently-paginated
    re-extraction landed under the same paper_id (confirmed for real and reverted -- see git history)."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur = conn.execute(
        "INSERT INTO papers (doi, title, year, file_path, extracted_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(file_path) DO UPDATE SET doi=excluded.doi, title=excluded.title, year=excluded.year, "
        "extracted_at=excluded.extracted_at "
        "RETURNING id",
        (doi, title, year, file_path, now),
    )
    paper_id = cur.fetchone()[0]
    conn.commit()
    return paper_id


def replace_authors(conn, paper_id, author_names):
    conn.execute("DELETE FROM paper_authors WHERE paper_id = ?", (paper_id,))
    for order, name in enumerate(author_names):
        conn.execute("INSERT OR IGNORE INTO authors (name) VALUES (?)", (name,))
        author_id = conn.execute("SELECT id FROM authors WHERE name = ?", (name,)).fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO paper_authors (paper_id, author_id, author_order) VALUES (?, ?, ?)",
            (paper_id, author_id, order),
        )
    conn.commit()


def replace_citations(conn, paper_id, citations):
    conn.execute("DELETE FROM citations WHERE paper_id = ?", (paper_id,))
    for order, text in enumerate(citations):
        conn.execute(
            "INSERT INTO citations (paper_id, citation_order, raw_text) VALUES (?, ?, ?)",
            (paper_id, order, text),
        )
    conn.commit()


def parse_args():
    parser = argparse.ArgumentParser(description="Extract metadata + paragraphs from downloaded paper PDFs.")
    parser.add_argument("--state-db", type=Path, default=Path("state.sqlite3"), help="retrieve_papers.py manifest")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"), help="output metadata database")
    parser.add_argument("--paragraphs-file", type=Path, default=Path("paragraphs.jsonl"),
                         help="output JSONL of paragraph text (input to embed_paragraphs.py)")
    parser.add_argument("--recompute", action="store_true",
                         help="Re-extract every downloaded paper, including ones already in library.sqlite3 "
                              "(rewrites paragraphs.jsonl from scratch instead of appending)")
    parser.add_argument("--limit", type=int, default=None,
                         help="Stop after extracting this many NEW papers -- lets a huge/still-growing corpus "
                              "be processed in bounded batches, re-run to continue from where it left off")
    parser.add_argument("--max-workers", type=int, default=os.cpu_count(),
                         help="Parallel processes for the CPU-bound PyMuPDF extraction step (default: all "
                              "CPU cores). DB/file writes stay single-process regardless -- see module "
                              "docstring. --max-workers 1 for the old fully-sequential behavior.")
    return parser.parse_args()


def _extract_worker(paper):
    """Module-level (so ProcessPoolExecutor can pickle it) wrapper around extract_paper() for
    exactly one paper -- the unit of work each worker process runs. Returns (paper, result,
    error) instead of raising/letting a missing file be a special case in the caller, so a
    corrupt/unreadable PDF or a file that's gone missing on disk doesn't take down the pool or
    lose track of which paper it happened to -- same per-paper isolation the old sequential
    try/except gave, just carried back across a process boundary. `error` is a short string
    ("missing", or PyMuPDF's own exception text) when extraction didn't happen; None on success,
    with `result` then the normal (paragraphs, citations, num_deduped) tuple."""
    path = Path(paper["file_path"])
    if not path.exists():
        return paper, None, "missing"
    try:
        result = extract_paper(path)
    except Exception as exc:  # PyMuPDF raises its own exception types for corrupt/unreadable PDFs
        return paper, None, str(exc)
    return paper, result, None


def main():
    args = parse_args()
    papers = load_downloaded_papers(args.state_db)

    conn = init_library_db(args.library_db)
    if args.recompute:
        pending = papers
    else:
        done = already_extracted_paths(conn)
        pending = [p for p in papers if p["file_path"] not in done]
    logger.info("found %d downloaded paper(s) in %s, %d already extracted, %d to process",
                len(papers), args.state_db, len(papers) - len(pending), len(pending))

    if args.limit is not None and len(pending) > args.limit:
        logger.info("--limit %d: processing first %d of %d pending paper(s) this run",
                    args.limit, args.limit, len(pending))
        pending = pending[:args.limit]

    total_paragraphs = total_citations = processed = 0
    mode = "w" if args.recompute else "a"
    logger.info("extracting across %d worker process(es)", args.max_workers)

    with open(args.paragraphs_file, mode, encoding="utf-8") as pf, \
            ProcessPoolExecutor(max_workers=args.max_workers) as executor:
        futures = [executor.submit(_extract_worker, paper) for paper in pending]
        # Completion order is whatever finishes first, not `pending`'s order -- fine everywhere
        # this loop touches: each paper's own DB writes/paragraph lines depend only on that
        # paper's own extraction result (see module docstring), never on another paper's.
        for future in as_completed(futures):
            paper, result, error = future.result()
            path = Path(paper["file_path"])
            if error == "missing":
                logger.warning("file missing on disk, skipping: %s", path)
                continue
            if error is not None:
                logger.error("PDF extraction failed on %s: %s", path, error)
                continue
            paragraphs, citations, num_deduped = result

            # paper["title"] can be NULL in state.sqlite3 (confirmed for real: 15 rows, e.g. a
            # starting-list entry OpenAlex itself had no title for) -- papers.title is NOT NULL,
            # so upsert_paper() crashed the whole run on the first one it hit rather than just
            # that one paper. Fall back to something identifying rather than crash or guess.
            title = paper["title"] or f"[untitled] {paper['doi'] or path.name}"
            paper_id = upsert_paper(conn, title, paper["doi"], paper["year"], str(path))
            # paper["authors"] can be the literal string "null" (json.dumps(None)) rather than
            # missing/empty -- retrieve_papers.py stores that whenever a starting.json entry has no
            # "authors" field at all (e.g. built from a source with no author data, like OpenAlex
            # candidates here), and "null" is truthy so the `if paper["authors"]` guard alone doesn't
            # catch it; json.loads("null") then returns None, not [], which used to crash
            # replace_authors() trying to iterate it.
            author_names = (json.loads(paper["authors"]) if paper["authors"] else None) or []
            replace_authors(conn, paper_id, author_names)
            replace_citations(conn, paper_id, citations)

            for i, para in enumerate(paragraphs):
                pf.write(json.dumps({"file_path": str(path), "para_index": i, "text": para}) + "\n")
            pf.flush()  # so a killed/interrupted run still leaves a consistent, resumable file on disk

            total_paragraphs += len(paragraphs)
            total_citations += len(citations)
            processed += 1
            dedup_note = f", {num_deduped} exact-repeat dropped" if num_deduped else ""
            logger.info("extracted %s -> %d paragraphs, %d citations%s",
                        path.name, len(paragraphs), len(citations), dedup_note)
            if processed % 100 == 0:
                logger.info("progress: %d/%d processed this run", processed, len(pending))

    remaining = len(papers) - len(already_extracted_paths(conn))
    conn.close()
    logger.info("done. processed %d paper(s) this run (%d paragraph(s), %d citation(s)); %d still pending",
                processed, total_paragraphs, total_citations, remaining)


if __name__ == "__main__":
    main()

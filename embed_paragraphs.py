#!/usr/bin/env python3
"""Generate embeddings for paragraphs extracted by extract_papers.py.

Reads paragraphs.jsonl (one paragraph of text per line, tagged with the
source PDF's file_path), embeds each paragraph with a local
sentence-transformers model, and stores (paper_id, para_index, text,
embedding) in a `paragraphs` table in library.sqlite3 -- this is the first
and only place paragraph text is persisted to disk in a table, alongside
the vector that makes it useful for similarity/duplicate search.

Re-running is safe and cheap: paragraphs already embedded (matched by
paper_id + para_index) are skipped unless --recompute is passed. Stale rows
whose (paper_id, para_index) no longer appears in paragraphs.jsonl -- e.g.
after extract_papers.py's paragraph count/ordering changes for a paper --
are deleted first, so indices never end up pointing at the wrong text.

Every new/changed embedding is also hashed into the persisted LSH candidate
index (lsh_index.py) as soon as it's stored -- see todo.md's "Candidate
index design (LSH)" for why. This is what lets build_dupe_candidates.py find
candidates without a brute-force N x N compare and without waiting for the
whole corpus to be embedded first.

paragraphs.jsonl is treated as a transient staging area, not a permanent
archive: once a paper's paragraphs are all durably embedded, this script
prunes that paper's lines back out of the file (prune_embedded_paragraphs(),
default on, --no-prune to keep the old grows-forever behavior). Before this,
extract_papers.py's append-only writes meant the file only ever grew --
7,596,632 lines / 4.96GB by 2026-08-21, read into memory in full on every
single run just to figure out what was already done (see todo.md's
"paragraphs.jsonl needs splitting"). Steady-state size after this change is
"paragraphs extracted since the last embed_paragraphs.py run", not "every
paragraph ever extracted in this corpus's history".
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np

import classify_dupes
import db
import lsh_index
from review_dupes import english_score

# Optional: train_boilerplate_family_classifier.py's ML generalization layer over
# classify_dupes.TEXT_PATTERNS (see skip_boilerplate_paragraphs()'s docstring for the full
# framing). Guarded because the model artifact is a gitignored build product, not checked in --
# a fresh clone/environment has neither the trained model file nor necessarily scikit-learn
# installed, and this must degrade to regex-only in that case, not crash at import time.
try:
    import joblib
    import train_boilerplate_family_classifier as boilerplate_ml
except ImportError:
    joblib = None
    boilerplate_ml = None

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_BATCH_SIZE = 32
DEFAULT_ML_BOILERPLATE_MODEL_PATH = Path("boilerplate_family_classifier.joblib")

# 2026-08-21: added after the full-corpus plagiarism audit found 249 of 341 hard-to-classify
# candidates that session were pure embedding noise on non-English (Russian/Ukrainian/Korean)
# text -- all-MiniLM-L6-v2 is English-tuned, so cosine similarity between two non-English
# paragraphs it was never trained on is cost with no signal, not a real finding. 0.03 matches
# the cutoff that session's manual review used and confirmed against real examples (English
# prose on this corpus scores well above this; non-English prose scores indistinguishable
# from 0). See todo.md's "Full-corpus plagiarism audit" section.
DEFAULT_MIN_ENGLISH_SCORE = 0.03
SKIPPED_MODEL_SENTINEL = "skipped-non-english"

# 2026-08-24: added after a day of hand-classifying candidate PAIRS in classify_dupes.py found
# ~150 recurring boilerplate patterns (CC-license blocks, funding-acknowledgment templates,
# journal mastheads, institutional-repository deposit notices, ...) -- every one of those
# patterns was, until now, pure downstream cleanup: the paragraph still got embedded, still got
# LSH-indexed (exactly the kind of paragraph that saturates the biggest buckets, per todo.md's
# LSH post-mortems), and still generated a potential_dupes candidate row, all before
# classify_dupes.py ever got a chance to mark it ai_check='no'. Skipping it here instead means
# it never gets any of that -- real compute/storage saved, and one less contributor to bucket
# saturation. Uses classify_dupes.classify_text_patterns_only(), not the full classify_text()
# (see that function's own docstring for why the generic fallback heuristics -- 2+ citation
# years, 2+ emails, leading quote mark -- are deliberately excluded from this pre-embedding
# context: they were only ever validated against already-paired candidates, and a paragraph
# skipped here never gets a second chance at any pairing at all, unlike a wrongly-classified
# candidate row a human can still catch in review_dupes.py).
SKIPPED_MODEL_SENTINEL_BOILERPLATE = "skipped-boilerplate"

# 2026-08-30: skip_boilerplate_paragraphs()/reclassify_embedded_boilerplate() optionally run a
# SECOND pass, after the regex check above, using train_boilerplate_family_classifier.py's
# trained model (--ml-boilerplate-model, auto-loaded if the file exists) -- a cheap TF-IDF +
# logistic regression classifier that generalizes the regex patterns to reworded variants of the
# same known boilerplate families, at a validated 0.10% false-flag rate against true
# non-boilerplate text (down from 8.08% before tuning class_weight/min_confidence for this
# specific safety bar), shadow-mode-checked against every status='confirmed' real match in this
# corpus and the full tests/cases/*.json back-test suite with zero real content ever
# misclassified. See that script's own docstring for the full validation writeup. Off by default
# in the sense that it only activates when the model artifact is present (a gitignored build
# product) -- --no-ml-boilerplate-filter opts back out explicitly.

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("embed_paragraphs")


def init_paragraphs_table(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS paragraphs (
            id INTEGER PRIMARY KEY,
            paper_id INTEGER NOT NULL REFERENCES papers(id),
            para_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding BLOB,
            embedding_dim INTEGER,
            model TEXT,
            UNIQUE(paper_id, para_index)
        );
        """
    )
    conn.commit()


def load_file_path_to_paper_id(conn):
    rows = conn.execute("SELECT id, file_path FROM papers").fetchall()
    return {file_path: paper_id for paper_id, file_path in rows}


def load_paragraph_records(jsonl_path: Path, path_to_paper_id):
    """Returns (records, raw_lines, unresolved_lines): records is the parsed
    (paper_id, para_index, text) tuples embed_and_store() etc. work with;
    raw_lines is the original stripped JSON text of every line that resolved
    to a known paper_id, keyed by (paper_id, para_index) -- kept around so
    prune_embedded_paragraphs() can rewrite paragraphs.jsonl from the exact
    original line text (preserving any extra fields/formatting) rather than
    reconstructing JSON from the parsed tuple. unresolved_lines is every
    line whose file_path doesn't match any current papers row (a paper
    still mid-extraction -- its paragraphs.jsonl lines got appended before
    its papers row commit, per extract_papers.py's own docstring on write
    order -- or, seen for real on this corpus, a paper whose file_path
    changed after these lines were written, leaving them permanently
    orphaned) -- prune_embedded_paragraphs() must always keep these
    verbatim, never silently drop them just because it has no paper_id to
    reason about them with (confirmed as a real bug on this corpus's actual
    file the first time this ran: 328 such lines across 6 papers would have
    been silently discarded on rewrite otherwise, since a line this function
    never resolves was never getting into raw_lines/records for prune to
    preserve at all)."""
    records = []
    raw_lines = {}
    unresolved_lines = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            paper_id = path_to_paper_id.get(row["file_path"])
            if paper_id is None:
                logger.warning("no paper row for %s, skipping paragraph %d", row["file_path"], row["para_index"])
                unresolved_lines.append(line)
                continue
            records.append((paper_id, row["para_index"], row["text"]))
            raw_lines[(paper_id, row["para_index"])] = line
    return records, raw_lines, unresolved_lines


def reconcile_stale_paragraphs(conn, all_records):
    """Delete DB rows that no longer match paragraphs.jsonl's current
    (paper_id, para_index) set, for whichever papers actually appear in that
    set. Only meaningful for a paper extract_papers.py --recompute'd (a full,
    from-scratch rewrite of paragraphs.jsonl, unlike the normal incremental
    append) with a different paragraph count/ordering than before (e.g. its
    same-document dedup dropping a repeated paragraph shifts every later
    index) -- old rows would otherwise linger under indices that now point
    at different text.

    A paper with NO paragraphs left in the current file is silently skipped,
    not treated as "delete everything for it" -- this is what makes it safe
    for prune_embedded_paragraphs() to remove a paper's lines entirely once
    they're durably embedded (see that function): once removed, this
    function has no opinion on that paper at all, rather than misreading its
    absence as staleness. prune_embedded_paragraphs() only ever removes a
    paper's lines as a whole (never some of a paper's lines while leaving
    others), specifically so this function is never handed a *partial* view
    of a paper's paragraph set, which would look identical to genuine
    staleness and cause real data loss."""
    valid_by_paper = {}
    for paper_id, para_index, _ in all_records:
        valid_by_paper.setdefault(paper_id, set()).add(para_index)

    deleted = 0
    for paper_id, valid_indices in valid_by_paper.items():
        rows = conn.execute("SELECT para_index FROM paragraphs WHERE paper_id = ?", (paper_id,)).fetchall()
        stale = [idx for (idx,) in rows if idx not in valid_indices]
        for idx in stale:
            conn.execute("DELETE FROM paragraphs WHERE paper_id = ? AND para_index = ?", (paper_id, idx))
        deleted += len(stale)
    if deleted:
        conn.commit()
    return deleted


def already_embedded(conn):
    """Maps (paper_id, para_index) -> stored text, for every paragraph
    that's *finished* -- either it already has a real embedding, or it was
    deliberately skipped as non-English (SKIPPED_MODEL_SENTINEL, see
    skip_non_english_paragraphs()) or as known boilerplate
    (SKIPPED_MODEL_SENTINEL_BOILERPLATE, see skip_boilerplate_paragraphs())
    and isn't getting one. All three count as
    "done" for this function's two callers (computing what's still pending,
    and deciding what prune_embedded_paragraphs() can drop from
    paragraphs.jsonl) -- a skipped paragraph should stop being reconsidered
    every run exactly like an embedded one, it just never gets a vector.
    Every other consumer of the `paragraphs` table (lsh_index.py,
    find_duplicates.py, build_dupe_candidates.py) filters on `embedding IS
    NOT NULL` directly, so a skipped row -- real embedding always NULL --
    stays invisible to LSH indexing/candidate generation without needing
    any changes there.

    Keyed by text, not just presence, so that a paragraph whose text
    changed at an existing index (e.g. a later paragraph sliding into an
    earlier one's slot after extract_papers.py drops a same-document
    duplicate ahead of it) is detected and re-embedded/re-scored rather
    than silently left stale."""
    rows = conn.execute(
        "SELECT paper_id, para_index, text FROM paragraphs WHERE embedding IS NOT NULL OR model IN (?, ?)",
        (SKIPPED_MODEL_SENTINEL, SKIPPED_MODEL_SENTINEL_BOILERPLATE),
    ).fetchall()
    return {(paper_id, para_index): text for paper_id, para_index, text in rows}


def skip_non_english_paragraphs(conn, records, min_english_score, logger_):
    """Splits `records` into (to_embed, skipped) by english_score(text) --
    see DEFAULT_MIN_ENGLISH_SCORE. Every skipped paragraph is stored with
    `model=SKIPPED_MODEL_SENTINEL` and embedding left NULL -- a deliberate
    decision it never gets a real vector, not merely "not yet embedded" --
    so already_embedded()'s next call treats it as finished (see that
    function's docstring) instead of retrying it, and
    prune_embedded_paragraphs() can drop it from paragraphs.jsonl like any
    other finished paragraph. min_english_score=0 (via --min-english-score 0)
    disables this entirely -- every paragraph goes to to_embed."""
    if min_english_score <= 0:
        return records, []

    to_embed, skipped = [], []
    for paper_id, para_index, text in records:
        if english_score(text) < min_english_score:
            skipped.append((paper_id, para_index, text))
        else:
            to_embed.append((paper_id, para_index, text))

    if skipped:
        conn.executemany(
            """INSERT INTO paragraphs (paper_id, para_index, text, embedding, embedding_dim, model)
               VALUES (?, ?, ?, NULL, NULL, ?)
               ON CONFLICT(paper_id, para_index) DO UPDATE SET
               text=excluded.text, embedding=NULL, embedding_dim=NULL, model=excluded.model""",
            [(paper_id, para_index, text, SKIPPED_MODEL_SENTINEL) for paper_id, para_index, text in skipped],
        )
        conn.commit()
        logger_.info("skipped %d paragraph(s) below --min-english-score=%.2f (not embedded)",
                     len(skipped), min_english_score)
    return to_embed, skipped


def load_ml_boilerplate_classifier(path, logger_):
    """Loads the trained boilerplate-family classifier (train_boilerplate_family_classifier.py)
    for skip_boilerplate_paragraphs()'s optional ML layer. Returns None -- a pure degrade to
    regex-only, unchanged from before this existed -- rather than raising, in either case a
    missing model is expected, not an error: scikit-learn/joblib not importable (an environment
    that predates this feature, or deliberately without it), or the model file not present (a
    gitignored build artifact -- see train_boilerplate_family_classifier.py's own docstring --
    that a fresh clone has no reason to already have until someone runs that script). Matches
    every other optional-enhancement precedent in this pipeline (OPENALEX_API_KEY unset,
    CORE_API_KEY unset, ...): silently unavailable, never a crash."""
    if boilerplate_ml is None:
        logger_.info("scikit-learn/joblib not installed -- ML boilerplate filter unavailable (regex-only)")
        return None
    if not path.exists():
        logger_.info("%s not found -- ML boilerplate filter unavailable (train it with "
                     "train_boilerplate_family_classifier.py first if you want this); regex-only for now", path)
        return None
    artifact = joblib.load(path)
    logger_.info("loaded ML boilerplate classifier from %s (families=%s, min_confidence=%.2f)",
                 path, artifact["kept_families"], artifact["min_confidence"])
    return artifact


def skip_boilerplate_paragraphs(conn, records, logger_, enabled=True, ml_classifier=None):
    """Splits `records` into (to_embed, skipped) by
    classify_dupes.classify_text_patterns_only(text) -- see
    SKIPPED_MODEL_SENTINEL_BOILERPLATE's module-level comment for why this
    exists and why it's the patterns-only classifier, not classify_text()'s
    full logic. Same storage/re-run convention as
    skip_non_english_paragraphs(): model=SKIPPED_MODEL_SENTINEL_BOILERPLATE,
    embedding left NULL, so already_embedded() treats it as finished and
    prune_embedded_paragraphs() can drop it from paragraphs.jsonl like any
    other finished paragraph. enabled=False (via --no-skip-boilerplate)
    disables this entirely -- every paragraph goes to to_embed, the old
    (pre-2026-08-24) behavior.

    ml_classifier (added 2026-08-30, see train_boilerplate_family_classifier.py):
    an optional second pass, over whatever survives the regex check, using
    that script's trained family classifier via predict_families() -- the
    SAME confidence-gated decision function validated there (shadow-mode,
    zero false positives against every status='confirmed' real match in
    this corpus and the full tests/cases/*.json back-test suite, at the
    class_weight=None + min_confidence=0.9 settings that artifact was
    trained with). Generalizes the regex list to reworded variants of known
    boilerplate families it doesn't have an exact pattern for; it does not
    replace the regex check (still runs first, still perfect precision on
    what it matches) and does not discover new families. None (the default,
    and what you get if load_ml_boilerplate_classifier() couldn't load one)
    disables this second pass entirely -- pure regex behavior, unchanged
    from before this parameter existed. Stored under the SAME
    SKIPPED_MODEL_SENTINEL_BOILERPLATE as a regex hit (functionally
    identical: skip, no embedding, done) -- if ever needed, "was this
    ML-only" is recoverable by checking whether
    classify_text_patterns_only() still returns None for the stored text."""
    if not enabled:
        return records, []

    to_embed, skipped = [], []
    for paper_id, para_index, text in records:
        if classify_dupes.classify_text_patterns_only(text) is not None:
            skipped.append((paper_id, para_index, text))
        else:
            to_embed.append((paper_id, para_index, text))
    regex_skipped_count = len(skipped)

    ml_skipped_count = 0
    if ml_classifier is not None and to_embed:
        texts = [text for _, _, text in to_embed]
        predictions = boilerplate_ml.predict_families(
            ml_classifier["vectorizer"], ml_classifier["model"], texts,
            min_confidence=ml_classifier["min_confidence"],
        )
        still_to_embed = []
        for (paper_id, para_index, text), (family, _conf) in zip(to_embed, predictions):
            if family != boilerplate_ml.NOT_BOILERPLATE:
                skipped.append((paper_id, para_index, text))
                ml_skipped_count += 1
            else:
                still_to_embed.append((paper_id, para_index, text))
        to_embed = still_to_embed

    if skipped:
        conn.executemany(
            """INSERT INTO paragraphs (paper_id, para_index, text, embedding, embedding_dim, model)
               VALUES (?, ?, ?, NULL, NULL, ?)
               ON CONFLICT(paper_id, para_index) DO UPDATE SET
               text=excluded.text, embedding=NULL, embedding_dim=NULL, model=excluded.model""",
            [(paper_id, para_index, text, SKIPPED_MODEL_SENTINEL_BOILERPLATE) for paper_id, para_index, text in skipped],
        )
        conn.commit()
        if ml_skipped_count:
            logger_.info("skipped %d paragraph(s) matching a known boilerplate pattern "
                         "(%d regex, %d ML-generalized) (not embedded)",
                         len(skipped), regex_skipped_count, ml_skipped_count)
        else:
            logger_.info("skipped %d paragraph(s) matching a known boilerplate pattern (not embedded)", len(skipped))
    return to_embed, skipped


def reclassify_embedded_boilerplate(conn, logger_, batch_size=2000, ml_classifier=None, scan_batch_size=20_000):
    """Retroactive complement to skip_boilerplate_paragraphs(): that function
    only ever affects paragraphs embedded GOING FORWARD (its own docstring
    says so explicitly) -- this one goes back and finds already-embedded
    paragraphs (embedding IS NOT NULL, i.e. not already a skip-* sentinel)
    that match a known boilerplate pattern via
    classify_dupes.classify_text_patterns_only(), and converts them to the
    same SKIPPED_MODEL_SENTINEL_BOILERPLATE sentinel a paragraph gets when
    the pre-embedding filter catches it before ever being embedded at all.
    Most of this corpus predates that filter (added 2026-08-24), so this is
    a real backlog, not a hypothetical one.

    Why this matters beyond tidiness: todo.md's LSH post-mortems 5/6 found
    max_bucket_size is silently dropping most of the corpus from candidate
    generation (median bucket size 72 vs. the 30 cap, as of 2026-08-25), and
    post-mortem 5 specifically confirmed a real share of the biggest buckets
    ARE exactly this kind of enumerable, fingerprint-able boilerplate
    (license blocks, "author contributions" sections, standard methodology
    templates) -- as opposed to post-mortem 6's separate, harder-to-fix
    "organic topical convergence" story for this corpus's OTHER giant
    buckets (not boilerplate at all, just many independently-written
    paragraphs on the same narrow topic -- classify_text_patterns_only()
    correctly leaves those alone; there's no enumerable pattern to match).
    Retroactively stripping the boilerplate slice directly shrinks exactly
    those buckets, unblocking whatever real candidates were hiding behind
    them in the SAME table/bucket, without needing the disk-heavy full
    bits_per_table rehash that fixing post-mortem 6's category would need --
    a strictly smaller, zero-risk-to-this-machine's-documented-disk-issues
    fix that's still worth doing on its own.

    Leaves every existing potential_dupes row referencing a reclassified
    paragraph in place at the time this runs -- build_dupe_candidates.py's
    delete_orphaned_candidates() is what actually cleans those up (checks
    embedding IS NOT NULL directly, so it catches exactly what this
    function just cleared), on its own next run, not this one. Idempotent:
    a paragraph already carrying a skip-* sentinel has embedding IS NULL
    and is excluded from the WHERE clause on every re-run.

    ml_classifier (added 2026-08-30): same optional second pass as
    skip_boilerplate_paragraphs() -- see that function's docstring for the
    validation behind it. None (default): regex-only, unchanged from before
    this parameter existed.

    scan_batch_size (added 2026-08-30): streams the SELECT via fetchmany()
    in chunks of this size instead of one `.fetchall()` of every embedded
    paragraph in the corpus -- at the main corpus's real scale (9.9M
    embedded paragraphs) that's multiple GB of Python string objects held
    at once for no reason, exactly the kind of unbounded-materialization
    mistake todo.md's LSH post-mortems already hit once for a different
    query in this same codebase. Each scan batch's ML pass (if any) also
    only ever holds that batch's TF-IDF matrix, not the whole corpus's."""
    lsh_index.init_lsh_tables(conn)  # idempotent -- CREATE TABLE IF NOT EXISTS; this may run standalone

    total_checked = total_marked = total_regex = total_ml = 0
    cursor = conn.execute("SELECT id, text FROM paragraphs WHERE embedding IS NOT NULL")
    while True:
        rows = cursor.fetchmany(scan_batch_size)
        if not rows:
            break
        total_checked += len(rows)

        regex_hits = {pid for pid, text in rows if classify_dupes.classify_text_patterns_only(text) is not None}
        to_mark = set(regex_hits)
        if ml_classifier is not None:
            remaining = [(pid, text) for pid, text in rows if pid not in regex_hits]
            if remaining:
                texts = [text for _, text in remaining]
                predictions = boilerplate_ml.predict_families(
                    ml_classifier["vectorizer"], ml_classifier["model"], texts,
                    min_confidence=ml_classifier["min_confidence"],
                )
                for (pid, _text), (family, _conf) in zip(remaining, predictions):
                    if family != boilerplate_ml.NOT_BOILERPLATE:
                        to_mark.add(pid)
        total_regex += len(regex_hits)
        total_ml += len(to_mark) - len(regex_hits)

        if to_mark:
            to_mark = list(to_mark)
            lsh_index.invalidate_paragraphs(conn, to_mark)  # drop stale bucket/scanned rows first
            for start in range(0, len(to_mark), batch_size):
                chunk = to_mark[start:start + batch_size]
                conn.executemany(
                    "UPDATE paragraphs SET embedding=NULL, embedding_dim=NULL, model=? WHERE id=?",
                    [(SKIPPED_MODEL_SENTINEL_BOILERPLATE, pid) for pid in chunk],
                )
                conn.commit()
            total_marked += len(to_mark)
        logger_.info("checked %d already-embedded paragraph(s) so far, %d reclassified as boilerplate",
                     total_checked, total_marked)

    if total_ml:
        logger_.info("done: %d already-embedded paragraph(s) reclassified as boilerplate "
                     "(%d regex, %d ML-generalized; embedding cleared, LSH entries removed)",
                     total_marked, total_regex, total_ml)
    else:
        logger_.info("done: %d already-embedded paragraph(s) reclassified as boilerplate "
                     "(embedding cleared, LSH entries removed)", total_marked)
    return total_marked


def embed_and_store(conn, model, records, batch_size, logger_):
    # Sort by text length before batching, purely for throughput: a transformer
    # pads every sequence in a batch out to that batch's longest member, so an
    # unsorted batch that happens to mix a long paragraph in with several short
    # ones wastes compute on padding. Paragraph length varies a lot in this
    # corpus (short captions next to full paragraphs), and sorting first
    # measured ~25-30% faster on a real sample. Each record still carries its
    # own (paper_id, para_index), so storing out of original file order is
    # harmless -- nothing downstream depends on insertion order.
    ordered = sorted(records, key=lambda r: len(r[2]))
    touched_ids = []
    for start in range(0, len(ordered), batch_size):
        batch = ordered[start:start + batch_size]
        texts = [text for _, _, text in batch]
        vectors = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
        for (paper_id, para_index, text), vector in zip(batch, vectors):
            vector = np.asarray(vector, dtype=np.float32)
            cur = conn.execute(
                "INSERT INTO paragraphs (paper_id, para_index, text, embedding, embedding_dim, model) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(paper_id, para_index) DO UPDATE SET "
                "text=excluded.text, embedding=excluded.embedding, "
                "embedding_dim=excluded.embedding_dim, model=excluded.model "
                "RETURNING id",
                (paper_id, para_index, text, vector.tobytes(), vector.shape[0], model_name_for(model)),
            )
            touched_ids.append(cur.fetchone()[0])
        conn.commit()
        logger_.info("embedded %d/%d paragraphs", min(start + batch_size, len(records)), len(records))
    return touched_ids


def prune_embedded_paragraphs(jsonl_path: Path, raw_lines, text_by_key, embedded, unresolved_lines, logger_):
    """paragraphs.jsonl's job is a transient staging area for paragraphs not
    yet durably embedded, not a permanent archive -- see todo.md's
    "paragraphs.jsonl needs splitting" section for the growth problem this
    fixes (7,596,632 lines / 4.96GB before this, appended to forever by
    extract_papers.py with nothing ever pruned). Once a paragraph's text is
    embedded and committed to library.sqlite3 (embedded[(paper_id,
    para_index)] == text), library.sqlite3 IS its durable home; there's no
    reason to also keep it in the jsonl staging file. Rewrites the file to
    contain only lines whose (paper_id, para_index) either aren't embedded
    yet or whose stored text doesn't match (the same staleness check
    already_embedded()'s caller uses) -- i.e. exactly the paragraphs a
    future run still needs to act on. Steady-state file size becomes
    "paragraphs extracted since the last embed_paragraphs.py run", not
    "every paragraph ever extracted in this corpus's history".

    Prunes a paper's lines only as a whole, never partially: if even one of
    a paper's paragraphs in this file still isn't embedded (e.g. an earlier
    embed_paragraphs.py run got interrupted partway through that paper's
    batch -- a real scenario on this project's own hardware, see todo.md's
    "System/hardware reliability" section), none of that paper's lines are
    pruned this run, even the ones that individually are already embedded.
    Reason: reconcile_stale_paragraphs() treats "this paper's paragraphs
    aren't in the file at all" as "nothing to reconcile for this paper" (safe
    -- it only ever deletes indices it can see are actually missing from a
    paper's CURRENT full set), but treats "this paper appears in the file,
    just with fewer indices than before" as "those missing indices are
    stale, delete them" -- correct when extract_papers.py --recompute
    genuinely shrank a paper's paragraph count, wrong (real data loss) if
    the paper is just *partially* pruned. All-or-nothing per paper keeps the
    file a paper always sees as either its complete current set or not
    present at all, never a partial, misleading subset.

    `unresolved_lines` (lines load_paragraph_records() couldn't match to any
    known paper_id at all) are always kept verbatim, unconditionally -- this
    function has no paper_id to reason about them with, so "prune" must mean
    "leave alone", never "drop". Confirmed necessary the first time this ran
    for real: without this, 328 such lines across 6 papers on this corpus
    would have been silently discarded (see this function's own docstring).

    Atomic (write-to-temp-then-rename, like retrieve_papers.py's
    download_pdf()) so a crash mid-write can't leave paragraphs.jsonl
    truncated/corrupt. Returns (kept, pruned) counts."""
    keys_by_paper = {}
    for key in raw_lines:
        keys_by_paper.setdefault(key[0], []).append(key)
    fully_embedded_papers = {
        paper_id for paper_id, keys in keys_by_paper.items()
        if all(embedded.get(key) == text_by_key[key] for key in keys)
    }
    kept_lines = [line for key, line in raw_lines.items() if key[0] not in fully_embedded_papers]
    kept_lines.extend(unresolved_lines)
    pruned = len(raw_lines) - (len(kept_lines) - len(unresolved_lines))
    if pruned == 0:
        return len(kept_lines), 0

    tmp_path = jsonl_path.with_suffix(jsonl_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for line in kept_lines:
            f.write(line + "\n")
    tmp_path.replace(jsonl_path)
    logger_.info("pruned %d paragraph(s) already durably embedded from %s (%d line(s) remain)",
                 pruned, jsonl_path, len(kept_lines))
    return len(kept_lines), pruned


def model_name_for(model):
    return getattr(model, "_dupefinder_model_name", DEFAULT_MODEL)


def load_model(model_name, logger_):
    """Load the sentence-transformers model, preferring the local cache over
    a network round-trip. By default, sentence-transformers (via
    huggingface_hub) does a HEAD request per model file on *every* load --
    even when it's already fully cached from a previous run -- just to check
    the cache is current. That's not a re-download, but it is a real network
    dependency and added latency on a step that's otherwise purely local.

    Uses SentenceTransformer's own `local_files_only` constructor kwarg
    rather than the HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE env vars an earlier
    version of this function set: those are read into module-level constants
    the *first* time huggingface_hub is imported and never re-read after,
    so popping them mid-process to "fall back online" doesn't actually work
    -- confirmed for real, on the very first machine to ever run this
    against a cold cache (every prior machine already had the model cached,
    so the broken fallback path was never actually exercised until then).
    `local_files_only` is a genuine per-call parameter with no such trap.

    Tries offline first; if that fails (nothing cached yet -- the genuine
    first-run case this docstring elsewhere calls out, "first run downloads
    it, ~90MB"), falls back to a normal networked load.
    """
    from sentence_transformers import SentenceTransformer
    try:
        model = SentenceTransformer(model_name, local_files_only=True)
        logger_.info("loaded model %s from local cache (offline, no network)", model_name)
    except Exception:
        logger_.info("model %s not fully cached locally -- downloading (first run only, ~90MB)", model_name)
        model = SentenceTransformer(model_name, local_files_only=False)
    model._dupefinder_model_name = model_name
    return model


def parse_args():
    parser = argparse.ArgumentParser(description="Embed extracted paragraphs and store them in library.sqlite3.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--paragraphs-file", type=Path, default=Path("paragraphs.jsonl"))
    parser.add_argument("--model", default=DEFAULT_MODEL, help="sentence-transformers model name")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--recompute", action="store_true", help="Re-embed paragraphs that already have an embedding")
    parser.add_argument("--no-prune", action="store_true",
                         help="Don't rewrite paragraphs.jsonl to drop paragraphs already durably embedded "
                              "(default: prune -- see prune_embedded_paragraphs()/todo.md's "
                              "'paragraphs.jsonl needs splitting'; --no-prune keeps the old "
                              "grows-forever-as-a-full-archive behavior, e.g. for debugging)")
    parser.add_argument("--min-english-score", type=float, default=DEFAULT_MIN_ENGLISH_SCORE,
                         help=f"skip embedding paragraphs scoring below this on english_score() -- {DEFAULT_MODEL} "
                              f"is English-tuned and produces pure noise on other languages (default "
                              f"{DEFAULT_MIN_ENGLISH_SCORE}; see todo.md's 'Full-corpus plagiarism audit'). "
                              "0 disables the filter entirely (embed everything, old behavior).")
    parser.add_argument("--no-skip-boilerplate", action="store_true",
                         help="Don't skip embedding paragraphs that match one of classify_dupes.py's curated "
                              "boilerplate TEXT_PATTERNS (CC-license blocks, funding-acknowledgment templates, "
                              "journal mastheads, institutional-repository deposit notices, ...). Default: skip "
                              "them (SKIPPED_MODEL_SENTINEL_BOILERPLATE) -- see that constant's module-level "
                              "comment for why. --no-skip-boilerplate restores the old (pre-2026-08-24) "
                              "behavior of embedding/LSH-indexing/candidate-generating every paragraph "
                              "regardless of known boilerplate patterns.")
    parser.add_argument("--ml-boilerplate-model", type=Path, default=DEFAULT_ML_BOILERPLATE_MODEL_PATH,
                         help="path to a train_boilerplate_family_classifier.py model artifact -- if it "
                              "exists (and --no-skip-boilerplate isn't set), skip_boilerplate_paragraphs() "
                              "runs it as a second pass over whatever the regex patterns don't catch, "
                              "generalizing to reworded variants of known boilerplate families (see that "
                              "script's docstring for the safety validation behind this). Missing file or "
                              "scikit-learn/joblib not installed: silently regex-only, unchanged from before "
                              "this flag existed -- never an error.")
    parser.add_argument("--no-ml-boilerplate-filter", action="store_true",
                         help="Disable the ML boilerplate layer even if --ml-boilerplate-model exists. "
                              "Regex-only boilerplate skip, same as before 2026-08-30.")
    parser.add_argument("--reclassify-existing-boilerplate", action="store_true",
                         help="One-off maintenance mode: retroactively find ALREADY-embedded paragraphs "
                              "matching a known boilerplate pattern (most of the corpus predates "
                              "--no-skip-boilerplate's 2026-08-24 filter) and convert them to the same "
                              "skipped-boilerplate sentinel, clearing their embedding and LSH bucket rows -- "
                              "see reclassify_embedded_boilerplate()'s docstring for why (todo.md's LSH "
                              "post-mortem 5/6 bucket-saturation findings). Runs this pass and exits -- "
                              "doesn't also run a normal embedding pass in the same invocation.")
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)  # extract_papers.py may be writing concurrently (see db.py: WAL mode)
    init_paragraphs_table(conn)

    if args.reclassify_existing_boilerplate:
        ml_classifier = None
        if not args.no_ml_boilerplate_filter:
            ml_classifier = load_ml_boilerplate_classifier(args.ml_boilerplate_model, logger)
        reclassify_embedded_boilerplate(conn, logger, ml_classifier=ml_classifier)
        conn.close()
        return

    path_to_paper_id = load_file_path_to_paper_id(conn)
    all_records, raw_lines, unresolved_lines = load_paragraph_records(args.paragraphs_file, path_to_paper_id)
    logger.info("loaded %d paragraph(s) from %s", len(all_records), args.paragraphs_file)

    stale = reconcile_stale_paragraphs(conn, all_records)
    if stale:
        logger.info("removed %d stale paragraph row(s) no longer present in %s", stale, args.paragraphs_file)

    done = already_embedded(conn)
    if args.recompute:
        pending = all_records
    else:
        pending = [r for r in all_records if done.get((r[0], r[1])) != r[2]]
    logger.info("%d paragraph(s) need embedding (%d already done)", len(pending), len(all_records) - len(pending))

    ml_classifier = None
    if not args.no_skip_boilerplate and not args.no_ml_boilerplate_filter:
        ml_classifier = load_ml_boilerplate_classifier(args.ml_boilerplate_model, logger)

    to_embed, skipped_boilerplate = skip_boilerplate_paragraphs(
        conn, pending, logger, enabled=not args.no_skip_boilerplate, ml_classifier=ml_classifier)
    for paper_id, para_index, text in skipped_boilerplate:
        done[(paper_id, para_index)] = text

    to_embed, skipped_non_english = skip_non_english_paragraphs(conn, to_embed, args.min_english_score, logger)
    for paper_id, para_index, text in skipped_non_english:
        done[(paper_id, para_index)] = text

    touched_ids = []
    if to_embed:
        model = load_model(args.model, logger)
        touched_ids = embed_and_store(conn, model, to_embed, args.batch_size, logger)
        # Update in-memory rather than re-querying already_embedded() -- at this corpus's scale
        # (7M+ paragraphs) a second full "SELECT ... WHERE embedding IS NOT NULL" scan just to
        # learn what embed_and_store() already told us it just wrote is a real, avoidable cost
        # (see todo.md's LSH post-mortems on why full-table re-reads at this size are the actual
        # recurring performance trap in this codebase, not a hypothetical one).
        for paper_id, para_index, text in to_embed:
            done[(paper_id, para_index)] = text

    if not args.no_prune:
        text_by_key = {(paper_id, para_index): text for paper_id, para_index, text in all_records}
        prune_embedded_paragraphs(args.paragraphs_file, raw_lines, text_by_key, done, unresolved_lines, logger)

    total = conn.execute("SELECT COUNT(*) FROM paragraphs WHERE embedding IS NOT NULL").fetchone()[0]
    if total:
        lsh_index.init_lsh_tables(conn)
        removed_orphans = lsh_index.delete_orphaned_buckets(conn)
        if removed_orphans:
            logger.info("removed %d orphaned LSH bucket row(s)", removed_orphans)
        if touched_ids:
            lsh_index.invalidate_paragraphs(conn, touched_ids)
        dim, model_name = conn.execute(
            "SELECT embedding_dim, model FROM paragraphs WHERE embedding IS NOT NULL LIMIT 1"
        ).fetchone()
        _, n_indexed = lsh_index.sync_index(conn, model=model_name, embedding_dim=dim, logger_=logger)
        if n_indexed:
            logger.info("LSH-indexed %d paragraph(s) into the candidate lookup", n_indexed)

    conn.close()
    logger.info("done. %d paragraph(s) with embeddings in %s", total, args.library_db)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Persisted locality-sensitive hashing (LSH) index over paragraph embeddings.

This is the "hashtable where the key is the vector (or anything close enough
to it) and the value is a list of paragraph ids" from todo.md's "Candidate
index design (LSH)" write-up -- read that first for the full rationale and
the measured recall/bucket-occupancy numbers behind the DEFAULT_* constants
below.

Scheme: random-hyperplane LSH (SimHash), the standard approach for cosine
similarity. Embeddings are unit-normalized (see embed_paragraphs.py), so
cosine similarity is a dot product and a uniformly-random hyperplane through
the origin splits any two vectors with probability proportional to the angle
between them -- P[same side] = 1 - theta/pi. A paragraph's hash in one table
is a `bits_per_table`-bit code (one bit per hyperplane in that table); two
paragraphs are candidates if they share a code in ANY of `num_tables`
independently-hashed tables ("banding": trades index size for recall instead
of needing an enormous single table).

A shared bucket is a candidate, not a verdict -- callers still compute exact
cosine similarity on whatever this module returns before treating a pair as
a real near-duplicate.

Two entry points cover both ways this index gets used:
  - sync_index(): bulk-hash and insert every embedded-but-not-yet-bucketed
    paragraph. Called by embed_paragraphs.py right after it stores new/
    changed embeddings (so a paragraph is indexed the moment it's seen, no
    separate step), and by build_dupe_candidates.py as a cheap backfill
    safety net before it reads the index.
  - scan_candidate_pairs(): by default, INCREMENTAL -- only generates pairs
    that involve at least one paragraph not seen by a previous scan (tracked
    in lsh_buckets_scanned), instead of re-deriving the whole corpus's
    candidate set on every run. See its docstring for why this matters and
    the --full-rescan escape hatch's tradeoff.
"""

import numpy as np

DEFAULT_NUM_TABLES = 16
# 2026-08-23: raised from 12 -> 16 (2^12 x 16 = 65,536 total bucket slots was sized against a
# ~330K-916K paragraph corpus and had fully saturated by 10.35M paragraphs -- every slot occupied,
# candidate generation effectively stopped finding anything new. See todo.md's LSH design post-mortem 4.
#
# 2026-08-25: raised again, 16 -> 21, after post-mortem 6 (and a direct real-corpus measurement:
# median bucket size 72, mean 165, at 96,900 papers/~11M paragraphs) showed the 08-23 fix's own
# sizing comment ("~10/bucket average") was simply arithmetic-wrong -- it computed
# paragraphs/total_slots instead of (paragraphs x num_tables)/total_slots, undershooting the real
# mean by a factor of num_tables (16x). Corrected formula: mean occupancy = (paragraphs x num_tables)
# / (num_tables x 2^bits_per_table) = paragraphs / 2^bits_per_table -- i.e. num_tables cancels out of
# the AVERAGE (it only matters for how many independent chances a pair gets to collide, not average
# bucket size), so raising num_tables alone was never going to fix mean occupancy; bits_per_table is
# the only lever that does. At bits=21: 2^21 = 2,097,152 slots/table -> mean = ~11M paragraphs /
# 2,097,152 =~5.2/bucket, comfortably under max_bucket_size=30 even leaving real headroom for skew
# (this corpus's actual distribution is heavy-tailed, not uniform -- see post-mortem 6 on why: dense
# topical convergence in a narrow-topic corpus, not just enumerable boilerplate, so some buckets will
# stay oversized regardless of bits_per_table). Changing this invalidates every existing lsh_buckets
# row and lsh_scanned mark (load_or_create_planes() clears and rebuilds automatically) -- expect the
# next sync_index()/scan_candidate_pairs() pass to redo the full corpus, not an incremental top-up.
DEFAULT_BITS_PER_TABLE = 21
DEFAULT_SEED = 0
# Buckets bigger than this (per table) are skipped when generating candidate
# pairs -- see todo.md's "Candidate index design (LSH)" post-mortem for how
# this default was picked (300 was shipped without finishing the real-scale
# measurement and generated 263M pairs before dedup on this corpus -- ate
# 9.7GB RAM + 13GB swap and had to be killed; 30 measured at 3.4M, tractable).
DEFAULT_MAX_BUCKET_SIZE = 30
# Hard backstop regardless of max_bucket_size: scan_candidate_pairs() refuses
# to proceed past this projected pair count (checked cheaply from in-memory
# group sizes before doing any of the expensive numpy work), so a bad
# --max-bucket-size fails loudly and immediately instead of silently
# thrashing the machine. Overridable per-call via scan_candidate_pairs()'s
# max_candidate_pairs= param / build_dupe_candidates.py's --max-candidate-pairs
# -- this default is deliberately conservative for an UNKNOWN machine, not a
# hard ceiling on what's actually safe everywhere. Real measured cost of the
# final returned structure (a Python set of (int, int) tuples, both large/
# uncached paragraph ids): ~155 bytes/pair (resource.getrusage RSS delta,
# 5M-pair sample, 2026-08-26) -- budget from that, not the smaller ~35
# bytes/pair a set-of-tuples' own container overhead alone would suggest.
# 2026-08-23 real incident for scale: max_bucket_size=300 (unmeasured at the
# time) generated 263M pairs and hit 9.7GB RAM + 13GB swap before being
# killed, on a machine with real, separately-documented disk/memory distress
# under sustained load (see todo.md's "System / hardware reliability")-- that
# specific number was never a clean, uncontended measurement of pure
# pair-storage cost, which is why the 155 bytes/pair figure above (measured
# directly, in isolation) is the one to actually plan against.
MAX_CANDIDATE_PAIRS = 20_000_000


def init_lsh_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS lsh_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            model TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            num_tables INTEGER NOT NULL,
            bits_per_table INTEGER NOT NULL,
            seed INTEGER NOT NULL,
            planes BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lsh_buckets (
            table_num INTEGER NOT NULL,
            bucket_key INTEGER NOT NULL,
            paragraph_id INTEGER NOT NULL REFERENCES paragraphs(id),
            PRIMARY KEY (table_num, bucket_key, paragraph_id)
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS idx_lsh_buckets_paragraph ON lsh_buckets(paragraph_id);

        CREATE TABLE IF NOT EXISTS lsh_scanned (
            paragraph_id INTEGER PRIMARY KEY REFERENCES paragraphs(id)
        );

        CREATE TABLE IF NOT EXISTS lsh_indexed (
            paragraph_id INTEGER PRIMARY KEY REFERENCES paragraphs(id)
        );
        """
    )
    conn.commit()


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def load_or_create_planes(conn, model, embedding_dim, num_tables=DEFAULT_NUM_TABLES,
                           bits_per_table=DEFAULT_BITS_PER_TABLE, seed=DEFAULT_SEED, logger_=None):
    """Return the persisted (num_tables, bits_per_table, embedding_dim) random
    hyperplane matrix, generating and storing it on first use.

    Bucket keys only mean something relative to the exact hyperplanes that
    produced them, so if the requested config doesn't match what's on file
    (different model / embedding_dim / num_tables / bits_per_table / seed),
    every existing bucket row -- and every "already scanned"/"already indexed"
    mark, since a paragraph's bucket keys under the new hyperplanes are
    unrelated to its old ones -- is invalid and cleared along with the old
    planes.
    """
    row = conn.execute(
        "SELECT model, embedding_dim, num_tables, bits_per_table, seed, planes FROM lsh_config WHERE id = 1"
    ).fetchone()
    if row and row[:5] == (model, embedding_dim, num_tables, bits_per_table, seed):
        return np.frombuffer(row[5], dtype=np.float32).reshape(num_tables, bits_per_table, embedding_dim)

    if row and logger_:
        stale = conn.execute("SELECT COUNT(*) FROM lsh_buckets").fetchone()[0]
        logger_.warning(
            "LSH config changed (was model=%s dim=%s tables=%s bits=%s seed=%s -- now model=%s dim=%s "
            "tables=%s bits=%s seed=%s): regenerating hyperplanes and clearing %d existing bucket row(s)",
            row[0], row[1], row[2], row[3], row[4], model, embedding_dim, num_tables, bits_per_table, seed, stale,
        )

    rng = np.random.default_rng(seed)
    planes = rng.standard_normal((num_tables, bits_per_table, embedding_dim)).astype(np.float32)
    conn.execute("DELETE FROM lsh_buckets")
    conn.execute("DELETE FROM lsh_scanned")
    conn.execute("DELETE FROM lsh_indexed")
    conn.execute(
        """
        INSERT INTO lsh_config (id, model, embedding_dim, num_tables, bits_per_table, seed, planes)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            model=excluded.model, embedding_dim=excluded.embedding_dim, num_tables=excluded.num_tables,
            bits_per_table=excluded.bits_per_table, seed=excluded.seed, planes=excluded.planes
        """,
        (model, embedding_dim, num_tables, bits_per_table, seed, planes.tobytes()),
    )
    conn.commit()
    return planes


def bucket_keys(vectors, planes):
    """vectors: (n, dim) float32 array of unit-normalized embeddings.
    planes: (L, k, dim) hyperplane normals from load_or_create_planes.
    Returns (n, L) int64 -- each row's L per-table bucket keys, a k-bit code
    where bit i is set iff vectors[row] . planes[table, i] >= 0."""
    projections = np.einsum("nd,lkd->nlk", vectors, planes)  # (n, L, k)
    bits = (projections >= 0).astype(np.int64)
    weights = 1 << np.arange(planes.shape[1], dtype=np.int64)
    return bits @ weights  # (n, L)


def delete_orphaned_buckets(conn):
    """Remove bucket rows (and their "scanned"/"indexed" marks) for paragraphs that no
    longer exist (e.g. after embed_paragraphs.py's reconcile_stale_paragraphs
    drops a stale row)."""
    cur = conn.execute("DELETE FROM lsh_buckets WHERE paragraph_id NOT IN (SELECT id FROM paragraphs)")
    conn.execute("DELETE FROM lsh_scanned WHERE paragraph_id NOT IN (SELECT id FROM paragraphs)")
    conn.execute("DELETE FROM lsh_indexed WHERE paragraph_id NOT IN (SELECT id FROM paragraphs)")
    conn.commit()
    return cur.rowcount


def invalidate_paragraphs(conn, paragraph_ids):
    """Delete any existing bucket rows, "scanned" marks, AND "indexed" marks for these
    paragraph_ids. Call before sync_index() for paragraphs whose embedding
    may have changed at an existing id -- embed_paragraphs.py's
    upsert-by-(paper_id, para_index) keeps the same paragraph_id across a
    text/embedding change, so without this: (a) sync_index would see an
    already-indexed id (lsh_indexed) and skip it, leaving bucket rows computed
    from the old, now-overwritten embedding, and (b) scan_candidate_pairs's
    incremental mode would see an already-"scanned" id and never re-check it
    against anyone under its new bucket keys."""
    paragraph_ids = list(paragraph_ids)
    removed = 0
    for chunk in _chunks(paragraph_ids, 500):
        placeholders = ",".join("?" * len(chunk))
        cur = conn.execute(f"DELETE FROM lsh_buckets WHERE paragraph_id IN ({placeholders})", chunk)
        removed += cur.rowcount
        conn.execute(f"DELETE FROM lsh_scanned WHERE paragraph_id IN ({placeholders})", chunk)
        conn.execute(f"DELETE FROM lsh_indexed WHERE paragraph_id IN ({placeholders})", chunk)
    conn.commit()
    return removed


def mark_scanned(conn, paragraph_ids):
    paragraph_ids = list(paragraph_ids)
    for chunk in _chunks(paragraph_ids, 5000):
        conn.executemany("INSERT OR IGNORE INTO lsh_scanned (paragraph_id) VALUES (?)", [(pid,) for pid in chunk])
        conn.commit()


def sync_index(conn, model, embedding_dim, num_tables=DEFAULT_NUM_TABLES, bits_per_table=DEFAULT_BITS_PER_TABLE,
                seed=DEFAULT_SEED, logger_=None, id_chunk_size=200_000, log_every_chunk=True):
    """Ensure every embedded paragraph matching (model, embedding_dim) has
    bucket rows. Finds paragraphs with an embedding but not yet marked in
    `lsh_indexed` and hashes+inserts them (into both `lsh_buckets` and
    `lsh_indexed`) in one vectorized pass. Cheap to call every run: the
    common case is nothing to do. Returns (planes, count_indexed).

    Paragraphs whose model/embedding_dim don't match the current config are
    left alone (same latent gap as find_duplicates.py's brute force, which
    also doesn't segregate by model -- not introduced here, not fixed here).

    Why a separate `lsh_indexed(paragraph_id)` tracking table instead of just
    checking "is this paragraph missing from lsh_buckets" directly: it was
    checked that way originally (`LEFT JOIN lsh_buckets ... WHERE
    b.paragraph_id IS NULL`, table_num=0 as a stand-in for "present at all",
    since a paragraph is always inserted into every table in one batch). That
    query plan is genuinely fine (confirmed via EXPLAIN QUERY PLAN -- an
    index-covered search, no full scan) but at real corpus scale (~14M
    embedded paragraphs x up to num_tables=16 rows each -- lsh_buckets north
    of 200M rows) it OOM-killed two separate real runs anyway (see todo.md's
    2026-09-03 post-mortem for the full investigation, including a from-
    scratch chunked-by-id-range rewrite that *also* wasn't enough on its
    own): probing a 200M+-row table millions of times is enormous real work
    regardless of how the ID list feeding it is chunked, because every chunk
    still has to touch the same giant table. `lsh_indexed` has exactly one
    row per paragraph (not per paragraph-per-table) -- checking against it
    instead is a ~16x smaller table to search against, structurally, not just
    a smaller chunk of the same search.

    log_every_chunk (default True): logs a progress line after every id-range chunk, not just
    ones that found something pending. Real progress visibility on a run spanning many minutes
    across ~800+ chunks, and empirically the safer default: every clean, flat-memory run of this
    function during the 2026-09-03 investigation happened to have per-chunk logging on, and every
    run that grew to multiple GB and had to be killed had it off -- the exact mechanism was never
    pinned down (plausibly: interleaving Python-level logging/flush calls between chunks changes
    how the process's memory allocator behaves, keeping it from growing one large contiguous
    arena the way sparser allocation patterns might), but the correlation held up over repeated,
    alternating trials on the same real corpus, and logging every chunk costs nothing meaningful
    on its own. Pass False to opt back into the old quiet-unless-something-changed behavior.
    """
    init_lsh_tables(conn)
    planes = load_or_create_planes(conn, model, embedding_dim, num_tables, bits_per_table, seed, logger_)

    id_range = conn.execute("SELECT MIN(id), MAX(id) FROM paragraphs").fetchone()
    if id_range[0] is None:
        return planes, 0
    min_id, max_id = id_range

    # One-time, chunked migration for a database that already has lsh_buckets rows from
    # before lsh_indexed existed: populate lsh_indexed from lsh_buckets' own contents
    # (table_num=0 as the "present at all" marker, same convention as the old pending
    # check) rather than assuming everything needs re-hashing -- INSERT OR IGNORE into
    # lsh_buckets would just waste a lot of time re-deriving buckets already there, not
    # cause wrong results, but re-doing ~14M paragraphs' worth of work for nothing is
    # exactly the kind of avoidable cost this whole rewrite is about removing. Chunked by
    # id range for the same reason as the main loop below -- SELECT DISTINCT over the
    # whole lsh_buckets table in one query is itself a large-table scan at this scale.
    for chunk_start in range(min_id, max_id + 1, id_chunk_size):
        chunk_end = chunk_start + id_chunk_size - 1
        migrated = conn.execute(
            """
            INSERT OR IGNORE INTO lsh_indexed (paragraph_id)
            SELECT DISTINCT paragraph_id FROM lsh_buckets
            WHERE table_num = 0 AND paragraph_id BETWEEN ? AND ?
            """,
            (chunk_start, chunk_end),
        )
        conn.commit()
        if migrated.rowcount:
            logger_ and logger_.info("lsh_indexed migration: %d paragraph(s) backfilled (id<=%d of %d)",
                                      migrated.rowcount, chunk_end, max_id)

    # Batched on both the read and write side -- see the docstring above for why the read
    # side (which paragraphs are pending) is chunked by id range against the small
    # lsh_indexed table rather than the large lsh_buckets one, and why that distinction
    # matters at this corpus's scale. Commit after every batch, not once at the end --
    # library.sqlite3 has other writers (embed_paragraphs.py itself, or another script)
    # that connect with a 30s busy-timeout; one long uncommitted transaction over the whole
    # backfill can hold the write lock past that and crash a concurrent writer with
    # "database is locked" (this happened for real -- see todo.md's post-mortem). A commit
    # per batch bounds the lock hold to about one executemany() call.
    batch_size = 2000  # paragraphs per commit
    total_indexed = 0
    for chunk_start in range(min_id, max_id + 1, id_chunk_size):
        chunk_end = chunk_start + id_chunk_size - 1
        pending_ids = [
            r[0]
            for r in conn.execute(
                """
                SELECT p.id FROM paragraphs p
                LEFT JOIN lsh_indexed idx ON idx.paragraph_id = p.id
                WHERE p.id BETWEEN ? AND ?
                  AND p.embedding IS NOT NULL AND p.model = ? AND p.embedding_dim = ? AND idx.paragraph_id IS NULL
                """,
                (chunk_start, chunk_end, model, embedding_dim),
            ).fetchall()
        ]
        for start in range(0, len(pending_ids), batch_size):
            batch_ids = pending_ids[start:start + batch_size]
            placeholders = ",".join("?" * len(batch_ids))
            rows = conn.execute(
                f"SELECT id, embedding FROM paragraphs WHERE id IN ({placeholders})", batch_ids
            ).fetchall()
            ids = [r[0] for r in rows]
            vectors = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
            keys = bucket_keys(vectors, planes)  # (n, L)
            insert_batch = [(t, int(keys[i, t]), ids[i]) for i in range(len(ids)) for t in range(keys.shape[1])]
            conn.executemany(
                "INSERT OR IGNORE INTO lsh_buckets (table_num, bucket_key, paragraph_id) VALUES (?, ?, ?)",
                insert_batch,
            )
            conn.executemany(
                "INSERT OR IGNORE INTO lsh_indexed (paragraph_id) VALUES (?)", [(i,) for i in ids]
            )
            conn.commit()
            total_indexed += len(ids)
        if logger_ is not None and (total_indexed or log_every_chunk):
            logger_.info("LSH sync: %d paragraph(s) indexed so far (through id<=%d of %d)",
                          total_indexed, chunk_end, max_id)
    return planes, total_indexed


def _pairs_for_group(pids, is_new):
    """pids, is_new: same-length int64/bool numpy arrays for one bucket
    group. Returns an int64 array of (lo << 32 | hi) pair codes covering
    every new-new and new-old pair -- deliberately NOT old-old (see
    scan_candidate_pairs's docstring: those were already generated in
    whichever earlier scan first saw them co-occurring)."""
    new_idx = np.flatnonzero(is_new)
    old_idx = np.flatnonzero(~is_new)
    parts = []
    if len(new_idx) >= 2:
        i, j = np.triu_indices(len(new_idx), k=1)
        a, b = pids[new_idx[i]], pids[new_idx[j]]
        parts.append((np.minimum(a, b) << 32) | np.maximum(a, b))
    if len(new_idx) >= 1 and len(old_idx) >= 1:
        a = np.repeat(pids[new_idx], len(old_idx))
        b = np.tile(pids[old_idx], len(new_idx))
        parts.append((np.minimum(a, b) << 32) | np.maximum(a, b))
    if not parts:
        return np.empty(0, dtype=np.int64)
    return np.concatenate(parts)


def scan_candidate_pairs(conn, max_bucket_size=DEFAULT_MAX_BUCKET_SIZE, logger_=None, full_rescan=False,
                          max_candidate_pairs=MAX_CANDIDATE_PAIRS):
    """Pairs of paragraph_ids sharing a bucket in at least one table -- the
    replacement for a brute-force N x N compare.

    INCREMENTAL BY DEFAULT: only pairs involving at least one paragraph not
    covered by a previous scan (tracked in lsh_scanned) are generated. A
    bucket untouched by any new paragraph can't produce a pair that wasn't
    already found (or correctly rejected) the last time it was scanned, so
    re-deriving it is pure waste -- and at this corpus's real size, that
    "pure waste" was the actual mechanism behind repeated hour-plus stalls
    running concurrently with embed_paragraphs.py (todo.md's post-mortem):
    every run re-read the ENTIRE lsh_buckets table regardless of how much
    was actually new. Incremental scanning only touches buckets that contain
    at least one not-yet-scanned paragraph, which is normally a small
    fraction of the corpus.

    Tradeoff this creates, and why --full-rescan exists: once two paragraphs
    have co-occurred in a bucket in some past scan, that pair is never
    re-examined, REGARDLESS of what --threshold a later build_dupe_candidates.py
    run uses (scan_candidate_pairs doesn't know about --threshold -- that's
    applied afterward, to whatever this returns). So a pair that was
    correctly excluded at a high --threshold won't be picked back up by
    simply lowering --threshold later; only a --full-rescan will re-examine
    it. Use --full-rescan for that case, or periodically as a full audit --
    it reproduces the pre-incremental behavior exactly (and re-marks
    everything as scanned afterward, so the next default run is incremental
    again from that point).

    Buckets bigger than max_bucket_size are skipped entirely (in that table
    only -- the others still get an independent shot at the same
    paragraphs); see todo.md for the known gap this leaves for
    exact-duplicate clusters larger than max_bucket_size in every table.
    Group sizes are checked against max_candidate_pairs (default
    MAX_CANDIDATE_PAIRS, see its own comment on what's actually safe to raise
    it to) before any of the expensive numpy work -- real bucket occupancy on
    this corpus is skewed enough that a too-generous max_bucket_size can still project into the
    hundreds of millions of pairs (see todo.md's post-mortem for what
    happened before this existed).

    Returns a set of (paragraph_id_lo, paragraph_id_hi) tuples, lo < hi.
    See scan_candidate_pairs_to_table() for a streaming alternative when the
    result set itself (not just Pass 1's group bookkeeping) would be too
    large to hold in memory as a Python set -- this function's own Pass 2
    still isn't a good fit for a result in the tens of millions (see that
    function's docstring for the real incident this was split out for).
    """
    paragraph_ids, is_new, kept_groups, ids_to_mark = _discover_candidate_groups(
        conn, max_bucket_size, full_rescan, max_candidate_pairs, logger_
    )
    if kept_groups is None:  # nothing to do -- already logged/marked by the helper
        return set()

    # Pass 2: actually generate the pairs for the groups pass 1 kept.
    codes = [_pairs_for_group(paragraph_ids[g], is_new[g]) for g in kept_groups]
    codes = [c for c in codes if len(c)]
    pair_codes = np.unique(np.concatenate(codes)) if codes else np.empty(0, dtype=np.int64)

    mark_scanned(conn, ids_to_mark)

    return {(int(code >> 32), int(code & 0xFFFFFFFF)) for code in pair_codes}


def scan_candidate_pairs_to_table(conn, dest_table, max_bucket_size=DEFAULT_MAX_BUCKET_SIZE, logger_=None,
                                   full_rescan=False, max_candidate_pairs=MAX_CANDIDATE_PAIRS,
                                   group_batch_size=50_000):
    """Streaming alternative to scan_candidate_pairs() for when the result set
    itself is too large to hold as one Python set -- writes deduplicated
    (paragraph_id_lo, paragraph_id_hi) pairs directly into `dest_table`
    (caller-created, expected schema: `paragraph_id_lo INTEGER, paragraph_id_hi
    INTEGER, PRIMARY KEY (paragraph_id_lo, paragraph_id_hi)` -- a plain temp
    table is normal here) instead of returning them.

    Added 2026-08-26 after a real OOM: post-21-bit-rehash (see lsh_index.py's
    DEFAULT_BITS_PER_TABLE comment), a --full-rescan on this corpus's actual
    16.6M kept groups was killed by the kernel OOM-killer at 30.6GB RSS.
    scan_candidate_pairs()'s Pass 2 builds `codes` as a Python LIST holding
    one small numpy array PER KEPT GROUP (16.6M of them here) before ever
    concatenating -- the per-array Python/numpy object overhead across that
    many small arrays, not just the final pair data, is itself a huge
    allocation, and it's held alive simultaneously with everything else
    (SQLite connection buffers, the eventual concatenated/deduplicated
    array, and -- critically -- whatever the CALLER does next, which in
    build_dupe_candidates.py's case was loading full embedding+text rows
    for every touched paragraph, found separately to touch 65-96% of this
    corpus's 10.2M paragraphs regardless of --max-bucket-size). This
    function fixes its own Pass 2 by processing kept_groups in
    group_batch_size chunks, writing each chunk's pairs to `dest_table` and
    discarding the chunk's arrays immediately -- peak memory for THIS
    function is now bounded by group_batch_size, not total corpus size.
    Callers still need their OWN downstream processing (loading paragraph
    data, computing real similarity, persisting) to consume `dest_table` in
    batches too -- see build_dupe_candidates.py's
    process_lsh_candidates_streaming(), the actual fix for the OOM's other
    half.

    Same Pass 1 (group discovery, oversized-group skip, MAX_CANDIDATE_PAIRS
    safety check) as scan_candidate_pairs() -- see _discover_candidate_groups().
    """
    paragraph_ids, is_new, kept_groups, ids_to_mark = _discover_candidate_groups(
        conn, max_bucket_size, full_rescan, max_candidate_pairs, logger_
    )
    if kept_groups is None:
        return 0

    total_written = 0
    for start in range(0, len(kept_groups), group_batch_size):
        chunk = kept_groups[start:start + group_batch_size]
        codes = [_pairs_for_group(paragraph_ids[g], is_new[g]) for g in chunk]
        codes = [c for c in codes if len(c)]
        if codes:
            pair_codes = np.unique(np.concatenate(codes))
            conn.executemany(
                f"INSERT OR IGNORE INTO {dest_table} (paragraph_id_lo, paragraph_id_hi) VALUES (?, ?)",
                [(int(code >> 32), int(code & 0xFFFFFFFF)) for code in pair_codes],
            )
            conn.commit()
            total_written += len(pair_codes)
        if logger_:
            logger_.info("LSH scan: %d/%d group(s) processed, ~%d pair(s) written so far",
                         min(start + group_batch_size, len(kept_groups)), len(kept_groups), total_written)

    mark_scanned(conn, ids_to_mark)
    return total_written


def _discover_candidate_groups(conn, max_bucket_size, full_rescan, max_candidate_pairs, logger_):
    """Pass 1, shared by scan_candidate_pairs() and scan_candidate_pairs_to_table():
    load bucket membership, split into (table_num, bucket_key) groups, skip
    oversized ones, and safety-check the projected pair count -- all cheap
    relative to Pass 2's actual pair generation, and identical regardless of
    which Pass-2 strategy the caller wants.

    Returns (paragraph_ids, is_new, kept_groups, ids_to_mark), or
    (None, None, None, None) when there's nothing to do (already logged and
    mark_scanned() already called by this function in that case, matching
    scan_candidate_pairs()'s old early-return behavior exactly)."""
    if full_rescan:
        rows = conn.execute(
            "SELECT table_num, bucket_key, paragraph_id FROM lsh_buckets ORDER BY table_num, bucket_key"
        ).fetchall()
        if not rows:
            return None, None, None, None
        table_nums = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        keys = np.fromiter((r[1] for r in rows), dtype=np.int64, count=len(rows))
        paragraph_ids = np.fromiter((r[2] for r in rows), dtype=np.int64, count=len(rows))
        is_new = np.ones(len(rows), dtype=bool)  # everyone "new" -> reduces to the old full-table behavior
        ids_to_mark = paragraph_ids.tolist()
    else:
        # lsh_indexed (one row per paragraph), not lsh_buckets (one row per paragraph per
        # table -- ~16x larger, and needed a DISTINCT here to boot) -- same reasoning as
        # sync_index()'s own pending check (see its docstring and todo.md's 2026-09-03
        # post-mortem): "is this paragraph present in the LSH index at all" never needs to
        # touch the big table when lsh_indexed already answers exactly that, in one row per
        # paragraph, with no DISTINCT needed since it's already unique by primary key.
        new_rows = conn.execute(
            """
            SELECT i.paragraph_id FROM lsh_indexed i
            LEFT JOIN lsh_scanned s ON s.paragraph_id = i.paragraph_id
            WHERE s.paragraph_id IS NULL
            """
        ).fetchall()
        new_ids = [r[0] for r in new_rows]
        if not new_ids:
            if logger_:
                logger_.info("LSH scan (incremental): nothing new since the last scan -- 0 candidate pair(s)")
            return None, None, None, None

        # Only buckets touched by at least one new paragraph can produce a pair
        # that wasn't already accounted for -- restrict the (otherwise expensive)
        # full-membership fetch to just those, via a temp table of the new ids.
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _lsh_scan_new (paragraph_id INTEGER PRIMARY KEY)")
        conn.execute("DELETE FROM _lsh_scan_new")
        conn.executemany("INSERT INTO _lsh_scan_new (paragraph_id) VALUES (?)", [(pid,) for pid in new_ids])
        rows = conn.execute(
            """
            SELECT b.table_num, b.bucket_key, b.paragraph_id, (n.paragraph_id IS NOT NULL) AS is_new
            FROM lsh_buckets b
            JOIN (
                SELECT DISTINCT b2.table_num, b2.bucket_key
                FROM lsh_buckets b2
                JOIN _lsh_scan_new n2 ON n2.paragraph_id = b2.paragraph_id
            ) touched ON touched.table_num = b.table_num AND touched.bucket_key = b.bucket_key
            LEFT JOIN _lsh_scan_new n ON n.paragraph_id = b.paragraph_id
            ORDER BY b.table_num, b.bucket_key
            """
        ).fetchall()
        conn.execute("DROP TABLE _lsh_scan_new")
        if not rows:
            mark_scanned(conn, new_ids)
            return None, None, None, None
        table_nums = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        keys = np.fromiter((r[1] for r in rows), dtype=np.int64, count=len(rows))
        paragraph_ids = np.fromiter((r[2] for r in rows), dtype=np.int64, count=len(rows))
        is_new = np.fromiter((bool(r[3]) for r in rows), dtype=bool, count=len(rows))
        ids_to_mark = new_ids

    composite = table_nums * (1 << 32) + keys  # sortable (table_num, bucket_key) pair -- rows are already in this order
    boundaries = np.flatnonzero(np.diff(composite)) + 1
    groups = np.split(np.arange(len(composite)), boundaries)

    # Pass 1: filter oversized groups and project the pair count this will actually
    # generate (new-new + new-old, not naive C(size,2) -- incremental mode generates
    # fewer pairs per group than a full rescan would) before doing any of pass 2's work.
    kept_groups = []
    total_groups = 0
    skipped_groups = 0
    projected_pairs = 0
    for g in groups:
        m = len(g)
        if m < 2:
            continue
        total_groups += 1
        if m > max_bucket_size:
            skipped_groups += 1
            continue
        n_new = int(is_new[g].sum())
        n_old = m - n_new
        projected_pairs += n_new * (n_new - 1) // 2 + n_new * n_old
        kept_groups.append(g)

    if projected_pairs > max_candidate_pairs:
        raise RuntimeError(
            f"LSH candidate scan would generate ~{projected_pairs:,} pair(s) at "
            f"max_bucket_size={max_bucket_size} (> {max_candidate_pairs:,} safety ceiling). This corpus "
            f"has large near-duplicate/boilerplate clusters -- lower --max-bucket-size (or raise "
            f"--max-candidate-pairs, if you've sized real memory headroom for it -- see MAX_CANDIDATE_PAIRS's "
            f"own comment for the measured ~155 bytes/pair cost) and retry rather than proceeding "
            f"(see todo.md's LSH design post-mortem)."
        )
    if logger_:
        logger_.info(
            "LSH scan (%s): %d bucket(s) with >=2 member(s) (%d oversized/skipped), ~%d projected pair(s) "
            "-- proceeding", "full" if full_rescan else "incremental", total_groups, skipped_groups, projected_pairs,
        )

    return paragraph_ids, is_new, kept_groups, ids_to_mark

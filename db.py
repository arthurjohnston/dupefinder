#!/usr/bin/env python3
"""Shared sqlite3 connection helper -- always opens in WAL journal mode.

Why: this project's scripts intentionally connect to library.sqlite3/
state.sqlite3 concurrently (see each script's own "other pipeline stages may
be writing concurrently" comments) and rely on `timeout=` to wait out brief
lock contention. That works for writer-vs-writer contention, but SQLite's
*default* rollback-journal mode has a sharper problem: a plain read from one
connection holds a shared lock that blocks another connection's write from
ever reaching COMMIT (which needs an exclusive lock), so even a read-only
sanity-check query run at the wrong moment can starve a live writer past its
timeout and crash it with "database is locked" (see README's runbook and
todo.md's LSH post-mortem -- this happened for real, from a diagnostic
`SELECT COUNT(*)` colliding with a live embed_paragraphs.py run).

WAL mode removes that: readers read a stable snapshot and never block a
writer's commit, and a writer never blocks readers. It's the standard fix
for exactly this "one writer, occasional reader checks" pattern.

WAL is persisted in the database file itself once set, so this only truly
needs to run once -- but setting it on every connect() is cheap (a no-op if
already WAL) and keeps it guaranteed rather than assumed. Switching *to* WAL
needs exclusive access to the file: if another connection is already open
elsewhere when this runs, the PRAGMA silently has no effect (the connection
proceeds in whatever mode the file is currently in) rather than erroring --
it'll take effect the next time nothing else has the file open.

Also sets `synchronous=NORMAL` -- SQLite's own documented pairing for WAL
mode. The default (FULL) forces an fsync on every single commit, WAL or not;
NORMAL only syncs at checkpoint boundaries instead. This matters a lot here
because several write loops (lsh_index.py's sync_index(), build_dupe_candidates.py's
build_candidates()) deliberately commit every ~2000 rows to keep any one
transaction's lock-hold-time short (see todo.md's LSH post-mortem) -- with
the default FULL synchronous, that turned thousands of small commits into
thousands of full fsyncs, and on this machine's disk that alone made a
backfill run sit at ~0.3% CPU for 3 hours without finishing a single
scan (confirmed via /proc/<pid>/io showing almost no CPU time despite huge
wall-clock time, and no other process holding the file open -- see README's
"is a silent, running script actually stuck?" runbook). NORMAL still keeps
WAL mode's consistency guarantee; the only tradeoff is that a small window
of already-committed transactions could theoretically be lost on an actual
OS/power crash (not an app crash) before the next checkpoint -- an accepted
tradeoff for a local research tool, not a financial system.
"""

import sqlite3


def connect(path, timeout=120):
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Default wal_autocheckpoint is 1000 pages (~4MB) -- on this machine's data disk (a
    # spinning HDD under LUKS+LVM, confirmed via `lsblk`'s ROTA=1, not an assumption), a
    # write-heavy bulk-load (lsh_index.py's sync_index() scattering ~14M small rows across
    # a WITHOUT ROWID b-tree) hits that threshold roughly every few seconds, and each
    # checkpoint is itself an expensive scattered-write flush back into the main file on
    # media that's already close to worst-case for small random I/O. Raising the threshold
    # lets far more writes accumulate as a comparatively cheap sequential WAL append before
    # paying for a checkpoint -- purely a throughput/latency tradeoff (a bigger WAL file
    # between checkpoints, more to redo after an unclean shutdown), not a correctness one.
    conn.execute("PRAGMA wal_autocheckpoint=20000")
    return conn

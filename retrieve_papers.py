#!/usr/bin/env python3
"""Retrieve open-access PDFs for papers listed in a JSON file.

Pipeline per paper:
  1. Resolve a DOI via Crossref if one isn't already known.
  2. If the input entry already carries an "oa_url" and/or "oa_alt_urls" (e.g.
     from sourcing/build_oa_starting_list.py, which threads through OpenAlex's
     open_access.oa_url plus any other locations() entries OpenAlex had on
     file -- see enrich_open_access.py), try downloading each in turn first --
     skips step 3 entirely for the common case. Falls back to step 3 if all of
     them are missing, stale, or don't serve a PDF.
  3. Look up the DOI in Unpaywall to find an OA location.
  4. Download the PDF if one is available.

Step 2 exists because Unpaywall is a single shared host that RateLimiter
throttles to one request per --min-interval regardless of --max-workers --
on a large batch where most entries already carry a known-good OA URL, that
shared-host ceiling was the actual bottleneck, not per-item overhead (see
CLAUDE.md's retrieve_papers.py section).

All state is kept in a local SQLite manifest so re-running the script is
cheap and safe: papers already downloaded are skipped, and DOIs/OA lookups
that were already resolved are not re-queried.
"""

import argparse
import html
import json
import logging
import os
import random
import re
import threading
import time
import unicodedata
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

import db

USER_AGENT_TEMPLATE = "dupefinder-paper-fetcher/1.0 (mailto:{email})"
CROSSREF_API = "https://api.crossref.org/works"
UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}"
OPENALEX_API = "https://api.openalex.org/works"
OPENALEX_BATCH_SIZE = 50  # OpenAlex's own documented cap on pipe-separated filter values
OPENALEX_API_KEY_ENV_VAR = "OPENALEX_API_KEY"  # deliberately an env var, never a CLI flag/args
# attribute -- a CLI arg would put the key in plain sight in `ps aux` for the life of the
# process; an env var doesn't. Get a free key at openalex.org/settings/api -- raises the
# effective rate limit well past what anonymous access gets (see todo.md's "Full-corpus
# plagiarism audit" follow-up for why anonymous access wasn't enough for real).

DEFAULT_MIN_INTERVAL = 1.0  # seconds between requests to the same host
DEFAULT_MAX_RETRIES = 4
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_WORKERS = 8
DOI_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)


def slugify(text: str, max_len: int = 120) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:max_len] or "untitled"


def normalize_doi(doi):
    if not doi:
        return None
    return DOI_PREFIX_RE.sub("", doi.strip())


class RateLimiter:
    """Enforces a minimum delay between requests to the same host, so we
    never hammer Crossref/Unpaywall/publisher sites back-to-back. Already
    per-host (keyed by netloc, see http_get()) -- different hosts never wait
    on each other. Lock-protected so it's safe to share across threads: the
    old single-threaded callers never raced on the check-then-set in wait(),
    but concurrent_fetch() below does, and a race here would mean two
    requests to the same host slipping through closer together than
    min_interval allows."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last_request = {}
        self._lock = threading.Lock()

    def wait(self, host: str):
        while True:
            with self._lock:
                now = time.monotonic()
                last = self._last_request.get(host)
                remaining = self.min_interval - (now - last) if last is not None else 0
                if remaining <= 0:
                    self._last_request[host] = now
                    return
            time.sleep(remaining)


def concurrent_fetch(items, fetch_fn, max_workers=8, max_successes=None, is_success=lambda result: result == "downloaded",
                      logger=None):
    """Runs fetch_fn(item) across a thread pool instead of one item at a time.

    Why: the actual per-request delay (rate limiting, network latency, a slow
    publisher server) was never the bottleneck for total wall-clock time --
    RateLimiter is already per-host, so different hosts never blocked each
    other. What made retrieval slow was doing exactly one fetch at a time
    regardless of host, so N candidates against N different, mostly-idle
    hosts took N sequential round-trips instead of running mostly in
    parallel. This bounds concurrency instead (max_workers threads), and
    each thread still goes through the same shared RateLimiter, so any
    *single* host (e.g. Unpaywall, hit once per candidate) is still
    throttled exactly as politely as before.

    Stops submitting new work once max_successes is reached, but a handful
    of already-in-flight fetches past that point are allowed to finish
    rather than being hard-cancelled mid-request.

    Yields (item, result) pairs as they complete -- not in input order.
    """
    successes = 0
    lock = threading.Lock()
    stop = threading.Event()

    def guarded(item):
        nonlocal successes
        if stop.is_set():
            return None
        try:
            result = fetch_fn(item)
        except Exception as exc:
            # One candidate's unhandled exception (confirmed for real: concurrent
            # ThreadLocalPaperStore writes hit "database is locked" under load --
            # see its own docstring for the actual fix) used to propagate out of
            # future.result() below and kill the *entire* run, discarding every
            # not-yet-attempted candidate. A single candidate failing shouldn't
            # take down a multi-thousand-item batch -- log it and move on, the
            # same as every other failure mode already handled per-candidate.
            if logger:
                logger.error("unhandled error fetching %r: %s", item, exc)
            return "thread_error"
        if max_successes and is_success(result):
            with lock:
                successes += 1
                if successes >= max_successes:
                    stop.set()
        return result

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(guarded, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except CancelledError:
                # A future we cancelled below, after max_successes was already hit --
                # same "nothing to report" outcome as guarded() returning None for a
                # future that started too late to matter, just via a different path
                # (cancel() only succeeds on a future that hasn't started running yet;
                # one already running gets the stop.is_set() check inside guarded()
                # instead and returns None on its own). Confirmed reproducible with a
                # slow fetch_fn and few workers -- a fast fetch_fn racing ahead of the
                # cancel scan can make this path rare in practice, but "rare" isn't
                # "impossible", and an uncaught CancelledError here would otherwise
                # kill the whole run and its not-yet-attempted items.
                continue
            if result is not None:
                yield item, result
            if stop.is_set():
                # Let already-running futures finish (their requests are already
                # in flight), but don't wait around for ones that hadn't started.
                for f in futures:
                    if not f.running() and not f.done():
                        f.cancel()


class KeyedLock:
    """Per-key mutex so two threads never run process_paper() for the same
    make_key() result concurrently.

    Without this, two input entries that resolve to the same key (duplicate
    entries in the input list, or the same DOI appearing in two overlapping
    batches run at once by mistake) could both pass process_paper()'s
    "already downloaded" check before either has committed a status, then
    both call download_pdf() for the same dest_path at the same time --
    writing the exact same tmp_path from two independent HTTP streams at
    once. The result can still start with a valid %PDF- header (whichever
    download's first chunk wins the race) and pass the magic-byte check
    while the rest of the file is corrupted/interleaved. Dormant for the
    batches this project currently runs (no duplicate DOIs within either
    file, and batches are launched strictly sequentially -- see
    run_pipeline.py/the batch-2 watcher), but concurrent_fetch() makes this
    reachable in general, and it's cheap insurance against a plausible
    future mistake (a manual re-run overlapping a live one, an input list
    merged without full dedup).
    """

    def __init__(self):
        self._locks = {}
        self._registry_lock = threading.Lock()

    def _lock_for(self, key):
        with self._registry_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def acquire(self, key):
        self._lock_for(key).acquire()

    def release(self, key):
        self._lock_for(key).release()


class RetrievalError(Exception):
    """Non-retryable failure for a single request."""


class RateLimited(RetrievalError):
    """A 429 that a caller wants to react to distinctly from a generic
    failure (e.g. query_openalex_batch()'s callers stop trying OpenAlex
    entirely for the rest of the run rather than retrying batch after
    batch into the same wall). Raised by http_get() only when max_retries=0
    (a caller opting out of the built-in retry loop for exactly this
    reason) -- with any other max_retries value, a 429 is still retried/
    backed off internally and only ever surfaces as the plain RetrievalError
    it always has."""


MAX_RETRY_AFTER_DELAY = 60.0  # seconds


def _backoff_delay(attempt: int) -> float:
    return min(60, 2 ** attempt) + random.uniform(0, 1)


def _retry_after_delay(resp, attempt: int) -> float:
    """Confirmed necessary for real, not just defensive: OpenAlex sent a
    Retry-After of 71649 seconds (~20 hours) after this project's own bulk
    retrieval jobs pushed past its (undocumented for anonymous/keyless
    callers) rate limit -- uncapped, that's a single http_get() call
    blocking the entire process for most of a day. A server is free to ask
    for a long wait; a single request handling that wait by sleeping
    through it, rather than surfacing "still limited, try again later" to
    the caller, is not. Capped at MAX_RETRY_AFTER_DELAY regardless of what
    the header says -- a caller that legitimately needs to keep waiting
    will just see another 429 on the next attempt and back off again, up to
    max_retries."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(MAX_RETRY_AFTER_DELAY, max(0.0, float(header)))
        except ValueError:
            try:
                dt = parsedate_to_datetime(header)
                return min(MAX_RETRY_AFTER_DELAY, max(0.0, (dt - datetime.now(dt.tzinfo)).total_seconds()))
            except Exception:
                pass
    return _backoff_delay(attempt)


def http_get(session, url, params, *, rate_limiter, max_retries, timeout, logger, stream=False):
    host = urlparse(url).netloc
    attempt = 0
    while True:
        rate_limiter.wait(host)
        attempt += 1
        try:
            resp = session.get(url, params=params, timeout=timeout, stream=stream)
        except requests.RequestException as exc:
            if attempt > max_retries:
                raise RetrievalError(f"network error after {attempt} attempts: {exc}") from exc
            delay = _backoff_delay(attempt)
            logger.warning("network error on %s (%s), retrying in %.1fs [%d/%d]", host, exc, delay, attempt, max_retries)
            time.sleep(delay)
            continue

        if resp.status_code == 429:
            if attempt > max_retries:
                if max_retries == 0:
                    # A caller that explicitly opted out of retries (max_retries=0) wants to
                    # react to "rate limited" distinctly from "failed for some other reason" --
                    # see RateLimited's docstring.
                    raise RateLimited(f"rate limited (429) by {host}")
                raise RetrievalError(f"rate limited (429) by {host} after {attempt} attempts")
            delay = _retry_after_delay(resp, attempt)
            logger.warning("429 from %s, waiting %.1fs [%d/%d]", host, delay, attempt, max_retries)
            resp.close()
            time.sleep(delay)
            continue

        if resp.status_code >= 500:
            if attempt > max_retries:
                raise RetrievalError(f"server error {resp.status_code} from {host} after {attempt} attempts")
            delay = _backoff_delay(attempt)
            logger.warning("HTTP %d from %s, retrying in %.1fs [%d/%d]", resp.status_code, host, delay, attempt, max_retries)
            resp.close()
            time.sleep(delay)
            continue

        return resp


# How long an 'in_progress' claim (PaperStore.claim()) is trusted before being treated as
# abandoned/stale and reclaimable -- long enough that no real single download should still
# legitimately be in_progress this long, short enough that a crashed process's claim doesn't
# block that key forever.
CLAIM_STALE_AFTER_SECONDS = 7200  # 2 hours


class PaperStore:
    """SQLite-backed manifest of retrieval state, so re-runs are resumable
    and we never redundantly re-download or re-query a paper we've already
    resolved."""

    def __init__(self, db_path: Path):
        self.conn = db.connect(db_path)  # bulk_retrieve_arxiv.py may connect concurrently (see db.py: WAL mode)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS papers (
                key TEXT PRIMARY KEY,
                title TEXT,
                authors TEXT,
                year INTEGER,
                doi TEXT,
                status TEXT,
                oa_status TEXT,
                pdf_url TEXT,
                file_path TEXT,
                error TEXT,
                updated_at TEXT
            )
            """
        )
        self.conn.commit()

    def get(self, key):
        cur = self.conn.execute("SELECT * FROM papers WHERE key = ?", (key,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def upsert(self, key, **fields):
        fields["key"] = key
        fields["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cols = list(fields.keys())
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "key")
        sql = f"INSERT INTO papers ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(key) DO UPDATE SET {updates}"
        self.conn.execute(sql, [fields[c] for c in cols])
        self.conn.commit()

    def claim(self, key):
        """Atomically claims `key` for THIS call to do real work on (a download) --
        the fix for the cross-process retrieval dedup race documented in todo.md's
        "Cross-process retrieval dedup race" entry (added 2026-08-30, after confirming
        by code inspection that plain get()-then-eventually-upsert() has a real
        check-then-act window no lock in this codebase closes across separate OS
        processes -- ThreadLocalPaperStore's write lock is threading.Lock(), per-process
        memory only). Returns True if this call won the claim, False if another
        process/call already has (or already finished) it.

        Meant to be called AFTER a caller's own read-only pre-checks (excluded_*
        status, already-downloaded-with-file-present) -- those stay exactly as they
        were, race-tolerant by nature (two processes both concluding "skip" is
        harmless), not because they don't need fixing but because a race there can't
        cause duplicate WORK, only a duplicate (harmless) skip decision. This is
        specifically the atomicity for "we are about to do real work" that no
        read-then-act check can provide on its own.

        Implemented as `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE` -- SQLite
        guarantees only one such statement can win against a given row at a time,
        project-wide under WAL mode, not just within one process. A stale
        'in_progress' claim (a process that crashed mid-download, never reaching its
        own final upsert()) is reclaimable after CLAIM_STALE_AFTER_SECONDS rather than
        blocking that key forever.

        Does NOT special-case status='downloaded': by the time a caller reaches this
        (having already run its own pre-checks), a 'downloaded' row it's still asking
        to claim only means "downloaded, but the file's missing on disk, this is a
        legitimate recovery" -- see every fetch_candidate()'s existing file-existence
        check. One acknowledged residual gap: if a DIFFERENT process fixes that same
        missing file between this caller's pre-check and this claim() call, this call
        still wins the claim and redundantly re-downloads an already-fine file --
        wasteful, not corrupting, and rare enough (requires a missing file AND a
        second concurrent process racing to fix the exact same one) not to be worth
        closing with a filesystem check inside a SQL WHERE clause."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stale_cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - CLAIM_STALE_AFTER_SECONDS))
        cur = self.conn.execute(
            """INSERT INTO papers (key, status, updated_at) VALUES (?, 'in_progress', ?)
               ON CONFLICT(key) DO UPDATE SET status='in_progress', updated_at=excluded.updated_at
               WHERE papers.status NOT IN ('excluded_crank', 'excluded_offtopic')
                 AND (papers.status != 'in_progress' OR papers.updated_at < ?)""",
            (key, now, stale_cutoff),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def status_counts(self):
        cur = self.conn.execute("SELECT status, COUNT(*) FROM papers GROUP BY status")
        return dict(cur.fetchall())


class ThreadLocalPaperStore:
    """Same get()/upsert() interface as PaperStore, but for use with
    concurrent_fetch(): a bare sqlite3 connection can only be used from the
    thread that created it (Python's sqlite3 module enforces this), so
    sharing one PaperStore across worker threads would crash on the first
    concurrent write. This opens one real PaperStore per thread instead
    (lazily, cached in thread-local storage) -- each connects independently
    to the same db_path.

    upsert() is additionally serialized behind a shared lock -- confirmed for
    real that WAL mode's own busy-timeout isn't enough on its own here: SQLite
    only allows one writer at a time regardless of connection count, and with
    max_workers threads all landing a write attempt in the same rough window,
    the queue behind that single writer slot got long enough to exceed
    db.py's 30s timeout and crash the whole run with "database is locked"
    (see concurrent_fetch()'s per-item exception handling, which stopped that
    from taking down the entire batch, but doesn't fix the underlying
    contention). Serializing in Python first is cheap (an uncontended lock,
    no SQLite-level retry/backoff) and turns "N threads racing for one
    writer slot" into a plain queue -- get() stays unserialized since WAL
    readers never block on a writer or on each other."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()
        self._write_lock = threading.Lock()

    def _store(self):
        store = getattr(self._local, "store", None)
        if store is None:
            store = PaperStore(self.db_path)
            self._local.store = store
        return store

    def get(self, key):
        return self._store().get(key)

    def upsert(self, key, **fields):
        with self._write_lock:
            return self._store().upsert(key, **fields)

    def claim(self, key):
        with self._write_lock:
            return self._store().claim(key)


def make_key(paper) -> str:
    doi = normalize_doi(paper.get("doi"))
    if doi:
        return "doi:" + doi.lower()
    basis = f"{paper.get('title', '')}|{paper.get('year', '')}|{','.join(paper.get('authors') or [])}"
    return "title:" + slugify(basis, 200)


def resolve_doi(session, paper, rate_limiter, args, logger):
    title = paper.get("title", "")
    authors = paper.get("authors") or []
    params = {"query.bibliographic": title, "rows": 5, "mailto": args.email}
    if authors:
        params["query.author"] = " ".join(authors)
    resp = http_get(session, CROSSREF_API, params, rate_limiter=rate_limiter, max_retries=args.max_retries,
                     timeout=args.timeout, logger=logger)
    if resp.status_code != 200:
        raise RetrievalError(f"crossref lookup failed: HTTP {resp.status_code}")
    items = resp.json().get("message", {}).get("items", [])
    best, best_score = None, 0.0
    for item in items:
        item_title = " ".join(item.get("title") or [])
        score = SequenceMatcher(None, title.lower(), item_title.lower()).ratio()
        if score > best_score:
            best, best_score = item, score
    if best and best_score >= args.title_match_threshold:
        return best.get("DOI"), best_score
    return None, best_score


def query_unpaywall(session, doi, rate_limiter, args, logger):
    url = UNPAYWALL_API.format(doi=doi)
    resp = http_get(session, url, {"email": args.email}, rate_limiter=rate_limiter, max_retries=args.max_retries,
                     timeout=args.timeout, logger=logger)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RetrievalError(f"unpaywall lookup failed: HTTP {resp.status_code}")
    return resp.json()


def query_openalex_batch(session, dois, rate_limiter, args, logger):
    """Looks up an OA location for up to OPENALEX_BATCH_SIZE (50) DOIs in a SINGLE
    request, via OpenAlex's `filter=doi:a|b|c` pipe-separated batching
    (https://blog.openalex.org/fetch-multiple-dois-in-one-openalex-api-request/).
    OpenAlex ingests Unpaywall's own data plus more, so this covers the same ground
    as query_unpaywall() -- the entire point is doing it 50-at-a-time instead of
    one HTTP round-trip per DOI, which is what made bulk retrieval's Unpaywall phase
    the bottleneck (Unpaywall has no batch endpoint, ~1 req/sec polite-pool pace,
    tens of thousands of DOIs -> many hours; see todo.md's "Full-corpus plagiarism
    audit" follow-up for the real numbers this was measured against).

    Returns {doi.lower(): {"oa_status": ..., "pdf_url": ...}} for every DOI OpenAlex
    found an OA location for (best_oa_location.pdf_url preferred, falling back to
    open_access.oa_url when a location object wasn't resolved but the work is still
    flagged OA) -- a DOI OpenAlex doesn't know, or knows but has no OA location for,
    is simply absent from the result, which is exactly the signal callers use to
    fall back to query_unpaywall() for that one DOI instead (different coverage,
    kept as the safety net, not replaced). A single failed/erroring batch request
    degrades to "nothing found in this batch" (logged, not raised) rather than
    aborting the run -- Unpaywall remains the fallback for every DOI in it either way.

    Uses max_retries=0 (not args.max_retries) for its own http_get() call --
    with thousands of independent batches and a solid fallback for each one,
    there's little value in retrying a single batch internally, and it's
    what makes a 429 raise RateLimited immediately (no sleep at all) instead
    of potentially blocking on whatever delay the server's Retry-After
    header names -- confirmed necessary for real, not hypothetical: this
    project's own bulk retrieval hit exactly that, OpenAlex asking for a
    ~20-hour wait after enough cumulative anonymous/keyless traffic (see
    todo.md's "Full-corpus plagiarism audit" follow-up). RateLimited
    propagates to the caller uncaught (not swallowed to {} like other
    failures) so a caller can stop calling OpenAlex entirely for the rest of
    the run instead of hitting the same wall on every remaining batch.

    Reads an API key from the OPENALEX_API_KEY environment variable if set
    (see that constant's own comment for why an env var, not a CLI arg) and
    sends it as the `api_key` query param OpenAlex's own docs specify --
    without one, requests are anonymous and subject to the (undocumented for
    anonymous callers, confirmed low enough in practice to matter) rate
    limit that caused the real incident this module's RateLimited handling
    exists for.
    """
    raw_results = _openalex_works_batch_request(session, dois, rate_limiter, args, logger,
                                                 context="OA location", fallback_note=", falling back to Unpaywall for all")
    if raw_results is None:
        return {}

    results = {}
    for work in raw_results:
        doi_norm = normalize_doi(work.get("doi"))
        if not doi_norm:
            continue
        best_loc = work.get("best_oa_location") or {}
        open_access = work.get("open_access") or {}
        pdf_url = best_loc.get("pdf_url") or open_access.get("oa_url")
        if not pdf_url:
            continue
        results[doi_norm.lower()] = {"oa_status": open_access.get("oa_status"), "pdf_url": pdf_url}
    return results


def _openalex_works_batch_request(session, dois, rate_limiter, args, logger, context, fallback_note=""):
    """Shared low-level batch fetch behind query_openalex_batch() (OA location) and
    query_openalex_authorships_batch() (disambiguated per-author OpenAlex IDs) -- same
    request, same rate-limit/retry/API-key handling, different field(s) pulled out of the
    same `results` array by each caller. Returns the raw `results` list from OpenAlex's
    response, or None if the batch failed/degraded (a caller logs that in its own terms
    via `context`/`fallback_note` since "fall back to Unpaywall" only makes sense for one
    of the two callers). RateLimited still propagates uncaught -- see query_openalex_batch()'s
    own docstring for why."""
    if not dois:
        return []
    params = {"filter": "doi:" + "|".join(dois), "per-page": len(dois), "mailto": args.email}
    api_key = os.environ.get(OPENALEX_API_KEY_ENV_VAR)
    if api_key:
        params["api_key"] = api_key
    try:
        resp = http_get(session, OPENALEX_API, params, rate_limiter=rate_limiter,
                         max_retries=0, timeout=args.timeout, logger=logger)
    except RateLimited:
        raise
    except RetrievalError as exc:
        logger.warning("openalex batch lookup (%s) failed (%d DOIs)%s: %s", context, len(dois), fallback_note, exc)
        return None
    if resp.status_code != 200:
        logger.warning("openalex batch lookup (%s) HTTP %d (%d DOIs)%s",
                        context, resp.status_code, len(dois), fallback_note)
        return None
    return resp.json().get("results", [])


def query_openalex_authorships_batch(session, dois, rate_limiter, args, logger):
    """Looks up each work's disambiguated OpenAlex author IDs for up to
    OPENALEX_BATCH_SIZE (50) DOIs in one request -- same `filter=doi:a|b|c` batching as
    query_openalex_batch(), different field pulled from the same response
    (`authorships[].author.id`/`.display_name` instead of `best_oa_location`).

    Exists for bulk_retrieve_author_works.py's author-publication-graph expansion: our own
    `authors` table is name-only (no external ID), and searching OpenAlex's author-search
    endpoint by name alone is genuinely unreliable -- common names collide across different
    real researchers, and guessing wrong would pull in a stranger's entire unrelated
    bibliography (the same off-topic-pollution failure mode filter_low_relevance_papers.py
    was built to catch, see todo.md). But every paper we already have a DOI for, we can
    already ask OpenAlex "who wrote this" -- and that authorship is OpenAlex's own
    disambiguation, not ours, so it's free precision we're not paying any extra API cost for
    (this reuses the exact same batched work-lookup request shape as the OA-location lookup
    bulk retrieval already makes constantly).

    Returns {doi.lower(): [{"openalex_id": ..., "display_name": ...}, ...]} for every DOI
    OpenAlex has authorship data for; a DOI it doesn't know, or a work with no authorships
    listed, is simply absent -- there is no fallback source for this (unlike the OA-location
    lookup, nothing else in this codebase resolves author identity), so an absent DOI just
    means that paper contributes no author-ID evidence this round.
    """
    raw_results = _openalex_works_batch_request(session, dois, rate_limiter, args, logger, context="authorships")
    if raw_results is None:
        return {}

    results = {}
    for work in raw_results:
        doi_norm = normalize_doi(work.get("doi"))
        if not doi_norm:
            continue
        authorships = []
        for authorship in work.get("authorships", []) or []:
            author = authorship.get("author") or {}
            openalex_id, display_name = author.get("id"), author.get("display_name")
            if openalex_id and display_name:
                authorships.append({"openalex_id": openalex_id, "display_name": display_name})
        if authorships:
            results[doi_norm.lower()] = authorships
    return results


OPENALEX_WORKS_PER_PAGE = 200  # OpenAlex's own documented per-page cap for a cursor-paginated works listing


def search_openalex_works(session, rate_limiter, filter_str, max_results, args, logger):
    """Cursor-paginates `works?filter=<filter_str>`, yielding raw OpenAlex work
    objects. Generic over whatever filter_str a caller builds -- extracted
    2026-08-27 from bulk_retrieve_author_works.py's own (then author.id-specific)
    version once a second real caller (bulk_retrieve_openalex_concept.py,
    filter_str=f"concepts.id:{concept_id}") needed the identical pagination
    logic; parse_openalex_work() does the field extraction, kept separate so a
    test can feed this raw fixture JSON without needing a real title/doi.

    Uses args.max_retries (not 0) -- unlike query_openalex_batch()'s
    fire-and-fall-back-to-Unpaywall design, there's no fallback source for
    "what other works exist under this filter", so a transient failure is
    worth a normal retry rather than an immediate give-up; a caller still
    wraps this in try/except RetrievalError/RateLimited to degrade gracefully
    across the whole run on a PERSISTENT failure, same discipline as
    bulk_retrieve_core.py's harvest loop."""
    cursor = "*"
    seen = 0
    api_key = os.environ.get(OPENALEX_API_KEY_ENV_VAR)
    while cursor and seen < max_results:
        params = {"filter": filter_str, "per-page": min(OPENALEX_WORKS_PER_PAGE, max_results - seen),
                   "cursor": cursor, "mailto": args.email}
        if api_key:
            params["api_key"] = api_key
        resp = http_get(session, OPENALEX_API, params, rate_limiter=rate_limiter,
                         max_retries=args.max_retries, timeout=args.timeout, logger=logger)
        if resp.status_code != 200:
            logger.warning("openalex works search HTTP %d for filter=%r, stopping", resp.status_code, filter_str)
            return
        body = resp.json()
        results = body.get("results", [])
        if not results:
            return
        for work in results:
            yield work
            seen += 1
            if seen >= max_results:
                return
        cursor = (body.get("meta") or {}).get("next_cursor")


def parse_openalex_work(work):
    """One OpenAlex work object -> a candidate paper dict, or None for a work
    with no usable title or DOI. Generic over any works?filter=... source
    (author.id, concepts.id, ...) -- carries oa_status/pdf_url straight out
    of the SAME response object (best_oa_location/open_access, same fields
    query_openalex_batch() parses), no extra request needed, unlike a
    keyword-search harvest that only gets bibliographic fields and needs a
    separate OA lookup pass. A work with no DOI is skipped -- bulk_retrieve_core.py
    is this project's dedicated no-DOI channel; keeping DOI-keyed resumability
    simple here, like bulk_retrieve_crossref.py/bulk_retrieve_theses.py."""
    doi = normalize_doi(work.get("doi"))
    title = (work.get("title") or work.get("display_name") or "").strip()
    if not title or not doi:
        return None
    authors = [a.get("author", {}).get("display_name")
               for a in (work.get("authorships") or [])
               if (a.get("author") or {}).get("display_name")]
    best_loc = work.get("best_oa_location") or {}
    open_access = work.get("open_access") or {}
    pdf_url = best_loc.get("pdf_url") or open_access.get("oa_url")
    return {
        "title": title, "authors": authors, "year": work.get("publication_year"), "doi": doi,
        "oa_status": open_access.get("oa_status"), "pdf_url": pdf_url,
    }


def chunked(items, size):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


_META_TAG_RE = re.compile(rb"<meta\s+[^>]*>", re.IGNORECASE)
_META_NAME_RE = re.compile(rb"""name=["']([^"']+)["']""", re.IGNORECASE)
_META_CONTENT_RE = re.compile(rb"""content=["']([^"']+)["']""", re.IGNORECASE)


def find_citation_pdf_url(html_bytes: bytes):
    """Look for <meta name="citation_pdf_url" content="..."> -- a long-established,
    widely-supported convention (originated with Highwire Press, now emitted by Google
    Scholar-indexed sites generally, and specifically by most DSpace/EPrints/Bepress Digital
    Commons institutional repositories -- the exact platforms behind most university thesis
    repositories) that exists specifically to tell an automated crawler where the real PDF is
    when the page itself is just a landing/abstract page. Attribute order isn't fixed
    (content= can come before or after name=), so each <meta> tag is checked independently
    rather than assuming a fixed order."""
    for tag in _META_TAG_RE.findall(html_bytes):
        name_m = _META_NAME_RE.search(tag)
        if not name_m or name_m.group(1).lower() != b"citation_pdf_url":
            continue
        content_m = _META_CONTENT_RE.search(tag)
        if content_m:
            # html.unescape() is a no-op on the common already-plain-URL case, but at least one
            # real repository (jyx.jyu.fi, University of Jyväskylä) emits the whole URL as numeric
            # character references (e.g. "https&#x3A;&#x2F;&#x2F;jyx.jyu.fi&#x2F;...") -- confirmed
            # live, every such URL failed requests' own "Invalid URL: No scheme supplied" and
            # retried 4 times for nothing before giving up.
            return html.unescape(content_m.group(1).decode("utf-8", errors="replace"))
    return None


def resolve_landing_page_pdf_url(session, url, rate_limiter, args, logger):
    """If `url` turns out to be an HTML landing page rather than a direct PDF, look for a
    citation_pdf_url meta tag pointing at the actual PDF. Returns the resolved URL, or None if
    the page isn't HTML or doesn't have the tag. Reads only the first chunk (meta tags are
    always in <head>, near the top of the document) rather than the whole page."""
    resp = http_get(session, url, None, rate_limiter=rate_limiter, max_retries=args.max_retries,
                     timeout=args.timeout, logger=logger, stream=True)
    try:
        if resp.status_code != 200:
            return None
        if "html" not in resp.headers.get("Content-Type", "").lower():
            return None
        chunk = next(resp.iter_content(chunk_size=65536), b"")
    finally:
        resp.close()
    return find_citation_pdf_url(chunk)


def download_with_landing_page_fallback(session, url, dest_path, rate_limiter, args, logger):
    """download_pdf(), with one fallback: if `url` is an HTML landing/abstract page rather than
    a direct PDF, try resolve_landing_page_pdf_url() and retry against whatever it finds.

    Institutional repositories -- the DSpace/EPrints/Bepress-style platforms behind most
    university thesis repositories -- very often expose only a landing-page URL in OpenAlex's or
    Unpaywall's own metadata (no direct PDF link on file), with the actual PDF one click away on
    that same page. Confirmed as the single biggest lever for thesis retrieval's much
    worse-than-average success rate on this project's corpus (20% downloaded vs. ~47%
    corpus-wide, and roughly half of the shortfall landing in doi_unknown_to_unpaywall -- see
    CLAUDE.md/todo.md): this doesn't require re-fetching anything from OpenAlex, since it also
    applies to Unpaywall's own best_oa_location.url when url_for_pdf isn't set.

    Returns (ok, url_that_actually_worked) -- the second value is only different from the input
    `url` when the landing-page fallback is what succeeded, so callers can record the real
    working URL instead of the landing page.
    """
    if download_pdf(session, url, dest_path, rate_limiter, args, logger):
        return True, url
    resolved = resolve_landing_page_pdf_url(session, url, rate_limiter, args, logger)
    if not resolved or resolved == url:
        return False, url
    logger.info("found citation_pdf_url on landing page: %s -> %s", url, resolved)
    if download_pdf(session, resolved, dest_path, rate_limiter, args, logger):
        return True, resolved
    return False, url


def download_pdf(session, url, dest_path, rate_limiter, args, logger):
    resp = http_get(session, url, None, rate_limiter=rate_limiter, max_retries=args.max_retries,
                     timeout=args.timeout, logger=logger, stream=True)
    if resp.status_code != 200:
        resp.close()
        raise RetrievalError(f"download failed: HTTP {resp.status_code}")

    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    is_pdf = None
    try:
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                if is_pdf is None:
                    # Magic bytes only -- Content-Type is server-controlled and not trustworthy on
                    # its own. Used to accept "or 'pdf' in Content-Type", which let real HTML/plain-
                    # text bodies through whenever a server sent a stale/wrong Content-Type header
                    # (confirmed for real: a misconfigured journal site served a raw PHP debug dump
                    # with Content-Type: application/pdf, which got saved and even extracted into
                    # the corpus as paper id 9623 before this was caught). lstrip() to tolerate a
                    # leading blank line/BOM some servers prepend before the real %PDF- header.
                    is_pdf = chunk.lstrip()[:4] == b"%PDF"
                    if not is_pdf:
                        break
                f.write(chunk)
    finally:
        resp.close()

    if not is_pdf:
        tmp_path.unlink(missing_ok=True)
        return False
    tmp_path.rename(dest_path)
    return True


def process_paper(session, paper, store, rate_limiter, args, logger):
    key = make_key(paper)
    record = store.get(key)
    title = paper.get("title", "untitled")
    # paper.get("authors") is None whenever a starting.json entry has no "authors" field at all (e.g.
    # candidates built from a source with no author data, like OpenAlex) -- store [] rather than the
    # literal string "null" so a plain `if paper["authors"]` check downstream (extract_papers.py) can't
    # be fooled by a truthy-but-meaningless string.
    authors_json = json.dumps(paper.get("authors") or [])
    year = paper.get("year")

    if record and record["status"] == "downloaded" and record["file_path"] and Path(record["file_path"]).exists():
        logger.info("already have it, skipping: %s", title)
        return "skipped"

    # NOTE: process_paper() still lacks the store.claim() atomic-claim fix that
    # core.py/theses.py/crossref.py's fetch_candidate() now all have (see
    # bulk_retrieve_theses.py's fetch_candidate() docstring for the full incident
    # writeup) -- unlike those, this function's skip-without-recheck branches
    # (doi_not_found below, no_oa further down, plus a real-download known_urls
    # block in between) are scattered through the function rather than
    # consolidated in one pre-check block, so a single claim() insertion point
    # isn't safe to add without restructuring and re-verifying every branch.
    # Attempted 2026-09-04, reverted after it broke test_no_oa_previously_known_
    # is_skipped_without_recheck by claiming (and thus proceeding past a
    # legitimate skip) before that later check ever ran. Real risk is lower here
    # than the bulk scripts anyway -- this is the single-paper/starting.json
    # flow, not normally run concurrently with another process against the same
    # state.sqlite3 -- but it's still a real gap if that ever happens. Flagged
    # in todo.md rather than rushed.
    doi = normalize_doi(paper.get("doi")) or (record or {}).get("doi")

    if not doi:
        if record and record.get("status") == "doi_not_found" and not args.recheck:
            logger.info("skipping (no DOI match last time): %s", title)
            return "doi_not_found"
        try:
            doi, score = resolve_doi(session, paper, rate_limiter, args, logger)
        except RetrievalError as exc:
            logger.error("crossref error for %r: %s", title, exc)
            store.upsert(key, title=title, authors=authors_json, year=year, status="error", error=str(exc))
            return "error"
        if not doi:
            logger.warning("no confident DOI match for %r (best score %.2f)", title, score)
            store.upsert(key, title=title, authors=authors_json, year=year, status="doi_not_found")
            return "doi_not_found"
        logger.info("resolved DOI %s for %r (score %.2f)", doi, title, score)

    filename = f"{slugify(title)}-{slugify(doi)}.pdf"
    dest_path = args.outdir / filename

    # Try every pre-known OA location first -- sourcing/build_oa_starting_list.py threads
    # through OpenAlex's "best" open_access.oa_url plus enrich_open_access.py's oa_alt_urls
    # (other locations() entries OpenAlex already had on file but didn't consider "best";
    # see that script's docstring for why those are worth trying too, with a concrete example
    # of the "best" one being dead while an alternate one worked). DOWNLOAD-only, no Unpaywall
    # call spent on any of these. query_unpaywall() always hits the same host
    # (api.unpaywall.org), which RateLimiter throttles to one request per --min-interval
    # regardless of --max-workers -- with pre-known locations for most of a large batch, that
    # shared-host ceiling stops being the bottleneck for the bulk of the run (confirmed on this
    # project's own 120k+-paper OA-candidate batch: see CLAUDE.md). Not a blind substitute for
    # Unpaywall, though -- if every known location is stale/dead/blocked, this falls through to
    # the normal Unpaywall-backed flow below exactly as if none had been given at all, so this
    # can only make a paper easier to get, never harder.
    known_urls = [u for u in [paper.get("oa_url"), *(paper.get("oa_alt_urls") or [])] if u]
    if known_urls:
        if dest_path.exists() and not args.overwrite:
            logger.info("file already on disk, recording it: %s", dest_path.name)
            store.upsert(key, title=title, authors=authors_json, year=year, doi=doi, status="downloaded",
                         oa_status=paper.get("oa_status"), pdf_url=known_urls[0], file_path=str(dest_path))
            return "downloaded"
        for url in known_urls:
            try:
                ok, actual_url = download_with_landing_page_fallback(session, url, dest_path, rate_limiter, args, logger)
            except RetrievalError as exc:
                logger.info("known OA location failed for %r (%s), trying next", title, exc)
                continue
            if ok:
                logger.info("downloaded %r via known OA location -> %s", title, dest_path.name)
                store.upsert(key, title=title, authors=authors_json, year=year, doi=doi, status="downloaded",
                             oa_status=paper.get("oa_status"), pdf_url=actual_url, file_path=str(dest_path))
                return "downloaded"
            logger.info("known OA location for %r did not serve a PDF, trying next", title)
        logger.info("no known OA location worked for %r, falling back to Unpaywall", title)

    if record and record.get("doi") == doi and record.get("status") == "no_oa" and not args.recheck:
        logger.info("skipping (no OA copy known): %s", title)
        return "no_oa"

    try:
        data = query_unpaywall(session, doi, rate_limiter, args, logger)
    except RetrievalError as exc:
        logger.error("unpaywall error for %r (%s): %s", title, doi, exc)
        store.upsert(key, title=title, authors=authors_json, year=year, doi=doi, status="error", error=str(exc))
        return "error"

    if data is None:
        logger.warning("DOI unknown to unpaywall: %r (%s)", title, doi)
        store.upsert(key, title=title, authors=authors_json, year=year, doi=doi, status="doi_unknown_to_unpaywall")
        return "doi_unknown_to_unpaywall"

    oa_status = data.get("oa_status")
    best_loc = data.get("best_oa_location") or {}
    pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url")

    if not pdf_url:
        logger.info("no OA location for %r (oa_status=%s)", title, oa_status)
        store.upsert(key, title=title, authors=authors_json, year=year, doi=doi, status="no_oa", oa_status=oa_status)
        return "no_oa"

    if dest_path.exists() and not args.overwrite:
        logger.info("file already on disk, recording it: %s", dest_path.name)
        store.upsert(key, title=title, authors=authors_json, year=year, doi=doi, status="downloaded",
                     oa_status=oa_status, pdf_url=pdf_url, file_path=str(dest_path))
        return "downloaded"

    try:
        ok, actual_url = download_with_landing_page_fallback(session, pdf_url, dest_path, rate_limiter, args, logger)
    except RetrievalError as exc:
        logger.error("download error for %r: %s", title, exc)
        store.upsert(key, title=title, authors=authors_json, year=year, doi=doi, status="error",
                     oa_status=oa_status, pdf_url=pdf_url, error=str(exc))
        return "error"

    if ok:
        logger.info("downloaded %r -> %s", title, dest_path.name)
        store.upsert(key, title=title, authors=authors_json, year=year, doi=doi, status="downloaded",
                     oa_status=oa_status, pdf_url=actual_url, file_path=str(dest_path))
        return "downloaded"

    logger.warning("OA url for %r did not serve a PDF: %s", title, pdf_url)
    store.upsert(key, title=title, authors=authors_json, year=year, doi=doi, status="oa_url_not_pdf",
                 oa_status=oa_status, pdf_url=pdf_url)
    return "oa_url_not_pdf"


def load_papers(path):
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = [data]
    return data


def setup_logger(log_file: Path):
    logger = logging.getLogger("retrieve_papers")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch open-access PDFs for papers via Crossref + Unpaywall.")
    parser.add_argument("input", nargs="?", default="starting.json", help="JSON file: a paper object or a list of them")
    parser.add_argument("--email", default=None, help="Contact email sent to Crossref/Unpaywall (required by their terms of use)")
    parser.add_argument("--outdir", type=Path, default=Path("papers"), help="Directory to save PDFs into")
    parser.add_argument("--db", type=Path, default=Path("state.sqlite3"), help="SQLite manifest tracking retrieval state")
    parser.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL, help="Minimum seconds between requests to the same host")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Max retries on 429/5xx/network errors")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Per-request timeout in seconds")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS,
                         help="Papers processed concurrently (see concurrent_fetch()) -- different hosts run in "
                              "parallel; a single host is still throttled by --min-interval regardless of this")
    parser.add_argument("--title-match-threshold", type=float, default=0.82, help="Minimum title-similarity to accept a Crossref DOI match")
    parser.add_argument("--recheck", action="store_true", help="Re-query Unpaywall/Crossref even for papers previously marked no-OA / no-DOI-match")
    parser.add_argument("--overwrite", action="store_true", help="Re-download even if the destination file already exists")
    parser.add_argument("--log-file", type=Path, default=Path("retrieve_papers.log"))
    args = parser.parse_args()
    if not args.email:
        parser.error("--email is required (Crossref/Unpaywall require a contact email for their polite/API pools)")
    # Unpaywall answers every request from a placeholder address with HTTP 422, which would
    # otherwise surface only as a per-paper "error" status and an empty papers/ directory.
    email_domain = args.email.rpartition("@")[2].lower()
    if "@" not in args.email or email_domain.split(".")[0] in ("example", "your-institution"):
        parser.error(f"--email {args.email!r} looks like a placeholder -- use your own address "
                     "(Unpaywall rejects example.com-style addresses with HTTP 422)")
    return args


def main():
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(args.log_file)

    papers = load_papers(args.input)
    logger.info("loaded %d paper(s) from %s", len(papers), args.input)

    # ThreadLocalPaperStore, not PaperStore directly: a bare sqlite3 connection can only be
    # used from the thread that created it, and concurrent_fetch() below runs across
    # max_workers threads -- see ThreadLocalPaperStore's own docstring.
    store = ThreadLocalPaperStore(args.db)
    rate_limiter = RateLimiter(args.min_interval)
    key_lock = KeyedLock()  # see KeyedLock's docstring -- guards against two threads
                             # processing the same logical paper (same make_key()) at once

    # One requests.Session per worker thread (thread-local, lazily created) rather than one
    # shared Session -- avoids relying on urllib3's connection-pool thread-safety being
    # airtight under max_workers concurrent callers, at the cost of a handful of extra
    # sessions instead of one.
    thread_local = threading.local()

    def get_session():
        session = getattr(thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT_TEMPLATE.format(email=args.email)})
            thread_local.session = session
        return session

    def fetch_one(paper):
        key = make_key(paper)
        key_lock.acquire(key)
        try:
            return process_paper(get_session(), paper, store, rate_limiter, args, logger)
        finally:
            key_lock.release(key)

    total = len(papers)
    done = 0
    for paper, result in concurrent_fetch(papers, fetch_one, max_workers=args.max_workers, logger=logger):
        done += 1
        logger.info("[%d/%d] %s -> %s", done, total, paper.get("title", "untitled"), result)

    counts = PaperStore(args.db).status_counts()
    logger.info("done. status counts: %s", counts)


if __name__ == "__main__":
    main()

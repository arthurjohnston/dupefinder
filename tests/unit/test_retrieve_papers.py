#!/usr/bin/env python3
"""Unit tests for retrieve_papers.py -- pure functions, the retry/backoff
logic in http_get(), the thread pool in concurrent_fetch(), the SQLite
manifest stores, and process_paper()'s branching, all with the network
mocked out. Distinct from tests/run_tests.py, which back-tests the real
pipeline end-to-end against known-outcome cases over the real network."""

import os
import sys
import tempfile
import threading
import time
import types
import unittest
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

import retrieve_papers as rp  # noqa: E402


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(rp.slugify("Hello, World!"), "hello-world")

    def test_strips_accents(self):
        self.assertEqual(rp.slugify("Café Naïve"), "cafe-naive")

    def test_collapses_whitespace_and_punctuation_runs(self):
        self.assertEqual(rp.slugify("A  --  B"), "a-b")

    def test_empty_or_none_falls_back_to_untitled(self):
        self.assertEqual(rp.slugify(""), "untitled")
        self.assertEqual(rp.slugify(None), "untitled")

    def test_truncates_to_max_len(self):
        result = rp.slugify("word " * 50, max_len=10)
        self.assertLessEqual(len(result), 10)


class TestNormalizeDoi(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(rp.normalize_doi(None))

    def test_strips_doi_org_prefix(self):
        self.assertEqual(rp.normalize_doi("https://doi.org/10.1234/abc"), "10.1234/abc")

    def test_strips_dx_doi_org_prefix_case_insensitive(self):
        self.assertEqual(rp.normalize_doi("HTTP://DX.DOI.ORG/10.1234/abc"), "10.1234/abc")

    def test_bare_doi_passthrough(self):
        self.assertEqual(rp.normalize_doi("10.1234/abc"), "10.1234/abc")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(rp.normalize_doi("  10.1234/abc  "), "10.1234/abc")


class TestMakeKey(unittest.TestCase):
    def test_doi_takes_priority(self):
        paper = {"doi": "10.1234/ABC", "title": "Some Title", "year": 2020, "authors": ["A"]}
        self.assertEqual(rp.make_key(paper), "doi:10.1234/abc")

    def test_no_doi_falls_back_to_title_slug(self):
        paper = {"title": "Some Title", "year": 2020, "authors": ["Alice"]}
        key = rp.make_key(paper)
        self.assertTrue(key.startswith("title:"))
        self.assertIn("some-title", key)

    def test_missing_authors_field_does_not_crash(self):
        paper = {"title": "No Author Paper", "year": 2021}
        key = rp.make_key(paper)  # authors=None from paper.get("authors") -- must not raise
        self.assertTrue(key.startswith("title:"))

    def test_key_is_stable_for_same_input(self):
        paper = {"title": "Repeatable", "year": 2019, "authors": ["Bob"]}
        self.assertEqual(rp.make_key(paper), rp.make_key(dict(paper)))


class TestRateLimiter(unittest.TestCase):
    def test_enforces_minimum_interval_on_same_host(self):
        limiter = rp.RateLimiter(min_interval=0.1)
        limiter.wait("example.com")
        start = time.monotonic()
        limiter.wait("example.com")
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.09)  # small slack for scheduling jitter

    def test_different_hosts_dont_block_each_other(self):
        limiter = rp.RateLimiter(min_interval=1.0)
        limiter.wait("a.example.com")
        start = time.monotonic()
        limiter.wait("b.example.com")  # different host -- shouldn't wait ~1s
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.5)


class TestBackoffDelay(unittest.TestCase):
    def test_grows_with_attempt_before_cap(self):
        d1 = rp._backoff_delay(1)
        d2 = rp._backoff_delay(2)
        self.assertGreaterEqual(d1, 2.0)
        self.assertLess(d1, 3.0)
        self.assertGreaterEqual(d2, 4.0)
        self.assertLess(d2, 5.0)

    def test_capped_at_60_plus_jitter(self):
        d = rp._backoff_delay(10)  # 2**10 = 1024, way past the min(60, ...) cap
        self.assertGreaterEqual(d, 60.0)
        self.assertLess(d, 61.0)


class TestRetryAfterDelay(unittest.TestCase):
    def test_numeric_header(self):
        resp = Mock(headers={"Retry-After": "5"})
        self.assertEqual(rp._retry_after_delay(resp, attempt=1), 5.0)

    def test_http_date_header(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=30)
        resp = Mock(headers={"Retry-After": format_datetime(future)})
        delay = rp._retry_after_delay(resp, attempt=1)
        self.assertGreater(delay, 25)
        self.assertLessEqual(delay, 30)

    def test_missing_header_falls_back_to_backoff(self):
        resp = Mock(headers={})
        with patch("retrieve_papers._backoff_delay", return_value=42.0) as mock_backoff:
            delay = rp._retry_after_delay(resp, attempt=3)
        mock_backoff.assert_called_once_with(3)
        self.assertEqual(delay, 42.0)

    def test_garbage_header_falls_back_to_backoff(self):
        resp = Mock(headers={"Retry-After": "not-a-number-or-date"})
        with patch("retrieve_papers._backoff_delay", return_value=7.0):
            delay = rp._retry_after_delay(resp, attempt=1)
        self.assertEqual(delay, 7.0)

    def test_negative_numeric_header_clamped_to_zero(self):
        resp = Mock(headers={"Retry-After": "-5"})
        self.assertEqual(rp._retry_after_delay(resp, attempt=1), 0.0)


def _fake_response(status_code):
    resp = Mock()
    resp.status_code = status_code
    resp.headers = {}
    resp.close = Mock()
    return resp


def _html_response(status_code, html_bytes, content_type="text/html; charset=utf-8"):
    resp = _fake_response(status_code)
    resp.headers = {"Content-Type": content_type}
    resp.iter_content = Mock(return_value=iter([html_bytes]))
    return resp


class TestHttpGet(unittest.TestCase):
    """http_get()'s retry logic, with time.sleep patched out so retry/backoff
    tests run instantly instead of burning real wall-clock seconds."""

    def setUp(self):
        self.sleep_patcher = patch("retrieve_papers.time.sleep")
        self.mock_sleep = self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)
        self.rate_limiter = rp.RateLimiter(min_interval=0)
        self.logger = Mock()

    def test_success_on_first_try(self):
        session = Mock()
        session.get.return_value = _fake_response(200)
        resp = rp.http_get(session, "http://example.com/x", {}, rate_limiter=self.rate_limiter,
                            max_retries=3, timeout=5, logger=self.logger)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(session.get.call_count, 1)

    def test_client_error_not_retried(self):
        # 403/404/etc. aren't 429 or 5xx -- http_get returns them as-is for the caller to handle.
        session = Mock()
        session.get.return_value = _fake_response(403)
        resp = rp.http_get(session, "http://example.com/x", {}, rate_limiter=self.rate_limiter,
                            max_retries=3, timeout=5, logger=self.logger)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(session.get.call_count, 1)

    def test_network_error_then_success(self):
        session = Mock()
        session.get.side_effect = [requests.ConnectionError("boom"), _fake_response(200)]
        resp = rp.http_get(session, "http://example.com/x", {}, rate_limiter=self.rate_limiter,
                            max_retries=3, timeout=5, logger=self.logger)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(session.get.call_count, 2)
        self.mock_sleep.assert_called()

    def test_network_error_exhausts_retries_and_raises(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("boom")
        with self.assertRaises(rp.RetrievalError):
            rp.http_get(session, "http://example.com/x", {}, rate_limiter=self.rate_limiter,
                        max_retries=2, timeout=5, logger=self.logger)
        self.assertEqual(session.get.call_count, 3)  # initial attempt + 2 retries

    def test_429_retried_with_retry_after_then_succeeds(self):
        session = Mock()
        session.get.side_effect = [_fake_response(429), _fake_response(200)]
        resp = rp.http_get(session, "http://example.com/x", {}, rate_limiter=self.rate_limiter,
                            max_retries=3, timeout=5, logger=self.logger)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(session.get.call_count, 2)

    def test_500_exhausts_retries_and_raises(self):
        session = Mock()
        session.get.return_value = _fake_response(503)
        with self.assertRaises(rp.RetrievalError):
            rp.http_get(session, "http://example.com/x", {}, rate_limiter=self.rate_limiter,
                        max_retries=1, timeout=5, logger=self.logger)
        self.assertEqual(session.get.call_count, 2)  # initial attempt + 1 retry


class TestFindCitationPdfUrl(unittest.TestCase):
    def test_finds_tag_with_name_before_content(self):
        html = b'<html><head><meta name="citation_pdf_url" content="http://example.com/real.pdf"></head></html>'
        self.assertEqual(rp.find_citation_pdf_url(html), "http://example.com/real.pdf")

    def test_finds_tag_with_content_before_name(self):
        # Attribute order isn't guaranteed -- some repository platforms emit it this way.
        html = b'<meta content="http://example.com/real.pdf" name="citation_pdf_url">'
        self.assertEqual(rp.find_citation_pdf_url(html), "http://example.com/real.pdf")

    def test_case_insensitive_tag_name_match(self):
        html = b'<META NAME="citation_pdf_url" CONTENT="http://example.com/real.pdf">'
        self.assertEqual(rp.find_citation_pdf_url(html), "http://example.com/real.pdf")

    def test_ignores_other_meta_tags(self):
        html = (b'<meta name="citation_title" content="Some Paper">'
                b'<meta name="citation_author" content="A. Author">'
                b'<meta name="citation_pdf_url" content="http://example.com/real.pdf">')
        self.assertEqual(rp.find_citation_pdf_url(html), "http://example.com/real.pdf")

    def test_no_tag_returns_none(self):
        html = b'<html><head><title>Landing page</title></head><body>Abstract only.</body></html>'
        self.assertIsNone(rp.find_citation_pdf_url(html))

    def test_single_quotes(self):
        html = b"<meta name='citation_pdf_url' content='http://example.com/real.pdf'>"
        self.assertEqual(rp.find_citation_pdf_url(html), "http://example.com/real.pdf")

    def test_html_entity_encoded_url_is_unescaped(self):
        """Confirmed live against jyx.jyu.fi (University of Jyväskylä): the whole URL emitted
        as numeric character references instead of a plain URL -- every one of these previously
        failed requests' own "Invalid URL: No scheme supplied" and retried 4 times for nothing."""
        html = (b'<meta name="citation_pdf_url" '
                b'content="https&#x3A;&#x2F;&#x2F;jyx.jyu.fi&#x2F;bitstreams&#x2F;abc&#x2F;download">')
        self.assertEqual(rp.find_citation_pdf_url(html), "https://jyx.jyu.fi/bitstreams/abc/download")


class TestResolveLandingPagePdfUrl(unittest.TestCase):
    def setUp(self):
        self.rate_limiter = rp.RateLimiter(min_interval=0)
        self.logger = Mock()

    def test_finds_citation_pdf_url_on_html_page(self):
        session = Mock()
        html = b'<meta name="citation_pdf_url" content="http://example.com/real.pdf">'
        session.get.return_value = _html_response(200, html)
        result = rp.resolve_landing_page_pdf_url(session, "http://example.com/landing", rate_limiter=self.rate_limiter,
                                                   args=_args(), logger=self.logger)
        self.assertEqual(result, "http://example.com/real.pdf")

    def test_non_html_content_type_returns_none(self):
        session = Mock()
        resp = _fake_response(200)
        resp.headers = {"Content-Type": "application/pdf"}
        resp.iter_content = Mock(return_value=iter([b"%PDF-1.4 ..."]))
        session.get.return_value = resp
        result = rp.resolve_landing_page_pdf_url(session, "http://example.com/x", rate_limiter=self.rate_limiter,
                                                   args=_args(), logger=self.logger)
        self.assertIsNone(result)

    def test_non_200_returns_none(self):
        session = Mock()
        session.get.return_value = _fake_response(404)
        result = rp.resolve_landing_page_pdf_url(session, "http://example.com/x", rate_limiter=self.rate_limiter,
                                                   args=_args(), logger=self.logger)
        self.assertIsNone(result)

    def test_html_without_tag_returns_none(self):
        session = Mock()
        session.get.return_value = _html_response(200, b"<html><body>No PDF link here.</body></html>")
        result = rp.resolve_landing_page_pdf_url(session, "http://example.com/x", rate_limiter=self.rate_limiter,
                                                   args=_args(), logger=self.logger)
        self.assertIsNone(result)


class TestDownloadWithLandingPageFallback(unittest.TestCase):
    def setUp(self):
        self.rate_limiter = rp.RateLimiter(min_interval=0)
        self.logger = Mock()

    def test_direct_pdf_success_skips_landing_page_check(self):
        with patch("retrieve_papers.download_pdf", return_value=True) as mock_download, \
             patch("retrieve_papers.resolve_landing_page_pdf_url") as mock_resolve:
            ok, url = rp.download_with_landing_page_fallback(Mock(), "http://example.com/x.pdf", Path("/tmp/x.pdf"),
                                                               self.rate_limiter, _args(), self.logger)
        self.assertTrue(ok)
        self.assertEqual(url, "http://example.com/x.pdf")
        mock_resolve.assert_not_called()

    def test_landing_page_resolved_and_retried(self):
        with patch("retrieve_papers.download_pdf", side_effect=[False, True]) as mock_download, \
             patch("retrieve_papers.resolve_landing_page_pdf_url", return_value="http://example.com/real.pdf"):
            ok, url = rp.download_with_landing_page_fallback(Mock(), "http://example.com/landing", Path("/tmp/x.pdf"),
                                                               self.rate_limiter, _args(), self.logger)
        self.assertTrue(ok)
        self.assertEqual(url, "http://example.com/real.pdf")
        self.assertEqual(mock_download.call_count, 2)

    def test_no_citation_pdf_url_found_reports_original_url_failure(self):
        with patch("retrieve_papers.download_pdf", return_value=False), \
             patch("retrieve_papers.resolve_landing_page_pdf_url", return_value=None):
            ok, url = rp.download_with_landing_page_fallback(Mock(), "http://example.com/landing", Path("/tmp/x.pdf"),
                                                               self.rate_limiter, _args(), self.logger)
        self.assertFalse(ok)
        self.assertEqual(url, "http://example.com/landing")

    def test_resolved_url_also_fails(self):
        with patch("retrieve_papers.download_pdf", side_effect=[False, False]), \
             patch("retrieve_papers.resolve_landing_page_pdf_url", return_value="http://example.com/also-bad.pdf"):
            ok, url = rp.download_with_landing_page_fallback(Mock(), "http://example.com/landing", Path("/tmp/x.pdf"),
                                                               self.rate_limiter, _args(), self.logger)
        self.assertFalse(ok)
        self.assertEqual(url, "http://example.com/landing")

    def test_retrieval_error_on_first_attempt_propagates_without_landing_page_check(self):
        with patch("retrieve_papers.download_pdf", side_effect=rp.RetrievalError("HTTP 404")), \
             patch("retrieve_papers.resolve_landing_page_pdf_url") as mock_resolve:
            with self.assertRaises(rp.RetrievalError):
                rp.download_with_landing_page_fallback(Mock(), "http://example.com/dead", Path("/tmp/x.pdf"),
                                                         self.rate_limiter, _args(), self.logger)
        mock_resolve.assert_not_called()


class TestConcurrentFetch(unittest.TestCase):
    def test_all_items_processed_exactly_once(self):
        items = list(range(20))

        def fetch_fn(item):
            return f"result-{item}"

        results = dict(rp.concurrent_fetch(items, fetch_fn, max_workers=4))
        self.assertEqual(set(results.keys()), set(items))
        for item, result in results.items():
            self.assertEqual(result, f"result-{item}")

    def test_one_item_raising_does_not_kill_the_whole_batch(self):
        def fetch_fn(item):
            if item == 3:
                raise ValueError("simulated failure")
            return "ok"

        results = dict(rp.concurrent_fetch(list(range(6)), fetch_fn, max_workers=3, logger=Mock()))
        self.assertEqual(results[3], "thread_error")
        for item in (0, 1, 2, 4, 5):
            self.assertEqual(results[item], "ok")

    def test_max_successes_stops_early_without_crashing(self):
        # Slow fetch_fn + few workers deliberately maximizes the chance that
        # concurrent_fetch cancels still-pending futures after max_successes is
        # hit -- a real crash (CancelledError propagating from future.result())
        # used to be reachable here; this is the regression test for that fix.
        def fetch_fn(item):
            time.sleep(0.05)
            return "downloaded"

        results = list(rp.concurrent_fetch(list(range(200)), fetch_fn, max_workers=4, max_successes=5))
        successes = [r for _, r in results if r == "downloaded"]
        self.assertGreaterEqual(len(successes), 5)
        self.assertLess(len(results), 200)  # stopped well short of processing everything

    def test_max_successes_with_fast_fetch_fn_still_completes_cleanly(self):
        # Fast path (no sleep) -- exercises the "many futures already done by the
        # time cancellation is attempted" branch instead of the slow one above.
        def fetch_fn(item):
            return "downloaded"

        results = list(rp.concurrent_fetch(list(range(200)), fetch_fn, max_workers=4, max_successes=5))
        successes = [r for _, r in results if r == "downloaded"]
        self.assertGreaterEqual(len(successes), 5)

    def test_cancelled_future_is_skipped_not_raised(self):
        # The two tests above exercise the real thread-pool race honestly, but
        # whether cancel() actually wins that race (vs. guarded()'s own
        # stop.is_set() check winning it first) isn't guaranteed on any given run
        # -- confirmed by hand: forcing the bug back in and re-running the slow-path
        # test above didn't reliably fail either. This test instead reproduces the
        # exact failure deterministically, no thread timing involved: a bare
        # concurrent.futures.Future() can be cancelled directly (it starts out
        # PENDING), so ThreadPoolExecutor is mocked to hand concurrent_fetch a
        # pre-cancelled future for one item, and a normal completed one for
        # another. Before the fix, future.result() on the cancelled one raised
        # CancelledError straight out of this generator.
        from concurrent.futures import Future

        done_future = Future()
        done_future.set_result("downloaded")
        cancelled_future = Future()
        self.assertTrue(cancelled_future.cancel())
        # A real executor calls this on a task it finds already cancelled, right before it
        # would have run it -- it's what actually finalizes CANCELLED_AND_NOTIFIED state and
        # fires done-callbacks. A bare .cancel() alone leaves as_completed()'s waiter
        # permanently unnotified for this future (confirmed by hand: without this line,
        # as_completed() below hangs forever instead of raising).
        cancelled_future.set_running_or_notify_cancel()

        futures_by_item = {1: done_future, 2: cancelled_future}
        fake_pool = MagicMock()
        fake_pool.__enter__.return_value = fake_pool
        fake_pool.__exit__.return_value = False
        fake_pool.submit = Mock(side_effect=lambda fn, item: futures_by_item[item])

        with patch("retrieve_papers.ThreadPoolExecutor", return_value=fake_pool):
            results = list(rp.concurrent_fetch([1, 2], lambda x: x, max_workers=1))

        self.assertEqual(results, [(1, "downloaded")])  # item 2 silently skipped, no exception


class TestKeyedLock(unittest.TestCase):
    def test_same_key_serializes_across_threads(self):
        lock = rp.KeyedLock()
        order = []

        def worker(n):
            lock.acquire("shared-key")
            try:
                order.append(f"enter-{n}")
                # If both threads were ever inside at once, this would let the other
                # thread's "enter" slip in before this thread's "exit" is recorded.
                time.sleep(0.02)
                order.append(f"exit-{n}")
            finally:
                lock.release("shared-key")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every enter must be immediately followed by its own exit -- never another
        # thread's enter sandwiched in between (which would prove overlap).
        for i in range(0, len(order), 2):
            n = order[i].split("-")[1]
            self.assertEqual(order[i + 1], f"exit-{n}")

    def test_different_keys_do_not_block_each_other(self):
        lock = rp.KeyedLock()
        lock.acquire("key-a")
        start = time.monotonic()
        lock.acquire("key-b")  # different key -- must not wait on key-a's holder
        elapsed = time.monotonic() - start
        lock.release("key-b")
        lock.release("key-a")
        self.assertLess(elapsed, 0.5)


class TestPaperStores(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "state.sqlite3"
        self.addCleanup(self.tmpdir.cleanup)

    def test_paperstore_upsert_and_get_roundtrip(self):
        store = rp.PaperStore(self.db_path)
        store.upsert("doi:10.1/abc", title="A Paper", status="downloaded")
        record = store.get("doi:10.1/abc")
        self.assertEqual(record["title"], "A Paper")
        self.assertEqual(record["status"], "downloaded")

    def test_paperstore_get_missing_key_returns_none(self):
        store = rp.PaperStore(self.db_path)
        self.assertIsNone(store.get("doi:nonexistent"))

    def test_paperstore_upsert_overwrites_on_conflict(self):
        store = rp.PaperStore(self.db_path)
        store.upsert("doi:10.1/abc", title="Old Title", status="error")
        store.upsert("doi:10.1/abc", title="New Title", status="downloaded")
        record = store.get("doi:10.1/abc")
        self.assertEqual(record["title"], "New Title")
        self.assertEqual(record["status"], "downloaded")

    def test_status_counts(self):
        store = rp.PaperStore(self.db_path)
        store.upsert("k1", status="downloaded")
        store.upsert("k2", status="downloaded")
        store.upsert("k3", status="error")
        self.assertEqual(store.status_counts(), {"downloaded": 2, "error": 1})

    def test_threadlocal_store_shares_underlying_data_across_threads(self):
        store = rp.ThreadLocalPaperStore(self.db_path)

        def write(i):
            store.upsert(f"key{i}", title=f"paper {i}", status="downloaded")

        threads = [threading.Thread(target=write, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Read back via a fresh, single-threaded PaperStore over the same file.
        verifier = rp.PaperStore(self.db_path)
        for i in range(10):
            record = verifier.get(f"key{i}")
            self.assertIsNotNone(record)
            self.assertEqual(record["title"], f"paper {i}")


class TestPaperStoreClaim(unittest.TestCase):
    """PaperStore.claim() -- the fix for the cross-process retrieval dedup race
    documented in todo.md's "Cross-process retrieval dedup race" entry."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "state.sqlite3"
        self.addCleanup(self.tmpdir.cleanup)

    def test_claim_succeeds_on_novel_key(self):
        store = rp.PaperStore(self.db_path)
        self.assertTrue(store.claim("doi:10.1/new"))
        self.assertEqual(store.get("doi:10.1/new")["status"], "in_progress")

    def test_second_claim_on_same_key_fails(self):
        """The actual race fix: two 'processes' (here, two PaperStore instances over the same
        file, standing in for two separate OS processes) racing to claim the same key --
        exactly one must win."""
        store_a = rp.PaperStore(self.db_path)
        store_b = rp.PaperStore(self.db_path)
        self.assertTrue(store_a.claim("doi:10.1/race"))
        self.assertFalse(store_b.claim("doi:10.1/race"))

    def test_claim_fails_on_excluded_crank(self):
        store = rp.PaperStore(self.db_path)
        store.upsert("doi:10.1/x", status="excluded_crank")
        self.assertFalse(store.claim("doi:10.1/x"))

    def test_claim_fails_on_excluded_offtopic(self):
        store = rp.PaperStore(self.db_path)
        store.upsert("doi:10.1/x", status="excluded_offtopic")
        self.assertFalse(store.claim("doi:10.1/x"))

    def test_claim_succeeds_on_prior_error_status(self):
        """A candidate that failed on a PREVIOUS, non-concurrent run must still be retryable --
        claim() isn't a permanent lock, just protection against a live race."""
        store = rp.PaperStore(self.db_path)
        store.upsert("doi:10.1/x", status="error")
        self.assertTrue(store.claim("doi:10.1/x"))
        self.assertEqual(store.get("doi:10.1/x")["status"], "in_progress")

    def test_claim_succeeds_on_downloaded_status(self):
        """By the time a caller reaches claim(), a 'downloaded' row it's still asking to claim
        only means the file's missing on disk (a legitimate recovery) -- see fetch_candidate()'s
        own file-existence pre-check, which runs before claim() and is what actually filters out
        the "downloaded and file present" case. claim() itself doesn't special-case this status."""
        store = rp.PaperStore(self.db_path)
        store.upsert("doi:10.1/x", status="downloaded", file_path="/nonexistent/gone.pdf")
        self.assertTrue(store.claim("doi:10.1/x"))

    def test_stale_in_progress_claim_is_reclaimable(self):
        store = rp.PaperStore(self.db_path)
        old_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - rp.CLAIM_STALE_AFTER_SECONDS - 60))
        store.conn.execute(
            "INSERT INTO papers (key, status, updated_at) VALUES (?, 'in_progress', ?)",
            ("doi:10.1/crashed", old_timestamp),
        )
        store.conn.commit()
        self.assertTrue(store.claim("doi:10.1/crashed"))

    def test_fresh_in_progress_claim_is_not_reclaimable(self):
        store_a = rp.PaperStore(self.db_path)
        store_b = rp.PaperStore(self.db_path)
        store_a.claim("doi:10.1/active")
        self.assertFalse(store_b.claim("doi:10.1/active"))

    def test_final_upsert_after_claim_overwrites_in_progress(self):
        """The normal end-to-end flow: claim() then a real upsert() records the actual
        outcome, same as any other source's fetch_candidate()."""
        store = rp.PaperStore(self.db_path)
        store.claim("doi:10.1/x")
        store.upsert("doi:10.1/x", status="downloaded", file_path="/tmp/x.pdf")
        self.assertEqual(store.get("doi:10.1/x")["status"], "downloaded")


class TestThreadLocalPaperStoreClaim(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "state.sqlite3"
        self.addCleanup(self.tmpdir.cleanup)

    def test_exactly_one_thread_wins_a_concurrent_claim(self):
        store = rp.ThreadLocalPaperStore(self.db_path)
        results = []
        results_lock = threading.Lock()

        def try_claim():
            won = store.claim("doi:10.1/contested")
            with results_lock:
                results.append(won)

        threads = [threading.Thread(target=try_claim) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 9)


class FakeStore:
    """Minimal in-memory stand-in for PaperStore/ThreadLocalPaperStore's get()/upsert() interface."""

    def __init__(self, initial=None):
        self.data = {k: dict(v) for k, v in (initial or {}).items()}

    def get(self, key):
        return self.data.get(key)

    def upsert(self, key, **fields):
        record = self.data.setdefault(key, {})
        record.update(fields)
        record["key"] = key

    def claim(self, key):
        record = self.data.get(key)
        if record and record.get("status") in ("excluded_crank", "excluded_offtopic", "in_progress"):
            return False
        self.upsert(key, status="in_progress")
        return True


def _args(**overrides):
    # outdir defaults to the system tempdir -- process_paper() computes dest_path (an .exists()
    # check, no writes unless a test's mocked download_pdf is actually reached) unconditionally
    # once a DOI is known, even along branches that return before ever using it.
    defaults = dict(recheck=False, overwrite=False, title_match_threshold=0.82, max_retries=4, timeout=5,
                     email="test@example.com", outdir=Path(tempfile.gettempdir()))
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


class TestProcessPaper(unittest.TestCase):
    """process_paper()'s branching, with resolve_doi/query_unpaywall/download_pdf
    mocked out -- those each have their own http_get()-level coverage above."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.outdir = Path(self.tmpdir.name)

    def test_already_downloaded_and_file_exists_is_skipped(self):
        existing = self.outdir / "existing.pdf"
        existing.write_bytes(b"%PDF-1.4 fake")
        store = FakeStore({"doi:10.1/abc": {"status": "downloaded", "file_path": str(existing), "doi": "10.1/abc"}})
        paper = {"title": "T", "doi": "10.1/abc"}
        with patch("retrieve_papers.resolve_doi") as mock_resolve:
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, "skipped")
        mock_resolve.assert_not_called()

    def test_no_doi_resolvable_records_doi_not_found(self):
        store = FakeStore()
        paper = {"title": "Unresolvable Paper"}
        with patch("retrieve_papers.resolve_doi", return_value=(None, 0.1)):
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, "doi_not_found")
        self.assertEqual(store.get(rp.make_key(paper))["status"], "doi_not_found")

    def test_crossref_error_records_error_status(self):
        store = FakeStore()
        paper = {"title": "Some Paper"}
        with patch("retrieve_papers.resolve_doi", side_effect=rp.RetrievalError("crossref down")):
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, "error")
        self.assertEqual(store.get(rp.make_key(paper))["status"], "error")

    def test_no_oa_location_records_no_oa(self):
        store = FakeStore()
        paper = {"title": "Paywalled Paper", "doi": "10.1/xyz"}
        with patch("retrieve_papers.query_unpaywall", return_value={"oa_status": "closed", "best_oa_location": None}):
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, "no_oa")

    def test_unpaywall_unknown_doi(self):
        store = FakeStore()
        paper = {"title": "Unknown To Unpaywall", "doi": "10.1/unknown"}
        with patch("retrieve_papers.query_unpaywall", return_value=None):
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, "doi_unknown_to_unpaywall")

    def test_successful_download_records_downloaded_with_file_path(self):
        store = FakeStore()
        paper = {"title": "Open Paper", "doi": "10.1/open"}
        unpaywall_data = {
            "oa_status": "gold",
            "best_oa_location": {"url_for_pdf": "http://example.com/open.pdf"},
        }
        with patch("retrieve_papers.query_unpaywall", return_value=unpaywall_data), \
             patch("retrieve_papers.download_with_landing_page_fallback", return_value=(True, "http://example.com/open.pdf")):
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "downloaded")
        record = store.get(rp.make_key(paper))
        self.assertEqual(record["status"], "downloaded")
        self.assertTrue(record["file_path"])

    def test_download_serves_non_pdf_records_oa_url_not_pdf(self):
        store = FakeStore()
        paper = {"title": "Fake PDF Paper", "doi": "10.1/fake"}
        unpaywall_data = {
            "oa_status": "gold",
            "best_oa_location": {"url_for_pdf": "http://example.com/fake.pdf"},
        }
        with patch("retrieve_papers.query_unpaywall", return_value=unpaywall_data), \
             patch("retrieve_papers.download_with_landing_page_fallback", return_value=(False, "http://example.com/fake.pdf")):
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "oa_url_not_pdf")

    def test_download_error_records_error_status(self):
        store = FakeStore()
        paper = {"title": "Broken Download", "doi": "10.1/broken"}
        unpaywall_data = {
            "oa_status": "gold",
            "best_oa_location": {"url_for_pdf": "http://example.com/broken.pdf"},
        }
        with patch("retrieve_papers.query_unpaywall", return_value=unpaywall_data), \
             patch("retrieve_papers.download_with_landing_page_fallback", side_effect=rp.RetrievalError("HTTP 500")):
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "error")

    def test_no_oa_previously_known_is_skipped_without_recheck(self):
        store = FakeStore({"doi:10.1/closed": {"status": "no_oa", "doi": "10.1/closed"}})
        paper = {"title": "T", "doi": "10.1/closed"}
        with patch("retrieve_papers.query_unpaywall") as mock_unpaywall:
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), _args(recheck=False), Mock())
        self.assertEqual(result, "no_oa")
        mock_unpaywall.assert_not_called()

    def test_recheck_forces_a_fresh_unpaywall_lookup(self):
        store = FakeStore({"doi:10.1/closed": {"status": "no_oa", "doi": "10.1/closed"}})
        paper = {"title": "T", "doi": "10.1/closed"}
        with patch("retrieve_papers.query_unpaywall", return_value=None) as mock_unpaywall:
            rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), _args(recheck=True), Mock())
        mock_unpaywall.assert_called_once()

    def test_known_oa_url_downloads_without_querying_unpaywall(self):
        store = FakeStore()
        paper = {"title": "Known OA Paper", "doi": "10.1/known", "oa_url": "http://example.com/known.pdf",
                  "oa_status": "gold"}
        with patch("retrieve_papers.download_with_landing_page_fallback",
                    return_value=(True, "http://example.com/known.pdf")) as mock_download, \
             patch("retrieve_papers.query_unpaywall") as mock_unpaywall:
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "downloaded")
        mock_unpaywall.assert_not_called()
        mock_download.assert_called_once()
        record = store.get(rp.make_key(paper))
        self.assertEqual(record["pdf_url"], "http://example.com/known.pdf")
        self.assertEqual(record["oa_status"], "gold")

    def test_known_oa_url_failure_falls_back_to_unpaywall(self):
        store = FakeStore()
        paper = {"title": "Stale Known URL", "doi": "10.1/stale", "oa_url": "http://example.com/dead.pdf"}
        unpaywall_data = {
            "oa_status": "gold",
            "best_oa_location": {"url_for_pdf": "http://example.com/real.pdf"},
        }
        with patch("retrieve_papers.download_with_landing_page_fallback") as mock_download, \
             patch("retrieve_papers.query_unpaywall", return_value=unpaywall_data) as mock_unpaywall:
            # First call (the known_oa_url attempt) fails to serve a PDF; second call
            # (the real fallback download, using Unpaywall's URL) succeeds.
            mock_download.side_effect = [(False, "http://example.com/dead.pdf"), (True, "http://example.com/real.pdf")]
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "downloaded")
        mock_unpaywall.assert_called_once()
        self.assertEqual(mock_download.call_count, 2)
        record = store.get(rp.make_key(paper))
        self.assertEqual(record["pdf_url"], "http://example.com/real.pdf")

    def test_known_oa_url_retrieval_error_falls_back_to_unpaywall(self):
        store = FakeStore()
        paper = {"title": "Broken Known URL", "doi": "10.1/broken-known", "oa_url": "http://example.com/broken.pdf"}
        unpaywall_data = {
            "oa_status": "gold",
            "best_oa_location": {"url_for_pdf": "http://example.com/real2.pdf"},
        }
        with patch("retrieve_papers.download_with_landing_page_fallback") as mock_download, \
             patch("retrieve_papers.query_unpaywall", return_value=unpaywall_data) as mock_unpaywall:
            mock_download.side_effect = [rp.RetrievalError("HTTP 404"), (True, "http://example.com/real2.pdf")]
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "downloaded")
        mock_unpaywall.assert_called_once()
        record = store.get(rp.make_key(paper))
        self.assertEqual(record["pdf_url"], "http://example.com/real2.pdf")

    def test_oa_alt_urls_tried_in_order_after_oa_url_fails(self):
        store = FakeStore()
        paper = {"title": "Multi-Location Paper", "doi": "10.1/multi",
                  "oa_url": "http://example.com/best-dead.pdf",
                  "oa_alt_urls": ["http://example.com/alt1-dead.pdf", "http://example.com/alt2-good.pdf"]}
        with patch("retrieve_papers.download_with_landing_page_fallback") as mock_download, \
             patch("retrieve_papers.query_unpaywall") as mock_unpaywall:
            # oa_url fails, alt1 fails, alt2 succeeds -- Unpaywall never needed at all.
            mock_download.side_effect = [(False, "http://example.com/best-dead.pdf"), rp.RetrievalError("HTTP 403"),
                                          (True, "http://example.com/alt2-good.pdf")]
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "downloaded")
        mock_unpaywall.assert_not_called()
        self.assertEqual(mock_download.call_count, 3)
        record = store.get(rp.make_key(paper))
        self.assertEqual(record["pdf_url"], "http://example.com/alt2-good.pdf")

    def test_all_oa_alt_urls_fail_falls_back_to_unpaywall(self):
        store = FakeStore()
        paper = {"title": "All Dead Paper", "doi": "10.1/alldead",
                  "oa_url": "http://example.com/best-dead.pdf",
                  "oa_alt_urls": ["http://example.com/alt1-dead.pdf"]}
        unpaywall_data = {"oa_status": "gold", "best_oa_location": {"url_for_pdf": "http://example.com/rescued.pdf"}}
        with patch("retrieve_papers.download_with_landing_page_fallback") as mock_download, \
             patch("retrieve_papers.query_unpaywall", return_value=unpaywall_data) as mock_unpaywall:
            mock_download.side_effect = [(False, "http://example.com/best-dead.pdf"), (False, "http://example.com/alt1-dead.pdf"),
                                          (True, "http://example.com/rescued.pdf")]
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "downloaded")
        mock_unpaywall.assert_called_once()
        self.assertEqual(mock_download.call_count, 3)
        record = store.get(rp.make_key(paper))
        self.assertEqual(record["pdf_url"], "http://example.com/rescued.pdf")

    def test_oa_alt_urls_without_oa_url_still_tried(self):
        # oa_url missing/empty but oa_alt_urls present -- shouldn't be silently skipped.
        store = FakeStore()
        paper = {"title": "Alt Only Paper", "doi": "10.1/altonly", "oa_alt_urls": ["http://example.com/only-alt.pdf"]}
        with patch("retrieve_papers.download_with_landing_page_fallback",
                    return_value=(True, "http://example.com/only-alt.pdf")) as mock_download, \
             patch("retrieve_papers.query_unpaywall") as mock_unpaywall:
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "downloaded")
        mock_unpaywall.assert_not_called()
        mock_download.assert_called_once()

    def test_no_oa_url_field_behaves_exactly_as_before(self):
        # No "oa_url" key at all (the common case for entries that don't come from
        # build_oa_starting_list.py) -- must go straight to Unpaywall, unchanged.
        store = FakeStore()
        paper = {"title": "Plain Paper", "doi": "10.1/plain"}
        unpaywall_data = {"oa_status": "gold", "best_oa_location": {"url_for_pdf": "http://example.com/plain.pdf"}}
        with patch("retrieve_papers.download_with_landing_page_fallback", return_value=(True, "http://example.com/plain.pdf")), \
             patch("retrieve_papers.query_unpaywall", return_value=unpaywall_data) as mock_unpaywall:
            args = _args()
            args.outdir = self.outdir
            result = rp.process_paper(Mock(), paper, store, rp.RateLimiter(0), args, Mock())
        self.assertEqual(result, "downloaded")
        mock_unpaywall.assert_called_once()


class TestChunked(unittest.TestCase):
    def test_splits_into_fixed_size_groups(self):
        self.assertEqual(list(rp.chunked([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]])

    def test_empty_input(self):
        self.assertEqual(list(rp.chunked([], 3)), [])

    def test_exact_multiple(self):
        self.assertEqual(list(rp.chunked([1, 2, 3, 4], 2)), [[1, 2], [3, 4]])


class TestQueryOpenalexBatch(unittest.TestCase):
    """query_openalex_batch() -- the batched (up to 50-DOI) OA-location lookup
    added 2026-08-21 to get around Unpaywall having no batch endpoint (see
    todo.md's "Full-corpus plagiarism audit" follow-up: a bulk retrieval job
    with tens of thousands of candidates projected ~19 hours at Unpaywall's
    1-DOI-per-request pace; batches of 50 cut that by ~50x)."""

    def test_empty_input_returns_empty_without_a_request(self):
        with patch("retrieve_papers.http_get") as mock_get:
            result = rp.query_openalex_batch(Mock(), [], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {})
        mock_get.assert_not_called()

    def test_parses_best_oa_location_pdf_url(self):
        body = {"results": [{
            "doi": "https://doi.org/10.1/a",
            "best_oa_location": {"pdf_url": "http://example.com/a.pdf"},
            "open_access": {"oa_status": "gold", "oa_url": "http://example.com/a-fallback.pdf"},
        }]}
        resp = _fake_response(200)
        resp.json = Mock(return_value=body)
        with patch("retrieve_papers.http_get", return_value=resp):
            result = rp.query_openalex_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {"10.1/a": {"oa_status": "gold", "pdf_url": "http://example.com/a.pdf"}})

    def test_falls_back_to_open_access_oa_url_without_best_location(self):
        body = {"results": [{
            "doi": "https://doi.org/10.1/b",
            "best_oa_location": None,
            "open_access": {"oa_status": "green", "oa_url": "http://example.com/b.pdf"},
        }]}
        resp = _fake_response(200)
        resp.json = Mock(return_value=body)
        with patch("retrieve_papers.http_get", return_value=resp):
            result = rp.query_openalex_batch(Mock(), ["10.1/b"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {"10.1/b": {"oa_status": "green", "pdf_url": "http://example.com/b.pdf"}})

    def test_doi_with_no_oa_location_at_all_is_absent_from_result(self):
        body = {"results": [{
            "doi": "https://doi.org/10.1/c",
            "best_oa_location": None,
            "open_access": {"oa_status": "closed", "oa_url": None},
        }]}
        resp = _fake_response(200)
        resp.json = Mock(return_value=body)
        with patch("retrieve_papers.http_get", return_value=resp):
            result = rp.query_openalex_batch(Mock(), ["10.1/c"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {}, "no OA location means the caller should fall back to Unpaywall, not get a false miss")

    def test_doi_openalex_never_heard_of_is_simply_absent(self):
        resp = _fake_response(200)
        resp.json = Mock(return_value={"results": []})
        with patch("retrieve_papers.http_get", return_value=resp):
            result = rp.query_openalex_batch(Mock(), ["10.1/unknown"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {})

    def test_non_200_degrades_to_empty_rather_than_raising(self):
        resp = _fake_response(500)
        with patch("retrieve_papers.http_get", return_value=resp):
            result = rp.query_openalex_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {})

    def test_retrieval_error_degrades_to_empty_rather_than_raising(self):
        with patch("retrieve_papers.http_get", side_effect=rp.RetrievalError("network down")):
            result = rp.query_openalex_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {})

    def test_calls_http_get_with_max_retries_zero(self):
        """Regression test: this is what makes a 429 fail fast (RateLimited,
        no sleep) instead of blocking on args.max_retries retries, each
        potentially sleeping up to MAX_RETRY_AFTER_DELAY."""
        resp = _fake_response(200)
        resp.json = Mock(return_value={"results": []})
        with patch("retrieve_papers.http_get", return_value=resp) as mock_get:
            rp.query_openalex_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(max_retries=4), Mock())
        self.assertEqual(mock_get.call_args.kwargs["max_retries"], 0)

    def test_api_key_from_env_var_is_sent_as_param(self):
        resp = _fake_response(200)
        resp.json = Mock(return_value={"results": []})
        with patch("retrieve_papers.http_get", return_value=resp) as mock_get, \
             patch.dict(os.environ, {rp.OPENALEX_API_KEY_ENV_VAR: "fake-test-key-not-a-secret"}):
            rp.query_openalex_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(), Mock())
        sent_params = mock_get.call_args.args[2]
        self.assertEqual(sent_params.get("api_key"), "fake-test-key-not-a-secret")

    def test_no_api_key_param_when_env_var_unset(self):
        resp = _fake_response(200)
        resp.json = Mock(return_value={"results": []})
        with patch("retrieve_papers.http_get", return_value=resp) as mock_get, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop(rp.OPENALEX_API_KEY_ENV_VAR, None)
            rp.query_openalex_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(), Mock())
        sent_params = mock_get.call_args.args[2]
        self.assertNotIn("api_key", sent_params)

    def test_rate_limited_propagates_uncaught(self):
        """Regression test for the real incident this was added for: OpenAlex
        sent a 71649-second Retry-After after this project's own bulk
        retrieval traffic; RateLimited must reach the caller (so it can stop
        calling OpenAlex for the rest of the run), not be swallowed to {}
        like other RetrievalErrors."""
        with patch("retrieve_papers.http_get", side_effect=rp.RateLimited("rate limited (429) by api.openalex.org")):
            with self.assertRaises(rp.RateLimited):
                rp.query_openalex_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(), Mock())


class TestQueryOpenalexAuthorshipsBatch(unittest.TestCase):
    """query_openalex_authorships_batch() -- disambiguated per-author OpenAlex IDs pulled
    from the same batched work lookup, added for bulk_retrieve_author_works.py's
    author-publication-graph expansion (see its own docstring)."""

    def test_empty_input_returns_empty_without_a_request(self):
        with patch("retrieve_papers.http_get") as mock_get:
            result = rp.query_openalex_authorships_batch(Mock(), [], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {})
        mock_get.assert_not_called()

    def test_parses_authorships(self):
        body = {"results": [{
            "doi": "https://doi.org/10.1/a",
            "authorships": [
                {"author": {"id": "https://openalex.org/A123", "display_name": "Jane Doe"}},
                {"author": {"id": "https://openalex.org/A456", "display_name": "John Smith"}},
            ],
        }]}
        resp = _fake_response(200)
        resp.json = Mock(return_value=body)
        with patch("retrieve_papers.http_get", return_value=resp):
            result = rp.query_openalex_authorships_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {"10.1/a": [
            {"openalex_id": "https://openalex.org/A123", "display_name": "Jane Doe"},
            {"openalex_id": "https://openalex.org/A456", "display_name": "John Smith"},
        ]})

    def test_work_with_no_authorships_is_absent(self):
        body = {"results": [{"doi": "https://doi.org/10.1/b", "authorships": []}]}
        resp = _fake_response(200)
        resp.json = Mock(return_value=body)
        with patch("retrieve_papers.http_get", return_value=resp):
            result = rp.query_openalex_authorships_batch(Mock(), ["10.1/b"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {})

    def test_author_missing_id_or_name_is_skipped(self):
        body = {"results": [{
            "doi": "https://doi.org/10.1/c",
            "authorships": [{"author": {"id": None, "display_name": "No Id"}},
                             {"author": {"id": "https://openalex.org/A1", "display_name": None}}],
        }]}
        resp = _fake_response(200)
        resp.json = Mock(return_value=body)
        with patch("retrieve_papers.http_get", return_value=resp):
            result = rp.query_openalex_authorships_batch(Mock(), ["10.1/c"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {})

    def test_non_200_degrades_to_empty_rather_than_raising(self):
        resp = _fake_response(500)
        with patch("retrieve_papers.http_get", return_value=resp):
            result = rp.query_openalex_authorships_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(), Mock())
        self.assertEqual(result, {})

    def test_rate_limited_propagates_uncaught(self):
        with patch("retrieve_papers.http_get", side_effect=rp.RateLimited("rate limited (429) by api.openalex.org")):
            with self.assertRaises(rp.RateLimited):
                rp.query_openalex_authorships_batch(Mock(), ["10.1/a"], rp.RateLimiter(0), _args(), Mock())


class TestParseOpenalexWork(unittest.TestCase):
    """Generic works?filter=... field extraction, shared by
    bulk_retrieve_author_works.py (filter=author.id:...) and
    bulk_retrieve_openalex_concept.py (filter=concepts.id:...)."""

    def test_extracts_title_authors_year_doi_oa(self):
        work = {
            "doi": "https://doi.org/10.1/a", "title": "A Real Paper", "publication_year": 2021,
            "authorships": [{"author": {"display_name": "Jane Doe"}}],
            "best_oa_location": {"pdf_url": "http://example.com/a.pdf"},
            "open_access": {"oa_status": "gold"},
        }
        paper = rp.parse_openalex_work(work)
        self.assertEqual(paper["title"], "A Real Paper")
        self.assertEqual(paper["doi"], "10.1/a")
        self.assertEqual(paper["authors"], ["Jane Doe"])
        self.assertEqual(paper["pdf_url"], "http://example.com/a.pdf")

    def test_no_doi_returns_none(self):
        self.assertIsNone(rp.parse_openalex_work({"doi": None, "title": "No DOI"}))

    def test_no_title_returns_none(self):
        self.assertIsNone(rp.parse_openalex_work({"doi": "https://doi.org/10.1/b", "title": ""}))


class TestSearchOpenalexWorks(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.args = Mock(email="test@example.com", max_retries=4, timeout=30.0)

    def test_sends_given_filter(self):
        resp = _fake_response(200)
        resp.json = Mock(return_value={"results": []})
        with patch("retrieve_papers.http_get", return_value=resp) as mock_get:
            list(rp.search_openalex_works(self.session, MagicMock(), "concepts.id:C19165224", 100, self.args, Mock()))
        params = mock_get.call_args.args[2]
        self.assertEqual(params["filter"], "concepts.id:C19165224")

    def test_paginates_via_next_cursor(self):
        page1 = _fake_response(200)
        page1.json = Mock(return_value={"results": [{"id": 1}], "meta": {"next_cursor": "page2"}})
        page2 = _fake_response(200)
        page2.json = Mock(return_value={"results": [{"id": 2}], "meta": {"next_cursor": None}})
        with patch("retrieve_papers.http_get", side_effect=[page1, page2]):
            results = list(rp.search_openalex_works(self.session, MagicMock(), "concepts.id:X", 100, self.args, Mock()))
        self.assertEqual(len(results), 2)

    def test_stops_at_max_results(self):
        page = _fake_response(200)
        page.json = Mock(return_value={"results": [{"id": 1}, {"id": 2}, {"id": 3}], "meta": {"next_cursor": "more"}})
        with patch("retrieve_papers.http_get", return_value=page):
            results = list(rp.search_openalex_works(self.session, MagicMock(), "concepts.id:X", 2, self.args, Mock()))
        self.assertEqual(len(results), 2)

    def test_non_200_stops(self):
        resp = _fake_response(500)
        with patch("retrieve_papers.http_get", return_value=resp) as mock_get:
            results = list(rp.search_openalex_works(self.session, MagicMock(), "concepts.id:X", 100, self.args, Mock()))
        self.assertEqual(results, [])
        self.assertEqual(mock_get.call_count, 1)


class TestRetryAfterDelayCap(unittest.TestCase):
    """_retry_after_delay() must never blindly honor an enormous Retry-After
    -- confirmed necessary for real (see RateLimited's docstring): OpenAlex
    sent 71649 seconds (~20 hours) after this project's own retrieval
    traffic, which would otherwise block a single http_get() call for most
    of a day."""

    def test_numeric_header_is_capped(self):
        resp = _fake_response(429)
        resp.headers = {"Retry-After": "71649"}
        self.assertEqual(rp._retry_after_delay(resp, 1), rp.MAX_RETRY_AFTER_DELAY)

    def test_date_header_far_in_future_is_capped(self):
        resp = _fake_response(429)
        far_future = format_datetime(datetime.now(timezone.utc) + timedelta(hours=20))
        resp.headers = {"Retry-After": far_future}
        self.assertEqual(rp._retry_after_delay(resp, 1), rp.MAX_RETRY_AFTER_DELAY)

    def test_short_header_is_not_altered(self):
        resp = _fake_response(429)
        resp.headers = {"Retry-After": "5"}
        self.assertEqual(rp._retry_after_delay(resp, 1), 5.0)


class TestHttpGetRateLimitedWithZeroRetries(unittest.TestCase):
    def test_429_with_max_retries_zero_raises_rate_limited_without_sleeping(self):
        resp = _fake_response(429)
        resp.headers = {"Retry-After": "71649"}
        session = Mock()
        session.get = Mock(return_value=resp)
        with patch("retrieve_papers.time.sleep") as mock_sleep:
            with self.assertRaises(rp.RateLimited):
                rp.http_get(session, "http://example.com", None, rate_limiter=rp.RateLimiter(0),
                            max_retries=0, timeout=5, logger=Mock())
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()

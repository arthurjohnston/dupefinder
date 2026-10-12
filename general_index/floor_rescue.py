"""Floor experiment, part 3b: gi_rescue_pdfs.py's second try, Unpaywall only.

gi_rescue_pdfs.py also uses OpenAlex (title search, batched locations), which this experiment may not call.
This keeps its Unpaywall half: for papers whose free copy failed (error, oa_url_not_pdf), every location
Unpaywall lists (oa_locations, direct PDF links first, then landing pages, the already-failed URL skipped),
up to --max-tries each, through download_with_landing_page_fallback(). Writes back into the run's own
state.sqlite3 (data/floor_experiment/verify_<run>/), never gi.sqlite3. The OpenAlex half is NOT done, so this
run's both-PDF rate is a little below what a production run (with rescue) gets: see FLOOR_EXPERIMENT.md.

Usage: floor_rescue.py RUN [--email ADDR] [--workers 2] [--max-tries 6]
"""
import argparse
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

import floor_common as fc
import gi_rescue_pdfs as grp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieve_papers as rp  # noqa: E402

RETRY = ("error", "oa_url_not_pdf")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--email", default="arthur.j.johnston@gmail.com")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--max-tries", type=int, default=6)
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--min-interval", type=float, default=1.0)
    args = ap.parse_args()
    args.overwrite = False
    os.environ.pop(rp.OPENALEX_API_KEY_ENV_VAR, None)
    work = fc.OUT / f"verify_{args.run}"
    papers_dir = work / "papers"
    log = rp.setup_logger(work / "rescue.log")
    st = sqlite3.connect(work / "state.sqlite3", check_same_thread=False)
    st.row_factory = sqlite3.Row
    lock, limiter, local = threading.Lock(), rp.RateLimiter(args.min_interval), threading.local()

    def session():
        if not hasattr(local, "s"):
            local.s = requests.Session()
            local.s.headers.update({"User-Agent": rp.USER_AGENT_TEMPLATE.format(email=args.email)})
        return local.s

    rows = st.execute(f"select key, title, doi, pdf_url, status from papers where doi is not null and status in "
                      f"({','.join('?' * len(RETRY))})", RETRY).fetchall()
    log.info("%d papers to retry (Unpaywall oa_locations only)", len(rows))

    def rescue(r):
        doi = r["doi"].lower()
        up_pdf, up_land = grp.unpaywall_urls(session(), limiter, args, log, doi)
        urls, seen = [], {r["pdf_url"]}
        for u in up_pdf + up_land:
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        dest = papers_dir / f"{rp.slugify(r['title'] or doi, 100)}-{rp.slugify(doi, 60)}.pdf"
        for u in urls[:args.max_tries]:
            try:
                ok, actual = rp.download_with_landing_page_fallback(session(), u, dest, limiter, args, log)
            except rp.RetrievalError as e:
                log.info("  %s: %s", u, e)
                continue
            if ok:
                return r, actual, dest
        return r, None, None

    got = 0
    with ThreadPoolExecutor(args.workers) as pool:
        for r, url, dest in pool.map(rescue, rows):
            if url:
                got += 1
                with lock:
                    st.execute("update papers set status = 'downloaded', pdf_url = ?, file_path = ?, error = null "
                               "where key = ?", (url, f"papers/{dest.name}", r["key"]))
                    st.commit()
                log.info("rescued %r <- %s", r["title"], url)
    log.info("done: %d of %d rescued", got, len(rows))
    print(f"rescue: {got} of {len(rows)} rescued")


if __name__ == "__main__":
    main()

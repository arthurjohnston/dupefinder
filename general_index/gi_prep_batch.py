"""Prep a batch of cases with the gi-cases agent ahead of review, a few at a time.

Picks the next --per-order pairs that haven't been prepped (no findings.md) from the review queue under each
of --orders (gi_review_cli.load_queue(): your unreviewed pairs, as the review tool would show them), a case
picked under one order not counted again under the next, then runs gi_review_cli.BackgroundPrep on them,
--parallel at once. Exactly what the review tool's own background run does: the same prompt, the same
tools (read, read-only sqlite3, pdftotext / pdfinfo, web search and fetch, writing only in the case's folder
and journal_contacts.json) and your claude.ai subscription only: nothing starts unless `claude auth status`
says claude.ai, and API-key variables are removed from its environment, so it never bills an API account.
In the review tool such a case then shows what the run concluded, and d resumes its session.

--at HH:MM waits until then (today, or tomorrow if that's past) before picking the cases, so the picks
reflect the queue at that time. Each finished case is logged with its minutes and API-equivalent cost
(total_cost_usd: what the run would have cost on the API; on the subscription it comes out of your usage
instead) to data/prep_batch/<start>.log, with a summary at the end. Ctrl-C or SIGTERM stops the runs still
going; a stopped case is simply prepped again later.

Usage: gi_prep_batch.py [--orders affiliation,professor,publisher] [--per-order 15] [--parallel 3]
                        [--at 22:00] [--dry-run] [--db data/gi.sqlite3]
"""
import argparse
import datetime
import signal
import sqlite3
import sys
import time

import gi_case_findings
import gi_db
import gi_review_cli as cli

ORDERS = ("affiliation", "professor", "publisher", "overlap")


def pick(db, orders, per_order):
    """[(case, order)]: per order, the next per_order queue pairs with no findings.md, skipping earlier picks."""
    picked, seen = [], set()
    for order in orders:
        n = 0
        for r in cli.load_queue(db, order=order):
            if n >= per_order:
                break
            case = f"GI-{r['page_number']:02d}"
            if case in seen or gi_case_findings.findings_path(case).exists():
                continue
            seen.add(case)
            picked.append((case, order))
            n += 1
    return picked


def wait_until(hhmm):
    now = datetime.datetime.now()
    h, m = map(int, hhmm.split(":"))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if at <= now:
        at += datetime.timedelta(days=1)
    print(f"waiting until {at:%Y-%m-%d %H:%M} ...", flush=True)
    time.sleep((at - now).total_seconds())


def run(picked, parallel, log):
    """Run the preps, parallel at a time; returns [(case, order, result dict or {})]."""
    todo, running, done = list(picked), [], []

    def say(text):
        line = f"{datetime.datetime.now():%H:%M:%S} {text}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    def stop_all(*_):
        for prep, case, order in running:
            prep.stop()
            say(f"{case}: stopped")
        sys.exit(1)

    signal.signal(signal.SIGTERM, stop_all)
    try:
        while todo or running:
            while todo and len(running) < parallel:
                case, order = todo.pop(0)
                prep = cli.BackgroundPrep(case)
                if prep.start():
                    running.append((prep, case, order))
                    say(f"{case}: started ({order})")
                else:
                    done.append((case, order, {}))
                    say(f"{case}: not started")
            time.sleep(10)
            for item in [x for x in running if not x[0].running()]:
                prep, case, order = item
                running.remove(item)
                res = prep.result()
                done.append((case, order, res))
                said = (res.get("result") or "").strip().splitlines()
                say(f"{case}: {'failed' if res.get('is_error') or not res else 'done'} in "
                    f"{(res.get('duration_ms') or 0) / 60000:.1f} min, ${res.get('total_cost_usd') or 0:.2f} "
                    f"API-equivalent: {said[-1][:160] if said else 'no result, see ' + str(prep.dir / 'prep.log')}")
    except KeyboardInterrupt:
        stop_all()
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--orders", type=lambda v: v.split(","), default=["affiliation", "professor", "publisher"])
    ap.add_argument("--per-order", type=int, default=15)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--at", help="HH:MM: wait until then before picking and starting")
    ap.add_argument("--dry-run", action="store_true", help="print what would be picked now and stop")
    args = ap.parse_args()
    bad = [o for o in args.orders if o not in ORDERS]
    if bad:
        ap.error(f"--orders: unknown {', '.join(bad)} (use: {', '.join(ORDERS)})")
    if not args.dry_run:
        why = cli.subscription_problem()
        if why:
            sys.exit(f"not starting: {why}")
        if args.at:
            wait_until(args.at)
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
    db.row_factory = sqlite3.Row
    picked = pick(db, args.orders, args.per_order)
    db.close()
    for o in args.orders:
        print(f"{o}: " + " ".join(c for c, oo in picked if oo == o), flush=True)
    if args.dry_run or not picked:
        return
    out = gi_db.DATA / "prep_batch"
    out.mkdir(exist_ok=True)
    start = time.time()
    with open(out / f"{datetime.datetime.now():%Y-%m-%d_%H%M}.log", "a") as log:
        done = run(picked, args.parallel, log)
        ok = [r for _, _, r in done if r and not r.get("is_error")]
        mins = (time.time() - start) / 60
        cost = sum(r.get("total_cost_usd") or 0 for r in ok)
        summary = (f"summary: {len(ok)} of {len(picked)} prepped in {mins:.0f} min, {args.parallel} at a time; "
                   f"${cost:.2f} API-equivalent in all, ${cost / max(len(ok), 1):.2f} a case")
        print(summary, flush=True)
        log.write(summary + "\n")


if __name__ == "__main__":
    main()

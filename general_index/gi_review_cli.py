"""Go through the General Index result pages one pair at a time and record your verdict.

For each pair: prints the overlap figures and the agent's verdict, opens the comparison page (xdg-open, or
$GI_REVIEW_OPENER; --pdfs opens both PDFs too, or press p at the prompt), then asks
  1. what you think of it: decision and type, offered as one numbered list, most common first (counted over
     your reviews and the agent's);
  2. severity (problem / unsure only; Enter gives medium, the agent's is shown; high also writes the emails
     to the earlier authors, their publisher and the later authors' institution, see below);
  3. the common reasons for that decision (pick any, by number);
  4. for an unsure verdict, flags: m = you think both papers came from a paper mill, e = not qualified to
     judge it (higher-level math, say);
  5. an optional note in your own words ('e' opens $EDITOR).
It saves to `reviews` in gi.sqlite3 exactly like REVIEW_COMMANDS.md's gi_review (same vocabulary, same
verdict column, replaces your earlier review of the pair), the flags to `review_flags` (replacing the
pair's earlier flags), and rewrites the pages' index.html on exit.

Queue (default): pages you haven't reviewed, the agent's cross-author / data copying calls first, then the
rest of its problem calls, then everything else; within each of those, pairs whose later (copying) paper is
from a major publisher first (gi_preprints.pair_priority(), major_publisher_doi_prefixes.tsv; a known
paper-mill venue inside one ranks ordinary), then larger overlap first. Pairs whose later (copying) paper is
a preprint or repository upload (gi_preprints.py, preprint_doi_prefixes.tsv) go after all of that, still
in the queue, with a note: such uploads are most often the authors' own or an aggregator's copy, rarely a
publishable finding. Pairs where the order is unknown and one side is a preprint go just before them. --gi 12,40 for specific pages
(reviewed or not); --include-reviewed to go back over yours too. --ai-type cross-author,data (or duplicate,
recycling, legitimate, same-paper, third-source, boilerplate, citations, artifact, unsure, none = not reviewed
by the agent) and --ai-decision problem / no-problem / unsure / none keep only those pairs.

A problem verdict on two papers from the same year is saved as unsure (the type kept as the best guess),
with "can't tell which paper came first (both published YEAR)" as a reason.

On the first menu, c is one key for "problem: cross-author copying, medium, most of the paper copied".

PubPeer: each pair shows the PubPeer link of its later (copying) paper (both papers' when the order is
unknown). Saving a problem verdict, or agreeing (a) with the AI's problem verdict (even when the same year
saves it as unsure), writes the pair's report files (gi_report_case.py: in data/dupe_publisher/GI-NN/ the PubPeer
page, with the findings as Markdown and a Copy button, the attachment for the journal, and the draft email
to the later article's journal; a high-severity verdict also gets the drafts to the later authors' institution,
the earlier authors and the earlier publisher;
the comparison page and index on the project site, example_output/general_index/) and opens the PubPeer page in a
new tab, unless you answer s ("skip them") when asked; t at the prompt does the same for any pair.

Usage: gi_review_cli.py [--gi 12,40] [--ai-type cross-author,data] [--ai-decision problem] [--include-reviewed] [--pdfs] [--no-open] [--pages-dir data/fulltext_pages_s16]
                        [--db data/gi.sqlite3]
"""
import argparse
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import gi_db

# (decision, type) pairs REVIEW_COMMANDS.md documents; the menu orders them by how often they're used
CHOICES = [
    ("problem", "cross-author copying", "different authors, text taken without credit"),
    ("problem", "data copying", "different authors, someone else's data/results presented as new"),
    ("problem", "duplicate publication", "same authors, the same paper published twice"),
    ("problem", "text recycling", "same authors, substantial undisclosed reuse of their own text"),
    ("no problem", "text recycling", "same authors, acceptable reuse of their own text"),
    ("no problem", "legitimate reuse", "declared extension, quoted and cited, reprint"),
    ("no problem", "same paper", "one work under two records"),
    ("no problem", "shared third source", "both quote or reuse a third document"),
    ("no problem", "boilerplate", "standard wording, templates, forms"),
    ("no problem", "shared citations", "the overlap is reference lists"),
    ("no problem", "artifact", "wrong PDF, extraction or index error"),
    ("unsure", "unsure", "can't tell yet: say what would settle it"),
]

REASONS = {
    "problem": [
        "bylines read on both PDFs: no shared names",
        "the later paper doesn't cite the earlier one",
        "results, data or figures copied",
        "most of the paper copied",
        "copied passages aren't quoted",
        "shared authors on the bylines (self-reuse not disclosed)",
    ],
    "no problem": [
        "shared author on the PDF bylines",
        "same work under two DOIs / records",
        "the overlap is quoted and cited",
        "a declared extension or reprint",
        "the shared text is boilerplate or references",
        "wrong PDF or extraction error",
    ],
    "unsure": [
        "can't read a byline (scan, image, missing page)",
        "need the earlier version (thesis, preprint) to tell",
        "can't tell which paper came first",
        "might be a shared template or third source",
    ],
}

SEVERITIES = {"l": "low", "m": "medium", "h": "high"}

# the one-key verdict on the first menu: decision, type, severity, reason (skips the severity and reasons steps)
QUICK = ("problem", "cross-author copying", "medium", "most of the paper copied")
DEFAULT_SEVERITY = "medium"

# --ai-type names -> the agent's type
AI_TYPES = {"cross-author": "cross-author copying", "data": "data copying", "duplicate": "duplicate publication",
            "recycling": "text recycling", "legitimate": "legitimate reuse", "same-paper": "same paper",
            "third-source": "shared third source", "boilerplate": "boilerplate", "citations": "shared citations",
            "artifact": "artifact", "unsure": "unsure", "none": None}
AI_DECISIONS = {"problem": "problem", "no-problem": "no problem", "unsure": "unsure", "none": None}

# key -> flag stored in review_flags.flag
FLAGS = {"m": ("paper mill", "both papers look like paper-mill output"),
         "e": ("not qualified", "outside my expertise, not qualified to judge it (e.g. higher-level math)")}

FLAGS_DDL = """create table if not exists review_flags (   -- your extra judgements on a pair, beside the verdict
    pair_lo text, pair_hi text,
    flag text,                    -- 'paper mill': you think both papers came from a paper mill;
                                  -- 'not qualified': you can't judge the content (e.g. higher-level math)
    flagged_at text,
    primary key (pair_lo, pair_hi, flag)
);"""


def verdict_for(type_):
    """The old `verdict` column, from the type (as gi_review fills it; gi_report.py reads 'same paper')."""
    if type_ in ("cross-author copying", "data copying", "duplicate publication", "text recycling"):
        return "copying"
    if type_ in ("same paper", "legitimate reuse", "unsure"):
        return type_
    return "not copying"


def ordered_choices(db):
    """CHOICES, most used first (your reviews plus the agent's); ties keep the documented order."""
    counts = {}
    for table in ("reviews", "agent_reviews"):
        for d, t, n in db.execute(f"select decision, type, count(*) from {table} group by 1, 2"):
            counts[(d, t)] = counts.get((d, t), 0) + n
    return sorted(CHOICES, key=lambda c: -counts.get(c[:2], 0)), counts


def build_note(reasons, text):
    """The saved note: the reasons, then your own words."""
    return "; ".join(list(reasons) + ([text.strip()] if text and text.strip() else []))


def same_year(pair):
    """The publication year when both papers have the same one (so which came first can't be told), else None."""
    ya, yb = pair["year_a"], pair["year_b"]
    return ya if ya and ya == yb else None


def same_year_reason(year):
    return f"can't tell which paper came first (both published {year})"


def parse_picks(answer, n):
    """'1 3' / '1,3' -> [0, 2]; None if anything isn't a number from 1 to n."""
    out = []
    for tok in answer.replace(",", " ").split():
        if not tok.isdigit() or not 1 <= int(tok) <= n:
            return None
        if int(tok) - 1 not in out:
            out.append(int(tok) - 1)
    return out


def parse_flags(answer):
    """'m' -> ['paper mill']; None if a key isn't in FLAGS."""
    keys = answer.replace(",", " ").replace(" ", "").lower()
    if any(k not in FLAGS for k in keys):
        return None
    return sorted({FLAGS[k][0] for k in keys})


def pair_flags(db, pair):
    return [r[0] for r in db.execute("select flag from review_flags where pair_lo = ? and pair_hi = ? order by flag",
                                     (pair["pair_lo"], pair["pair_hi"]))]


def save_review(db, pair, decision, type_, severity, note, flags=()):
    db.execute("""insert into reviews (doi_a, doi_b, pair_lo, pair_hi, verdict, decision, type, severity, note,
                                       reviewed_at)
                  values (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                  on conflict (pair_lo, pair_hi) do update set verdict = excluded.verdict,
                    decision = excluded.decision, type = excluded.type, severity = excluded.severity,
                    note = excluded.note, reviewed_at = excluded.reviewed_at""",
               (pair["doi_a"], pair["doi_b"], pair["pair_lo"], pair["pair_hi"], verdict_for(type_), decision,
                type_, severity, note))
    db.execute("delete from review_flags where pair_lo = ? and pair_hi = ?", (pair["pair_lo"], pair["pair_hi"]))
    db.executemany("insert into review_flags values (?, ?, ?, datetime('now'))",
                   [(pair["pair_lo"], pair["pair_hi"], f) for f in flags])
    db.commit()


QUEUE_SQL = """
select f.page_number, f.page, f.pair_lo, f.pair_hi, f.doi_a, f.doi_b, f.year_a, f.year_b, f.library,
       f.paper_a, f.paper_b, f.ft_pct_a, f.ft_pct_b, f.ft_matched_words, f.ft_longest, f.byline,
       r.ai_decision, r.ai_type, r.ai_severity, r.ai_confidence, r.ai_summary, r.ai_checks, r.ai_prompt_version,
       r.human_decision, r.human_type, r.human_severity, r.human_note
from fulltext f join results r using (pair_lo, pair_hi)
where f.ft_verdict = 'real overlap' and f.byline = 'no byline overlap' and f.page_number is not null
order by case when r.ai_decision = 'problem' and r.ai_type in ('cross-author copying', 'data copying') then 0
              when r.ai_decision = 'problem' then 1 else 2 end,
         max(f.ft_pct_a, f.ft_pct_b) desc
"""


def load_queue(db, gi=None, include_reviewed=False, ai_types=None, ai_decisions=None):
    """ai_types / ai_decisions: lists of AI_TYPES / AI_DECISIONS names to keep ('none': the agent hasn't
    reviewed the pair). Ignored with gi."""
    rows = db.execute(QUEUE_SQL).fetchall()
    if gi:
        by_n = {r["page_number"]: r for r in rows}
        missing = [n for n in gi if n not in by_n]
        if missing:
            print(f"no result page for GI-{', GI-'.join(map(str, missing))}")
        return [by_n[n] for n in gi if n in by_n]
    if ai_types:
        keep = {AI_TYPES[t] for t in ai_types}
        rows = [r for r in rows if r["ai_type"] in keep]
    if ai_decisions:
        keep = {AI_DECISIONS[d] for d in ai_decisions}
        rows = [r for r in rows if r["ai_decision"] in keep]
    rows = [r for r in rows if include_reviewed or r["human_decision"] is None]
    import gi_write_pages
    return sorted(rows, key=lambda r: queue_key(r, gi_write_pages.priority_status(r)[0]))


def ai_group(r):
    """0: the agent's cross-author / data copying calls, 1: its other problem calls, 2: the rest (QUEUE_SQL)."""
    if r["ai_decision"] == "problem":
        return 0 if r["ai_type"] in ("cross-author copying", "data copying") else 1
    return 2


def queue_key(r, priority):
    """The queue order. priority: gi_preprints.pair_priority()'s rank (< 0 promoted, > 0 demoted).
    1. demoted pairs (copying BY a preprint/repository upload, or possibly by one) go last overall, "possibly
       by" (1) before "later paper is a preprint" (2): such uploads are most often the authors' own or an
       aggregator's copy, rarely a publishable finding;
    2. otherwise the agent's grouping leads (ai_group()), so a major-publisher pair the agent called "no
       problem" never jumps an ordinary pair it called cross-author copying;
    3. within an agent group, later paper from a major publisher (-2) first, order unknown with one side
       major (-1) next, then ordinary (0);
    4. larger overlap first. Verdicts are untouched; only the order."""
    pct = max(r["ft_pct_a"] or 0, r["ft_pct_b"] or 0)
    return (max(priority, 0), ai_group(r), min(priority, 0), -pct)


def paper_info(lib, pid):
    """(title, absolute PDF path or None) from the verification library."""
    conn = sqlite3.connect(f"file:{lib}?mode=ro", uri=True)
    row = conn.execute("select title, file_path from papers where id = ?", (pid,)).fetchone()
    conn.close()
    if not row:
        return "(not in library)", None
    path = None
    if row[1]:
        path = Path(row[1]) if Path(row[1]).is_absolute() else Path(lib).parent / row[1]
        path = path if path.exists() else None
    return row[0] or "(untitled)", path


def page_path(pages_dir, pair):
    if pair["page"] and (pages_dir / pair["page"]).exists():
        return pages_dir / pair["page"]
    found = sorted(pages_dir.glob(f"GI-{pair['page_number']:02d}-*.html"))
    return found[0] if found else None


def open_files(paths):
    opener = shlex.split(os.environ.get("GI_REVIEW_OPENER", "xdg-open"))
    for p in paths:
        subprocess.Popen(opener + [str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


def wrap(label, text, width=100):
    return textwrap.fill(text or "", width=width, initial_indent=label, subsequent_indent=" " * len(label))


def show_pair(pair, i, total, titles, flags=(), priority=(0, None)):
    pct = lambda v: "?" if v is None else f"{v:.0f}%"
    print("\n" + "=" * 100)
    print(f"GI-{pair['page_number']:02d}   ({i} of {total})   A {pct(pair['ft_pct_a'])} / B {pct(pair['ft_pct_b'])} "
          f"of words shared, {pair['ft_matched_words'] or 0:,} matched, longest run {pair['ft_longest'] or 0:,}")
    for side, (title, pdf) in zip("AB", titles):
        doi, year = pair[f"doi_{side.lower()}"], pair[f"year_{side.lower()}"]
        print(wrap(f"  {side}: ", f"{title} ({year or '?'}) {doi}"))
        if not pdf:
            print("     (no local PDF)")
    rank, note = priority
    if note and rank > 0:
        print(f"  NOTE: {note} -- queued after the other pairs: a preprint/repository upload is most "
              "often the authors' own or an aggregator's copy")
    elif note and rank < 0:
        print(f"  NOTE: {note} -- queued first within the agent's group: a copy in a major journal is the "
              "most consequential finding")
    elif note:
        print(f"  NOTE: {note}")
    import gi_pubpeer
    earlier = pair.get("earlier")
    if earlier in ("a", "b"):
        later = "b" if earlier == "a" else "a"
        url = gi_pubpeer.pubpeer_url(pair[f"doi_{later}"])
        print(f"  PubPeer (later paper, {later.upper()}): {url or 'no DOI'}")
    else:
        urls = [gi_pubpeer.pubpeer_url(pair[f"doi_{s}"]) for s in "ab"]
        print(f"  PubPeer (order unknown): A {urls[0] or 'no DOI'}   B {urls[1] or 'no DOI'}")
    if pair["ai_decision"]:
        pv = pair["ai_prompt_version"] or ""
        who = f"local {pv.split('local:', 1)[1].removesuffix('.gguf')}" if "local:" in pv else "Claude"
        print(f"  AI ({who}): {pair['ai_decision']} / {pair['ai_type']} / {pair['ai_severity']} "
              f"(confidence {pair['ai_confidence'] or '?'})")
        if pair.get("swapped"):
            print("      (the AI's \"Paper A\" is B here and its \"Paper B\" is A: it numbers them the other way)")
        print(wrap("      ", pair["ai_summary"]))
        if pair["ai_checks"]:
            print(wrap("      check: ", pair["ai_checks"]))
    else:
        print("  AI: not reviewed")
    if pair["human_decision"]:
        print(f"  YOU (saved): {pair['human_decision']} / {pair['human_type']} / {pair['human_severity']}")
        print(wrap("      ", pair["human_note"]))
    if flags:
        print(f"  YOUR FLAGS (saved): {', '.join(flags)}")


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


def edit_note(initial=""):
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as f:
        f.write(initial)
        name = f.name
    subprocess.run(shlex.split(os.environ.get("EDITOR", "nano")) + [name])
    text = Path(name).read_text()
    os.unlink(name)
    return " ".join(text.split())


def ask_severity_and_reasons(pair, decision):
    """Enter gives DEFAULT_SEVERITY, whatever the agent said: high also writes the emails to the victims and the
    institution (gi_report_case.audiences()), so it has to be chosen."""
    severity = "none"
    if decision != "no problem":
        default = DEFAULT_SEVERITY
        ai = f", AI: {pair['ai_severity']}" if pair["ai_severity"] in SEVERITIES.values() else ""
        while True:
            ans = ask(f"Severity  l)ow  m)edium  h)igh  [Enter: {default}{ai}] ").lower()
            if not ans:
                severity = default
                break
            if ans[:1] in SEVERITIES:
                severity = SEVERITIES[ans[:1]]
                break

    reasons = REASONS[decision]
    print("Reasons (numbers, e.g. '1 3'; Enter for none):")
    for k, r in enumerate(reasons, 1):
        print(f"  {k}  {r}")
    while True:
        picked = parse_picks(ask("> "), len(reasons))
        if picked is not None:
            break
        print(f"  numbers from 1 to {len(reasons)}")
    return severity, [reasons[i] for i in picked]


def review_one(pair, choices, counts, saved_flags=()):
    """Ask for one verdict. Returns (decision, type, severity, note, flags, agreed), agreed True when it was 'a'
    (agree with the AI), or 'skip' / 'quit' / 'reopen' / 'pdfs' / 'pubpeer'."""
    ai = (pair["ai_decision"], pair["ai_type"]) if pair["ai_decision"] else None
    print("\nWhat do you think?")
    for k, (d, t, desc) in enumerate(choices, 1):
        mark = "  <- AI" if ai == (d, t) else ""
        print(f"  {k:>2}  {d + ': ' + t:<34} {desc} [{counts.get((d, t), 0)}]{mark}")
    print(f"   c  {QUICK[0]}: {QUICK[1]}, {QUICK[2]}, {QUICK[3]} (skips severity and reasons)")
    keys = "   a  agree with the AI" if ai and ai[1] else ""
    print(f"{keys}   o  reopen the page   p  open the PDFs   t  report files   s  skip   q  quit")
    quick = agreed = False
    while True:
        ans = ask("> ").lower()
        if ans == "c":
            decision, type_, severity, reason = QUICK
            reasons, quick = [reason], True
            break
        if ans in ("q", "s", "o", "p", "t"):
            return {"q": "quit", "s": "skip", "o": "reopen", "p": "pdfs", "t": "pubpeer"}[ans]
        if ans == "a" and ai and ai[1]:
            decision, type_ = ai
            agreed = True
            break
        if ans.isdigit() and 1 <= int(ans) <= len(choices):
            decision, type_ = choices[int(ans) - 1][:2]
            break
        print("  a number from the list, or c / a / o / p / t / s / q")

    converted = False
    year = same_year(pair)
    if decision == "problem" and year:
        # same publication year: which paper copied can't be told from the dates, so it's recorded as unsure,
        # the type kept as the best guess
        print(f"  Both papers are from {year}: saving as unsure ({type_} as the best guess) for that reason.")
        decision, converted = "unsure", True
    if quick:
        reasons = reasons + ([same_year_reason(year)] if converted else [])
    else:
        severity, reasons = ask_severity_and_reasons(pair, decision)
        if converted:
            reasons = [same_year_reason(year)] + reasons

    flags = list(saved_flags)
    keys = "  ".join(f"{k}) {desc}" for k, (_, desc) in FLAGS.items())
    now = f"; saved: {', '.join(saved_flags)}, '-' clears" if saved_flags else ""
    while decision == "unsure":  # the paper-mill question only comes with an unsure verdict
        ans = ask(f"Flags  {keys}  [Enter: {'keep' if saved_flags else 'none'}{now}] ")
        if not ans:
            flags = list(saved_flags)
            break
        if ans == "-":
            flags = []
            break
        flags = parse_flags(ans)
        if flags is not None:
            break
        print(f"  one of: {', '.join(FLAGS)}")

    text = ask("Note (optional; Enter to skip, 'e' for $EDITOR): ")
    if text.lower() == "e":
        text = edit_note()
    return decision, type_, severity, build_note(reasons, text), flags, agreed


def pubpeer_page(db, pair, open_it=True, severity=None):
    """Write the pair's report files (gi_report_case.py: the PubPeer page, the attachment and the draft emails in
    data/dupe_publisher/GI-NN/, the page and index on the project site) and open the PubPeer page; a
    failure is printed, never fatal. severity picks the emails (None: the saved review's, else medium)."""
    import gi_report_case
    print("  writing the report files ...")
    try:
        paths = gi_report_case.write_report(db, pair, sender=gi_report_case.git_email(), severity=severity)
    except Exception as e:
        print(f"  report failed: {e}")
        return
    print(f"  report: {paths['pubpeer'].parent}/  (site page: {paths.get('site', 'none')})")
    print("  emails: " + ", ".join(k.split(" ", 1)[1] for k in paths if k.startswith("email ")))
    if open_it:
        open_files([paths["pubpeer"]])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--pages-dir", default=str(gi_db.DATA / "fulltext_pages_s16"))
    ap.add_argument("--gi", type=lambda s: [int(x) for x in s.split(",")], help="page numbers, e.g. 12,40")
    ap.add_argument("--ai-type", type=lambda v: v.split(","), default=[],
                    help="only pairs the agent gave these types, comma-separated: " + ", ".join(AI_TYPES))
    ap.add_argument("--ai-decision", type=lambda v: v.split(","), default=[],
                    help="only pairs with these agent decisions: " + ", ".join(AI_DECISIONS))
    ap.add_argument("--include-reviewed", action="store_true", help="also pairs you've already reviewed")
    ap.add_argument("--pdfs", action="store_true", help="also open both PDFs for every pair (p at the prompt "
                                                        "opens them for one pair)")
    ap.add_argument("--no-open", action="store_true", help="don't open anything automatically")
    args = ap.parse_args()
    pages_dir = Path(args.pages_dir)

    db = sqlite3.connect(args.db, timeout=300)
    db.row_factory = sqlite3.Row
    if not db.execute("select 1 from sqlite_master where name = 'review_flags'").fetchone():
        db.executescript(FLAGS_DDL)
    for name, value, known in (("--ai-type", args.ai_type, AI_TYPES), ("--ai-decision", args.ai_decision, AI_DECISIONS)):
        bad = [v for v in value if v not in known]
        if bad:
            ap.error(f"{name}: unknown {', '.join(bad)} (use: {', '.join(known)})")
    queue = load_queue(db, args.gi, args.include_reviewed, args.ai_type, args.ai_decision)
    if not queue:
        print("nothing to review")
        return
    choices, counts = ordered_choices(db)
    saved = 0
    for i, pair in enumerate(queue, 1):
        import gi_write_pages
        # A / B as the page shows them (earlier paper on the left), not fulltext's own order
        shown = gi_write_pages.oriented(pair, gi_write_pages.page_order(pair["library"], pair["paper_a"],
                                                                       pair["paper_b"])[0])
        shown["swapped"] = shown["paper_a"] != pair["paper_a"]
        e = gi_write_pages.earlier_paper(pair["library"], shown["paper_a"], shown["paper_b"])
        shown["earlier"] = "a" if e == shown["paper_a"] else "b" if e == shown["paper_b"] else None
        titles = [paper_info(pair["library"], shown["paper_a"]), paper_info(pair["library"], shown["paper_b"])]
        page = page_path(pages_dir, pair)
        pdfs = [p for p in [titles[0][1], titles[1][1]] if p]
        saved_flags = pair_flags(db, pair)
        show_pair(shown, i, len(queue), titles, saved_flags, gi_write_pages.priority_status(pair))
        print(f"  page: {page or '(no page in ' + str(pages_dir) + ')'}")
        for side, pdf in zip("AB", pdfs):
            print(f"  PDF {side}: {pdf}")
        if not args.no_open:
            open_files(([page] if page else []) + (pdfs if args.pdfs else []))
        while True:
            out = review_one(pair, choices, counts, saved_flags)
            if out in ("reopen", "pdfs"):
                open_files(pdfs if out == "pdfs" else [page] if page else [])
                continue
            if out == "pubpeer":
                pubpeer_page(db, pair, not args.no_open)
                continue
            break
        if out == "quit":
            break
        if out == "skip":
            continue
        decision, type_, severity, note, flags, agreed = out
        print(wrap("Save: ", f"{decision} / {type_} / {severity}" + (f" [{', '.join(flags)}]" if flags else "")
                   + (f" -- {note}" if note else "")))
        if ask("[Enter to save, n to discard (the pair stays unreviewed)] ").lower().startswith("n"):
            continue
        save_review(db, pair, decision, type_, severity, note, flags)
        counts[(decision, type_)] = counts.get((decision, type_), 0) + 1
        saved += 1
        print(f"saved GI-{pair['page_number']:02d}")
        # agreeing with the AI's problem call also counts when the same year turned it into unsure
        if decision == "problem" or (agreed and pair["ai_decision"] == "problem"):
            emails = "all four emails" if severity == "high" else "email to the journal"
            if ask(f"[Enter for the report files (PubPeer page, {emails}, site page), s to skip them] ").lower().startswith("s"):
                print("  report skipped (t on this pair later writes it)")
            else:
                pubpeer_page(db, pair, not args.no_open, severity=severity)
    print(f"\n{saved} review(s) saved")
    if saved:
        import gi_write_pages
        ro = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
        ro.row_factory = sqlite3.Row
        gi_write_pages.write_index(ro, pages_dir)


if __name__ == "__main__":
    main()

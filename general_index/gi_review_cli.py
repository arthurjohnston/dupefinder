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
  5. a note in your own words, only if you added ' n' to the verdict ('3 n', 'c n'; 'e' opens $EDITOR).
f at step 1 (shown when there are any) goes through the gi-cases agent's proposed findings for any case
(data/dupe_publisher/GI-NN/findings.md, gi_case_findings.py) and regenerates the report files of the cases
you changed, so the findings you include are in the PubPeer Markdown and the emails.

Three quick keys at step 1 skip 2-4: c = problem, cross-author copying, medium; e = unsure, flagged not
qualified (outside your expertise, e.g. higher-level math); r = problem (the agent's type, else cross-author
copying), flagged already retracted, and no report files.
It saves to `reviews` in gi.sqlite3 exactly like REVIEW_COMMANDS.md's gi_review (same vocabulary, same
verdict column, replaces your earlier review of the pair), the flags to `review_flags` (replacing the
pair's earlier flags), and rewrites the pages' index.html on exit.

Queue (default): pages you haven't reviewed, the agent's cross-author / data copying calls first, then the
rest of its problem calls, then everything else; within each of those, pairs whose later (copying) paper is
from a major publisher first (gi_preprints.pair_priority(), major_publisher_doi_prefixes.tsv; a known
paper-mill venue inside one ranks ordinary), then larger overlap first. --order overlap puts larger overlap
first within the first group instead, the publisher only breaking ties. Pairs whose later (copying) paper is
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
new tab, unless you answer s ("skip") when asked; t at the prompt does the same for any pair. Then, each step
skippable (report_flow()):
  - the site pages are pushed to main (gi_publish_site.py), so the emails' link works;
  - the gi-cases agent's findings. The agent starts in the background (BackgroundPrep, headless claude -p)
    as soon as a pair's page opens, unless --no-agent or the case already has a findings.md, so it reads
    both PDFs while you do; it's stopped if you don't report the pair. It may only read, use read-only
    sqlite3 / pdftotext and the web, and write in the case's folder and journal_contacts.json
    (headless_tools()). Here the tool waits for it if it's still going (Ctrl-C skips it), prints its
    summary, and d resumes its session in this terminal to talk it through (/exit comes back). Without a
    background run, Enter opens the agent interactively instead. Either way it runs on your claude.ai
    subscription only: nothing starts unless `claude auth status` says claude.ai, and API-key variables
    are removed from its environment.
  - you tick its findings (y include, n leave out, e edit, Enter pending, q stop) and the report files are
    regenerated with the included ones in the PubPeer Markdown and the emails;
  - you paste your PubPeer post's address once it's up, and the emails link to it;
  - o opens the drafts as Thunderbird compose windows.

Usage: gi_review_cli.py [--gi 12,40] [--ai-type cross-author,data] [--ai-decision problem] [--include-reviewed] [--order publisher|overlap] [--pdfs] [--no-open] [--no-agent] [--pages-dir data/fulltext_pages_s16]
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
         "e": ("not qualified", "outside my expertise, not qualified to judge it (e.g. higher-level math)"),
         "r": ("already retracted", "the copying paper is already retracted, nothing to report")}

FLAGS_DDL = """create table if not exists review_flags (   -- your extra judgements on a pair, beside the verdict
    pair_lo text, pair_hi text,
    flag text,                    -- 'paper mill': you think both papers came from a paper mill;
                                  -- 'not qualified': you can't judge the content (e.g. higher-level math);
                                  -- 'already retracted': real copying, but the copying paper is retracted
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


def load_queue(db, gi=None, include_reviewed=False, ai_types=None, ai_decisions=None, order="publisher"):
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
    return sorted(rows, key=lambda r: queue_key(r, gi_write_pages.priority_status(r)[0], order))


def ai_group(r):
    """0: the agent's cross-author / data copying calls, 1: its other problem calls, 2: the rest (QUEUE_SQL)."""
    if r["ai_decision"] == "problem":
        return 0 if r["ai_type"] in ("cross-author copying", "data copying") else 1
    return 2


def queue_key(r, priority, order="publisher"):
    """The queue order. priority: gi_preprints.pair_priority()'s rank (< 0 promoted, > 0 demoted).
    1. demoted pairs (copying BY a preprint/repository upload, or possibly by one) go last overall, "possibly
       by" (1) before "later paper is a preprint" (2): such uploads are most often the authors' own or an
       aggregator's copy, rarely a publishable finding;
    2. otherwise the agent's grouping leads (ai_group()), so a major-publisher pair the agent called "no
       problem" never jumps an ordinary pair it called cross-author copying;
    3. within an agent group, later paper from a major publisher (-2) first, order unknown with one side
       major (-1) next, then ordinary (0);
    4. larger overlap first.
    order="overlap" swaps 3 and 4 in the first group (the agent's cross-author / data copying calls): larger
    overlap first there, the publisher only breaking ties. Verdicts are untouched; only the order."""
    pct = max(r["ft_pct_a"] or 0, r["ft_pct_b"] or 0)
    group = ai_group(r)
    if order == "overlap" and group == 0:
        return (max(priority, 0), group, -pct, min(priority, 0))
    return (max(priority, 0), group, min(priority, 0), -pct)


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


def split_note_suffix(ans):
    """'3 n' -> ('3', True), 'c' -> ('c', False): a trailing ' n' on a verdict asks for a note."""
    head, sep, tail = ans.strip().rpartition(" ")
    if sep and tail == "n":
        return head.strip(), True
    return ans.strip(), False


def review_one(pair, choices, counts, saved_flags=(), prep=None):
    """Ask for one verdict. Returns (decision, type, severity, note, flags, agreed), agreed True when it was 'a'
    (agree with the AI), or 'skip' / 'quit' / 'reopen' / 'pdfs' / 'pubpeer' / 'findings'."""
    ai = (pair["ai_decision"], pair["ai_type"]) if pair["ai_decision"] else None
    print("\nWhat do you think?")
    for k, (d, t, desc) in enumerate(choices, 1):
        mark = "  <- AI" if ai == (d, t) else ""
        print(f"  {k:>2}  {d + ': ' + t:<34} {desc} [{counts.get((d, t), 0)}]{mark}")
    print(f"   c  {QUICK[0]}: {QUICK[1]}, {QUICK[2]}, {QUICK[3]} (skips severity and reasons)")
    print(f"   e  unsure, flagged {FLAGS['e'][0]}: {FLAGS['e'][1]} (skips severity, reasons and flags)")
    print(f"   r  problem, flagged {FLAGS['r'][0]}: {FLAGS['r'][1]} (skips severity, reasons, flags and report)")
    keys = "   a  agree with the AI" if ai and ai[1] else ""
    print(f"{keys}   o  reopen the page   p  open the PDFs   t  report it   s  skip   q  quit")
    import gi_case_findings
    waiting = gi_case_findings.pending_cases()
    if waiting:
        print("   f  check the agent's findings: " + ", ".join(f"{c} ({n})" for c, n in waiting))
    print("  add ' n' to a verdict to leave a note (e.g. '3 n', 'c n'); without it you aren't asked for one")
    if prep and prep.status():
        print("  " + prep.status())
    quick = agreed = False
    preset = None  # the flag a quick key sets ('e' / 'r'); the flags step is then skipped
    while True:
        ans, want_note = split_note_suffix(ask("> ").lower())
        if ans == "g" and prep:
            calls = prep.tool_calls()
            print("  " + prep.status())
            for c in calls[-12:]:
                print(f"    {c}")
            continue
        if ans == "c":
            decision, type_, severity, reason = QUICK
            reasons, quick = [reason], True
            break
        if ans == "e":
            decision, type_, severity, reasons = "unsure", "unsure", "none", []
            quick, preset = True, FLAGS["e"][0]
            break
        if ans == "r":
            # the agent's problem type if it has one: the copying is real, it's just already dealt with
            type_ = ai[1] if ai and ai[0] == "problem" and ai[1] else QUICK[1]
            decision, severity, reasons = "problem", DEFAULT_SEVERITY, []
            quick, preset = True, FLAGS["r"][0]
            break
        if ans in ("q", "s", "o", "p", "t", "f"):
            return {"q": "quit", "s": "skip", "o": "reopen", "p": "pdfs", "t": "pubpeer", "f": "findings"}[ans]
        if ans == "a" and ai and ai[1]:
            decision, type_ = ai
            agreed = True
            break
        if ans.isdigit() and 1 <= int(ans) <= len(choices):
            decision, type_ = choices[int(ans) - 1][:2]
            break
        print("  a number from the list, or c / e / r / a / o / p / t / g / s / q")

    converted = False
    year = same_year(pair)
    if decision == "problem" and year and not preset:  # a retraction already settled who copied
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

    flags = sorted(set(saved_flags) | {preset}) if preset else list(saved_flags)
    keys = "  ".join(f"{k}) {desc}" for k, (_, desc) in FLAGS.items())
    now = f"; saved: {', '.join(saved_flags)}, '-' clears" if saved_flags else ""
    while decision == "unsure" and not preset:  # the paper-mill question only comes with an unsure verdict
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

    text = ask("Note ('e' for $EDITOR): ") if want_note else ""
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
        return None
    print(f"  report: {paths['pubpeer'].parent}/  (site page: {paths.get('site', 'none')})")
    print("  emails: " + ", ".join(k.split(" ", 1)[1] for k in paths if k.startswith("email ")))
    if open_it:
        open_files([paths["pubpeer"]])
    return paths


# environment variables that would make claude bill an API account instead of your claude.ai subscription
API_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
           "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
AGENT_PROMPT = ("Prep {case}: follow \"Prep a case\" in your instructions. I'm running you from gi_review_cli.py: "
                "when we're done talking it through I'll /exit, and the review tool then has me tick the findings "
                "you wrote to findings.md.")


def agent_env():
    return {k: v for k, v in os.environ.items() if k not in API_ENV}


def subscription_problem(claude="claude"):
    """None when Claude Code is logged in with a claude.ai subscription, else why the agent won't start."""
    import json
    import shutil
    if not shutil.which(claude):
        return "claude (Claude Code) isn't on PATH"
    try:
        st = json.loads(subprocess.run([claude, "auth", "status"], capture_output=True, text=True,
                                       env=agent_env(), timeout=60).stdout)
    except (subprocess.TimeoutExpired, ValueError) as e:
        return f"couldn't read `claude auth status`: {e}"
    if not st.get("loggedIn"):
        return "Claude Code isn't logged in"
    if st.get("authMethod") != "claude.ai":
        return f"Claude Code is using {st.get('authMethod')!r}, not your claude.ai subscription"
    return None


def run_agent(case, claude="claude"):
    """The gi-cases agent as an ordinary interactive Claude Code session in this terminal, started on the case
    (you approve its actions as usual; /exit returns here). Always your subscription, never an API key."""
    why = subscription_problem(claude)
    if why:
        print(f"  agent not started: {why}")
        return False
    print(f"  opening the gi-cases agent on {case} (/exit comes back here) ...")
    subprocess.run([claude, "--agent", "gi-cases", AGENT_PROMPT.format(case=case)],
                   cwd=Path(__file__).resolve().parent, env=agent_env())
    return True


HEADLESS_PROMPT = ("Prep {case}: follow \"Prep a case\" in your instructions. This is a background run while the "
                   "user looks at the comparison page: nobody can answer questions or approve anything, so put "
                   "anything uncertain under ## Questions in findings.md. Afterwards the user may resume this "
                   "session to talk it through. In this run only these tools work: Read, Glob, Grep, web search "
                   "and fetch, `sqlite3 -readonly` (not python) for the databases, pdftotext and pdfinfo, and "
                   "writing in the case's folder and journal_contacts.json. Don't run gi_report_case.py: the "
                   "review tool writes the report files when the user confirms. End with one line saying what "
                   "you concluded, e.g. '4 findings proposed, contacts found' or 'not a case: already "
                   "retracted'.")


def headless_tools(case):
    """What the background agent may do; anything else is refused (--permission-mode dontAsk): read files,
    read-only sqlite3, pdftotext / pdfinfo, web search and fetch, and write only in the case's folder and the
    journal contacts file. Nothing that sends, posts, commits or writes to gi.sqlite3."""
    import gi_case_findings
    d = gi_case_findings.case_dir(case).resolve()
    contacts = (gi_db.DATA / "journal_contacts.json").resolve()
    return ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "Bash(sqlite3 -readonly:*)", "Bash(pdftotext:*)",
            "Bash(pdfinfo:*)", f"Edit(/{d}/**)", f"Write(/{d}/**)", f"Edit(/{contacts})"]


class BackgroundPrep:
    """The gi-cases agent analysing a case headless (claude -p) while you look at its comparison page, so its
    findings are ready when you've decided. Your claude.ai subscription only (subscription_problem()). Output:
    GI-NN/prep-stream.jsonl (claude's stream-json events as they happen: its tool calls, then the result with
    the session id to resume) and GI-NN/prep.log (stderr)."""

    def __init__(self, case, claude="claude"):
        import gi_case_findings
        self.case, self.claude, self.proc = case, claude, None
        self.dir = gi_case_findings.case_dir(case)
        self.out = self.dir / "prep-stream.jsonl"
        self.started = None

    def start(self):
        why = subscription_problem(self.claude)
        if why:
            print(f"  agent prep not started: {why}")
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        cmd = [self.claude, "-p", HEADLESS_PROMPT.format(case=self.case), "--agent", "gi-cases",
               "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
               "--allowedTools", *headless_tools(self.case)]
        with open(self.out, "w") as out, open(self.dir / "prep.log", "a") as log:
            self.proc = subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent, env=agent_env(),
                                         stdin=subprocess.DEVNULL, stdout=out, stderr=log, start_new_session=True)
        import time
        self.started = time.time()
        print(f"  agent prep started on {self.case} in the background (g at the menu shows what it's doing)")
        return True

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        """Stop it (a pair you didn't call a problem doesn't need it)."""
        if self.running():
            import signal
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait()

    def wait(self):
        """Wait for it to finish; Ctrl-C stops waiting (and the agent). True when it finished."""
        if self.proc is None:
            return False
        if self.running():
            print("  waiting for the agent's analysis to finish (Ctrl-C to skip it) ...")
            try:
                self.proc.wait()
            except KeyboardInterrupt:
                self.stop()
                print("  agent stopped")
                return False
        return True

    def events(self):
        import json
        try:
            lines = self.out.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass   # the line being written
        return out

    def result(self):
        """claude's final result event ({} until there is one)."""
        res = [e for e in self.events() if e.get("type") == "result"]
        return res[-1] if res else {}

    def tool_calls(self):
        """['Read  .../a.pdf', 'WebSearch  ...', ...]: what it has done so far, in order."""
        calls = []
        for e in self.events():
            if e.get("type") != "assistant":
                continue
            for c in (e.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    what = next((str(inp[k]) for k in ("file_path", "command", "query", "url", "pattern")
                                 if inp.get(k)), "")
                    calls.append(f"{c.get('name')}  {' '.join(what.split())[:100]}")
        return calls

    def status(self):
        """One line for the menu."""
        import time
        if self.proc is None:
            return ""
        mins = int((time.time() - (self.started or time.time())) // 60)
        n = len(self.tool_calls())
        if self.running():
            return f"agent: working on {self.case} ({mins} min, {n} step(s) so far; g shows them)"
        res = self.result()
        if res and not res.get("is_error"):
            said = (res.get("result") or "").strip().splitlines()
            took = round((res.get("duration_ms") or 0) / 60000, 1)
            return f"agent: finished {self.case} in {took} min: {said[-1][:160] if said else 'no summary'}"
        return f"agent: {self.case} stopped without a result (see {self.dir / 'prep.log'})"


def publish_site(case):
    """Push the site pages to main (gi_publish_site.py), printing what happened; never fatal."""
    import gi_publish_site
    try:
        sha = gi_publish_site.publish([case])
        print(f"  site: pushed {sha} to main" if sha else "  site: main already has it")
    except Exception as e:
        print(f"  site push failed: {getattr(e, 'stderr', '') or e}")


def report_flow(db, pair, open_it=True, severity=None, prep=None):
    """Everything for reporting the pair, in order: the report files (PubPeer page opened), the gi-cases agent
    to prep it, ticking its findings (the files regenerated if you change any), your PubPeer post's address,
    and the drafts opened in Thunderbird. Every step but the first can be skipped with Enter / s."""
    import gi_case_findings
    import gi_report_case
    paths = pubpeer_page(db, pair, open_it, severity=severity)
    if not paths:
        return
    case = f"GI-{pair['page_number']:02d}"
    if paths.get("site"):
        publish_site(case)
    if prep and prep.wait():
        res = prep.result()
        if res.get("is_error") or not res:
            print(f"  agent prep failed: {(res.get('result') or 'no result, see ' + str(prep.dir / 'prep.log'))[:300]}")
        else:
            print(wrap("  agent: ", " ".join((res.get("result") or "").split())[:600]))
        sid = res.get("session_id")
        if sid and not ask("[Enter: go to its findings, d: discuss them with the agent first] ").lower().startswith("d"):
            sid = None
        if sid:
            print("  resuming the agent's session (/exit comes back here) ...")
            subprocess.run([prep.claude, "--resume", sid, "--agent", "gi-cases"],
                           cwd=Path(__file__).resolve().parent, env=agent_env())
    elif not ask(f"[Enter: open the agent to prep {case} (findings, contacts), s: skip] ").lower().startswith("s"):
        run_agent(case)
    if gi_case_findings.counts(case)["pending"] and gi_case_findings.review(case, ask=ask):
        paths = pubpeer_page(db, pair, open_it, severity=severity) or paths   # the Markdown now has them
    have = gi_case_findings.pubpeer_url(case)
    url = ask(f"PubPeer post's address once it's up [Enter: {'keep ' + have if have else 'later (t asks again)'}] ")
    if url:
        try:
            gi_case_findings.save_pubpeer_url(case, url)
            paths = pubpeer_page(db, pair, False, severity=severity) or paths   # the emails now link it
        except ValueError as e:
            print(f"  {e}: not saved")
    emls = [p for k, p in paths.items() if k.startswith("email ")]
    if emls and ask("[o: open the drafts in Thunderbird, Enter: not now] ").lower().startswith("o"):
        try:
            gi_report_case.open_drafts(emls)
        except Exception as e:
            print(f"  couldn't open the drafts: {e}")


def check_findings(db, open_it=True):
    """Go through every case's pending findings.md items (gi_case_findings.review()), regenerating the report
    files of each case you changed."""
    import gi_case_findings
    for case, _ in gi_case_findings.pending_cases():
        if gi_case_findings.review(case, ask=ask):
            f = db.execute("select * from fulltext where page_number = ?", (int(case[3:]),)).fetchone()
            if f is not None:
                pubpeer_page(db, f, open_it)


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
    ap.add_argument("--order", choices=("publisher", "overlap"), default="publisher",
                    help="within the agent's cross-author / data copying group: major publisher first, then "
                         "overlap (default), or overlap first, then publisher")
    ap.add_argument("--no-agent", action="store_true", help="don't start the agent's analysis in the background "
                                                             "when a pair opens")
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
    queue = load_queue(db, args.gi, args.include_reviewed, args.ai_type, args.ai_decision, args.order)
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
        import gi_case_findings
        case = f"GI-{pair['page_number']:02d}"
        prep = None
        if not args.no_agent and not gi_case_findings.findings_path(case).exists():
            prep = BackgroundPrep(case)
            if not prep.start():
                prep = None
        while True:
            out = review_one(pair, choices, counts, saved_flags, prep)
            if out in ("reopen", "pdfs"):
                open_files(pdfs if out == "pdfs" else [page] if page else [])
                continue
            if out == "pubpeer":
                report_flow(db, pair, not args.no_open, prep=prep)
                prep = None
                continue
            if out == "findings":
                check_findings(db, not args.no_open)
                continue
            break
        reporting = out not in ("quit", "skip") and FLAGS["r"][0] not in out[4] and (
            out[0] == "problem" or (out[5] and pair["ai_decision"] == "problem"))
        if prep and not reporting:   # not going to be reported: the analysis isn't needed
            prep.stop()
            prep = None
        if out == "quit":
            break
        if out == "skip":
            continue
        decision, type_, severity, note, flags, agreed = out
        print(wrap("Save: ", f"{decision} / {type_} / {severity}" + (f" [{', '.join(flags)}]" if flags else "")
                   + (f" -- {note}" if note else "")))
        if ask("[Enter to save, n to discard (the pair stays unreviewed)] ").lower().startswith("n"):
            if prep:
                prep.stop()
            continue
        save_review(db, pair, decision, type_, severity, note, flags)
        counts[(decision, type_)] = counts.get((decision, type_), 0) + 1
        saved += 1
        print(f"saved GI-{pair['page_number']:02d}")
        # agreeing with the AI's problem call also counts when the same year turned it into unsure
        if FLAGS["r"][0] in flags:
            print("  already retracted: no report files (t on this pair still writes them)")
        elif decision == "problem" or (agreed and pair["ai_decision"] == "problem"):
            emails = "all four emails" if severity == "high" else "email to the journal"
            if ask(f"[Enter to report it (PubPeer page, {emails}, site page pushed, then the agent's findings), "
                   f"s to skip] ").lower().startswith("s"):
                print("  report skipped (t on this pair later does it)")
                if prep:
                    prep.stop()
            else:
                report_flow(db, pair, not args.no_open, severity=severity, prep=prep)
    print(f"\n{saved} review(s) saved")
    if saved:
        import gi_write_pages
        ro = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
        ro.row_factory = sqlite3.Row
        gi_write_pages.write_index(ro, pages_dir)


if __name__ == "__main__":
    main()

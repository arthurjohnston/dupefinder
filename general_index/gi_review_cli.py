"""Go through the General Index result pages one pair at a time and record your verdict.

For each pair: prints the overlap figures and the agent's verdict, opens the comparison page (xdg-open, or
$GI_REVIEW_OPENER; --pdfs opens both PDFs too, or press p at the prompt), then asks
  1. what you think of it: decision and type, offered as one numbered list, most common first (counted over
     your reviews and the agent's);
  2. severity (problem / unsure only; Enter gives medium, the agent's is shown; high also writes the emails
     to the earlier authors, their publisher and the later authors' institution, see below);
  3. for an unsure verdict, flags: m = you think both papers came from a paper mill, e = not qualified to
     judge it (higher-level math, say);
  4. a note in your own words, only if you added ' n' to the verdict ('3 n', 'c n'; 'e' opens $EDITOR).
The verdict is saved as soon as it's complete, with no confirmation step (--gi NN --include-reviewed to change
one). No reasons are asked for: a no-problem type that is its own reason (same paper, boilerplate, shared
citations, artifact: TYPE_REASON) saves that reason, and a quick key saves its own.
f at step 1 goes through the gi-cases agent's proposed findings for the pair on screen
(data/dupe_publisher/GI-NN/findings.md, gi_case_findings.py; all of them when none is still pending, so you
can change one), then offers the other cases' pending ones. A case already reported gets its report files
rewritten after a change, so the findings you include are in the PubPeer Markdown and the emails; one not
reported yet is left alone until it is.

Four quick keys at step 1 skip 2-4: c = problem, cross-author copying, medium; e = unsure, flagged not
qualified (outside your expertise, e.g. higher-level math); r = problem (the agent's type, else cross-author
copying), flagged already retracted, and no report files; w = problem, cross-author copying, low, flagged
not worth reporting: real copying too minor to complain about, so no report files, and the site page is
published marked not reported (as p does at the report prompt).
Self-author copying (a problem verdict of type text recycling or duplicate publication, SELF_TYPES) defaults
to low at the severity step and isn't reported or published: no report prompt and no site page, and the
agent isn't started on a pair the AI gave one of those types. t on the pair still reports it.
It saves to `reviews` in gi.sqlite3 exactly like REVIEW_COMMANDS.md's gi_review (same vocabulary, same
verdict column, replaces your earlier review of the pair), the flags to `review_flags` (replacing the
pair's earlier flags), and rewrites the pages' index.html on exit.

Queue (--order publisher): pages you haven't reviewed, the agent's cross-author / data copying calls first, then the
rest of its problem calls, then everything else; within each of those, pairs whose later (copying) paper is
from a major publisher first (gi_preprints.pair_priority(), major_publisher_doi_prefixes.tsv; a known
paper-mill venue inside one ranks ordinary), then larger overlap first. --order overlap puts larger overlap
first within the first group instead, the publisher only breaking ties. Pairs whose later (copying) paper is
a preprint or repository upload (gi_preprints.py, preprint_doi_prefixes.tsv) go after all of that, still
in the queue, with a note: such uploads are most often the authors' own or an aggregator's copy, rarely a
publishable finding. Pairs where the order is unknown and one side is a preprint go just before them.
--order affiliation (the default since 2026-10-17; --order publisher for the order above alone) ranks by who
the later paper's authors are now (gi_affiliations.py: an author still
publishing, at a US or European university, first; then at a university elsewhere; then no data; then no author
active in academia), within the agent's problem calls and within the rest separately; it fetches any missing
OpenAlex/ORCID data first. --us-europe-only keeps only the first of those. --no-medical drops pairs where
either paper's OpenAlex primary topic is in the Health Sciences domain (medicine, nursing, dentistry, health
professions); a paper with no cached OpenAlex record (gi_metadata.py fetch) doesn't count. --order professor ranks by the
authors above everything else: a later paper with an author whose ORCID lists a current (not emeritus)
professor/lecturer post at a US/European institution first, then the affiliation ranks, the agent's calls and
the publisher order holding within each; only authors with an ORCID record can be found this way. Each pair
shows its later paper's authors' line whenever the data is cached (gi_affiliations.py fetch). Pages whose
later paper is the later paper of another page too (one article copied from several earlier ones) are kept
together in any order, where the first of them falls, and each says which others share it and what you
decided on those (later_paper_groups()). --gi 12,40 for specific pages
(reviewed or not); --include-reviewed to go back over yours too. --ai-type cross-author,data (or duplicate,
recycling, legitimate, same-paper, third-source, boilerplate, citations, artifact, unsure, none = not reviewed
by the agent) and --ai-decision problem / no-problem / unsure / none keep only those pairs.

Which paper came first: y at step 1 sets it by hand (a / b, u = can't tell, - = back to the years), for two
papers from the same year or when the years have it the wrong way round (a PDF date, a preprint's year). It is
saved in the pair's verification library (earlier_overrides, write_dupe_reports_html.resolve_earlier_id()),
so the comparison page (rewritten at once), the PubPeer page and the emails all follow it. A problem verdict
when the order is unknown asks the same question first; Enter there (can't tell) saves a same-year pair as
unsure (the type kept as the best guess), with "can't tell which paper came first (both published YEAR)" as
a reason.

On the first menu, c is one key for "problem: cross-author copying, medium, most of the paper copied".

PubPeer: each pair shows the PubPeer link of its later (copying) paper (both papers' when the order is
unknown). Saving a problem verdict, or agreeing (a) with the AI's problem verdict (even when the same year
saves it as unsure), reports the pair (report_flow()) unless you answer s ("skip") when asked, or p
("publish only": just the site page, marked not reported, pushed to main; for a pair not worth reporting,
low say, whose comparison should be public), or a ("authors only": the finding published and the earlier
authors told, nothing else -- the site page and their email, no PubPeer page and no journal emails, which
then don't claim the journal was told); t at the prompt does the same for any pair. In order, each
step but the report files skippable:
  - the gi-cases agent's findings. The agent starts in the background (BackgroundPrep, headless claude -p)
    as soon as a pair's page opens, unless --no-agent, so it reads both PDFs while you do; it also starts on
    the next pair in the queue that hasn't been prepped (one run ahead at a time, --no-lookahead to turn
    that off), so that one is usually ready when you get to it; quitting stops a run still going ahead, and it
    starts again when you reach that pair. A case that
    already has a findings.md isn't prepped again: the menu shows what the earlier run concluded and when
    (BackgroundPrep.previous()), and d below resumes that session; it's stopped if you don't report the
    pair. It may only read, use read-only sqlite3 / pdftotext and the web, and write in the case's folder
    and journal_contacts.json (headless_tools()). Here the tool waits for it if it's still going (Ctrl-C
    stops it and reports without it), prints its summary, and d resumes its session in this terminal to
    talk it through (/exit comes back). Without a background run, Enter opens the agent interactively
    instead. Either way it runs on your claude.ai subscription only: nothing starts unless `claude auth
    status` says claude.ai, and API-key variables are removed from its environment. d at the menu talks the
    case through with the agent at any point, verdict or not (discuss()): its session resumed when it has
    one, else a new conversation started from the case's findings.md; /exit comes back to the menu.
  - you tick its findings (y include, n leave out, e edit, Enter pending, q stop);
  - the report files, written once the agent is done or skipped, so they have the findings you included
    (PubPeer Markdown, emails) and the contacts it found, and the PubPeer page opened (gi_report_case.py: in data/dupe_publisher/GI-NN/ the PubPeer
page, with the findings as Markdown and a Copy button, the attachment for the journal, and the draft email
to the later article's journal; a high-severity verdict also gets the drafts to the later authors' institution,
the earlier authors and the earlier publisher;
the comparison page and index on the project site, example_output/general_index/);
  - you paste your PubPeer post's address once it's up, and the emails link to it;
  - the site pages are pushed to main (gi_publish_site.py), after the last rewrite (the emails don't link
    it: the comparison is attached);
  - o opens the drafts as Thunderbird compose windows.
After the agent's summary, and again after you talk it through with it (d), v changes your verdict (the
menu again; the new one replaces the saved one) and s stops there, not reported: nothing has been written
yet at that point. A pair reported with t before its verdict isn't taken through all that again when the
verdict is saved: its emails are just rewritten for the severity you gave. Saving a verdict that isn't a
problem on a pair already reported (--gi NN --include-reviewed) offers t to take it down: the site page and
cases.json entry removed and pushed, the report files moved to GI-NN/withdrawn/ (gi_report_case.take_down());
emails and a PubPeer post already sent are yours to withdraw.

Usage: gi_review_cli.py [--gi 12,40] [--ai-type cross-author,data] [--ai-decision problem] [--include-reviewed] [--order affiliation|professor|publisher|overlap] [--us-europe-only] [--no-medical] [--pdfs] [--no-open] [--no-agent] [--no-lookahead] [--pages-dir data/fulltext_pages_s16] [--sample-rate 8]
                        [--db data/gi.sqlite3]
"""
import argparse
import json
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

# no-problem types that say their own reason: picking one saves that reason with it
TYPE_REASON = {"same paper": "same work under two DOIs / records",
               "boilerplate": "the shared text is boilerplate or references",
               "shared citations": "the shared text is boilerplate or references",
               "artifact": "wrong PDF or extraction error"}

SEVERITIES = {"l": "low", "m": "medium", "h": "high"}

# the one-key verdict on the first menu: decision, type, severity, reason (skips the severity step)
QUICK = ("problem", "cross-author copying", "medium", "most of the paper copied")
DEFAULT_SEVERITY = "medium"

# the same authors reusing their own text: a problem verdict of these types is low by default (Enter at the
# severity step) and isn't reported or published -- no report prompt, no site page, and the agent isn't
# started on a pair the AI gave one of these types. t on the pair still reports it.
SELF_TYPES = ("text recycling", "duplicate publication")
SELF_SEVERITY = "low"

# --ai-type names -> the agent's type
AI_TYPES = {"cross-author": "cross-author copying", "data": "data copying", "duplicate": "duplicate publication",
            "recycling": "text recycling", "legitimate": "legitimate reuse", "same-paper": "same paper",
            "third-source": "shared third source", "boilerplate": "boilerplate", "citations": "shared citations",
            "artifact": "artifact", "unsure": "unsure", "none": None}
AI_DECISIONS = {"problem": "problem", "no-problem": "no problem", "unsure": "unsure", "none": None}

# key -> flag stored in review_flags.flag
FLAGS = {"m": ("paper mill", "both papers look like paper-mill output"),
         "e": ("not qualified", "outside my expertise, not qualified to judge it (e.g. higher-level math)"),
         "r": ("already retracted", "the copying paper is already retracted, nothing to report"),
         "w": ("not worth reporting", "real cross-author copying, too minor to complain about")}

# the w quick key: problem, this type, this severity; no report, the site page published as not reported
NOT_WORTH = ("cross-author copying", "low")

FLAGS_DDL = """create table if not exists review_flags (   -- your extra judgements on a pair, beside the verdict
    pair_lo text, pair_hi text,
    flag text,                    -- 'paper mill': you think both papers came from a paper mill;
                                  -- 'not qualified': you can't judge the content (e.g. higher-level math);
                                  -- 'already retracted': real copying, but the copying paper is retracted;
                                  -- 'not worth reporting': real copying, too minor to report
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


def load_queue(db, gi=None, include_reviewed=False, ai_types=None, ai_decisions=None, order="publisher",
               us_europe_only=False, groups=None, no_medical=False):
    """ai_types / ai_decisions: lists of AI_TYPES / AI_DECISIONS names to keep ('none': the agent hasn't
    reviewed the pair). us_europe_only: only pairs whose later paper has an author active at a US/European
    university (gi_affiliations.py). no_medical: drop pairs with a Health Sciences paper (medical_pairs()).
    Ignored with gi. Pages sharing a later paper (groups:
    later_paper_groups(), computed when not given) are kept together, where the first of them falls."""
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
    if no_medical:
        medical = medical_pairs(db, rows)
        rows = [r for r in rows if pair_key(r) not in medical]
    import gi_write_pages
    aff = {}
    profs = {}
    if order in ("affiliation", "professor") or us_europe_only:
        aff = affiliation_ranks(db, rows)
        if us_europe_only:
            import gi_affiliations
            rows = [r for r in rows if aff[r["pair_lo"], r["pair_hi"]] == gi_affiliations.TARGET]
    if order == "professor":
        profs = professor_pairs(db, rows)
    ranked = sorted(rows, key=lambda r: queue_key(r, gi_write_pages.priority_status(r)[0], order,
                                                  aff.get((r["pair_lo"], r["pair_hi"])),
                                                  bool(profs.get((r["pair_lo"], r["pair_hi"])))))
    return keep_groups_together(ranked, groups if groups is not None else later_paper_groups(db))


MEDICAL_DOMAIN = "Health Sciences"


def medical_pairs(db, rows):
    """The pair keys of rows where either paper's OpenAlex primary topic is in the Health Sciences domain,
    from the records gi_metadata.py caches in oa_work_records. A paper without a cached record isn't medical."""
    if not db.execute("select 1 from sqlite_master where name = 'oa_work_records'").fetchone():
        return set()
    domain = {}
    for r in rows:
        for doi in (r["doi_a"], r["doi_b"]):
            if doi and doi not in domain:
                rec = db.execute("select raw from oa_work_records where doi = ?", (doi,)).fetchone()
                topic = ((json.loads(rec[0]) if rec and rec[0] else None) or {}).get("primary_topic") or {}
                domain[doi] = (topic.get("domain") or {}).get("display_name")
    return {pair_key(r) for r in rows if MEDICAL_DOMAIN in (domain.get(r["doi_a"]), domain.get(r["doi_b"]))}


def pair_key(r):
    return r["pair_lo"], r["pair_hi"]


def later_paper_groups(db, rows=None, earlier=None):
    """{pair key: [page numbers of every page in its group]}, for pages whose later (copying) paper is the
    later paper of another page too -- one article built from several earlier ones, best reviewed together.
    Only pages in a group of two or more are keys. A page whose order is unknown joins the group of either of
    its papers that is another page's later paper (which can join two groups into one). Over every page in
    the queue (QUEUE_SQL), reviewed or not, so a group still shows the members you've done. earlier: as
    gi_write_pages.earlier_side() (the default), 'a' / 'b' / None for a row."""
    if earlier is None:
        import gi_write_pages
        earlier = gi_write_pages.earlier_side
    rows = db.execute(QUEUE_SQL).fetchall() if rows is None else rows
    parent = {pair_key(r): pair_key(r) for r in rows}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    later_of, unknown = {}, []
    for r in rows:
        side = earlier(r)
        if side in ("a", "b"):
            doi = r["doi_b" if side == "a" else "doi_a"].lower()
            if doi in later_of:
                parent[find(pair_key(r))] = find(later_of[doi])
            else:
                later_of[doi] = pair_key(r)
        else:
            unknown.append(r)
    for r in unknown:
        for doi in (r["doi_a"].lower(), r["doi_b"].lower()):
            if doi in later_of:
                parent[find(pair_key(r))] = find(later_of[doi])
    members = {}
    for r in rows:
        members.setdefault(find(pair_key(r)), []).append(r["page_number"])
    return {pair_key(r): sorted(members[find(pair_key(r))]) for r in rows
            if len(members[find(pair_key(r))]) > 1}


def keep_groups_together(queue, groups):
    """The queue with each later_paper_groups() group pulled together where its first member falls, the
    others following in their own queue order."""
    by_page = {}
    for r in queue:
        by_page.setdefault(tuple(groups.get(pair_key(r), ())), []).append(r)
    out, placed = [], set()
    for r in queue:
        if pair_key(r) in placed:
            continue
        group = by_page[tuple(groups[pair_key(r)])] if pair_key(r) in groups else [r]
        out += group
        placed.update(pair_key(g) for g in group)
    return out


def group_line(db, pair, groups):
    """'Same later paper as GI-144 (problem), GI-163' for a page in a later_paper_groups() group, else None."""
    pages = [n for n in groups.get(pair_key(pair), ()) if n != pair["page_number"]]
    if not pages:
        return None
    done = dict(db.execute(f"""select f.page_number, r.human_decision from fulltext f join results r
                               using (pair_lo, pair_hi) where f.page_number in ({','.join('?' * len(pages))})""",
                           pages).fetchall())
    return ", ".join(f"GI-{n:02d}" + (f" ({done[n]})" if done.get(n) else "") for n in pages)


def affiliation_ranks(db, rows):
    """{(pair_lo, pair_hi): gi_affiliations rank of the later paper's authors}, fetching what the cache lacks."""
    import gi_affiliations
    import gi_write_pages
    dois = [d for r in rows for d in (r["doi_a"], r["doi_b"])]
    print(gi_affiliations.Fetcher(db).ensure(dois))
    scores = gi_affiliations.doi_scores(db, dois)
    return {(r["pair_lo"], r["pair_hi"]): gi_affiliations.pair_rank(scores, r["doi_a"], r["doi_b"],
                                                                    gi_write_pages.earlier_side(r))[0]
            for r in rows}


def professor_pairs(db, rows):
    """{(pair_lo, pair_hi): the later paper's US/European professors (gi_affiliations.pair_professors)}."""
    import gi_affiliations
    import gi_write_pages
    profs = gi_affiliations.doi_professors(db, [d for r in rows for d in (r["doi_a"], r["doi_b"])])
    return {(r["pair_lo"], r["pair_hi"]): gi_affiliations.pair_professors(profs, r["doi_a"], r["doi_b"],
                                                                         gi_write_pages.earlier_side(r))
            for r in rows}


def ai_group(r):
    """0: the agent's cross-author / data copying calls, 1: its other problem calls, 2: the rest (QUEUE_SQL)."""
    if r["ai_decision"] == "problem":
        return 0 if r["ai_type"] in ("cross-author copying", "data copying") else 1
    return 2


def queue_key(r, priority, order="publisher", affiliation=None, professor=False):
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
    overlap first there, the publisher only breaking ties. order="affiliation" (affiliation: the
    gi_affiliations rank of the later paper's authors) puts that rank right after 1, within the agent's problem
    calls and within the rest separately (so a pair the agent cleared never jumps one it flagged), the
    publisher order then holding within each rank. order="professor" puts who the authors are ahead of
    everything: a later paper with an author whose ORCID lists a current professor/lecturer post at a
    US/European institution (professor: gi_affiliations.professor_job) first, then by affiliation rank, then
    the affiliation order within each (so the agent's calls and the publisher order still hold inside a rank,
    and a preprint copy still goes after the rest of its rank). Verdicts are untouched; only the order."""
    pct = max(r["ft_pct_a"] or 0, r["ft_pct_b"] or 0)
    group = ai_group(r)
    aff = 2 if affiliation is None else affiliation        # gi_affiliations.UNKNOWN
    if order == "professor":
        return (not professor, aff, max(priority, 0), group == 2, group, min(priority, 0), -pct)
    if order == "affiliation":
        return (max(priority, 0), group == 2, aff, group, min(priority, 0), -pct)
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


def show_pair(pair, i, total, titles, flags=(), priority=(0, None), authors=None, professors=None, group=None):
    pct = lambda v: "?" if v is None else f"{v:.0f}%"
    print("\n" + "=" * 100)
    print(f"GI-{pair['page_number']:02d}   ({i} of {total})   A {pct(pair['ft_pct_a'])} / B {pct(pair['ft_pct_b'])} "
          f"of words shared, {pair['ft_matched_words'] or 0:,} matched, longest run {pair['ft_longest'] or 0:,}")
    for side, (title, pdf) in zip("AB", titles):
        doi, year = pair[f"doi_{side.lower()}"], pair[f"year_{side.lower()}"]
        print(wrap(f"  {side}: ", f"{title} ({year or '?'}) {doi}"))
        if not pdf:
            print("     (no local PDF)")
    if group:
        print(wrap("  SAME LATER PAPER as: ", group + " -- queued together"))
    rank, note = priority
    if note and rank > 0:
        print(f"  NOTE: {note} -- queued after the other pairs: a preprint/repository upload is most "
              "often the authors' own or an aggregator's copy")
    elif note and rank < 0:
        print(f"  NOTE: {note} -- queued first within the agent's group: a copy in a major journal is the "
              "most consequential finding")
    elif note:
        print(f"  NOTE: {note}")
    if authors:
        print(wrap("  Authors: ", authors))
    if professors:
        print(wrap("  Professors (ORCID): ", professors))
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


def affiliation_line(scores, shown):
    """The later paper's authors as gi_affiliations ranks them (both papers' when the order is unknown), or
    None when nothing is cached for either."""
    import gi_affiliations as ga
    if not any(scores.get((shown[f"doi_{s}"] or "").lower(), (0, []))[1] for s in "ab"):
        return None
    if shown.get("earlier") in ("a", "b"):
        later = "b" if shown["earlier"] == "a" else "a"
        return f"(later paper, {later.upper()}) " + ga.describe(scores, shown[f"doi_{later}"])
    return " | ".join(f"{s.upper()}: " + ga.describe(scores, shown[f"doi_{s}"]) for s in "ab")


def professor_line(profs, shown):
    """The later paper's US/European professors (both papers' when the order is unknown), or None."""
    import gi_affiliations as ga
    found = ga.pair_professors(profs, shown["doi_a"], shown["doi_b"], shown.get("earlier"))
    return "; ".join(f"{n} ({why})" for n, why in found) or None


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


def ask_severity(pair, decision, type_=None):
    """Enter gives DEFAULT_SEVERITY (SELF_SEVERITY for a SELF_TYPES type), whatever the agent said: high also
    writes the emails to the victims and the institution (gi_report_case.audiences()), so it has to be chosen.
    "none" for a no-problem verdict."""
    if decision == "no problem":
        return "none"
    default = SELF_SEVERITY if type_ in SELF_TYPES else DEFAULT_SEVERITY
    ai = f", AI: {pair['ai_severity']}" if pair["ai_severity"] in SEVERITIES.values() else ""
    while True:
        ans = ask(f"Severity  l)ow  m)edium  h)igh  [Enter: {default}{ai}] ").lower()
        if not ans:
            return default
        if ans[:1] in SEVERITIES:
            return SEVERITIES[ans[:1]]


def split_note_suffix(ans):
    """'3 n' -> ('3', True), 'c' -> ('c', False): a trailing ' n' on a verdict asks for a note."""
    head, sep, tail = ans.strip().rpartition(" ")
    if sep and tail == "n":
        return head.strip(), True
    return ans.strip(), False


def orient(pair):
    """The pair as the page shows it: A / B swapped so A is the page's left paper (the earlier one when known),
    shown["earlier"] = 'a' / 'b' / None, shown["swapped"] when that isn't fulltext's own order."""
    import gi_write_pages
    shown = gi_write_pages.oriented(pair, gi_write_pages.page_order(pair["library"], pair["paper_a"],
                                                                   pair["paper_b"])[0])
    shown["swapped"] = shown["paper_a"] != pair["paper_a"]
    e = gi_write_pages.earlier_paper(pair["library"], shown["paper_a"], shown["paper_b"])
    shown["earlier"] = "a" if e == shown["paper_a"] else "b" if e == shown["paper_b"] else None
    return shown


def ask_order(shown, enter="keep it"):
    """Which paper came first, as you see it: 'a' / 'b' (A / B as shown), 'u' (can't tell), '-' (back to what
    the years say), or '' (Enter: leave it)."""
    now = {"a": "A", "b": "B"}.get(shown["earlier"], "unknown")
    while True:
        ans = ask(f"Which came first (A {shown['year_a'] or '?'}, B {shown['year_b'] or '?'}; now {now})?  "
                  f"a) A  b) B  u) can't tell  -) as the years say  [Enter: {enter}] ").strip().lower()
        if ans in ("", "a", "b", "u", "-"):
            return ans
        print("  a, b, u, - or Enter")


def set_earlier(shown, ans):
    """Save ask_order()'s answer in the pair's library (earlier_overrides): 'a' / 'b' that paper first, 'u' the
    order unknown whatever the years say, '-' removes the override."""
    import gi_write_pages
    if str(gi_write_pages.ROOT) not in sys.path:
        sys.path.insert(0, str(gi_write_pages.ROOT))
    from write_dupe_reports_html import EARLIER_OVERRIDES_DDL
    lo, hi = sorted((shown["paper_a"], shown["paper_b"]))
    conn = sqlite3.connect(shown["library"], timeout=300)
    try:
        conn.executescript(EARLIER_OVERRIDES_DDL)
        if ans == "-":
            conn.execute("delete from earlier_overrides where paper_lo = ? and paper_hi = ?", (lo, hi))
        else:
            earlier = shown[f"paper_{ans}"] if ans in ("a", "b") else None
            conn.execute("insert or replace into earlier_overrides values (?, ?, ?, ?, datetime('now'))",
                         (lo, hi, earlier, "set by hand in gi_review_cli.py"))
        conn.commit()
    finally:
        conn.close()


def titles_of(pair, shown):
    return [paper_info(pair["library"], shown["paper_a"]), paper_info(pair["library"], shown["paper_b"])]


def rewrite_page(pair, pages_dir, sample_rate):
    """Rewrite the pair's comparison page (gi_write_pages.py --pages), so it shows the order just set; the
    new page's path, or None if that failed (printed)."""
    import gi_write_pages
    p = subprocess.run([sys.executable, str(Path(__file__).with_name("gi_write_pages.py")), str(pages_dir),
                        "--sample-rate", str(sample_rate), "--pages", str(pair["page_number"])],
                       capture_output=True, text=True)
    found = sorted(Path(pages_dir).glob(f"GI-{pair['page_number']:02d}-*.html"))
    if p.returncode != 0 or not found:
        print(f"  page not rewritten: {(p.stderr or p.stdout).strip()[-300:]}")
        return None
    return found[0]


def findings_line(case, prep=None):
    """What f at the menu would show for this pair's case."""
    import gi_case_findings
    if prep and prep.running():
        return f"{case}'s findings: the agent is still writing them"
    if not gi_case_findings.findings_path(case).exists():
        return f"{case}'s findings: none yet"
    c = gi_case_findings.counts(case)
    total = sum(c.values())
    return f"{case}'s findings: {total}, " + (f"{c['pending']} to check" if c["pending"] else "all decided")


def review_one(pair, choices, counts, saved_flags=(), prep=None, shown=None, set_order=None, case=None):
    """Ask for one verdict. Returns (decision, type, severity, note, flags, agreed), agreed True when it was 'a'
    (agree with the AI), or 'skip' / 'quit' / 'reopen' / 'pdfs' / 'pubpeer' / 'findings' / 'order' / 'discuss'.
    shown: the pair as displayed (orient()); set_order(ans): saves an ask_order() answer and returns the
    re-oriented pair. With both, a problem verdict while the order is unknown asks which came first. case
    ('GI-NN'): the menu line for f says where its findings stand."""
    ai = (pair["ai_decision"], pair["ai_type"]) if pair["ai_decision"] else None
    print("\nWhat do you think?")
    for k, (d, t, desc) in enumerate(choices, 1):
        mark = "  <- AI" if ai == (d, t) else ""
        print(f"  {k:>2}  {d + ': ' + t:<34} {desc} [{counts.get((d, t), 0)}]{mark}")
    print(f"   c  {QUICK[0]}: {QUICK[1]}, {QUICK[2]}, {QUICK[3]} (skips severity)")
    print(f"   e  unsure, flagged {FLAGS['e'][0]}: {FLAGS['e'][1]} (skips severity and flags)")
    print(f"   r  problem, flagged {FLAGS['r'][0]}: {FLAGS['r'][1]} (skips severity, flags and report)")
    print(f"   w  problem: {NOT_WORTH[0]}, {NOT_WORTH[1]}, flagged {FLAGS['w'][0]}: {FLAGS['w'][1]}"
          " (no report; publishes the site page as not reported)")
    keys = "   a  agree with the AI" if ai and ai[1] else ""
    print(f"{keys}   o  reopen the page   p  open the PDFs   y  set which paper came first   t  report it   "
          "s  skip   q  quit")
    if case:
        print("   d  talk the case through with the agent (its session resumed, or a new one from findings.md)")
    if case:
        import gi_case_findings
        others = sum(1 for c, _ in gi_case_findings.pending_cases() if c != case)
        print(f"   f  {findings_line(case, prep)}" + (f"; {others} other case(s) have findings to check" if others else ""))
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
        if ans == "w":
            decision, (type_, severity), reasons = "problem", NOT_WORTH, []
            quick, preset = True, FLAGS["w"][0]
            break
        if ans in ("q", "s", "o", "p", "t", "f", "y") or (ans == "d" and case):
            return {"q": "quit", "s": "skip", "o": "reopen", "p": "pdfs", "t": "pubpeer", "f": "findings",
                    "y": "order", "d": "discuss"}[ans]
        if ans == "a" and ai and ai[1]:
            decision, type_ = ai
            agreed = True
            break
        if ans.isdigit() and 1 <= int(ans) <= len(choices):
            decision, type_ = choices[int(ans) - 1][:2]
            if decision == "no problem" and type_ in TYPE_REASON:
                severity, reasons, quick = "none", [TYPE_REASON[type_]], True
            break
        print("  a number from the list, or c / e / r / w / a / o / p / y / t / f / d / g / s / q")

    converted = False
    year = same_year(pair)
    if decision == "problem" and not preset and shown is not None and set_order and shown["earlier"] is None:
        ans = ask_order(shown, enter="can't tell" + (": saved as unsure" if year else ""))
        if ans:
            shown = set_order(ans)
    known = shown is not None and shown["earlier"] is not None
    if decision == "problem" and year and not preset and not known:  # a retraction already settled who copied
        # same publication year: which paper copied can't be told from the dates, so it's recorded as unsure,
        # the type kept as the best guess
        print(f"  Both papers are from {year}: saving as unsure ({type_} as the best guess) for that reason.")
        decision, converted = "unsure", True
    if quick:
        reasons = reasons + ([same_year_reason(year)] if converted else [])
    else:
        severity = ask_severity(pair, decision, type_)
        reasons = [same_year_reason(year)] if converted else []

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


def pubpeer_page(db, pair, open_it=True, severity=None, authors_only=False):
    """Write the pair's report files (gi_report_case.py: the PubPeer page, the attachment and the draft emails in
    data/dupe_publisher/GI-NN/, the page and index on the project site) and open the PubPeer page; a
    failure is printed, never fatal. severity picks the emails (None: the saved review's, else medium).
    authors_only: the site page and the earlier authors' email only (write_report(authors_only=True))."""
    import gi_report_case
    print("  writing the report files ...")
    try:
        paths = gi_report_case.write_report(db, pair, sender=gi_report_case.git_email(), severity=severity,
                                            authors_only=authors_only)
    except Exception as e:
        print(f"  report failed: {e}")
        return None
    print(f"  report: {paths['comparison'].parent}/  (site page: {paths.get('site', 'none')})")
    print("  emails: " + ", ".join(k.split(" ", 1)[1] for k in paths if k.startswith("email ")))
    for k in paths:
        if k.startswith("no email "):
            print(f"  no {k[9:]} email: {paths[k]}")
        if k.startswith("page "):   # a journal with no email: its draft goes into this page instead
            print(f"  {k[5:]}: the journal has no email; report it at {paths[k]} (paste from the draft)")
    if open_it and paths.get("pubpeer"):
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


DISCUSS_PROMPT = ("Let's talk through {case}. Read its findings.md first (and notes.md if there is one): that's where "
                  "an earlier run of yours left off; reread the PDFs only where we need to. I'm running you from "
                  "gi_review_cli.py and haven't decided on the pair yet: when we're done I'll /exit, and the review "
                  "tool then has me tick the findings in findings.md, so put anything new there.")


def discuss(case, prep=None, claude="claude"):
    """d at the menu: talk the case through with the gi-cases agent in this terminal (/exit comes back). Resumes
    the background run's session when it finished with one (prep, or an earlier session's run); otherwise a new
    conversation started from the case's findings.md (DISCUSS_PROMPT), or, with no findings.md either, the
    agent opened to prep it (run_agent()). A run still going is asked about first: wait for it, or stop it and
    talk from what it has written so far. Your claude.ai subscription only. False when nothing was opened."""
    import gi_case_findings
    if prep and prep.running():
        ans = ask(f"[{prep.status()}. w: wait for it, then talk, x: stop it and talk from what it has written, "
                  f"Enter: back] ").lower()
        if ans.startswith("w"):
            if not prep.wait():
                return False
        elif ans.startswith("x"):
            prep.stop()
        else:
            return False
    sid = prep.result().get("session_id") if prep else None
    if not sid and not gi_case_findings.findings_path(case).exists():
        return run_agent(case, claude)
    why = subscription_problem(claude)
    if why:
        print(f"  agent not started: {why}")
        return False
    if sid:
        print(f"  resuming the agent's session on {case} (/exit comes back here) ...")
        cmd = [claude, "--resume", sid, "--agent", "gi-cases"]
    else:
        print(f"  opening the gi-cases agent on {case}'s findings.md (/exit comes back here) ...")
        cmd = [claude, "--agent", "gi-cases", DISCUSS_PROMPT.format(case=case)]
    subprocess.run(cmd, cwd=Path(__file__).resolve().parent, env=agent_env())
    return True


HEADLESS_PROMPT = ("Prep {case}: follow \"Prep a case\" in your instructions. This is a background run while the "
                   "user looks at the comparison page: nobody can answer questions or approve anything, so put "
                   "anything uncertain under ## Questions in findings.md. Afterwards the user may resume this "
                   "session to talk it through. In this run only these tools work: Read, Glob, Grep, web search "
                   "and fetch, `sqlite3 -readonly` (not python) for the databases, pdftotext and pdfinfo, and "
                   "writing in the case's folder and journal_contacts.json. Don't run gi_report_case.py: the "
                   "review tool writes the report files when the user confirms. Do look up the later authors' "
                   "institutions' research integrity officers (one contacts.json entry per institution, with "
                   "the later authors there) and the earlier article's byline names, and write contacts.json "
                   "(see Contacts): an institution without an address there gets no email. End with one line saying what "
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
        self.earlier = False   # previous(): an earlier session's finished run, not one started here

    @classmethod
    def previous(cls, case, claude="claude"):
        """The case's earlier background run (its prep-stream.jsonl) if it finished with a result, else None.
        status(), result() and wait() work on it as on a live run, so the menu says what it concluded and
        reporting offers to resume its session."""
        prep = cls(case, claude)
        if not prep.result():
            return None
        prep.earlier = True
        prep.started = prep.out.stat().st_mtime
        return prep

    def start(self, ahead=False):
        """ahead: started on the next pair in the queue, not the one on screen (only the message differs)."""
        why = subscription_problem(self.claude)
        if why:
            print(f"  agent prep not started{' on ' + self.case if ahead else ''}: {why}")
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
        if ahead:
            print(f"  agent prep also started on {self.case}, the next pair to prep, so it's ready when you get there")
        else:
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
            return self.earlier
        if self.running():
            print("  waiting for the agent's analysis to finish (Ctrl-C: stop it and report without it) ...")
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
        if self.earlier:
            import datetime
            said = (self.result().get("result") or "").strip().splitlines()
            when = datetime.date.fromtimestamp(self.started).isoformat()
            return f"agent: prepped {self.case} on {when}: {said[-1][:160] if said else 'no summary'}"
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


def next_to_prep(rest):
    """The first pair in rest (the queue after the one on screen) the agent hasn't prepped (no findings.md) and
    the AI didn't call self-author copying (SELF_TYPES: not reported by default). None when there isn't one."""
    import gi_case_findings
    for r in rest:
        case = f"GI-{r['page_number']:02d}"
        if r["ai_type"] not in SELF_TYPES and not gi_case_findings.findings_path(case).exists():
            return case
    return None


def publish_site(case):
    """Push the site pages to main (gi_publish_site.py), printing what happened; never fatal."""
    import gi_publish_site
    try:
        sha = gi_publish_site.publish([case])
        print(f"  site: pushed {sha} to main" if sha else "  site: main already has it")
    except Exception as e:
        print(f"  site push failed: {getattr(e, 'stderr', '') or e}")


def publish_only(db, pair):
    """Not reported, but public: the site page and index entry (gi_report_case.publish_only()), pushed."""
    import gi_report_case
    case = f"GI-{pair['page_number']:02d}"
    try:
        paths = gi_report_case.publish_only(db, pair)
    except Exception as e:
        print(f"  site page failed: {e}")
        return
    print(f"  site page (not reported): {paths['site']}")
    publish_site(case)


CHANGE_MIND = "v: change your verdict, s: don't report it"


def changed_mind(ans):
    """'verdict' / 'stopped' for a CHANGE_MIND answer, else None (go on)."""
    if ans.startswith("v"):
        return "verdict"
    if ans.startswith("s"):
        return "stopped"
    return None


def take_down_flow(db, pair):
    """Offered when a verdict that isn't a problem replaces one that was reported: take the site page down
    (gi_report_case.take_down(), pushed) and move the report files aside. Emails and PubPeer posts already out
    are yours to withdraw."""
    import gi_report_case
    case = f"GI-{pair['page_number']:02d}"
    when = gi_report_case.is_reported(case)
    if not when:
        return
    if not ask(f"[{case} was reported on {when}. t: take its site page down and set its report files aside, "
               f"Enter: leave it] ").lower().startswith("t"):
        return
    moved = gi_report_case.take_down(case)
    print(f"  {case}: site page removed; {len(moved)} report file(s) moved to "
          f"{gi_report_case.REPORT_DIR / case / 'withdrawn'}")
    publish_site(f"{case} taken down")
    print("  any email or PubPeer post already sent isn't undone by this")


def report_flow(db, pair, open_it=True, severity=None, prep=None, authors_only=False):
    """Everything for reporting the pair, in order: the gi-cases agent to prep it (waited for, or Ctrl-C to go
    on without it), ticking its findings, then the report files written and the PubPeer page opened, once,
    with what the agent found and what you ticked; your PubPeer post's address, the site page pushed (last,
    so it has the final files), and the drafts opened in Thunderbird. Every step but writing the report
    files can be skipped with Enter / s. After the agent's summary, and again after talking it through with
    it, you can change your mind before anything is written: returns 'verdict' (ask the verdict again) or
    'stopped' (not reported), else None. authors_only (a at the report prompt): the finding published and the
    earlier authors told, nothing else -- no PubPeer page or post, no journal emails."""
    import gi_case_findings
    import gi_report_case
    case = f"GI-{pair['page_number']:02d}"
    agent_ran = bool(prep and prep.wait())
    if agent_ran:
        res = prep.result()
        if res.get("is_error") or not res:
            print(f"  agent prep failed: {(res.get('result') or 'no result, see ' + str(prep.dir / 'prep.log'))[:300]}")
        else:
            print(wrap("  agent: ", " ".join((res.get("result") or "").split())[:600]))
        sid = res.get("session_id")
        ans = ask("[Enter: go to its findings, " + ("d: discuss them with the agent first, " if sid else "")
                  + CHANGE_MIND + "] ").lower()
        if sid and ans.startswith("d"):
            print("  resuming the agent's session (/exit comes back here) ...")
            subprocess.run([prep.claude, "--resume", sid, "--agent", "gi-cases"],
                           cwd=Path(__file__).resolve().parent, env=agent_env())
            ans = ask(f"[Enter: go on reporting it, {CHANGE_MIND}] ").lower()
        if changed_mind(ans):
            return changed_mind(ans)
    elif prep:   # Ctrl-C while waiting: you've decided to report without it
        print("  reporting without the agent's findings")
    elif gi_case_findings.findings_path(case).exists():
        print(f"  {case} already has findings (no agent run recorded); going to them")
    elif not ask(f"[Enter: open the agent to prep {case} (findings, contacts), s: skip] ").lower().startswith("s"):
        run_agent(case)
    if gi_case_findings.counts(case)["pending"]:
        gi_case_findings.review(case, ask=ask)
    paths = pubpeer_page(db, pair, open_it, severity=severity, authors_only=authors_only)
    if not paths:
        return
    have = gi_case_findings.pubpeer_url(case)
    url = "" if authors_only else ask(f"PubPeer post's address once it's up [Enter: {'keep ' + have if have else 'later (t asks again)'}] ")
    if url:
        try:
            gi_case_findings.save_pubpeer_url(case, url)
            paths = pubpeer_page(db, pair, False, severity=severity) or paths   # the emails now link it
        except ValueError as e:
            print(f"  {e}: not saved")
    if paths.get("site"):
        publish_site(case)
    emls = [p for k, p in paths.items() if k.startswith("email ")]
    if emls and ask("[o: open the drafts in Thunderbird, Enter: not now] ").lower().startswith("o"):
        try:
            gi_report_case.open_drafts(emls)
        except Exception as e:
            print(f"  couldn't open the drafts: {e}")


def refresh_report(db, case, open_it=True):
    """Rewrite a case's report files after its findings changed, but only once it has been reported (its
    PubPeer page exists): writing them also writes the site page and marks the case reported in cases.json,
    which ticking a finding on a pair you haven't decided on must not do."""
    import gi_case_findings
    if not (gi_case_findings.case_dir(case) / f"{case}-pubpeer.html").exists():
        print(f"  {case}: not reported yet, so no report files written (reporting it picks these findings up)")
        return
    f = db.execute("select * from fulltext where page_number = ?", (int(case[3:]),)).fetchone()
    if f is not None:
        pubpeer_page(db, f, open_it)


def check_findings(db, case, open_it=True, prep=None):
    """f at the menu: the pair's own case first (every finding when none is pending, to change one), then,
    if you want, every other case's pending ones (gi_case_findings.review()). Report files are rewritten only
    for cases already reported (refresh_report())."""
    import gi_case_findings
    if prep and prep.running():
        print(f"  {case}: the agent is still working, its findings aren't written yet ({prep.status()})")
    elif not gi_case_findings.findings_path(case).exists():
        print(f"  {case}: no findings yet" + ("" if prep else " (no agent run on it: t runs one)"))
    elif gi_case_findings.review(case, ask=ask, only_pending=bool(gi_case_findings.counts(case)["pending"])):
        refresh_report(db, case, open_it)
    others = [(c, n) for c, n in gi_case_findings.pending_cases() if c != case]
    if others and ask("[o: the other cases' findings too (" + ", ".join(f"{c} {n}" for c, n in others)
                      + "), Enter: back] ").lower().startswith("o"):
        for c, _ in others:
            if gi_case_findings.review(c, ask=ask):
                refresh_report(db, c, open_it)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--pages-dir", default=str(gi_db.DATA / "fulltext_pages_s16"))
    ap.add_argument("--sample-rate", type=int, default=8,
                    help="the pages' n-gram sampling rate, for a page rewritten after y (8 for the 16-slice pages)")
    ap.add_argument("--gi", type=lambda s: [int(x) for x in s.split(",")], help="page numbers, e.g. 12,40")
    ap.add_argument("--ai-type", type=lambda v: v.split(","), default=[],
                    help="only pairs the agent gave these types, comma-separated: " + ", ".join(AI_TYPES))
    ap.add_argument("--ai-decision", type=lambda v: v.split(","), default=[],
                    help="only pairs with these agent decisions: " + ", ".join(AI_DECISIONS))
    ap.add_argument("--include-reviewed", action="store_true", help="also pairs you've already reviewed")
    ap.add_argument("--pdfs", action="store_true", help="also open both PDFs for every pair (p at the prompt "
                                                        "opens them for one pair)")
    ap.add_argument("--no-open", action="store_true", help="don't open anything automatically")
    ap.add_argument("--order", choices=("affiliation", "professor", "publisher", "overlap"), default="affiliation",
                    help="affiliation (default): pairs whose later paper has an author still at a US/European "
                         "university first (gi_affiliations.py), then as publisher; professor: by the authors "
                         "above all else -- an author whose ORCID lists a current professor/lecturer post at a "
                         "US/European institution first, then US/European academics, then the rest; publisher: "
                         "within the agent's "
                         "cross-author / data copying group, major publisher first, then overlap; overlap: "
                         "overlap first, then publisher")
    ap.add_argument("--us-europe-only", action="store_true",
                    help="only pairs whose later paper has an author still at a US/European university")
    ap.add_argument("--no-medical", action="store_true",
                    help="skip pairs where either paper's OpenAlex primary topic is in Health Sciences (medicine, "
                         "nursing, dentistry, ...); papers with no cached OpenAlex record are kept")
    ap.add_argument("--no-agent", action="store_true", help="don't start the agent's analysis in the background "
                                                             "when a pair opens (nor on the next one)")
    ap.add_argument("--no-lookahead", action="store_true",
                    help="start the agent only on the pair on screen, not also on the next pair in the queue that "
                         "hasn't been prepped")
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
    groups = later_paper_groups(db)
    queue = load_queue(db, args.gi, args.include_reviewed, args.ai_type, args.ai_decision, args.order,
                       args.us_europe_only, groups, args.no_medical)
    if not queue:
        print("nothing to review")
        return
    import gi_affiliations
    aff_scores = gi_affiliations.doi_scores(db, [d for r in queue for d in (r["doi_a"], r["doi_b"])])
    aff_profs = gi_affiliations.doi_professors(db, [d for r in queue for d in (r["doi_a"], r["doi_b"])])
    choices, counts = ordered_choices(db)
    saved = set()   # page numbers: a verdict changed while reporting counts once
    ahead = {}   # case: the BackgroundPrep started on a later pair (lookahead), None if it couldn't start
    try:
        for i, pair in enumerate(queue, 1):
            import gi_write_pages
            # A / B as the page shows them (earlier paper on the left), not fulltext's own order
            shown = orient(pair)
            titles = titles_of(pair, shown)
            page = page_path(pages_dir, pair)
            pdfs = [p for p in [titles[0][1], titles[1][1]] if p]
            saved_flags = pair_flags(db, pair)
            reported = False   # t at the menu ran report_flow() for this pair already
            show_pair(shown, i, len(queue), titles, saved_flags, gi_write_pages.priority_status(pair),
                      affiliation_line(aff_scores, shown), professor_line(aff_profs, shown),
                      group_line(db, pair, groups))
            print(f"  page: {page or '(no page in ' + str(pages_dir) + ')'}")
            for side, pdf in zip("AB", pdfs):
                print(f"  PDF {side}: {pdf}")
            if not args.no_open:
                open_files(([page] if page else []) + (pdfs if args.pdfs else []))
            import gi_case_findings
            case = f"GI-{pair['page_number']:02d}"
            prep = ahead.pop(case, None)   # started while you were on an earlier pair
            if prep:
                print("  " + prep.status())
            elif gi_case_findings.findings_path(case).exists():   # prepped already: say so rather than start again
                prep = BackgroundPrep.previous(case)
                print("  " + (prep.status() if prep else f"agent not started: {case} already has a findings.md"))
            elif not args.no_agent and pair["ai_type"] not in SELF_TYPES:   # those aren't reported by default
                prep = BackgroundPrep(case)
                if not prep.start():
                    prep = None
            if not (args.no_agent or args.no_lookahead or ahead):   # one run ahead at a time
                nxt = next_to_prep(queue[i:])
                if nxt:
                    ahead[nxt] = BackgroundPrep(nxt)
                    if not ahead[nxt].start(ahead=True):
                        ahead[nxt] = None   # not retried for this pair; its own turn tries again
            def set_order(ans, pair=pair):
                """Save the order, rewrite the comparison page to match, and return the pair re-oriented."""
                nonlocal shown, page
                set_earlier(shown, ans)
                shown = orient(pair)
                page = rewrite_page(pair, pages_dir, args.sample_rate) or page
                now = {"a": "A", "b": "B"}.get(shown["earlier"])
                print(f"  order saved: {'paper ' + now + ' first' if now else 'unknown'}"
                      + (" (A / B relabelled to match the page: the earlier paper is A)" if now == "A" else ""))
                return shown

            while True:   # asked again when you change your verdict while reporting (report_flow())
                while True:
                    out = review_one(pair, choices, counts, saved_flags, prep, shown, set_order, case)
                    if out == "order":
                        ans = ask_order(shown)
                        if ans:
                            set_order(ans)
                            show_pair(shown, i, len(queue), titles_of(pair, shown), saved_flags,
                                      gi_write_pages.priority_status(pair), affiliation_line(aff_scores, shown),
                                      professor_line(aff_profs, shown), group_line(db, pair, groups))
                            if not args.no_open and page:
                                open_files([page])
                        continue
                    if out in ("reopen", "pdfs"):
                        open_files(pdfs if out == "pdfs" else [page] if page else [])
                        continue
                    if out == "pubpeer":
                        if report_flow(db, pair, not args.no_open, prep=prep) is None:
                            prep, reported = None, True
                        else:   # changed your mind: back to this menu, nothing written
                            print("  not reported")
                        continue
                    if out == "findings":
                        check_findings(db, case, not args.no_open, prep)
                        continue
                    if out == "discuss":
                        discuss(case, prep)
                        continue
                    break
                reporting = out not in ("quit", "skip") and not {FLAGS["r"][0], FLAGS["w"][0]} & set(out[4]) and (
                    out[1] not in SELF_TYPES) and (out[0] == "problem" or (out[5] and pair["ai_decision"] == "problem"))
                if prep and not reporting:   # not going to be reported: the analysis isn't needed
                    prep.stop()
                    prep = None
                if out in ("quit", "skip"):
                    break
                decision, type_, severity, note, flags, agreed = out
                print(wrap("Save: ", f"{decision} / {type_} / {severity}" + (f" [{', '.join(flags)}]" if flags else "")
                           + (f" -- {note}" if note else "")))
                save_review(db, pair, decision, type_, severity, note, flags)
                counts[(decision, type_)] = counts.get((decision, type_), 0) + 1
                saved.add(pair["page_number"])
                print(f"saved GI-{pair['page_number']:02d}")
                if decision != "problem" and not (agreed and pair["ai_decision"] == "problem"):
                    take_down_flow(db, pair)   # only asks when the pair had been reported
                # agreeing with the AI's problem call also counts when the same year turned it into unsure
                if FLAGS["r"][0] in flags:
                    print("  already retracted: no report files (t on this pair still writes them)")
                elif FLAGS["w"][0] in flags:
                    print("  not worth reporting: no report files; publishing the site page as not reported")
                    publish_only(db, pair)
                elif reported and (decision == "problem" or (agreed and pair["ai_decision"] == "problem")):
                    # the emails follow the severity just chosen; the rest of the flow already ran under t
                    print(f"  already reported (t): emails rewritten for severity {severity}")
                    pubpeer_page(db, pair, False, severity=severity)
                elif type_ in SELF_TYPES and (decision == "problem" or (agreed and pair["ai_decision"] == "problem")):
                    print(f"  {type_} (self-author copying): not reported or published (t on this pair does it)")
                elif decision == "problem" or (agreed and pair["ai_decision"] == "problem"):
                    emails = "all four emails" if severity == "high" else "email to the journal"
                    ans = ask(f"[Enter to report it (the agent's findings, then the PubPeer page and {emails}, site page pushed), "
                              f"a to publish it and tell only the earlier authors (no PubPeer, no journal), "
                              f"p to only publish its site page (marked not reported), s to skip] ").lower()
                    if ans.startswith(("s", "p")):
                        if prep:
                            prep.stop()
                        if ans.startswith("p"):
                            publish_only(db, pair)
                        else:
                            print("  report skipped (t on this pair later does it)")
                    else:
                        again = report_flow(db, pair, not args.no_open, severity=severity, prep=prep,
                                            authors_only=ans.startswith("a"))
                        if again == "verdict":
                            print("  your verdict again (it replaces the one just saved):")
                            continue
                        if again == "stopped":
                            print("  not reported (t on this pair later does it)")
                break
            if out == "quit":
                break
    finally:   # quitting (or Ctrl-C) stops a run started ahead: it starts again when you reach that pair
        for p_ in ahead.values():
            if p_ and p_.running():
                p_.stop()
                print(f"  stopped the agent's run on {p_.case} (it starts again when you reach that pair)")
    print(f"\n{len(saved)} review(s) saved")
    if saved:
        import gi_write_pages
        ro = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
        ro.row_factory = sqlite3.Row
        gi_write_pages.write_index(ro, pages_dir)


if __name__ == "__main__":
    main()

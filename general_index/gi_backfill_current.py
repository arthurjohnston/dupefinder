"""Backfill the later authors' current institutions on cases prepped before the gi-cases agent looked for
them (NEXT_STEPS.md, "Backfill: emails to the later authors' current institutions", step 2).

Runs the gi-cases agent headless on each case, `--jobs` at a time, with the same tools as the review tool's
background prep (gi_review_cli.headless_tools(): read files, web search and fetch, write only in the case's
folder). It only looks up and records: new "current": true entries in contacts.json, a fallback address
(gi_contact.integrity_office()) for any institution entry without one, a section in notes.md,
anything uncertain under ## Questions in findings.md. Nothing is sent and no drafts are regenerated.
Each run's events go to GI-NN/backfill-current.jsonl, stderr to GI-NN/backfill-current.log.

Usage: gi_backfill_current.py [GI-NN ...] [--sent-only] [--limit N] [--jobs N]
       gi_backfill_current.py --review     write data/dupe_publisher/_institutions_review.md: every case's
                                           institution entries (current ones first) with severity, address,
                                           integrity office or fallback, and evidence, for checking by hand;
                                           and _institution_emails.html, a page linking every institution
                                           draft (.eml) to open
       no cases: every case folder whose contacts.json has a byline institution entry and no current one yet,
       or an institution entry with no address
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import gi_case_findings
from gi_review_cli import agent_env, headless_tools, subscription_problem

PROMPT = ("{case}: backfill current institutions only. This case was prepped before you looked for them. Follow "
          "\"The later authors' current institutions, too\" under Contacts in your instructions, for every "
          "author on the later article's byline (read the later PDF's byline, and contacts.json and notes.md "
          "first). Add \"current\": true entries to contacts.json (byline entries stay first and unchanged), "
          "only on evidence tied to that person, with `evidence` saying what it is. Also, for every institution "
          "entry (byline or current) whose `to` is empty, find a fallback address as described under Contacts "
          "(\"No integrity office address\") and fill in `to`, with `notes` saying it's a fallback. Don't "
          "change anything else in existing entries, and don't touch findings. Put anything uncertain under "
          "## Questions in findings.md, and add a dated '## Current institutions (backfill)' section to "
          "notes.md (or add to it if one is there): per author, where they are now, the evidence, or what you "
          "tried; and any fallback address used. Nobody can answer questions in this run. End with one line, "
          "e.g. '2 current institutions added, 1 author untraced' or 'no moves: all still at the byline "
          "institution'.")


def entries(case):
    path = gi_case_findings.case_dir(case) / "contacts.json"
    try:
        inst = json.loads(path.read_text()).get("institution")
    except (OSError, ValueError):
        return []
    return [inst] if isinstance(inst, dict) else [i for i in inst or [] if isinstance(i, dict)]


def sent(case):
    """notes.md's ## Sent section records a send (the agent writes the heading with "nothing yet" under it)."""
    try:
        m = re.search(r"^## Sent\n(.*?)(?=^## |\Z)", (gi_case_findings.case_dir(case) / "notes.md").read_text(),
                      re.S | re.M)
    except OSError:
        return False
    return bool(m) and not re.fullmatch(r"[-\s(]*nothing( yet| sent)?[.)\s]*", m.group(1).strip(), re.I)


def todo(sent_only):
    """Cases with a byline institution entry and either no current one yet or an entry with no address, the
    sent ones first."""
    cases = sorted(d.name for d in gi_case_findings.REPORT_DIR.glob("GI-*") if d.is_dir())
    cases = [c for c in cases if entries(c) and (not any(i.get("current") for i in entries(c))
                                                 or not all(i.get("to") for i in entries(c)))
             and (sent(c) or not sent_only)]
    return sorted(cases, key=lambda c: not sent(c))


def start(case, claude):
    d = gi_case_findings.case_dir(case)
    cmd = [claude, "-p", PROMPT.format(case=case), "--agent", "gi-cases", "--output-format", "stream-json",
           "--verbose", "--permission-mode", "dontAsk", "--allowedTools", *headless_tools(case)]
    with open(d / "backfill-current.jsonl", "w") as out, open(d / "backfill-current.log", "a") as log:
        return subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent, env=agent_env(),
                                stdin=subprocess.DEVNULL, stdout=out, stderr=log, start_new_session=True)


def result(case):
    """The agent's closing line from its stream, or None."""
    try:
        lines = (gi_case_findings.case_dir(case) / "backfill-current.jsonl").read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            return (ev.get("result") or "").strip().splitlines()[-1:] or [""]
    return None


REVIEW = gi_case_findings.REPORT_DIR / "_institutions_review.md"


def severities():
    """{GI-NN: your review's severity} (gi_report_case.review_severity()'s answer, by page number)."""
    import sqlite3
    import gi_db
    import gi_report_case
    db = sqlite3.connect(gi_db.DB_PATH)
    db.row_factory = sqlite3.Row
    out = {}
    for f in db.execute("select * from fulltext where page_number is not null"):
        out[f"GI-{f['page_number']:02d}"] = gi_report_case.review_severity(db, f)
    return out


def review(path=REVIEW):
    """The checking list: one section per case with institution entries, the cases with a current entry or a
    fallback address first (those are what's new), then the rest; returns the path."""
    import gi_contact
    sev = severities()
    cases = sorted((d.name for d in gi_case_findings.REPORT_DIR.glob("GI-*") if entries(d.name)),
                   key=lambda c: int(c[3:]))
    new = lambda c: any(i.get("current") or (i.get("to") and not gi_contact.integrity_office(i)) for i in entries(c))
    # what gi_report_case.audiences() decided last time the case's drafts were written (severity and share)
    emailed = lambda c: any(gi_case_findings.case_dir(c).glob(f"{c}-email-institution*.eml"))
    lines = ["# Institution contacts to check", "",
             "Every case's institution entries (contacts.json). **current** = where a later author is now; "
             "**fallback** = not an integrity/ethics office, so the email asks to be forwarded "
             "(gi_contact.integrity_office()). Cases with no institution draft (low, or medium with under half the "
             "later article shared) are listed last. Fix an entry in that case's contacts.json, then regenerate with gi_report_case.py.", ""]
    for c in sorted(cases, key=lambda c: (not emailed(c), not new(c), int(c[3:]))):
        lines.append(f"## {c} ({sev.get(c, '?')}){'' if emailed(c) else ' - no institution email (low, or medium with under half shared)'}")
        for i in entries(c):
            kind = "current" if i.get("current") else "byline"
            office = ("no address" if not i.get("to") else
                      "integrity office" if gi_contact.integrity_office(i) else "**fallback**")
            lines.append(f"- **{kind}**: {i.get('name') or '?'}: `{i.get('to') or '-'}` ({office})")
            if i.get("authors"):
                lines.append(f"  - authors: {', '.join(i['authors'])}")
            if i.get("current"):
                lines.append(f"  - published while at: {i.get('published_at') or '?'}")
                lines.append(f"  - evidence: {i.get('evidence') or '?'}")
            if i.get("source"):
                lines.append(f"  - address from: {i['source']}")
            if i.get("notes") and (office != "integrity office" or i.get("current")):
                lines.append(f"  - notes: {' '.join(str(i['notes']).split())}")
        lines.append("")
    path.write_text("\n".join(lines))
    return path


EMAILS_PAGE = gi_case_findings.REPORT_DIR / "_institution_emails.html"


def emails_page(path=EMAILS_PAGE):
    """A local page linking every institution draft (GI-NN-email-institution*.eml), one row each with its
    case, severity, To, whether it goes to a current institution or a fallback address, and its subject;
    the cases with a draft to a current institution first. Returns the path."""
    import email
    import email.policy
    import html
    import gi_open_draft
    sev = severities()
    rows = []
    for eml in gi_case_findings.REPORT_DIR.glob("GI-*/GI-*-email-institution*.eml"):
        msg = email.message_from_bytes(eml.read_bytes(), policy=email.policy.default)
        body = msg.get_body(("plain",)).get_content()
        case = eml.parent.name
        kind = "current" if "now affiliated" in body else "byline"   # gi_contact: "with your institution" / "there"
        office = "fallback" if "forward this" in body else "integrity"
        cite = re.search(r"^(?:Later|First) article: (.*)$", body, re.M)   # gi_contact._plain_cite()
        cite = cite.group(1).strip() if cite else ""
        rows.append((case, sev.get(case, "?"), kind, office, msg["Bcc"] or msg["To"] or "", str(msg["Subject"] or ""), eml, cite))
    has_current = {r[0] for r in rows if r[2] == "current"}
    rows.sort(key=lambda r: (r[0] not in has_current, int(r[0][3:]), r[2] != "current", r[6].name))
    e = html.escape
    out = []
    last = None
    for case, sv, kind, office, to, subject, eml, cite in rows:
        first = case != last
        last = case
        if first:
            link = re.search(r", (https://doi\.org/\S+)$", cite)
            text = cite[:link.start()] if link else cite
            paper = (f'<a href="{e(link.group(1))}">{e(text)}</a>' if link else e(text)) or "?"
            out.append(f'<tr class="paper"><td colspan="7">{paper}</td></tr>')
        comparison = eml.parent / f"{case}-comparison.html"
        case_cell = (f'<a href="{comparison.resolve().as_uri()}">{case}</a>' if comparison.exists() else case) \
            if first else ""
        out.append(f"<tr class=\"{'first' if first else ''}\"><td>{case_cell}</td><td>{e(sv) if first else ''}</td>"
                   f"<td><span class=\"tag {kind}\">{kind}</span></td>"
                   f"<td><span class=\"tag {office}\">{office}</span></td>"
                   f"<td><a class=\"open\" href=\"{eml.resolve().as_uri()}\">Open email</a>"
                   f"<br><a class=\"tb\" href=\"{e(gi_open_draft.link(eml))}\">Thunderbird compose</a></td>"
                   f"<td>{e(to)}</td>"
                   f"<td>{e(subject)}</td></tr>")
    n_cur = sum(r[2] == "current" for r in rows)
    n_fb = sum(r[3] == "fallback" for r in rows)
    path.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Institution email drafts</title>
<style>
:root {{ --bg:#fff; --fg:#1d1d1f; --muted:#6b6b70; --line:#e4e4e7; --cur:#e8f0fe; --cur-fg:#1a56c4;
         --fb:#fff4e0; --fb-fg:#9a5b00; --ok:#e7f6ec; --ok-fg:#1d7a3e; --link:#1a56c4; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#18181b; --fg:#ececef; --muted:#a1a1aa; --line:#33333a;
         --cur:#1e2a44; --cur-fg:#8fb4ff; --fb:#3a2a10; --fb-fg:#f0b860; --ok:#16301f; --ok-fg:#7fd49a; --link:#8fb4ff; }} }}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0; padding:24px 16px; }}
h1 {{ font-size:20px; margin:0 0 4px; }} p {{ color:var(--muted); margin:0 0 16px; max-width:70em; }}
table {{ border-collapse:collapse; width:100%; }} th, td {{ text-align:left; padding:6px 10px; vertical-align:top; }}
th {{ font-weight:600; color:var(--muted); border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--bg); }}
tr.paper td {{ border-top:1px solid var(--line); padding-top:12px; padding-bottom:0; font-weight:600; }} a {{ color:var(--link); }}
.tag {{ font-size:12px; padding:1px 7px; border-radius:9px; white-space:nowrap; }}
.current {{ background:var(--cur); color:var(--cur-fg); }} .byline {{ color:var(--muted); }}
.fallback {{ background:var(--fb); color:var(--fb-fg); }} .integrity {{ background:var(--ok); color:var(--ok-fg); }}
.wrap {{ overflow-x:auto; }} a.open {{ white-space:nowrap; font-weight:600; }}
a.open {{ display:inline-block; padding:5px 12px; border-radius:6px; background:var(--link); color:var(--bg);
           text-decoration:none; font-size:14px; }}
a.tb {{ font-size:12px; color:var(--muted); }}
</style></head><body>
<h1>Institution email drafts</h1>
<p>{len(rows)} drafts in {len({r[0] for r in rows})} cases: {n_cur} to a later author's current institution, {n_fb} to a
fallback address (the email asks to be forwarded). <b>Open email</b> opens the .eml draft (your browser opens it, or
downloads it to open in Thunderbird). <b>Thunderbird compose</b> opens it straight into a compose window with the
To, subject, body and attachments filled in. That link only works after
<code>gi_open_draft.py --install</code> has been run once. Click a case for its comparison page. Cases with a
current-institution draft come first. Nothing here has been sent. Check the entries in _institutions_review.md first.</p>
<div class="wrap"><table><thead><tr><th>Case</th><th>Severity</th><th>Institution</th><th>Address</th><th>Open</th><th>Sent to (Bcc)</th>
<th>Subject</th></tr></thead><tbody>
{chr(10).join(out)}
</tbody></table></div></body></html>
""")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--sent-only", action="store_true", help="only cases notes.md records as sent")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--claude", default="claude")
    ap.add_argument("--review", action="store_true", help=f"write {REVIEW.name} and stop")
    a = ap.parse_args()
    if a.review:
        print(review())
        print(emails_page())
        return
    why = subscription_problem(a.claude)
    if why:
        sys.exit(f"not started: {why}")
    cases = a.cases or todo(a.sent_only)
    cases = cases[:a.limit] if a.limit else cases
    print(f"{len(cases)} case(s): {' '.join(cases)}", flush=True)
    queue, running = list(cases), {}
    while queue or running:
        while queue and len(running) < a.jobs:
            c = queue.pop(0)
            running[c] = start(c, a.claude)
        time.sleep(5)
        for c, p in list(running.items()):
            if p.poll() is not None:
                del running[c]
                r = result(c)
                print(f"{c}: {r[0] if r else 'stopped without a result (see backfill-current.log)'}", flush=True)


if __name__ == "__main__":
    main()

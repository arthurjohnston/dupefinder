"""Which cases have been reported, and what was sent: the record in general_index/data/gi.sqlite3.

What was sent comes from your Gmail Sent Mail, as Thunderbird keeps it on disk (SENT_MBOX, or the
GI_SENT_MBOX environment variable; read-only, ~5 s): every message since SINCE whose attachments or body
name a case (GI-NN), its audience told from the subject the drafts use (gi_contact.py). Not from notes.md:
a 2026-10-28 note there ("the user reports every draft sent") turned out wrong for most of the 71 cases it
was written into (NEXT_STEPS.md). Anything sent another way (a journal's web form) isn't in Sent Mail; say
so in the case's notes.md `## Sent` and it shows as noted-only in `status`.

The site's cases.json (example_output/general_index/) has the other half: a case's "reported" date, set
when gi_report_case.py writes its report (drafts + site page), None for a page published as not reported.

Tables, rebuilt by every run from those sources (gi_db.py's rule: a script rebuilds only its own tables):
  report_sends   one row per case per sent message: case_name, sent_at, sent_on, audience (publisher,
                 institution, authors, source-publisher, reply, other), to_addr, cc, bcc, subject, message_id
  case_reports   one row per case in either source: page_number, on_site, site_reported_on,
                 site_published_on, first_sent_on, last_sent_on, audiences, sends, noted_audiences (what
                 notes.md's ## Sent claims)
  view case_report_status   case_reports plus your review (decision, type, severity) from `reviews`

Usage:
  gi_reports.py [sync]           rebuild the tables, print the counts
  gi_reports.py status           sync, then list the mismatches
  gi_reports.py mark-reported    sync, then set cases.json "reported" (first send date) on every case on the
                                 site with a send in Gmail and no reported date; rewrites the site index.
                                 Doesn't publish pages for cases not on the site, and doesn't push.
  gi_reports.py fix-notes        replace the wrong 2026-10-28 bulk note in each notes.md with what Gmail shows
"""
import argparse
import datetime
import email.parser
import email.policy
import email.utils
import json
import os
import re
import sys
from email.header import decode_header, make_header
from pathlib import Path

import gi_db

REPORT_DIR = gi_db.DATA / "dupe_publisher"
SITE_DIR = gi_db.ROOT / "example_output" / "general_index"
SENT_MBOX = Path(os.environ.get("GI_SENT_MBOX") or Path.home() / "snap/thunderbird/current/.config/thunderbird"
                 / "phsjbylx.default/ImapMail/imap.gmail.com/[Gmail].sbd/Sent Mail")
SINCE = datetime.date(2026, 10, 1)   # the first GI report went out 2026-10-13
BULK_NOTE = "- 2026-10-28: the user reports every draft sent:"

SCHEMA = """
drop view if exists case_report_status;
drop table if exists report_sends;
drop table if exists case_reports;
create table report_sends (
    case_name text not null, sent_at text, sent_on text, audience text not null, to_addr text, cc text,
    bcc text, subject text, message_id text
);
create table case_reports (
    case_name text primary key, page_number integer, on_site integer not null, site_reported_on text,
    site_published_on text, first_sent_on text, last_sent_on text, audiences text, sends integer not null,
    noted_audiences text
);
create view case_report_status as
    select c.*, r.decision, r.type, r.severity, r.reviewed_at
    from case_reports c
    left join fulltext f on f.page_number = c.page_number
    left join reviews r on r.pair_lo = f.pair_lo and r.pair_hi = f.pair_hi;
"""

CASE = re.compile(r"\bGI-(\d+)\b")
SECTION = re.compile(r"^## Sent[ \t]*\n(.*?)(?=^## |\Z)", re.S | re.M)
DATED = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2})\b")
AUDIENCE = re.compile(r"^\s+-\s+([a-z]+(?:-[a-z]+)*?)(?:-\d+)?:\s*(.*)$")
CC = re.compile(r"\s*\(cc ([^)]*)\)")


def case_number(case):
    m = re.fullmatch(r"GI-(\d+)", case)
    return int(m.group(1)) if m else None


def audience(subject):
    """Who a report email was for, from its subject (the drafts' subjects, gi_contact.py, and the earlier
    hand-written ones)."""
    s = " ".join((subject or "").split())
    if re.match(r"re:|fwd?:", s, re.I):
        return "reply"
    if "your institution" in s:
        return "institution"
    if "you published, reused" in s:
        return "source-publisher"
    if re.search(r"paper you published|^Text overlap in |^Results data in |^Duplicated text in one of your", s):
        return "publisher"
    if re.match(r"(Text|Results data) from your |Plagiarism of an article you wrote|Text overlap between your", s):
        return "authors"
    if s.startswith("Text from "):
        return "source-publisher"
    return "other"


def _header(v):
    try:
        return " ".join(str(make_header(decode_header(v or ""))).split())
    except Exception:  # noqa: BLE001 -- a malformed header is kept as it is
        return " ".join(str(v or "").split())


def _messages(path):
    """The raw messages of an mbox, as bytes, without parsing them."""
    buf = []
    with open(path, "rb") as f:
        for line in f:
            if line.startswith(b"From ") and buf:
                yield b"".join(buf)
                buf = []
            buf.append(line)
    if buf:
        yield b"".join(buf)


def message_record(raw, since=SINCE):
    """{message_id, sent_at, to, cc, bcc, subject, cases} for one raw message naming a case, sent on or after
    `since`; None otherwise. Headers are parsed first, the whole message only when it's recent."""
    end = min((i for i in (raw.find(b"\n\n"), raw.find(b"\r\n\r\n")) if i >= 0), default=len(raw))
    body = raw[raw.find(b"\n") + 1:] if raw.startswith(b"From ") else raw
    head = email.parser.BytesHeaderParser().parsebytes(body[:end + 4])
    try:
        when = email.utils.parsedate_to_datetime(head["Date"])
    except (TypeError, ValueError):
        return None
    if when is None or when.date() < since:
        return None
    msg = email.parser.BytesParser(policy=email.policy.compat32).parsebytes(body)
    names = [_header(p.get_filename()) for p in msg.walk() if p.get_filename()]
    text = []
    for p in msg.walk():
        if p.get_content_maintype() == "text" and not p.get_filename():
            try:
                text.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace"))
            except (LookupError, AttributeError):
                pass
    cases = sorted({f"GI-{int(n):02d}" for n in CASE.findall(" ".join(names + text))})   # GI-04, GI-85
    if not cases:
        return None
    return {"message_id": head["Message-ID"], "sent_at": when.isoformat(), "to": _header(head["To"]),
            "cc": _header(head["Cc"]) or None, "bcc": _header(head["Bcc"]) or None,
            "subject": _header(head["Subject"]), "cases": cases}


def scan_sent(path=SENT_MBOX, since=SINCE):
    """Every message in the Sent Mail mbox naming a case, sent on or after `since`."""
    out = []
    for raw in _messages(path):
        rec = message_record(raw, since)
        if rec:
            out.append(rec)
    return out


def parse_noted(text):
    """The audiences notes.md's ## Sent claims were sent: audience lines (`  - publisher: addr, "subject"`)
    under a dated line. Placeholders ("nothing yet") and dated lines with nothing under them claim nothing."""
    m = SECTION.search(text)
    if not m:
        return set()
    out, date = set(), None
    for line in m.group(1).splitlines():
        if d := DATED.match(line):
            date = d.group(1)
        elif not line.startswith((" ", "\t")):
            date = None
        elif (a := AUDIENCE.match(line)) and date:
            out.add(a.group(1))
    return out


def load_site(site_dir=SITE_DIR):
    path = Path(site_dir) / "cases.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def collect(messages, report_dir=REPORT_DIR, site_dir=SITE_DIR):
    """(sends, cases): one send row per case per message, and one summary row per case in any source."""
    sends = [{"case_name": c, "sent_at": m["sent_at"], "sent_on": m["sent_at"][:10], "audience": audience(m["subject"]),
              "to_addr": m["to"], "cc": m["cc"], "bcc": m["bcc"], "subject": m["subject"],
              "message_id": m["message_id"]} for m in messages for c in m["cases"]]
    noted = {}
    for notes in Path(report_dir).glob("GI-*/notes.md"):
        if aud := parse_noted(notes.read_text(encoding="utf-8", errors="replace")):
            noted[notes.parent.name] = aud
    site = load_site(site_dir)
    names = set(site) | {s["case_name"] for s in sends} | set(noted)
    cases = []
    for case in sorted(names, key=lambda c: (case_number(c) is None, case_number(c) or 0, c)):
        mine = [s for s in sends if s["case_name"] == case]
        dates = sorted(s["sent_on"] for s in mine)
        entry = site.get(case) or {}
        cases.append({"case_name": case, "page_number": case_number(case), "on_site": int(case in site),
                      "site_reported_on": entry.get("reported"), "site_published_on": entry.get("published"),
                      "first_sent_on": dates[0] if dates else None, "last_sent_on": dates[-1] if dates else None,
                      "audiences": ",".join(sorted({s["audience"] for s in mine})) or None, "sends": len(mine),
                      "noted_audiences": ",".join(sorted(noted.get(case, ()))) or None})
    return sends, cases


def sync(db, messages, report_dir=REPORT_DIR, site_dir=SITE_DIR):
    """Rebuild report_sends and case_reports; returns (sends, cases)."""
    sends, cases = collect(messages, report_dir, site_dir)
    with db:
        db.executescript(SCHEMA)
        db.executemany("insert into report_sends values (:case_name, :sent_at, :sent_on, :audience, :to_addr, :cc, "
                       ":bcc, :subject, :message_id)", sends)
        db.executemany("insert into case_reports values (:case_name, :page_number, :on_site, :site_reported_on, "
                       ":site_published_on, :first_sent_on, :last_sent_on, :audiences, :sends, :noted_audiences)",
                       cases)
    return sends, cases


def mark_reported(cases, site_dir=SITE_DIR):
    """Set "reported" in cases.json (to the first send date) on each on-site case with a send and no reported
    date, and rewrite the index. Returns the cases changed."""
    todo = {c["case_name"]: c["first_sent_on"] for c in cases
            if c["on_site"] and c["sends"] and not c["site_reported_on"]}
    if not todo:
        return []
    path = Path(site_dir) / "cases.json"
    site = json.loads(path.read_text(encoding="utf-8"))   # re-read: the review tool may have written since
    changed = [c for c in todo if c in site and not site[c].get("reported")]
    for case in changed:
        site[case]["reported"] = todo[case]
    if changed:
        path.write_text(json.dumps(site, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        import gi_site_index
        gi_site_index.write_index(site_dir)
    return changed


def corrected_note(sends, today=None):
    """The lines that replace the bulk note: what Gmail shows was sent for the case."""
    today = today or datetime.date.today().isoformat()
    head = (f"- {today}: checked against Gmail Sent Mail. An earlier note here said every draft was sent on "
            "2026-10-28; that was wrong. ")
    if not sends:
        return [head + "Gmail has no email for this case: nothing was sent by email. Anything sent another way "
                       "(a web form) goes on a dated line here."]
    lines = [head + "Sent, per Gmail:"]
    for s in sorted(sends, key=lambda s: (s["sent_at"], s["audience"])):
        lines.append(f"  - {s['sent_on']} {s['audience']}: {s['to_addr']}"
                     + (f" (cc {s['cc']})" if s["cc"] else "") + (f" (bcc {s['bcc']})" if s["bcc"] else "")
                     + f", \"{s['subject']}\"")
    return lines


def fix_note(text, sends, today=None):
    """notes.md with the bulk note's block (its line and the indented lines under it) replaced by
    corrected_note(); None when there's no bulk note."""
    lines = text.split("\n")
    try:
        i = lines.index(BULK_NOTE)
    except ValueError:
        return None
    j = i + 1
    while j < len(lines) and lines[j].startswith((" ", "\t")):
        j += 1
    return "\n".join(lines[:i] + corrected_note(sends, today) + lines[j:])


def fix_notes(sends, report_dir=REPORT_DIR):
    """fix_note() on every notes.md with the bulk note; returns the cases changed."""
    changed = []
    for notes in sorted(Path(report_dir).glob("GI-*/notes.md")):
        case = notes.parent.name
        new = fix_note(notes.read_text(encoding="utf-8"), [s for s in sends if s["case_name"] == case])
        if new is not None:
            notes.write_text(new, encoding="utf-8")
            changed.append(case)
    return changed


def mismatches(cases):
    key = lambda c: c["page_number"] or 0  # noqa: E731
    sent_unmarked = sorted((c for c in cases if c["sends"] and not c["site_reported_on"]), key=key)
    marked_unsent = sorted((c for c in cases if c["site_reported_on"] and not c["sends"]), key=key)
    noted_only = sorted((c for c in cases if c["noted_audiences"] and
                         set(c["noted_audiences"].split(",")) - set((c["audiences"] or "").split(","))), key=key)
    return sent_unmarked, marked_unsent, noted_only


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", nargs="?", default="sync", choices=("sync", "status", "mark-reported", "fix-notes"))
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--sent-mbox", default=str(SENT_MBOX), help="Thunderbird's Sent Mail mbox file")
    args = ap.parse_args()
    messages = scan_sent(args.sent_mbox)
    db = gi_db.connect(args.db)
    sends, cases = sync(db, messages)
    print(f"{len(messages)} report emails in Sent Mail since {SINCE}, {len(sends)} case sends in "
          f"{sum(1 for c in cases if c['sends'])} cases; {sum(1 for c in cases if c['site_reported_on'])} "
          "cases marked reported on the site")
    if args.command == "mark-reported":
        changed = mark_reported(cases)
        print(f"marked reported: {' '.join(changed) or 'none'}")
        sends, cases = sync(db, messages) if changed else (sends, cases)
    if args.command == "fix-notes":
        changed = fix_notes(sends)
        print(f"notes.md corrected ({len(changed)}): {' '.join(changed) or 'none'}")
        sends, cases = sync(db, messages) if changed else (sends, cases)
    if args.command in ("status", "mark-reported", "fix-notes"):
        sent_unmarked, marked_unsent, noted_only = mismatches(cases)
        print(f"\nsent, not marked reported ({len(sent_unmarked)}):")
        for c in sent_unmarked:
            print(f"  {c['case_name']:8} {'on site' if c['on_site'] else 'not on site':12} first sent "
                  f"{c['first_sent_on']}  {c['audiences']}")
        print(f"\nmarked reported, nothing in Sent Mail ({len(marked_unsent)}):")
        for c in marked_unsent:
            print(f"  {c['case_name']:8} reported {c['site_reported_on']}")
        print(f"\nnotes.md claims a send Sent Mail doesn't have ({len(noted_only)}):")
        for c in noted_only:
            print(f"  {c['case_name']:8} notes: {c['noted_audiences']}  Gmail: {c['audiences'] or '-'}")


if __name__ == "__main__":
    sys.exit(main())

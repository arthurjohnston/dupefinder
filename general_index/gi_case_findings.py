"""The extra findings for one case that go out beside the measured ones, and the case's PubPeer link.

data/dupe_publisher/GI-NN/findings.md is written by the gi-cases agent (gi_case_prep.py runs it in the
background) and ticked by you. Only [x] items are published, in the PubPeer Markdown and every email:

    # GI-NN findings
    (free text: who wrote it and when)

    ## Findings
    - [ ] Table 2 of the later article (p. 6) reports the same sample sizes as Table 3 of the earlier (p. 5).
      - checked: both PDFs, tables compared row by row      <- indented "- " lines are evidence, for you only
    - [x] ...                                               <- include
    - [-] ...                                               <- leave out

    ## Questions
    - anything the agent couldn't settle (never published)

An item's text may wrap onto indented lines that don't start with "- ". Items before any "## " heading
count as findings; everything under another heading (Questions, say) is ignored.

data/dupe_publisher/GI-NN/pubpeer_url.txt: the address of your PubPeer post once it's up
(gi_report_case.py --pubpeer-url); the emails then point to it.

data/dupe_publisher/GI-NN/contacts.json: the case's own addresses, found by the agent (the journals' are in
journal_contacts.json, shared across cases):

    {"institution": {"to": "rio@univ.example", "name": "Research Integrity Office, Univ. X",
                     "source": "https://univ.example/integrity", "checked": "2026-10-17"},
     "authors": {"to": "a@b.example", "source": "the earlier article, p. 1", "checked": "2026-10-17"}}

"to" is the email's To (comma-separated for several). No "to", no draft to the institution / the authors.

Usage: gi_case_findings.py GI_NUMBER      go through the case's findings (y / n / e / Enter / q), then
                                          regenerate its report files
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

import gi_db

REPORT_DIR = gi_db.DATA / "dupe_publisher"
ITEM = re.compile(r"^- \[([ xX-])\]\s?(.*)$")
MARKS = {" ": "pending", "x": "include", "X": "include", "-": "leave out"}


def case_dir(case, report_dir=REPORT_DIR):
    return Path(report_dir) / case


def findings_path(case, report_dir=REPORT_DIR):
    return case_dir(case, report_dir) / "findings.md"


def parse(text):
    """The findings items: [{"state": pending / include / leave out, "text", "evidence": [...], "start", "end"}],
    start/end the line range of the item's own text (evidence lines come after end)."""
    lines = text.split("\n")
    items, section, cur = [], None, None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section, cur = line[3:].strip().lower(), None
            continue
        if section not in (None, "findings"):
            continue
        m = ITEM.match(line)
        if m:
            cur = {"state": MARKS[m.group(1)], "text": m.group(2).strip(), "evidence": [], "start": i, "end": i + 1}
            items.append(cur)
            continue
        if not line.strip() or not line[:1].isspace():   # a blank or unindented line ends the item
            cur = None
        if cur is None:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            cur["evidence"].append(stripped[2:])
        elif cur["evidence"]:
            cur["evidence"][-1] += " " + stripped
        else:   # the item's text wrapped
            cur["text"] += " " + stripped
            cur["end"] = i + 1
    return items


def load(case, report_dir=REPORT_DIR):
    """parse() of the case's findings.md, [] when there isn't one."""
    p = findings_path(case, report_dir)
    return parse(p.read_text(encoding="utf-8")) if p.exists() else []


def included(case, report_dir=REPORT_DIR):
    """The texts of the [x] items, in file order: what gets published."""
    return [it["text"] for it in load(case, report_dir) if it["state"] == "include"]


def counts(case, report_dir=REPORT_DIR):
    """{pending, include, leave out: n} for the case's findings."""
    out = {s: 0 for s in ("pending", "include", "leave out")}
    for it in load(case, report_dir):
        out[it["state"]] += 1
    return out


def pending_cases(report_dir=REPORT_DIR):
    """[(case, n pending)] for every case with findings still to check, in case order (GI-80 before GI-235)."""
    out = []
    for p in sorted(Path(report_dir).glob("*/findings.md"), key=lambda p: (len(p.parent.name), p.parent.name)):
        n = counts(p.parent.name, report_dir)["pending"]
        if n:
            out.append((p.parent.name, n))
    return out


def update(text, changes):
    """text with each item changed: changes = {item index: (state, new text or None)}. Evidence lines stay."""
    lines = text.split("\n")
    items = parse(text)
    mark = {"pending": " ", "include": "x", "leave out": "-"}
    for k in sorted(changes, reverse=True):   # bottom up, so earlier line numbers stay valid
        it = items[k]
        state, new = changes[k]
        lines[it["start"]:it["end"]] = [f"- [{mark[state]}] {new if new is not None else it['text']}"]
    return "\n".join(lines)


def prefilled_input(prompt, text):
    """input() with text already typed in, to edit (readline where there is one, else plain input)."""
    try:
        import readline
    except ImportError:
        return input(prompt) or text
    readline.set_startup_hook(lambda: readline.insert_text(text))
    try:
        return input(prompt)
    finally:
        readline.set_startup_hook()


def review(case, ask=input, edit=prefilled_input, report_dir=REPORT_DIR, only_pending=True):
    """Go through the case's findings: y include, n leave out, e edit the wording (then include), Enter leave
    as it is, q stop. Writes findings.md; returns the number of items changed."""
    p = findings_path(case, report_dir)
    if not p.exists():
        print(f"  {case}: no findings.md yet")
        return 0
    text = p.read_text(encoding="utf-8")
    items = parse(text)
    todo = [k for k, it in enumerate(items) if it["state"] == "pending" or not only_pending]
    if not todo:
        print(f"  {case}: nothing to check ({len(items)} finding(s), all decided)")
        return 0
    changes = {}
    for n, k in enumerate(todo, 1):
        it = items[k]
        print(f"\n  {case} finding {n} of {len(todo)} [{it['state']}]:\n    {it['text']}")
        for ev in it["evidence"]:
            print(f"      - {ev}")
        while True:
            ans = ask("  y include  n leave out  e edit  Enter as it is  q stop > ").strip().lower()
            if ans in ("", "y", "n", "e", "q"):
                break
        if ans == "q":
            break
        if ans == "y":
            changes[k] = ("include", None)
        elif ans == "n":
            changes[k] = ("leave out", None)
        elif ans == "e":
            new = " ".join(edit("  > ", it["text"]).split())
            changes[k] = ("include", new or None)
    if changes:
        p.write_text(update(text, changes), encoding="utf-8")
    return len(changes)


def case_contacts(case, report_dir=REPORT_DIR):
    """contacts.json for the case, {} when there isn't one (or it isn't valid JSON: say so, don't fail).
    "institution" is one entry or a list (one email per institution, gi_contact.institutions()), each
    {to, cc, name, authors: [the later authors there], source, checked, notes}; "authors" is the earlier
    article's authors' {to, cc, names: [every author on its byline], source, checked, notes}."""
    import json
    p = case_dir(case, report_dir) / "contacts.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        print(f"  {p}: not valid JSON ({e}), ignored")
        return {}
    return data if isinstance(data, dict) else {}


def pubpeer_url_path(case, report_dir=REPORT_DIR):
    return case_dir(case, report_dir) / "pubpeer_url.txt"


def pubpeer_url(case, report_dir=REPORT_DIR):
    """Your PubPeer post's address for the case, None until you've given it."""
    p = pubpeer_url_path(case, report_dir)
    url = p.read_text(encoding="utf-8").strip() if p.exists() else ""
    return url or None


def save_pubpeer_url(case, url, report_dir=REPORT_DIR):
    if not re.match(r"^https://(www\.)?pubpeer\.com/\S+$", url):
        raise ValueError(f"not a PubPeer address: {url!r}")
    p = pubpeer_url_path(case, report_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(url + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gi", type=int)
    ap.add_argument("--all", action="store_true", help="go through every finding, not just the pending ones")
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    args = ap.parse_args()
    case = f"GI-{args.gi:02d}"
    if review(case, only_pending=not args.all):
        import gi_report_case
        db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
        db.row_factory = sqlite3.Row
        f = db.execute("select * from fulltext where page_number = ?", (args.gi,)).fetchone()
        if f is None:
            sys.exit(f"GI-{args.gi}: no such page")
        gi_report_case.write_report(db, f, sender=gi_report_case.git_email())
        print(f"  {case}: report files regenerated with {len(included(case))} extra finding(s)")


if __name__ == "__main__":
    main()

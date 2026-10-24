"""Everything for reporting one full-text-checked pair (one GI-NN result page), in one go.

Into data/dupe_publisher/GI-NN/:
  GI-NN-pubpeer.html     the PubPeer page (gi_pubpeer.write_page(): the Markdown findings with a Copy button,
                         the title pages as images, the passages as text)
  GI-NN-titles.jpg, GI-NN-passage-K.jpg
                         the images for the PubPeer comment: both titles and bylines, and gi_pubpeer.PASSAGE_IMAGES
                         passages as printed in both PDFs, side by side (gi_pubpeer.write_page())
  GI-NN-longest.jpg      passage 1 again, attached to the emails
  GI-NN-comparison.html  the attachment for the journal: the same comparison without the PubPeer box or the
                         title images, with the shared-text figures and the longest passage's image at the top
  GI-NN-email-*.eml      draft emails (all four for a high-severity case, only -publisher otherwise; see
                         audiences()) (RFC 822 .eml with X-Unsent: 1, From your git user.email or --from, To
                         and Cc from data/journal_contacts.json for the two journal emails, empty otherwise), each with the same findings as the PubPeer Markdown, extracts of the
                         the comparison page and the image attached, and how much else depends on who it's
                         for (gi_contact.AUDIENCE_LENGTH; the journal's also quotes the EMAIL_PASSAGES (3)
                         longest passages):
                           -publisher         the journal that published the later article
                           -institution       the later authors' institution; one per institution in
                                              contacts.json (-institution-2, -3, ...), each naming its authors
                           -authors           the earlier article's authors (not when the bylines share names)
                           -source-publisher  the earlier article's publisher
                         When the publication years are the same, none of them says who copied whom.

And on the project site (example_output/, which .github/workflows/pages.yml publishes from main to
SITE_URL), unless --no-site:
  example_output/general_index/GI-NN.html   the same file as GI-NN-comparison.html
  example_output/general_index/index.html   every published pair, rebuilt from cases.json there (one entry per
                                            case; the date first reported is kept on a re-run)

data/journal_contacts.json keeps each article's journal and publisher (asked of Crossref once per DOI) and, per
journal, a "to"/"cc"/"page"/"notes" entry that is blank until you fill it in; every later case in that journal then
gets those addresses in its drafts. "page" is for a journal with no email: the web page to report it on
instead (a complaints or contact form, or the page naming the editor), found by the gi-cases agent; the draft is
still written, to paste from, and write_report() returns the page as "page publisher" / "page source-publisher".
Re-run the case after filling one in. The institution's and the authors'
addresses are the case's own, in GI-NN/contacts.json (gi_case_findings.case_contacts()): with no institution
or authors' address there, a high case gets no -institution / -authors draft at all (nobody to send it to).

--publish-only writes just the site page and index entry, marked "not reported": for a pair you judged not
worth reporting (low, say) whose comparison should still be public.

Nothing is committed or pushed: the site page goes live once example_output/ is pushed to main.

Severity decides which emails are written: high (your review's severity in `reviews`, or --severity) gets
all four; low, medium or no review (medium is the default) gets only the email to the later article's
journal, alongside the PubPeer page. A ticked [data] finding in findings.md makes a case high. Drafts a case
no longer gets (it was high, now isn't) are deleted.

gi_review_cli.py runs this when you save (or agree with) a problem verdict, and on t at its prompt, then
opens the gi-cases agent and asks for the findings, the PubPeer link and whether to open the drafts.

--open-drafts then opens the drafts written as Thunderbird compose windows (thunderbird -compose: subject, From,
body from compose/GI-NN-email-*.txt, both attachments), so only the To line is left to fill in.

The findings you ticked in data/dupe_publisher/GI-NN/findings.md (gi_case_findings.py; the gi-cases agent
proposes them, gi_case_prep.py) go into the PubPeer Markdown and every email after the measured ones. Once
your PubPeer post is up, --pubpeer-url URL saves it (pubpeer_url.txt) and the emails then link to it.

Usage: gi_report_case.py GI_NUMBER [GI_NUMBER ...] [--problems] [--no-site] [--from ADDRESS] [--open-drafts]
                         [--severity low|medium|high] [--pubpeer-url URL] [--db data/gi.sqlite3]
       --problems: every pair you've reviewed as a problem
"""
import argparse
import datetime
import email
import email.policy
import html
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import gi_case_findings
import gi_contact
import gi_db
import gi_pubpeer as pp

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = gi_db.DATA / "dupe_publisher"
SITE_DIR = ROOT / "example_output" / "general_index"
SITE_URL = "https://arthurjohnston.github.io/dupefinder/general_index/"


def git_email():
    """The From address for the drafts: this repo's git user.email (None if unset)."""
    return gi_contact.git_config("user.email")


DEFAULT_SEVERITY = "medium"
ALL_EMAILS_SEVERITY = "high"


# the emails whose address is the case's own (contacts.json): no address there, no draft
CASE_AUDIENCES = {"institution": "research-integrity address", "authors": "address for the earlier authors"}


def audiences(severity, fd=None):
    """The emails a case of this severity gets: all of gi_contact.EMAIL_AUDIENCES when high (the earlier
    authors, their publisher and the later authors' institution too), else only the later article's journal.
    With fd: never the earlier authors when the two bylines share names (they'd be writing to the people who
    reused it)."""
    if severity != ALL_EMAILS_SEVERITY:
        return ("publisher",)
    if fd and gi_contact.shared_authors(fd):
        return tuple(a for a in gi_contact.EMAIL_AUDIENCES if a != "authors")
    return gi_contact.EMAIL_AUDIENCES


def review_severity(db, f):
    """Your review's severity for the pair, else DEFAULT_SEVERITY (not reviewed, or no severity)."""
    try:
        row = db.execute("select severity from reviews where pair_lo = ? and pair_hi = ?",
                         (f["pair_lo"], f["pair_hi"])).fetchone()
    except (sqlite3.OperationalError, KeyError, IndexError):   # no reviews table, or no pair key
        row = None
    return row[0] if row and row[0] in ("low", "medium", "high") else DEFAULT_SEVERITY


def case_findings(db, f, report_dir=REPORT_DIR):
    """pp.findings() plus your ticked findings.md items, the PubPeer post and both journals' names/contacts."""
    case = pp.case_name(f)
    fd = pp.findings(db, f)
    fd["case"] = case
    fd["data_extra"] = gi_case_findings.included(case, report_dir, data=True)   # your ticked [data] items
    fd["extra"] = gi_case_findings.included(case, report_dir, data=False)   # and the rest
    fd["pubpeer_post"] = gi_case_findings.pubpeer_url(case, report_dir)
    fd["case_contacts"] = gi_case_findings.case_contacts(case, report_dir)   # the institution's, the authors'
    contacts = gi_contact.load_contacts()
    for side in ("first", "second"):   # the journal names and contacts, for the emails and the index
        c = gi_contact.journal_contact(fd[side]["doi"], contacts)
        fd[side]["journal"] = c and c["journal"]
        fd[side]["contact"] = c
    gi_contact.save_contacts(contacts)
    return fd


def write_site(site_dir, case, page, fd, reported=True):
    """The case's page on the project site, its cases.json entry and the index; returns {site, index}."""
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    site = site_dir / f"{case}.html"
    site.write_text(page, encoding="utf-8")
    record(site_dir, case, fd, reported=reported)
    return {"site": site, "index": write_index(site_dir)}


def publish_only(db, f, site_dir=SITE_DIR, report_dir=REPORT_DIR):
    """Only the project-site page and index entry, marked not reported: for a pair you judged not worth
    reporting (low, say) whose comparison should still be public. No PubPeer page, no emails."""
    fd = case_findings(db, f, report_dir)
    page, _ = pp.publisher_page(f, fd)
    return write_site(site_dir, pp.case_name(f), page, fd, reported=False)


def write_report(db, f, report_dir=REPORT_DIR, site_dir=SITE_DIR, sender=None, severity=None):
    """Write the case's files; returns {kind: path}. severity (default: the pair's review, see review_severity())
    picks the emails (audiences()); a ticked [data] finding makes it high whatever the review says."""
    severity = severity or review_severity(db, f)
    case = pp.case_name(f)
    out = Path(report_dir) / case
    out.mkdir(parents=True, exist_ok=True)
    fd = case_findings(db, f, report_dir)
    if fd.get("data_extra"):   # copied results data: everyone hears of it
        severity = ALL_EMAILS_SEVERITY
    paths = {}
    paths["pubpeer"], _ = pp.write_page(db, f, out, fd=dict(fd), name=f"{case}-pubpeer.html")
    page, shot = pp.publisher_page(f, fd)
    paths["comparison"] = out / f"{case}-comparison.html"
    paths["comparison"].write_text(page, encoding="utf-8")
    shot_path = out / f"{case}-longest.jpg"
    if shot:
        paths["image"] = shot_path
    if site_dir:
        paths.update(write_site(site_dir, case, page, fd))
    attachments = [paths["comparison"]] + ([shot_path] if shot else [])
    for old in out.glob(f"{case}-email-*.txt"):   # the plain-text drafts an earlier version wrote
        old.unlink()
    for old in out.glob(f"{case}-email-institution-*.eml"):   # one per institution: the count may have changed
        old.unlink()
        (out / "compose" / f"{old.stem}.txt").unlink(missing_ok=True)
    wanted = audiences(severity, fd)
    for audience in gi_contact.EMAIL_AUDIENCES:
        eml = out / f"{case}-email-{audience}.eml"
        has_to = (any(i.get("to") for i in gi_contact.institutions(fd)) if audience == "institution"
                  else gi_contact.recipients(audience, fd)[0])
        if audience in CASE_AUDIENCES and audience in wanted and not has_to:
            # nobody to send it to: no address found (contacts.json), so no draft
            paths[f"no email {audience}"] = f"no {CASE_AUDIENCES[audience]} in {out / 'contacts.json'}"
            wanted = tuple(a for a in wanted if a != audience)
        if audience not in wanted:   # left from when the case was high: don't leave a draft to send by mistake
            eml.unlink(missing_ok=True)
            (out / "compose" / f"{eml.stem}.txt").unlink(missing_ok=True)
            continue
        if audience == "institution":   # one email per institution with an address, naming its authors
            for k, inst in enumerate([i for i in gi_contact.institutions(fd) if i.get("to")], 1):
                key, name = ("email institution", eml) if k == 1 else (
                    f"email institution {k}", out / f"{case}-email-institution-{k}.eml")
                msg = gi_contact.email_message(fd, attachments, audience=audience, sender=sender, contact=inst)
                name.write_bytes(bytes(msg))
                paths[key] = name
            continue
        msg = gi_contact.email_message(fd, attachments, audience=audience, sender=sender)
        paths[f"email {audience}"] = out / f"{case}-email-{audience}.eml"
        paths[f"email {audience}"].write_bytes(bytes(msg))
        page = gi_contact.report_page(audience, fd)
        if page:   # no email for the journal: the draft is for pasting into its web page
            paths[f"page {audience}"] = page
    return paths


def compose_arg(eml_path):
    """Thunderbird's -compose argument for one .eml draft: its subject, From and body (written to
    compose/<name>.txt beside it, since -compose takes the body as a file), and its attachments as the files
    of the same names in the same folder. Values are single-quoted, so a ' in the subject becomes \u2019."""
    eml_path = Path(eml_path)
    msg = email.message_from_bytes(eml_path.read_bytes(), policy=email.policy.default)
    body = (eml_path.parent / "compose" / (eml_path.stem + ".txt")).resolve()   # Thunderbird's cwd isn't ours
    body.parent.mkdir(exist_ok=True)
    body.write_text(msg.get_body(("plain",)).get_content(), encoding="utf-8")
    files = [eml_path.parent / a.get_filename() for a in msg.iter_attachments()]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"{eml_path.name}: attachment(s) not found beside it: {', '.join(missing)}")
    parts = [f"subject='{str(msg['Subject']).replace(chr(39), chr(0x2019))}'", f"message='{body}'",
             "format='text'"]
    if msg["From"]:
        parts.append(f"from='{msg['From']}'")
    for key in ("To", "Cc"):
        if msg[key]:
            parts.append(f"{key.lower()}='{msg[key]}'")
    if files:
        parts.append("attachment='" + ",".join(f.resolve().as_uri() for f in files) + "'")
    return ",".join(parts)


def open_drafts(emls, thunderbird="thunderbird"):
    """Open each .eml draft as a Thunderbird compose window (To left for you to fill in). The first call may
    have to start Thunderbird, so the rest wait for it rather than racing a second instance."""
    for i, eml in enumerate(emls):
        subprocess.Popen([thunderbird, "-compose", compose_arg(eml)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if i < len(emls) - 1:
            time.sleep(8 if i == 0 else 2)


def record(site_dir, case, fd, reported=True):
    """Add or update the case in site_dir/cases.json, keeping the date it was first reported (or published).
    reported=False (publish_only()) leaves "reported" None, unless the case already was reported."""
    path = Path(site_dir) / "cases.json"
    cases = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    keep = ("title", "year", "doi", "pct", "journal")
    today = datetime.date.today().isoformat()
    old = cases.get(case, {})
    cases[case] = {"file": f"{case}.html", "reported": old.get("reported") or (today if reported else None),
                   "published": old.get("published") or old.get("reported") or today,
                   "earlier_known": fd["earlier_known"],
                   "later": {k: fd["second"].get(k) for k in keep}, "earlier": {k: fd["first"].get(k) for k in keep},
                   "matched_words": fd["matched_words"], "runs": fd["runs"],
                   "longest": fd["passages"][0]["words"] if fd["passages"] else None}
    path.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def case_key(case):
    """GI-235 sorts as 235, after GI-80."""
    tail = case.rsplit("-", 1)[-1]
    return (0, int(tail), case) if tail.isdigit() else (1, 0, case)


INDEX_STYLE = """
:root {
  --bg: #f2f4ef; --surface: #ffffff; --ink: #1a1f1a; --ink-muted: #565f57;
  --border: #d7dbd0; --accent-strong: #7c4a15;
  --font-display: "Source Serif 4", Georgia, "Times New Roman", serif;
  --font-body: "Source Sans 3", -apple-system, "Segoe UI", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14170f; --surface: #1c2016; --ink: #e9ede4; --ink-muted: #a2ab98;
    --border: #333a29; --accent-strong: #f0b46a;
  }
}
:root[data-theme="dark"] {
  --bg: #14170f; --surface: #1c2016; --ink: #e9ede4; --ink-muted: #a2ab98;
  --border: #333a29; --accent-strong: #f0b46a;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 var(--font-body); }
main { max-width: 1300px; margin: 0 auto; padding: 2.5rem 16px 5rem; }
main > p { max-width: 78ch; }
h1 { font-family: var(--font-display); line-height: 1.2; font-size: 2.2rem; margin: 0 0 1rem; }
a { color: var(--accent-strong); }
.table-wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .45rem .7rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-muted); font-weight: 600; white-space: nowrap; }
td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
td.art { min-width: 18rem; }
"""


def _article(p):
    title = html.escape((p["title"] or p["doi"] or "").replace("*", ""))
    doi = p["doi"] or ""
    link = pp.doi_url(doi)
    year = f" ({p['year']})" if p["year"] else ""
    if p.get("journal"):
        year += f", <i>{html.escape(p['journal'])}</i>"
    return f'{title}{year}<br><a href="{html.escape(link)}">doi:{html.escape(doi)}</a>' if link else title + year


def is_reported(case, site_dir=SITE_DIR):
    """The date the case was reported on the site (cases.json), None when it wasn't or isn't there."""
    path = Path(site_dir) / "cases.json"
    cases = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return (cases.get(case) or {}).get("reported")


def take_down(case, site_dir=SITE_DIR, report_dir=REPORT_DIR):
    """Undo reporting a case you've changed your mind about: its site page and cases.json entry removed and the
    index rewritten (not pushed: gi_publish_site.py does that), and its report files (PubPeer page,
    comparison, image, email drafts) moved to GI-NN/withdrawn/, so nothing treats it as reported. The
    agent's findings, contacts and notes stay. Emails already sent and a PubPeer post already up are not
    undone. Returns the list of files moved."""
    site_dir = Path(site_dir)
    path = site_dir / "cases.json"
    cases = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if cases.pop(case, None) is not None:
        path.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (site_dir / f"{case}.html").unlink(missing_ok=True)
    if path.exists():
        write_index(site_dir)
    out = Path(report_dir) / case
    gone = out / "withdrawn"
    moved = []
    for f in sorted(out.glob(f"{case}-*")):
        if f.is_file():
            gone.mkdir(exist_ok=True)
            f.replace(gone / f.name)
            moved.append(gone / f.name)
    compose = out / "compose"
    if compose.is_dir():
        gone.mkdir(exist_ok=True)
        if (gone / "compose").exists():   # taken down before: this one's drafts replace those
            import shutil
            shutil.rmtree(gone / "compose")
        compose.replace(gone / "compose")
        moved.append(gone / "compose")
    return moved


def write_index(site_dir):
    site_dir = Path(site_dir)
    cases = json.loads((site_dir / "cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in sorted(cases, key=case_key):
        c = cases[case]
        later, earlier = c["later"], c["earlier"]
        rows.append(f"""<tr><td class="num"><a href="{html.escape(c['file'])}">{html.escape(case)}</a></td>
<td class="art">{_article(later)}</td><td class="art">{_article(earlier)}</td>
<td class="num">{pp.pct(later['pct'])} / {pp.pct(earlier['pct'])}</td>
<td class="num">{c['longest'] or '?'} words</td><td class="num">{html.escape(c['reported'] or 'not reported')}</td></tr>""")
    path = site_dir / "index.html"
    path.write_text(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>General Index: checked pairs</title>
<style>{INDEX_STYLE}</style>
</head>
<body>
<main>
<h1>General Index: checked pairs</h1>
<p>Pairs of articles found by this project's scan of the
<a href="https://archive.org/details/GeneralIndex">General Index</a> (word n-grams from over 100 million
articles), checked against both articles' full text, and reported to the journal concerned, except those marked
"not reported" (judged not worth a report, but published so the comparison is public). Each page shows
the measured overlap: the shared passages side by side with the differences marked, and the longest one as
printed in both articles. "Shared text" is the share of each article's words that fall inside a run of 10 or
more matching words (allowing substituted words within a run). A page shows measured text overlap; it does
not establish why the overlap exists, and none of these pages is a finding of misconduct by any named
person.</p>
<p>Where the publication years differ, the later article is listed first. Where they are the same, which
came first is unknown and the order means nothing.</p>
<div class="table-wrap"><table>
<thead><tr><th>Case</th><th>Later article</th><th>Earlier article</th><th>Shared text (later / earlier)</th>
<th>Longest passage</th><th>Reported</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table></div>
<p><a href="../">Example output</a></p>
</main>
</body>
</html>
""", encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gi", nargs="*", type=int, help="GI page numbers")
    ap.add_argument("--problems", action="store_true", help="every pair you've reviewed as a problem")
    ap.add_argument("--no-site", action="store_true", help="don't write the project-site page or index")
    ap.add_argument("--from", dest="sender", help="From address for the drafts (default: git config user.email)")
    ap.add_argument("--open-drafts", action="store_true",
                    help="open the drafts as Thunderbird compose windows, attachments included")
    ap.add_argument("--severity", choices=("low", "medium", "high"),
                    help="which emails to write: high = all four, otherwise only the later journal's "
                         "(default: your review's severity, else medium)")
    ap.add_argument("--pubpeer-url", help="your PubPeer post for the case (one GI number): saved to "
                                          "pubpeer_url.txt, and the emails point to it")
    ap.add_argument("--publish-only", action="store_true",
                    help="only the project-site page and index entry, marked not reported (no PubPeer page, "
                         "no emails): for a pair not worth reporting whose comparison should still be public")
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    args = ap.parse_args()
    if args.publish_only and args.no_site:
        ap.error("--publish-only writes only the site page, so not with --no-site")
    if args.pubpeer_url:
        if len(args.gi) != 1 or args.problems:
            ap.error("--pubpeer-url goes with exactly one GI number")
        try:
            gi_case_findings.save_pubpeer_url(f"GI-{args.gi[0]:02d}", args.pubpeer_url)
        except ValueError as e:
            ap.error(str(e))
    sender = args.sender or git_email()
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
    db.row_factory = sqlite3.Row
    rows = [db.execute("select * from fulltext where page_number = ?", (n,)).fetchone() or n for n in args.gi]
    if args.problems:
        rows += db.execute("""select f.* from fulltext f join reviews r using (pair_lo, pair_hi)
                              where r.decision = 'problem' and f.page_number is not null
                              order by f.page_number""").fetchall()
    if not rows:
        ap.error("name some GI numbers, or --problems")
    for f in rows:
        if isinstance(f, int):
            print(f"GI-{f}: no such page", file=sys.stderr)
            continue
        try:
            if args.publish_only:
                paths = publish_only(db, f)
            else:
                paths = write_report(db, f, site_dir=None if args.no_site else SITE_DIR, sender=sender,
                                     severity=args.severity)
            for kind, path in paths.items():
                print(f"{kind:>10}  {path}")
            if args.open_drafts:
                open_drafts([p for k, p in paths.items() if k.startswith("email ")])
        except Exception as e:  # one pair failing shouldn't stop a batch
            print(f"GI-{f['page_number']}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

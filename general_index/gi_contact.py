"""The emails about a reported pair: one each to the journal that published the later article, the later
authors' institution, the earlier article's authors and the earlier article's publisher (EMAIL_AUDIENCES).

The wording is here: _email_intro() has what differs per recipient (subject, greeting, why I'm writing, the
request at the end), email_parts() the body they share. email_message() makes a draft .eml of one, with the
comparison page and the longest passage's image attached; gi_report_case.py writes the four for a case.

The findings come from gi_pubpeer.findings(), so an email states the same measurements as the PubPeer
Markdown. When the publication years are the same, none of them says who copied whom.
"""
import email.message
import json
import mimetypes
import subprocess
from pathlib import Path

import gi_db
from gi_pubpeer import doi_url, pct, whole_document

ROOT = Path(__file__).resolve().parent.parent
CONTACTS = gi_db.DATA / "journal_contacts.json"
CROSSREF = "https://api.crossref.org/works/"
EMAIL_PASSAGES = 10   # the longest passages quoted in an email (the attachment has more)


def git_config(key):
    """A git config value for this repo (user.name for the sign-off, user.email for From), or None."""
    p = subprocess.run(["git", "-C", str(ROOT), "config", key], capture_output=True, text=True)
    return p.stdout.strip() or None


def load_contacts(path=CONTACTS):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("journals", {})
    data.setdefault("dois", {})
    return data


def save_contacts(data, path=CONTACTS):
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def crossref_journal(doi, get=None):
    """{journal, publisher, issn} for a DOI from Crossref's public API (no key; the index has no journal
    names), or None when there's no DOI or the lookup fails."""
    if not doi_url(doi):
        return None
    if get is None:
        import requests
        get = requests.get
    try:
        r = get(CROSSREF + doi, timeout=20,
                headers={"User-Agent": "dupefinder (https://github.com/arthurjohnston/dupefinder)"})
        r.raise_for_status()
        m = r.json()["message"]
    except Exception:
        return None
    return {"journal": (m.get("container-title") or [None])[0], "publisher": m.get("publisher"),
            "issn": m.get("ISSN") or []}


def journal_contact(doi, data, get=None):
    """The journal entry for a DOI: {journal, publisher, issn, to, cc, notes}, the to/cc/notes as filled in
    by hand in journal_contacts.json (empty until someone does). Crossref is asked once per DOI; the answer
    and a blank entry for a journal seen for the first time are added to `data` (save it afterwards)."""
    info = data["dois"].get(doi)
    if info is None:
        info = crossref_journal(doi, get)
        if info is None:
            return None
        data["dois"][doi] = info
    name = info["journal"] or info["publisher"]
    if not name:
        return None
    entry = data["journals"].setdefault(name, {"publisher": info["publisher"], "issn": info["issn"],
                                               "to": "", "cc": "", "notes": ""})
    return {"journal": info["journal"], **entry}


def _plain_cite(p):
    """Title (Year), Journal, https://doi.org/... for a plain-text email (the journal when it's known)."""
    title = (p["title"] or p["doi"] or "").replace("*", "")
    year = f" ({p['year']})" if p["year"] else ""
    journal = f", {p['journal']}" if p.get("journal") else ""
    link = doi_url(p["doi"])
    return f"{title}{year}{journal}" + (f", {link}" if link else "")


EMAIL_AUDIENCES = ("publisher", "institution", "authors", "source-publisher")


def institutions(fd):
    """The later authors' institutions from the case's contacts.json (fd["case_contacts"]["institution"]): one
    entry, or a list of them (one email each), each {to, cc, name, authors: [the later article's authors at that
    institution, as printed], source, checked, notes}. [] when there's none."""
    inst = (fd.get("case_contacts") or {}).get("institution")
    if isinstance(inst, dict):
        return [inst]
    return [i for i in inst if isinstance(i, dict)] if isinstance(inst, list) else []


def name_list(names):
    """'A', 'A and B', 'A, B and C'."""
    names = [n for n in names if n]
    return " and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1]


def victim_count(fd):
    """How many authors the earlier article has: contacts.json's authors.names when given (as printed on the
    article), else the index metadata's byline (fd["first"]["authors"]); 0 when neither says."""
    names = ((fd.get("case_contacts") or {}).get("authors") or {}).get("names")
    if isinstance(names, list) and names:
        return len(names)
    return len(fd["first"].get("authors") or [])


def _email_intro(audience, fd, contact=None):
    """(who it goes to, subject, greeting, opening lines, closing request) for one recipient. The journal that
    published the later article ("publisher"), the later authors' institution (contact: its institutions()
    entry, whose authors are named), the earlier article's authors ("Dear Professor/Doctor" when there is only
    one, victim_count()), and the earlier article's publisher. When the order is unknown, nothing says who
    copied whom."""
    first, second = fd["first"], fd["second"]
    known = fd["earlier_known"]
    later = (second["title"] or second["doi"] or "").replace("*", "")
    earlier = (first["title"] or first["doi"] or "").replace("*", "")
    doi = f" (doi:{second['doi']})" if doi_url(second["doi"]) else ""
    share = second["pct"] or 0
    verb = "to be largely a duplicate of" if share >= 50 else "to reuse substantial text from"
    distinct = fd["byline"] == "no byline overlap"   # only then say "other authors"
    by_others = " by other authors" if distinct else ""
    pair = ("two articles, published the same year, that share extensive text. Which one came first can't "
            "be told from the publication years.")
    if audience == "publisher":
        return ("[the journal's editor, or the publisher's research integrity contact]",
                f"Text overlap in {later}{doi}", "Dear Editor,",
                [f"I am writing about an article in your journal that appears {verb} an earlier article."
                 if known else f"I am writing about {pair} The first is in your journal."],
                "I would be grateful if you could look into this and let me know what you decide.")
    if audience == "institution":
        names = name_list((contact or {}).get("authors") or [])
        whose = f"{names}, affiliated with your institution" if names else "authors affiliated with your institution"
        return ("", f"Text overlap in {later}{doi}", "Dear Research Integrity Officer,",
                [f"I am writing about an article by {whose}, which appears "
                 f"{verb} an earlier article{by_others}." if known else
                 f"I am writing about {pair} The first is by {whose}.",
                 "I have written to the journal that published it as well."],
                "I would be grateful if you could look into this under your institution's research integrity "
                "procedures.")
    if audience == "authors":
        one = victim_count(fd) == 1
        return ("", f"Text from your article {earlier} in a {second['year'] or 'later'} article" if known
                else f"Text overlap between your article {earlier} and another article",
                "Dear Professor/Doctor," if one else "Dear authors,",
                [f"I am writing because your article appears to have been reused in a later article{by_others}."
                 if known else
                 f"I am writing about {pair} The second is yours, and the two author lists share no names."
                 if distinct else f"I am writing about {pair} The second is yours.",
                 "I have reported this to the journal that published the "
                 + ("later" if known else "first") + " article. I thought you would want to know."],
                "If you weren't aware of it, you may want to contact that journal "
                + ("yourself." if one else "yourselves."))
    if audience == "source-publisher":
        return ("", f"Text from {earlier} in an article in another journal" if known
                else f"Text overlap between {earlier} and an article in another journal",
                "Dear Editor,",
                [f"I am writing because an article you published appears to have been reused in a later "
                 f"article{by_others}, in another journal." if known else
                 f"I am writing about {pair} The second was published by you.",
                 "I have reported this to the journal that published the "
                 + ("later" if known else "first") + " article."],
                "You may wish to raise it with that journal as well, as the publisher of the "
                + ("original" if known else "other") + " article.")
    raise ValueError(f"unknown audience {audience!r} (use: {', '.join(EMAIL_AUDIENCES)})")


def email_parts(fd, attachment, shot_name=None, examples=EMAIL_PASSAGES, audience="publisher",
                name=None, contact=None):
    """(subject, body) of a plain-text email about the pair to one of EMAIL_AUDIENCES. The body, in order:
    greeting, why I'm writing, the two articles, the same findings as the PubPeer Markdown, what's attached,
    extracts of the `examples` longest passages, the request, and the sign-off
    with `name` (default your git user.name). contact: the institutions() entry an institution email is for."""
    first, second = fd["first"], fd["second"]
    known = fd["earlier_known"]
    _to, subject, greeting, opening, request = _email_intro(audience, fd, contact)
    lines = [greeting, "", " ".join(opening), ""]
    if known:
        lines += ["Later article: " + _plain_cite(second), "Earlier article: " + _plain_cite(first), "",
                  f"- About {pct(second['pct'])} of the later article's text is also found in the earlier article "
                  f"({fd['matched_words'] or 0:,} matched words in {fd['runs']} passages; the later article has "
                  f"about {second['words']:,} words). That is {pct(first['pct'])} of the earlier article's text."]
    else:
        lines += ["First article: " + _plain_cite(second), "Second article: " + _plain_cite(first), "",
                  f"- About {pct(second['pct'])} of the first article's text is also found in the second, "
                  f"and {pct(first['pct'])} of the second article's text in the first "
                  f"({fd['matched_words'] or 0:,} matched words in {fd['runs']} passages)."]
    if fd["byline"] == "no byline overlap":
        lines.append("- The two author lists share no names.")
    which, other = ("The later", "the earlier article") if known else ("The first", "the second")
    if fd["later_cites_earlier"] is False:
        lines.append(f"- {which} article's reference list does not appear to cite {other}.")
    elif fd["later_cites_earlier"]:
        lines.append(f"- {which} article's reference list appears to cite {other}.")
    lines += [f"- {t}" for t in fd.get("extra", ())]   # the findings you ticked (gi_case_findings.py)
    if whole_document(fd):
        what = "both articles end to end as one diff, the shared text highlighted and the differences marked"
    else:
        what = f"all {fd['runs']} shared passages, the two texts side by side with the differences marked"
    lines += ["", "I also attached:", f"- {attachment}: {what} (one HTML file; open it in a web browser)."]
    if shot_name:
        lines.append(f"- {shot_name}: passage 1 as printed in both articles "
                     f"({'earlier article left, later right' if known else 'order unknown'}), the shared words "
                     f"highlighted.")
    shown = fd["passages"][:examples]
    if shown:
        n = len(shown)
        whose = "the later" if known else "the first"
        lines += ["", f"Extracts of the {'longest shared passage' if n == 1 else f'{n} longest shared passages'}"
                      f" (as worded in {whose} article, with its word count; '…' marks words left out of the "
                      f"extract):", ""]
        lines += [f"{i}. {p['words']:,} words: \"{p['excerpt']}\"" for i, p in enumerate(shown, 1)]
    if fd.get("pubpeer_post"):
        lines += ["", f"I have also posted these findings on PubPeer: {fd['pubpeer_post']}"]
    lines += ["", request, "", "Thanks", name or git_config("user.name") or "[Your name]"]
    return subject, "\n".join(lines) + "\n"


def recipients(audience, fd, contact=None):
    """(to, cc) for one email from the contacts in fd (gi_report_case.py puts them there): the later article's
    journal for "publisher", the earlier's for "source-publisher", and the case's contacts.json
    (fd["case_contacts"]) for the institution (contact: which of institutions(), else the first) and the
    authors; empty when there's none."""
    side = {"publisher": "second", "source-publisher": "first"}.get(audience)
    if side:
        contact = fd[side].get("contact") or {}
    elif audience == "institution":
        contact = contact or (institutions(fd) or [{}])[0]
    else:
        contact = (fd.get("case_contacts") or {}).get(audience) or {}
    return contact.get("to", "") or "", contact.get("cc", "") or ""


def email_message(fd, attachments, examples=EMAIL_PASSAGES, audience="publisher", sender=None,
                  name=None, contact=None):
    """email_parts() as a draft .eml message: To and Cc from recipients() (empty when nobody has filled in
    the journal's contact yet, and always for the institution and the authors), From `sender` when given,
    X-Unsent: 1 so a mail client opens it as a draft, and `attachments` (paths) attached. The body names
    attachments[0] as the comparison page and attachments[1], if any, as the image."""
    attachments = [Path(a) for a in attachments]
    subject, body = email_parts(fd, attachments[0].name, attachments[1].name if len(attachments) > 1 else None,
                                examples=examples, audience=audience, name=name, contact=contact)
    to, cc = recipients(audience, fd, contact)
    msg = email.message.EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if sender:
        msg["From"] = sender
    msg["Subject"] = subject
    msg["X-Unsent"] = "1"
    msg.set_content(body)
    for path in attachments:
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = kind.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg

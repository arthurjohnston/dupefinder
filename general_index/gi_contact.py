"""The emails about a reported pair: one each to the journal that published the later article, the later
authors' institutions (one email, every institution's address on its To line), the earlier article's authors and the earlier article's publisher (EMAIL_AUDIENCES).

The wording is here: _email_intro() has what differs per recipient (subject, greeting, why I'm writing, the
request at the end), email_parts() the body they share. email_message() makes a draft .eml of one, with the
comparison page and the longest passage's image attached (and, for copied data, the largest shared table's); gi_report_case.py writes the four for a case.

The findings come from gi_pubpeer.findings(), so an email states the same measurements as the PubPeer
Markdown. When the publication years are the same, none of them says who copied whom. A ticked [data]
finding leads (subject, first sentence, first bullet); the others only say I've written to the journal when it
has an address in journal_contacts.json. ABOUT_ME is the one sentence about who I am (ABOUT_ME_AUTHORS to the
earlier article's authors).
"""
import email.message
import html
import json
import mimetypes
import re
import subprocess
from pathlib import Path

import gi_db
from gi_pubpeer import doi_url, pct, whole_document

ROOT = Path(__file__).resolve().parent.parent
CONTACTS = gi_db.DATA / "journal_contacts.json"
CROSSREF = "https://api.crossref.org/works/"
EMAIL_PASSAGES = 3   # the longest passages quoted in the journal's email (the attachment has them all)


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
    """The journal entry for a DOI: {journal, publisher, issn, to, cc, page, notes}, the to/cc/page/notes as filled in
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
                                               "to": "", "cc": "", "page": "", "notes": ""})
    return {"journal": info["journal"], **entry}


def _plain_cite(p):
    """Title (Year), Journal, https://doi.org/... for a plain-text email (the journal when it's known)."""
    title = html.unescape(p["title"] or p["doi"] or "").replace("*", "")   # metadata can carry &amp; etc.
    year = f" ({p['year']})" if p["year"] else ""
    journal = f", {html.unescape(p['journal'])}" if p.get("journal") else ""
    link = doi_url(p["doi"])
    return f"{title}{year}{journal}" + (f", {link}" if link else "")


EMAIL_AUDIENCES = ("publisher", "institution", "authors", "source-publisher")


def institutions(fd):
    """The later authors' institutions from the case's contacts.json (fd["case_contacts"]["institution"]): one
    entry, or a list of them (one email to all of them, addressed()), each {to, cc, name, authors: [the later article's authors at that
    institution, as printed], source, checked, notes}. An entry with "current": true is where those authors are
    now rather than on the byline, with "published_at" the byline institution; its email says so. [] when
    there's none."""
    inst = (fd.get("case_contacts") or {}).get("institution")
    if isinstance(inst, dict):
        return [inst]
    return [i for i in inst if isinstance(i, dict)] if isinstance(inst, list) else []


def addressed(fd):
    """The institutions() entries with an address: the ones the institution email goes to, all on its To line."""
    return [i for i in institutions(fd) if i.get("to")]


def _institution_contacts(fd, contact=None):
    """The institutions() entries one institution email is for: `contact` (one entry, or a list of them) when
    given, else every addressed() one, else every one (a draft with nobody on To yet)."""
    if isinstance(contact, dict):
        return [contact]
    if contact:
        return list(contact)
    return addressed(fd) or institutions(fd)[:1]


# integrity / ethics offices in the languages the cases have met (intégrité, etica, etika, ombudsperson for good
# research practice, bones pràctiques ...); a research office or vice-rector for research is a fallback
INTEGRITY_WORDS = re.compile(r"int[eé]gri[td]|misconduct|\bethic|\betic|\betik|ombuds|good (?:scientific|research) "
                             r"practice|gute[rn]? wissenschaftliche|bones ?pr[aà]ctiques|buenas pr[aá]cticas|"
                             r"research governance|\brio\b", re.I)


def integrity_office(contact):
    """Whether an institutions() entry's address is a research integrity or ethics office, so the email can
    simply ask it to look into this; anything else (a registrar, a dean, an IRB, a general info@) is a
    fallback and its email asks to be forwarded. The entry's "integrity": true / false decides when given,
    else its name and address do (INTEGRITY_WORDS)."""
    if isinstance(contact.get("integrity"), bool):
        return contact["integrity"]
    return bool(INTEGRITY_WORDS.search(f"{contact.get('name') or ''} {contact.get('to') or ''}"))


def name_list(names):
    """'A', 'A and B', 'A, B and C'."""
    names = [n for n in names if n]
    return " and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1]


def victim_names(fd):
    """The earlier article's authors: contacts.json's authors.names when given (as printed on the article),
    else the index metadata's byline (fd["first"]["authors"]); [] when neither says."""
    names = ((fd.get("case_contacts") or {}).get("authors") or {}).get("names")
    if isinstance(names, list) and names:
        return [n for n in names if n]
    return [n for n in fd["first"].get("authors") or [] if n]


def victim_count(fd):
    """How many authors the earlier article has (victim_names()); 0 when nothing says."""
    return len(victim_names(fd))


def shared_authors(fd):
    """True when the two articles' bylines share names (the index's byline check): self-reuse, not copying."""
    return (fd.get("byline") or "").startswith("byline overlap")


def journal_reported(fd):
    """True when the later (first, if the order is unknown) article's journal has an address in
    journal_contacts.json, so its email can go out: only then do the others say I've written to it. Never when
    fd["journal_told"] is False (gi_report_case.py --authors-only: no email goes to the journal)."""
    return fd.get("journal_told", True) and bool((fd["second"].get("contact") or {}).get("to"))


# who I am, at the top of every email: one sentence
ABOUT_ME = ("I am an independent researcher, with no connection to either set of authors, screening published "
            "articles for duplicated text.")
# the same for the earlier article's authors, who are one of the two sets
ABOUT_ME_AUTHORS = "I am an independent researcher, screening published articles for duplicated text."


def _institutions_intro(contacts, verb, whose_earlier, known, pair, reported, subject_for):
    """_email_intro() for one email to several institutions at once (all on its To line): every later author
    named in the opening, then which institution each address is and whose institution it is (a current one
    saying where the article was written), so each recipient can see who else has it."""
    authors = list(dict.fromkeys(a for c in contacts for a in c.get("authors") or [] if a))
    names = name_list(authors)
    whose = f"{names}, affiliated with your institutions" if names else "authors affiliated with your institutions"
    opening = [f"I am writing about an article by {whose}, which appears {verb} an earlier article"
               f"{whose_earlier}." if known else f"I am writing about {pair} The first is by {whose}."]
    lines = []
    for c in contacts:
        who = name_list(c.get("authors") or [])
        if c.get("current"):
            then = c.get("published_at")
            who = (f"{who or 'an author'}, now affiliated there"
                   + (f"; the article was published while they were at {then}" if then else ""))
        lines.append(f"- {c.get('name') or c.get('to')}" + (f": {who}" if who else ""))
    opening += ["I have written to the journal that published it as well."] if reported else []
    block = "\nThis email has gone to all of the authors' institutions at once:\n" + "\n".join(lines)
    subject = subject_for("a paper by authors associated with your institutions")
    offices = [integrity_office(c) for c in contacts]
    forward = ("forward this to the people at your institution who handle research integrity or research "
               "misconduct allegations, and let me know who it has gone to?")
    if all(offices):
        return ("", subject, "Dear Research Integrity Officers,", opening + [block],
                "I would be grateful if you could each look into this under your institution's research integrity "
                "procedures, and could you let me know that this has reached the right office?")
    if not any(offices):
        return ("", subject, "Dear Sir or Madam,",
                opening + ["I couldn't find a research integrity contact for any of your institutions, so I am "
                           "writing to these addresses instead.", block],
                "Could you please " + forward)
    return ("", subject, "Dear Sir or Madam,",
            opening + ["For some of your institutions I couldn't find a research integrity contact, so I am "
                       "writing to a general address instead.", block],
            "I would be grateful if you could each look into this under your institution's research integrity "
            "procedures or, where this address isn't the office that handles them, " + forward)


def _email_intro(audience, fd, contact=None):
    """(who it goes to, subject, greeting, opening lines, closing request) for one recipient. The journal that
    published the later article ("publisher"), the later authors' institutions (_institution_contacts(): one
    email to all of them, naming each one's authors when there are several), the earlier article's authors (greeted by name when they're one or two,
    victim_names()), and the earlier article's publisher. When the order is unknown, nothing says who
    copied whom. A ticked [data] finding (fd["data_extra"]) leads: the results data, not only the text.
    When the later article's journal has no address (journal_reported()), nobody is told I've written to it."""
    first, second = fd["first"], fd["second"]
    known = fd["earlier_known"]
    earlier = '"' + html.unescape(first["title"] or first["doi"] or "").replace("*", "") + '"'   # quoted, for the authors' subject
    share = second["pct"] or 0
    data = bool(fd.get("data_extra"))
    same = shared_authors(fd)
    reported = journal_reported(fd)
    journal = html.unescape(second.get("journal") or "") or "the journal that published the " + ("later" if known else "first") + " article"
    if data:
        verb = "to report the same results data as"
    elif same:
        verb = "to be largely a republication of" if share >= 50 else "to reuse substantial text from"
    else:
        verb = "to be largely a duplicate of" if share >= 50 else "to reuse substantial text from"
    # no author lists sharing no names / "by other authors": the byline check isn't stated in an email
    whose_earlier = " that shares authors with it" if same else ""
    pair = ("two articles, published the same year, that share extensive " + ("results data and text" if data
            else "text") + ". Which one came first can't be told from the publication years.")
    def subject_for(paper):
        """The subject for the later article's journal and the institutions: short, naming the recipient's
        connection rather than the title (the body has the title and DOI)."""
        if data:
            return f"Research integrity concern: results data in {paper}"
        return f"Possible redundant publication: {paper}" if same else f"Research integrity concern: {paper}"
    subject = subject_for("a paper you published")
    if audience == "publisher":
        return ("[the journal's editor, or the publisher's research integrity contact]",
                subject, "Dear Editor,",
                [f"I am writing about an article in your journal that appears {verb} an earlier article"
                 f"{whose_earlier}." if known else f"I am writing about {pair} The first is in your journal."],
                "I would be grateful if you could look into this and let me know what you decide.")
    if audience == "institution":
        contacts = _institution_contacts(fd, contact)
        if len(contacts) > 1:
            return _institutions_intro(contacts, verb, whose_earlier, known, pair, reported, subject_for)
        contact = contacts[0] if contacts else {}
        authors = contact.get("authors") or []
        names = name_list(authors)
        if contact.get("current"):   # where the author is now, not where the article was written
            plural = len(authors) != 1
            whose = (f"{names}, who {'are' if plural else 'is'} now affiliated with your institution" if names
                     else "authors now affiliated with your institution")
            then = contact.get("published_at")
            when = (f" It was published while they were at {then}"
                    if then else " It was published before they joined your institution")
            opening = [f"I am writing about an article by {whose}.{when}, and appears {verb} an earlier "
                       f"article{whose_earlier}." if known else
                       f"I am writing about {pair} The first is by {whose}.{when}."]
        else:
            whose = (f"{names}, affiliated with your institution" if names
                     else "authors affiliated with your institution")
            opening = [f"I am writing about an article by {whose}, which appears "
                       f"{verb} an earlier article{whose_earlier}." if known else
                       f"I am writing about {pair} The first is by {whose}."]
        opening += ["I have written to the journal that published it as well."] if reported else []
        subject = subject_for("a paper by someone associated with your institution")
        if not integrity_office(contact):   # a fallback address: ask for it to go to the right people
            return ("", subject, "Dear Sir or Madam,",
                    opening + ["I couldn't find a research integrity contact for your institution, so I am "
                               "writing to this address instead."],
                    "Could you please forward this to the people at your institution who handle research "
                    "integrity or research misconduct allegations, and let me know who it has gone to?")
        return ("", subject, "Dear Research Integrity Officer,", opening,
                "I would be grateful if you could look into this under your institution's research integrity "
                "procedures, and could you let me know that this has reached the right office?")
    if audience == "authors":
        names = victim_names(fd)
        one = len(names) == 1
        what = "results data" if data else "text"
        greeting = f"Dear {name_list(names)}," if 0 < len(names) <= 2 else "Dear authors,"
        tell = "I thought you would want to know."
        return ("", f"{what.capitalize()} from your article {earlier} in a {second['year'] or 'later'} article"
                    if known else f"Text overlap between your article {earlier} and another article",
                greeting,
                [("I am writing because the results data of your article appear" if data else
                  "I am writing because your article appears") + " to have been reused in a later article"
                 "." if known else
                 f"I am writing about {pair} The second is yours.",
                 (f"I have reported this to {journal}. " if reported else "") + tell],
                "If you weren't aware of it, you may want to contact "
                + ("that journal " if reported else f"{journal} ") + ("yourself." if one else "yourselves."))
    if audience == "source-publisher":
        role = "original" if known else "other"
        if reported:
            lines = [f"I am writing because an article you published appears to have been reused in a later "
                     f"article{whose_earlier}, in another journal." if known else
                     f"I am writing about {pair} The second was published by you.",
                     f"I have reported this to {journal}."]
            request = f"You may wish to raise it with that journal as well, as the publisher of the {role} article."
        else:
            lines = [f"I am writing because an article you published appears to have been reused in a later "
                     f"article{whose_earlier}, in another journal." if known else
                     f"I am writing about {pair} The second was published by you.",
                     f"I couldn't find a research-integrity contact for {journal}, so I am writing to you as the "
                     f"publisher of the {role} article."]
            request = (f"I would be grateful if you could raise it with {journal}, as you may be better placed "
                       f"to reach them.")
        return ("", "Text from a paper you published, reused in another journal" if known
                    else "Text overlap between a paper you published and one in another journal",
                "Dear Editor,", lines, request)
    raise ValueError(f"unknown audience {audience!r} (use: {', '.join(EMAIL_AUDIENCES)})")


# how much of each email: the journal decides, so it gets the findings and a few extracts; the institution
# the findings; the earlier side only needs to know, so the share and any data finding
FULL, FINDINGS, SHORT = "full", "findings", "short"
AUDIENCE_LENGTH = {"publisher": FULL, "institution": FINDINGS, "authors": SHORT, "source-publisher": SHORT}
NUMERIC_EXTRACT = 0.5   # an extract with more than this share of words containing digits is a table: not quoted


def readable(passage):
    """False for an extract that is mostly numbers (a table row run together), which reads as noise."""
    words = passage["excerpt"].split()
    return bool(words) and sum(any(c.isdigit() for c in w) for w in words) / len(words) <= NUMERIC_EXTRACT


def email_parts(fd, attachment, shot_name=None, examples=EMAIL_PASSAGES, audience="publisher",
                name=None, contact=None, table_name=None):
    """(subject, body) of a plain-text email about the pair to one of EMAIL_AUDIENCES. The body, in order:
    greeting, who I am (ABOUT_ME) and why I'm writing, the two articles, the findings, what's attached, extracts of the `examples` longest readable passages
    (the journal's email only), the request, and the sign-off with `name` (default your git user.name).
    The findings: the text share (with whether the later article cites the earlier) first, then the [data]
    ones and your other ticked findings; the earlier side's emails (AUDIENCE_LENGTH) get only the share
    and the [data] findings. contact: the institutions() entry (or entries) an institution email is for,
    default every addressed() one."""
    first, second = fd["first"], fd["second"]
    known = fd["earlier_known"]
    length = AUDIENCE_LENGTH.get(audience, FULL)
    _to, subject, greeting, opening, request = _email_intro(audience, fd, contact)
    blocks = [o.lstrip("\n") for o in opening if o.startswith("\n")]   # a list of its own (_institutions_intro())
    opening = [o for o in opening if not o.startswith("\n")]
    lines = [greeting, "", " ".join([ABOUT_ME_AUTHORS if audience == "authors" else ABOUT_ME] + opening), ""]
    lines += [x for b in blocks for x in (b, "")]
    if known:
        lines += ["Later article: " + _plain_cite(second), "", "Earlier article: " + _plain_cite(first), ""]
        share = [f"- About {pct(second['pct'])} of the later article's text is also found in the earlier article "
                 f"({fd['matched_words'] or 0:,} matched words in {fd['runs']} passages; the later article has "
                 f"about {second['words']:,} words). That is {pct(first['pct'])} of the earlier article's text."]
    else:
        lines += ["First article: " + _plain_cite(second), "", "Second article: " + _plain_cite(first), ""]
        share = [f"- About {pct(second['pct'])} of the first article's text is also found in the second, "
                 f"and {pct(first['pct'])} of the second article's text in the first "
                 f"({fd['matched_words'] or 0:,} matched words in {fd['runs']} passages)."]
    data = [f"- {t}" for t in fd.get("data_extra", ())]   # the [data] findings you ticked (gi_case_findings.py)
    extra = [f"- {t}" for t in fd.get("extra", ())]   # the other findings you ticked
    # the byline check (fd["byline"]) still picks the queue and the wording, but isn't stated in an email
    if length != SHORT:   # the earlier side's short emails leave the citation check out, as the byline one
        from gi_pubpeer import cites_clause
        share = [cites_clause(share[0], fd["later_cites_earlier"],
                              "the later article" if known else "the first article", known)]
    lines += share + data + ([] if length == SHORT else extra)   # the share (and whether it's cited) leads
    if whole_document(fd):
        what = "both articles end to end as one diff, the shared text highlighted and the differences marked"
    else:
        what = f"all {fd['runs']} shared passages, the two texts side by side with the differences marked"
    lines += ["", "I also attached:", f"- {attachment}: {what} (one HTML file; open it in a web browser)."]
    if shot_name:
        lines.append(f"- {shot_name}: the longest shared passage of running text, as printed in both articles "
                     f"({'earlier article left, later right' if known else 'order unknown'}), the shared words "
                     f"highlighted.")
    if table_name:
        lines.append(f"- {table_name}: the largest shared table, as printed in both articles "
                     f"({'earlier article left, later right' if known else 'order unknown'}), the shared words and "
                     f"values highlighted.")
    shown = [p for p in fd["passages"] if readable(p)][:examples] if length == FULL else []
    if shown:
        n = len(shown)
        whose = "the later" if known else "the first"
        some = shown != fd["passages"][:n]   # a table-like extract was skipped: not simply the n longest
        head = ("the longest shared passage" if n == 1 else f"{n} of the longest shared passages" if some
                else f"the {n} longest shared passages")
        lines += ["", f"Extracts of {head} (as worded in {whose} article, with its word count; '…' marks words "
                      f"left out of the extract):", ""]
        lines += [f"{i}. {p['words']:,} words: \"{p['excerpt']}\"" for i, p in enumerate(shown, 1)]
    if fd.get("pubpeer_post"):
        lines += ["", f"I have also posted these findings on PubPeer: {fd['pubpeer_post']}"]
    lines += ["", request, "", "Thanks", name or git_config("user.name") or "[Your name]"]
    return subject, "\n".join(lines) + "\n"


def recipients(audience, fd, contact=None):
    """(to, cc) for one email from the contacts in fd (gi_report_case.py puts them there): the later article's
    journal for "publisher", the earlier's for "source-publisher", and the case's contacts.json
    (fd["case_contacts"]) for the institutions (every addressed() one, or `contact`: one entry or a list,
    all on To) and the authors; empty when there's none."""
    side = {"publisher": "second", "source-publisher": "first"}.get(audience)
    if side:
        contact = fd[side].get("contact") or {}
    elif audience == "institution":
        contacts = _institution_contacts(fd, contact)
        join = lambda key: ", ".join(dict.fromkeys(a.strip() for c in contacts for a in (c.get(key) or "").split(",")
                                                   if a.strip()))
        return join("to"), join("cc")
    else:
        contact = (fd.get("case_contacts") or {}).get(audience) or {}
    return contact.get("to", "") or "", contact.get("cc", "") or ""


def report_page(audience, fd):
    """The web page to report it on for a journal with no email (its "page" in journal_contacts.json: a
    complaints or contact form, or the page naming the editor), for "publisher" / "source-publisher"; "" when
    the journal has an address, or nobody has found a page."""
    side = {"publisher": "second", "source-publisher": "first"}.get(audience)
    contact = (fd[side].get("contact") or {}) if side else {}
    return "" if contact.get("to") else (contact.get("page") or "")


# the emails addressed To yourself, their recipients on this line instead: the institutions Bcc (they don't see
# each other's addresses; the body still lists which institutions it went to), the earlier authors Cc (all on
# one email, each seeing who else has it)
SELF_ADDRESSED = {"institution": "Bcc", "authors": "Cc"}


def headers(audience, fd, sender=None, contact=None):
    """{To, Cc, Bcc} for one draft, only those with addresses. From recipients(): as they are for the journals;
    for SELF_ADDRESSED audiences To is you (`sender`, else your git user.email) and every one of its to and cc
    goes on that audience's line."""
    to, cc = recipients(audience, fd, contact)
    line = SELF_ADDRESSED.get(audience)
    if line:
        everyone = ", ".join(dict.fromkeys(a.strip() for a in f"{to},{cc}".split(",") if a.strip()))
        return {k: v for k, v in (("To", (sender or git_config("user.email") or "") if everyone else ""),
                                  (line, everyone)) if v}
    return {k: v for k, v in (("To", to), ("Cc", cc)) if v}


def email_message(fd, attachments, examples=EMAIL_PASSAGES, audience="publisher", sender=None,
                  name=None, contact=None):
    """email_parts() as a draft .eml message: To, Cc and Bcc from headers() (To yourself and the recipients on
    Bcc for the institutions, Cc for the earlier authors; no To when nobody has filled in an address yet),
    From `sender` when given,
    X-Unsent: 1 so a mail client opens it as a draft, and `attachments` (paths) attached. The body names
    attachments[0] as the comparison page, one named *-table.jpg as the largest shared table and any other as
    the longest passage's image."""
    attachments = [Path(a) for a in attachments]
    tables = [a.name for a in attachments[1:] if a.name.endswith("-table.jpg")]
    shots = [a.name for a in attachments[1:] if not a.name.endswith("-table.jpg")]
    subject, body = email_parts(fd, attachments[0].name, shots[0] if shots else None,
                                examples=examples, audience=audience, name=name, contact=contact,
                                table_name=tables[0] if tables else None)
    msg = email.message.EmailMessage()
    lines = headers(audience, fd, sender, contact)
    msg["To"] = lines.get("To", "")
    for key in ("Cc", "Bcc"):
        if key in lines:
            msg[key] = lines[key]
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

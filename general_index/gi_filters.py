"""Classify a General Index candidate pair by its metadata alone, before any PDF is fetched.

classify_pair() returns why a pair is NOT worth checking for copying, or "" when it is.
Every reason here was found in the slice 1 pilot (PILOT_FINDINGS.md). They are catalogue
artifacts or legitimate reuse, not copying:
  same work        same DOI, DOI differs only by a version suffix, or near-identical titles
                   (difflib >= TITLE_MATCH, the threshold retrieve_papers.py uses), which also
                   catches JSTOR mirrors of a publisher's article
  neighbouring DOI separate short items on one journal page, the same scan under each DOI
  generic title    "Information for authors", "blank", "[untitled]", ...
  one file twice   unrelated titles but equal sampled sizes, or both sides' shares >= BOTH_HIGH:
                   one PDF filed under two DOIs
  same issue       same venue (journal, else DOI stem) within a year, with unrelated titles or
                   both sides' shares >= SAME_ISSUE_BOTH: items printed on shared pages
                   (conference abstract supplements, letters, reviews)
  same book        a chapter against its book, or two editions/chapters of one book
  inside a compilation   (2022-update side only) one article inside a whole issue/proceedings PDF
For a side whose metadata comes from the 2022 update (text-extracted, see ALT_JUNK_*), titles are
also matched by word containment, junk titles count as generic, and authors are compared loosely.
author_relation() is separate because shared-author reuse is still worth reporting, just apart.
"""
import difflib
import html
import os
import re
import unicodedata

TITLE_MATCH = 0.82
EQUAL_SIZE = 0.02
SAME_ISSUE_BOTH = 50  # same venue and year, both sides' shares at least this: shared pages
BOTH_HIGH = 80  # both sides' shares at least this, titles unrelated: one file under two DOIs
GENERIC = re.compile(
    r"^(blank|untitled|n a|h|front matter|back matter|front cover|back cover|masthead|contents|"
    r"table of contents|index|erratum|errata|corrigendum|correction|editorial board|"
    r"(information|instructions?|guidelines?|guide) for (authors|contributors)|"
    r"publisher s (announcement|note)|news (and )?analysis|products (and )?services update|"
    r"books received|book reviews?|reviews|notes|letters?|correspondence|announcements?|"
    r"meeting abstracts?|abstracts|editorial|preface|foreword|introduction|"
    r"(the )?authors? reply|reply|in reply|response|(the )?authors? response|reply to .{0,60})$")
STOP = set("a an the of and in on for to with by from at as is are using based its their via into "
           "new study analysis "
           # German/French function words, so "Zur ... und ..." titles don't look related
           "der die das und zur zum von vom im den dem des ein eine einer mit auf fur uber "
           "le la les de du et des en pour sur une un aux par dans".split())


JUNK_TITLE_PREFIX = re.compile(
    r"^\s*(title\s*:?|uva-dare \(digital academic repository\)|this content has been downloaded from\S*|"
    r"key ?words\s*:?|letters? to the editor|research article|original article|review article)\s*", re.I)


def norm_title(t):
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", t or "")  # leading "[IEEE 2016 ...]" proceedings name
    t = JUNK_TITLE_PREFIX.sub("", t)  # text-extraction prefixes ahead of the real title
    t = unicodedata.normalize("NFKD", html.unescape(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t).split())


def title_parts(t):
    """'Book || Chapter' -> ('book', 'chapter'); a plain title -> ('', title)."""
    parts = [norm_title(p) for p in (t or "").split("||")]
    return (parts[0], parts[-1]) if len(parts) > 1 else ("", parts[0])


def similar(x, y):
    return bool(x and y) and difflib.SequenceMatcher(None, x, y).ratio() >= TITLE_MATCH


def title_words(t):
    return {w for w in norm_title(t).split() if w not in STOP and len(w) > 2}


def related_titles(a, b):
    """At least two shared significant title words, and 30% of the shorter title's: one shared word
    ("Fe laths deformed..." / "deformed hypernuclei") is coincidence."""
    wa, wb = title_words(a), title_words(b)
    shared = len(wa & wb)
    return shared >= 2 and shared / max(1, min(len(wa), len(wb))) >= 0.3


def _isbn(doi):
    m = re.search(r"97[89][-0-9]{10,16}", doi.replace("b978", "978"))
    return re.sub(r"\D", "", m.group())[:13] if m else ""


# Titles and authors in the 2022 update (gi_load_meta_alt.py) come from the document text, not a
# publisher, so they carry junk the publisher metadata doesn't: repository cover pages as titles,
# affiliations and running heads as authors. These rules apply only when a side comes from there.
ALT_JUNK_TITLE = re.compile(
    r"(research explorer|general rights|downloaded from|all rights reserved|copyright|cited scientist|"
    r"open access|creative commons|this article|accepted manuscript|^title\b|^\W|symposia|workshop|"
    r"conference|proceedings|abstracts?$|^review$|^chapter\b|^appendix\b)")
ALT_GARBLED_AUTHOR = re.compile(r"(\b[a-z]\s){4,}|\d|[~^#@{}]|key words|letters|editor")
ALT_JUNK_AUTHOR = re.compile(
    r"\b(accepted|university|universit|department|dept|institute|school|college|faculty|review|law|"
    r"journal|press|communicated|general|rights|editor|edited|switzerland|germany|munich|deutsche|"
    r"usa|china|india|see|for|in|the)\b")


def is_alt(m):
    return m["doi"].startswith("gi:") or (hasattr(m, "get") and m.get("source") == "alt")


def _plain(t):
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def contained_title(ta, tb):
    """The shorter title, as a contiguous run of words, inside the longer: the same title with
    text-extraction junk around it ("Title: ...", "... * A Sim"). Contiguous on purpose: a bag-of-
    words test merged "Bacteria-based self-healing concrete" with a different paper on bacteria in
    self-healing concrete -- a Retraction Watch plagiarism pair."""
    na, nb = norm_title(ta), norm_title(tb)
    small, big = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(title_words(small)) >= 4 and f" {small} " in f" {big} "


def alt_title_is_junk(m):
    t = norm_title(m["title"])
    if len(t.split()) < 3 or ALT_JUNK_TITLE.search(t):
        return True
    names = set(re.findall(r"[a-z]{3,}", _plain(m["author"])))
    words = set(re.findall(r"[a-z]{3,}", t))
    return bool(words) and words <= names  # the "title" is a byline


def classify_pair(a, b, sampled_a, sampled_b, pct_a, pct_b):
    """a, b: metadata rows with doi/title/journal/pub_date. Returns a reason, or "" if worth checking."""
    da, db = a["doi"].lower(), b["doi"].lower()
    if da == db or re.sub(r"[./_-](v\d+|s\d+|supp\w*|\d{1,2})$", "", da) == \
            re.sub(r"[./_-](v\d+|s\d+|supp\w*|\d{1,2})$", "", db):
        return "same work"
    book_a, ta = title_parts(a["title"])
    book_b, tb = title_parts(b["title"])
    if len(ta.split()) >= 3 and similar(ta, tb):
        return "same work"
    if any(re.match(r"\s*review(ed)? by\b", m["author"] or "", re.I) or "book review" in norm_title(m["title"])
           for m in (a, b)):
        return "generic title"  # book reviews quote and summarize the book they review
    alt_a, alt_b = is_alt(a), is_alt(b)
    no_titles = False
    if alt_a or alt_b:
        # A junk extracted title says nothing about the pair (the shared text is still evidence):
        # it just means no title rule can be applied to that pair.
        if (alt_a and alt_title_is_junk(a)) or (alt_b and alt_title_is_junk(b)) or not ta or not tb:
            no_titles = True
        else:
            no_titles = False
            if contained_title(a["title"], b["title"]):
                return "same work"
        # one article inside a whole issue / proceedings PDF: small side mostly found, large side barely
        big_alt = alt_a if sampled_a > sampled_b else alt_b
        if big_alt and min(pct_a, pct_b) < 5 and max(sampled_a, sampled_b) > 10 * min(sampled_a, sampled_b):
            return "inside a compilation"
    if not (da.startswith("gi:") or db.startswith("gi:")) \
            and len(os.path.commonprefix([da, db])) >= min(len(da), len(db)) - 4:
        return "neighbouring DOI"
    if not (alt_a or alt_b) and (GENERIC.match(ta) or GENERIC.match(tb) or not ta or not tb):
        return "generic title"
    if da.startswith(db) or db.startswith(da) or (_isbn(da) and _isbn(da) == _isbn(db)) \
            or (not no_titles and ((book_a and similar(book_a, book_b)) or (book_a and similar(book_a, tb))
                                   or (book_b and similar(book_b, ta)))):
        return "same book"
    if no_titles:  # every rule below needs both titles; size and DOI evidence alone isn't enough
        return ""
    related = related_titles(a["title"], b["title"])
    if not related and (abs(sampled_a - sampled_b) / max(sampled_a, sampled_b, 1) < EQUAL_SIZE
                        or min(pct_a, pct_b) >= BOTH_HIGH):
        return "one file twice"
    # Same venue within a year: unrelated titles, or both sides mostly shared (pages printed
    # together share text both ways). Same-venue pairs with lower overlap are kept on purpose:
    # that is also what a paper mill's template reuse inside one journal looks like.
    # A 2022-update side's date is usually the PDF's, not the issue's (a 2003 Stroke editorial comment
    # dated 2018), so the year test only applies when both dates come from publisher metadata.
    years_ok = alt_a or alt_b or abs(_year(a) - _year(b)) <= 1
    if same_venue(a, b) and years_ok \
            and (not related or min(pct_a, pct_b) >= SAME_ISSUE_BOTH):
        return "same issue"
    return ""


def venue(m):
    """Journal name if the index has one, else the DOI's journal stem: 10.1097/01.mpa.000037...
    -> 10.1097/01.mpa, 10.1136/jnnp.72.6.708 -> 10.1136/jnnp, 10.1642/0004-8038(2001)... ->
    10.1642/0004-8038."""
    if m["journal"]:
        return norm_title(m["journal"])
    prefix, _, suffix = m["doi"].lower().partition("/")
    stem = re.match(r"(01\.)?[a-z0-9-]+", suffix)
    return f"{prefix}/{stem.group()}" if stem else ""


def _doi_stem(m):
    return venue({"journal": None, "doi": m["doi"]})


def same_venue(a, b):
    """Same journal name, or the same DOI journal stem: one side may have a journal name and the other
    (2022-update metadata) only a DOI."""
    if a["journal"] and b["journal"] and norm_title(a["journal"]) == norm_title(b["journal"]):
        return True
    return bool(_doi_stem(a)) and _doi_stem(a) == _doi_stem(b)


def _year(m):
    y = (m["pub_date"] or "")[:4]
    return int(y) if y.isdigit() else -99


def author_keys(authors):
    """Surname + first initial per author. Index author strings mix 'Last, First; ...' with
    'First Last and First Last' (JSTOR), so split on ';' when present, else on ' and ' / ','."""
    s = unicodedata.normalize("NFKD", authors or "").encode("ascii", "ignore").decode().lower()
    truncated = "\u2026" in s or s.rstrip().endswith("...")  # "A B, C D…" is still a list
    s = s.replace("\u2026", "")
    s = re.sub(r",(?=\S)", ", ", s)
    s = re.sub(r",\s*(jr|sr|ii|iii|iv)\.?(?=\s*(;|$|\s(and|&)\s))", "", s)  # "Counselman, C.C., III"
    if ";" in s:
        names = s.split(";")
    elif s.count(",") == 1 and not re.search(r"\s(and|&)\s", s) and not truncated \
            and not re.search(r"\b[a-z]\.", s.split(",")[0]):
        names = [s]  # a single 'Last, First' (not 'First M. Last, First M. Last')
    else:
        names = re.split(r",\s*(?:and\s+)?|\s+and\s+|\s*&\s*", s)
    keys = set()
    for n in names:
        n = re.sub(r"-", "", n)  # "Chun-Gen" == "Chungen"
        n = re.sub(r",\s*(jr|sr|ii|iii|iv)\.?\s*$", "", n.strip())
        if "," in n:
            last, first = n.split(",", 1)
            if not re.search(r"[a-z]", first):  # IEEE-style "Casadei, " with no first name
                if re.search(r"[a-z]{2,}", last):
                    keys.add("surname:" + re.findall(r"[a-z]+", last)[-1])
                continue
            if len(re.findall(r"[a-z]+", last)) == 1 and len(last.strip()) <= 2:  # "R, Jerome"
                last, first = first, last
            n = f"{first} {last}"
        p = re.findall(r"[a-z]+", n)
        if len(p) >= 2:
            keys.add(f"{p[-1]} {p[0][0]}")
            if all(len(x) > 1 for x in p):  # full names only: "Chuancheng Ren" == "Ren Chuancheng"
                keys.add(" ".join(sorted(p)))
        elif p and len(p[0]) > 2:
            keys.add(p[0])
    return keys


def _surnames(keys):
    return {k.split(":", 1)[1] if k.startswith("surname:") else k.split()[0] if " " in k else k
            for k in keys if not (" " in k and len(k.split()) > 2)}


# Author strings that are a proceedings' editors, not the paper's authors: AIRCC's CS & IT volumes
# print "David C. Wyld et al. (Eds)" on every page, and the 2022 update took it as the byline (110
# documents, 2026-10-07; GI-13 and the MFCC pair were same-author pairs scored as cross-author because
# of it). Exact forms only: other people share the surname.
EDITOR_AUTHORS = {"c david; wyld"}


def author_relation(a, b):
    for m in (a, b):
        if is_alt(m) and " ".join(_plain(m["author"]).split()) in EDITOR_AUTHORS:
            return "authors unknown"
    if is_alt(a) or is_alt(b):
        for m in (a, b):
            if is_alt(m) and (ALT_JUNK_AUTHOR.search(_plain(m["author"]))
                              or ALT_GARBLED_AUTHOR.search(_plain(m["author"]))):
                return "authors unknown"
        # extracted bylines garble name order and splits, so compare surname-like tokens loosely
        ta = {w for w in re.findall(r"[a-z]{4,}", _plain(a["author"]))}
        tb = {w for w in re.findall(r"[a-z]{4,}", _plain(b["author"]))}
        if ta & tb:
            return "shared author"
    blank = {"blank", ""}
    if (a["author"] or "").strip().lower() in blank or (b["author"] or "").strip().lower() in blank:
        return "authors unknown"
    ka, kb = author_keys(a["author"]), author_keys(b["author"])
    if not ka or not kb:
        return "authors unknown"
    if ka & kb:
        return "shared author"
    # a surname-only author (no first name given) matches any author with that surname
    sa = {k.split(":", 1)[1] for k in ka if k.startswith("surname:")}
    sb = {k.split(":", 1)[1] for k in kb if k.startswith("surname:")}
    if (sa & _surnames(kb)) or (sb & _surnames(ka)):
        return "shared author"
    return "no shared author"

"""Preprint-server and repository DOIs, and the demotion of pairs where the copying side is one.

preprint_doi_prefixes.tsv (next to this file) lists the DOI prefixes that preprint servers and
repositories assign (arXiv, SSRN, Zenodo, figshare, ResearchGate, Research Square, the OSF family,
bioRxiv/medRxiv, ...), each verified against Crossref / DataCite / doi.org. Where a prefix is shared
with a publisher's journals (bioRxiv's 10.1101 with the Cold Spring Harbor journals, JMIR's 10.2196,
Copernicus's 10.5194, ...) the row's suffix_regex says which suffixes are the preprints; it is matched
at the start of the suffix, case-insensitively. An empty suffix_regex means every DOI under the prefix.

Why demote: a preprint or repository upload that repeats an earlier paper is most often the authors'
own copy, or an aggregator's re-posting, rarely a publishable plagiarism finding. So a pair whose
LATER (copying) side is a preprint goes to the back of the queues. A preprint that was copied FROM
(the earlier side) is an ordinary case and is not demoted. When which paper came first is unknown
(same year, no resolution), a preprint on either side is "possibly by a preprint": a milder demotion,
after the ordinary cases and before the ones known to be by a preprint. Nobody's verdict changes;
only the order and a note.

    is_preprint_doi(doi) -> server name or None
    earlier_by_year(year_a, year_b) -> 'a' / 'b' / None
    preprint_demotion(doi_a, doi_b, earlier) -> (rank, note): rank 0 ordinary, 1 possibly by a
        preprint (order unknown), 2 the later paper is a preprint

Major publishers (2026-10-12): major_publisher_doi_prefixes.tsv lists the DOI prefixes of the major
scholarly publishers, from Crossref's /members records. A pair whose LATER (copying) paper is from one goes
FIRST: a copy that got into a major journal is the most consequential finding. Combined with the preprint
demotion into one priority (pair_priority(), lower sorts first):

    PROMOTED  -2  the later paper is from an enabled major publisher (also: order unknown, but BOTH sides
                  are, so whichever is later is one)
    POSSIBLY_PROMOTED -1  order unknown and exactly one side is a major publisher: the mild promotion
    ORDINARY   0  everything else -- including a later paper in an excluded venue (below)
    POSSIBLY   1  possibly by a preprint (order unknown, a side is a preprint), as before
    LATER      2  the later paper is a preprint, as before

The preprint rules are checked first and win: a preprint DOI is never a major-publisher DOI (SSRN is
Elsevier's prefix, Research Square Springer Nature's, ChemRxiv ACS's ...), and when the order is unknown
and one side is a preprint the pair stays "possibly by a preprint" even if the other side is a major
journal (the usual shape is the authors' own preprint of their journal paper).

The TSV's group column: 'core' (the named publishers), 'etc' (other widely recognised publishers and
societies), 'debatable' (MDPI, Frontiers, Hindawi, Dove, Bentham, Inderscience, ...). Only ENABLED_GROUPS
promote; 'debatable' is off by default. A row with promote=no is an exclusion: a venue inside a major
publisher that is a known paper-mill target (IOP's conference series, Materials Today: Proceedings, ...),
matched by its suffix regex (or, as '@FILE', by a list of book DOIs: Springer's proceedings series,
major_publisher_excluded_books.tsv), or a preprint/non-journal prefix a publisher owns. Its later paper is noted
but ranks ordinary: worth reviewing, not "a prestigious journal printed a copy". Suffix-regex rows are
checked before a prefix's whole-prefix row, so the exclusions override.

    major_publisher(doi) -> Publisher(name, group, promote, venue, reason) or None
    pair_priority(doi_a, doi_b, earlier) -> (rank, note)
"""
import csv
import re
from functools import lru_cache
from pathlib import Path

from typing import NamedTuple

TSV = Path(__file__).resolve().parent / "preprint_doi_prefixes.tsv"
MAJOR_TSV = Path(__file__).resolve().parent / "major_publisher_doi_prefixes.tsv"

PROMOTED, POSSIBLY_PROMOTED, ORDINARY, POSSIBLY, LATER = -2, -1, 0, 1, 2
ENABLED_GROUPS = frozenset({"core", "etc"})


@lru_cache(maxsize=None)
def load(path=TSV):
    """{prefix: [(compiled suffix regex or None, server)]} from the TSV."""
    table = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            rx = row["suffix_regex"].strip()
            table.setdefault(row["prefix"].strip().lower(), []).append(
                (re.compile(rx, re.I) if rx else None, row["server"].strip()))
    return table


def normalize(doi):
    """'https://doi.org/10.1101/X' / 'doi:10.1101/X' / ' 10.1101/x ' -> '10.1101/x'; None for empty or gi: keys."""
    d = (doi or "").strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if d.startswith(p):
            d = d[len(p):].strip()
    return d if d.startswith("10.") and "/" in d else None


def is_preprint_doi(doi, path=TSV):
    """The preprint server / repository that assigned this DOI, or None."""
    d = normalize(doi)
    if not d:
        return None
    prefix, suffix = d.split("/", 1)
    for rx, server in load(path).get(prefix, ()):
        if rx is None or rx.match(suffix):
            return server
    return None


def earlier_by_year(year_a, year_b):
    """'a' / 'b' for the earlier side by publication year, None when either is missing or they're equal
    (the same year fallback write_dupe_reports_html.resolve_earlier_id uses)."""
    ya, yb = (str(y or "")[:4] for y in (year_a, year_b))
    if not (ya.isdigit() and yb.isdigit()) or ya == yb:
        return None
    return "a" if ya < yb else "b"


def preprint_demotion(doi_a, doi_b, earlier):
    """earlier: 'a', 'b' or None (unknown). (rank, note) -- see the module docstring for the rule."""
    sa, sb = is_preprint_doi(doi_a), is_preprint_doi(doi_b)
    if earlier in ("a", "b"):
        later = sb if earlier == "a" else sa
        if later:
            return LATER, f"later paper is a preprint ({later})"
        return ORDINARY, None
    if sa or sb:
        servers = " / ".join(dict.fromkeys(s for s in (sa, sb) if s))
        return POSSIBLY, f"possibly by a preprint ({servers}; which paper came first is unknown)"
    return ORDINARY, None


class Publisher(NamedTuple):
    name: str        # 'Elsevier', 'Springer Nature', ...
    group: str       # 'core' / 'etc' / 'debatable'
    promote: bool    # False for an excluded venue (paper-mill target) or a non-journal prefix
    venue: str       # the excluded venue's name ('' for a whole-prefix row)
    reason: str      # why not promoted ('' when promoted)


class _BookList:
    """A suffix rule that is a list of book DOIs rather than a regex: suffix_regex '@FILE' names a TSV (next to
    the publisher TSV) whose book_doi column lists them. Matches the book and its chapters (BOOK_N). For a
    book series, which a DOI does not encode: Springer's 10.1007/978-... chapter DOIs carry only the volume's
    ISBN, so AISC/LNEE/LNNS are listed volume by volume."""

    def __init__(self, path, prefix):
        with open(path, newline="", encoding="utf-8") as f:
            self.books = {r["book_doi"].strip().lower().split("/", 1)[1] for r in csv.DictReader(f, delimiter="\t")
                          if r["book_doi"].strip().lower().startswith(prefix + "/")}

    def match(self, suffix):
        return suffix.split("_", 1)[0] in self.books


@lru_cache(maxsize=None)
def load_major(path=MAJOR_TSV):
    """{prefix: [(compiled suffix regex or None, Publisher)]}, suffix-regex rows first (file order kept)."""
    table = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            rx = row["suffix_regex"].strip()
            promote = row["promote"].strip().lower() == "yes"
            prefix = row["prefix"].strip().lower()
            table.setdefault(prefix, []).append(
                (_BookList(Path(path).parent / rx[1:], prefix) if rx.startswith("@") else
                 re.compile(rx, re.I) if rx else None,
                 Publisher(row["publisher"].strip(), row["group"].strip(), promote,
                           row["imprint_notes"].strip() if rx and not promote else "",
                           "" if promote else row["reason"].strip())))
    for rows in table.values():
        rows.sort(key=lambda r: r[0] is None)   # stable: regex rows before the whole-prefix row
    return table


def major_publisher(doi, path=MAJOR_TSV):
    """The major publisher this DOI belongs to (a Publisher, promote=False for an excluded venue), or None --
    also None for any DOI that is_preprint_doi() claims."""
    d = normalize(doi)
    if not d or is_preprint_doi(d):
        return None
    prefix, suffix = d.split("/", 1)
    for rx, pub in load_major(path).get(prefix, ()):
        if rx is None or rx.match(suffix):
            return pub
    return None


def _label(pub):
    return f"{pub.name}, {pub.venue}" if pub.venue else pub.name


def pair_priority(doi_a, doi_b, earlier, groups=ENABLED_GROUPS):
    """earlier: 'a', 'b' or None (unknown). (rank, note), lower first -- the rule is in the module docstring.
    groups: the TSV groups that promote ('core' and 'etc' by default)."""
    rank, note = preprint_demotion(doi_a, doi_b, earlier)
    if rank != ORDINARY:
        return rank, note
    pa, pb = (p if p and p.group in groups else None for p in (major_publisher(doi_a), major_publisher(doi_b)))
    if earlier in ("a", "b"):
        later = pb if earlier == "a" else pa
        if not later:
            return ORDINARY, None
        if not later.promote:
            return ORDINARY, f"later paper: {_label(later)} (not promoted: {later.reason})"
        return PROMOTED, f"later paper: {later.name}"
    promoted = [p for p in (pa, pb) if p and p.promote]
    if len(promoted) == 2:
        names = " / ".join(dict.fromkeys(p.name for p in promoted))
        return PROMOTED, f"later paper: {names} (which came first is unknown; both sides are major publishers)"
    if promoted:
        return POSSIBLY_PROMOTED, f"possibly later paper: {promoted[0].name} (which paper came first is unknown)"
    excluded = [p for p in (pa, pb) if p and not p.promote]
    if excluded:
        return ORDINARY, f"possibly later paper: {_label(excluded[0])} (not promoted: {excluded[0].reason})"
    return ORDINARY, None

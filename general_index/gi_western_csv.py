"""Where the Western academics on copying papers are now: one CSV row per academic per university.

Two lists, both from gi_affiliations' cached OpenAlex/ORCID data (gi.sqlite3; nothing is fetched):
  cases          unreviewed cases in the review tool's --western-only queue (gi_review_cli.load_queue, its
                 affiliation order), with the title of the academic's paper, the agent's type (data copying,
                 cross-author copying, ...), copied_pct (the share of that paper's words in shared runs), the
                 agent's decision, overlap_pct (the larger of the two papers' shares) and a health-sciences flag;
                 cases the agent typed as text recycling, legitimate reuse, duplicate publication or an artifact
                 are left out (--all-types keeps them)
  unobtainable   pairs from the pool with no full text (data/unobtainable_openalex_screen.tsv) whose larger GI
                 5-gram share is at least --min-pct (15, the original lead floor), cross-author only (the
                 screen's eliminated pairs dropped), in the n-gram lead ranking (gi_rank_leads.py's lead_rank,
                 with its tier), with the title of the academic's paper and copied_pct (that paper's GI 5-gram
                 share); tier 5 (a side with no metadata, or one PDF held twice) is left out (--all-tiers keeps it),
                 and so are pairs with a front-matter side or two proceedings-volume records
                 (proceedings_noise; --keep-proceedings keeps them); venue_record names a side that is a
                 volume record facing a real paper (A or B), which may be that paper inside the volume

The copying paper is the later one (gi_write_pages.earlier_side / gi_preprints.earlier_by_year); with the order
unknown, academics on both papers count. An academic is listed when gi_affiliations.author_status says target,
at each university in TARGET_COUNTRIES from OpenAlex's last known institutions (type education) or a current
ORCID job with an academic-looking name. One university from both sources is one row ("... + ...").

OpenAlex sometimes merges different people with one name, which shows up as one person at several unrelated
universities. n_universities counts a person's universities in the row's case or pair; likely_merge is "yes"
when that is MERGE_UNIVERSITIES or more and none comes from ORCID (which the person fills in themselves). It is
a "check first" flag, not a verdict: a merge showing one university, or one with an ORCID job, isn't flagged.

Usage:
  gi_western_csv.py cases data/western_cases_YYYY-MM-DD.csv [--all-types]
  gi_western_csv.py unobtainable data/unobtainable_western_YYYY-MM-DD.csv [--min-pct 15] [--all-tiers]
                       [--keep-proceedings]
"""
import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path

import gi_affiliations as ga
import gi_db
import gi_preprints

MERGE_UNIVERSITIES = 3
ORCID_SRC, OPENALEX_SRC = "ORCID current job", "OpenAlex last known"
NOT_COPYING = ("text recycling", "legitimate reuse", "duplicate publication", "artifact")   # cases: dropped by default
SCREEN = Path(__file__).resolve().parent / "data" / "unobtainable_openalex_screen.tsv"

CASE_FIELDS = ["case", "copying_paper", "title", "type", "copied_pct", "author", "university", "country", "source",
               "n_universities", "likely_merge", "orcid_professor_post", "last_published", "agent_decision",
               "overlap_pct", "health_sciences"]
POOL_FIELDS = ["pair_id", "lead_rank", "tier", "copying_paper", "title", "copied_pct", "author", "university",
               "country", "source", "n_universities", "likely_merge", "orcid_professor_post", "last_published",
               "model_score", "gi_pct_a", "gi_pct_b", "venue_record", "same_title", "year_a", "year_b", "doi_a", "doi_b",
               "title_a", "title_b"]
UNIDENTIFIED_TIER = 5    # gi_rank_leads: a side with no metadata, or one PDF held twice; unobtainable drops it

# Proceedings records: Crossref titles like "[IEEE 2009 Chinese Control and Decision Conference (CCDC) - Guilin,
# China (2009.6.17-2009.6.19)] 2009 Chinese Control and Decision Conference" name a volume, not a paper. Two of
# them matching is two volumes' shared boilerplate; one against a paper may be that paper inside the volume.
_VENUE_PREFIX = re.compile(r"(?:SPIE Proceedings\s*)?\[(.*)\]\s*(.*)$", re.S)
_VENUE_WORDS = re.compile(r"conference|symposium|proceedings|workshop|congress|meeting|exhibition|colloquium|"
                          r"convention|seminar|forum|summit|expo|summaries of papers|technical digest", re.I)
_FRONT_MATTER = re.compile(r"front ?matter|table of contents|^contents$|author index|subject index|title page|"
                           r"copyright page|masthead|editorial board|summaries of papers|technical digest", re.I)


def copying_sides(earlier):
    """The side(s) whose academics count, and the copying_paper label prefix's suffix."""
    if earlier:
        return ["b" if earlier == "a" else "a"], ""
    return ["a", "b"], " (order unknown)"


def western_academics(db, doi):
    """(name, last_year, {university: (country, source)}) for each target author of doi with a Western
    university; a university from both sources gets "OpenAlex last known + ORCID current job"."""
    out = []
    for a in db.execute("""select coalesce(s.name, au.name) name, au.*, o.current from oa_authorships s
                           join oa_authors au using (author_id) left join orcid_employments o on o.orcid = au.orcid
                           where s.doi = ? order by s.position""", ((doi or "").lower(),)):
        jobs = json.loads(a["current"]) if a["current"] is not None else None
        if ga.author_status(a, jobs)[0] != "target":
            continue
        insts = [(i, OPENALEX_SRC) for i in json.loads(a["institutions"] or "[]")
                 if i.get("type") in ga.ACADEMIC_TYPES]
        insts += [(j, ORCID_SRC) for j in jobs or [] if ga.ACADEMIC_NAME.search(j.get("name") or "")]
        unis = {}
        for i, src in insts:
            if (i.get("country") or "").upper() not in ga.TARGET_COUNTRIES:
                continue
            if i["name"] not in unis:
                unis[i["name"]] = (i.get("country"), src)
            elif src not in unis[i["name"]][1]:
                unis[i["name"]] = (unis[i["name"]][0], unis[i["name"]][1] + " + " + src)
        if unis:
            out.append((a["name"], a["last_year"], unis))
    return out


def venue_only(title):
    """True for a proceedings-volume record: a bracketed venue, then nothing or only a venue name."""
    m = _VENUE_PREFIX.match((title or "").strip())
    return bool(m) and (not m.group(2) or bool(_VENUE_WORDS.search(m.group(2))))


def front_matter(title):
    """True for a title (after any bracketed venue) that is front matter: contents, index, title page, ..."""
    return bool(_FRONT_MATTER.search(re.sub(r"^.*?\]\s*", "", (title or "").strip(), count=1, flags=re.S)))


def proceedings_noise(title_a, title_b):
    """Drop the pair: either side is front matter, or both are proceedings-volume records."""
    return front_matter(title_a) or front_matter(title_b) or (venue_only(title_a) and venue_only(title_b))


def merge_flag(unis):
    """(n_universities, likely_merge) for one person's {university: (country, source)}."""
    n = len(unis)
    orcid = any(ORCID_SRC in src for _, src in unis.values())
    return n, "yes" if n >= MERGE_UNIVERSITIES and not orcid else ""


def academic_rows(db, doi_a, doi_b, earlier, profs):
    """The shared columns (copying_paper .. last_published) for every Western academic on the copying paper."""
    sides, note = copying_sides(earlier)
    rows, seen = [], set()
    for s in sides:
        doi = doi_a if s == "a" else doi_b
        prof = dict(profs.get((doi or "").lower(), []))
        for name, last_year, unis in western_academics(db, doi):
            n, merge = merge_flag(unis)
            for uni, (country, src) in unis.items():
                if (name, uni) in seen:      # an author of both papers (order unknown): listed once, under A
                    continue
                seen.add((name, uni))
                rows.append({"copying_paper": s.upper() + note, "author": name, "university": uni,
                             "country": country, "source": src, "n_universities": n, "likely_merge": merge,
                             "orcid_professor_post": prof.get(name, ""), "last_published": last_year or ""})
    return rows


def cmd_cases(db, args):
    import gi_review_cli as cli
    import gi_write_pages as pages
    queue = cli.load_queue(db, order="affiliation", western_only=True)
    medical = cli.medical_pairs(db, queue)
    profs = ga.doi_professors(db, [d for r in queue for d in (r["doi_a"], r["doi_b"])])
    rows = []
    for r in queue:
        if r["ai_type"] in NOT_COPYING and not args.all_types:
            continue
        titles = {s: cli.paper_info(r["library"], r[f"paper_{s}"])[0] for s in "ab"}
        for row in academic_rows(db, r["doi_a"], r["doi_b"], pages.earlier_side(r), profs):
            side = row["copying_paper"][0].lower()
            rows.append({"case": f"GI-{r['page_number']:02d}", "copying_paper": row["copying_paper"],
                         "title": titles[side], "type": r["ai_type"] or "",
                         "copied_pct": "" if r[f"ft_pct_{side}"] is None else f"{r[f'ft_pct_{side}']:.0f}", **row,
                         "agent_decision": r["ai_decision"] or "not reviewed",
                         "overlap_pct": f"{max(r['ft_pct_a'] or 0, r['ft_pct_b'] or 0):.0f}",
                         "health_sciences": "yes" if cli.pair_key(r) in medical else ""})
    return CASE_FIELDS, rows, "case"


def cmd_unobtainable(db, args):
    csv.field_size_limit(10**9)
    with open(args.screen, newline="", encoding="utf-8") as f:
        pool = [r for r in csv.DictReader(f, delimiter="\t") if r["eliminated"] != "1"]
    pct, rank = {}, {}
    ids = [int(r["pair_id"]) for r in pool]
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        for pid, a, b in db.execute(f"select pair_id, pct_a, pct_b from pairs where pair_id in ({ga._qs(chunk)})",
                                    chunk):
            pct[pid] = (a or 0, b or 0)
        for pid, tier, lead_rank in db.execute(
                f"select pair_id, tier, lead_rank from leads where pair_id in ({ga._qs(chunk)})", chunk):
            rank[pid] = (tier, lead_rank)
    pool = [r for r in pool if max(pct.get(int(r["pair_id"]), (0, 0))) >= args.min_pct
            and (args.all_tiers or rank.get(int(r["pair_id"]), (None,))[0] != UNIDENTIFIED_TIER)
            and (args.keep_proceedings or not proceedings_noise(r["title_a"], r["title_b"]))]
    dois = [d for r in pool for d in (r["doi_a"], r["doi_b"])]
    scores = ga.doi_scores(db, dois)
    profs = ga.doi_professors(db, dois)
    rows = []
    unranked = (None, float("inf"))
    for r in sorted(pool, key=lambda r: ((rank.get(int(r["pair_id"])) or unranked)[1] or float("inf"),
                                         -float(r["p"] or 0))):
        earlier = gi_preprints.earlier_by_year(r["year_a"] or None, r["year_b"] or None)
        sides, _ = copying_sides(earlier)
        if not any(scores.get((r[f"doi_{s}"] or "").lower(), (ga.UNKNOWN,))[0] == ga.TARGET for s in sides):
            continue
        pa, pb = pct[int(r["pair_id"])]
        tier, lead_rank = rank.get(int(r["pair_id"]), ("", ""))
        for row in academic_rows(db, r["doi_a"], r["doi_b"], earlier, profs):
            side = row["copying_paper"][0].lower()
            rows.append({"pair_id": r["pair_id"], "lead_rank": lead_rank, "tier": tier,
                         "copying_paper": row["copying_paper"], "title": r[f"title_{side}"].strip(),
                         "copied_pct": f"{pa if side == 'a' else pb:.0f}", **row, "model_score": r["p"],
                         "gi_pct_a": f"{pa:.0f}", "gi_pct_b": f"{pb:.0f}",
                         "venue_record": "".join(x.upper() for x in "ab" if venue_only(r[f"title_{x}"])),
                         "same_title": "yes" if r["same_title"] == "1" else "",
                         "year_a": r["year_a"], "year_b": r["year_b"], "doi_a": r["doi_a"], "doi_b": r["doi_b"],
                         "title_a": r["title_a"].strip(), "title_b": r["title_b"].strip()})
    return POOL_FIELDS, rows, "pair_id"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["cases", "unobtainable"])
    ap.add_argument("out", type=Path, help="the CSV to write")
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--all-types", action="store_true",
                    help="cases: keep the agent types that aren't copying by someone else (NOT_COPYING)")
    ap.add_argument("--all-tiers", action="store_true", help="unobtainable: keep tier 5 (UNIDENTIFIED_TIER)")
    ap.add_argument("--keep-proceedings", action="store_true",
                    help="unobtainable: keep front-matter and volume-against-volume pairs (proceedings_noise)")
    ap.add_argument("--screen", type=Path, default=SCREEN, help="unobtainable: the screen TSV")
    ap.add_argument("--min-pct", type=float, default=15, help="unobtainable: larger GI 5-gram share at least this")
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    fields, rows, key = {"cases": cmd_cases, "unobtainable": cmd_unobtainable}[args.command](db, args)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)
    people = {(r[key], r["author"]): r["likely_merge"] for r in rows}
    print(f"{args.out}: {len({r[key] for r in rows})} {key}s, {len(people)} academics, {len(rows)} rows; "
          f"{sum(1 for m in people.values() if m)} academics flagged likely_merge")


if __name__ == "__main__":
    main()

"""Build data/priorities.sqlite3: the preprint and major-publisher DOI lists, and every pair's priority.

A separate database, rebuilt from scratch each run, so gi.sqlite3 (a WAL database with live readers and
writers) is only ever read. Tables:
  preprint_prefixes, major_publisher_prefixes, major_publisher_excluded_books
      the three TSVs as they are (preprint_doi_prefixes.tsv, major_publisher_doi_prefixes.tsv,
      major_publisher_excluded_books.tsv)
  doi_class        one row per DOI in the ranked leads or fulltext: preprint server, major publisher,
                   its group (core / etc / debatable), whether it promotes, and an excluded venue's reason
  lead_priority    one row per ranked lead (tiers 1-5): which side came first by the index's years, and
                   gi_preprints.pair_priority()'s rank and note -- as gi_rank_leads.py sorts them
  fulltext_priority one row per full-text-checked pair: the same, with "which came first" decided as on
                   the result page (gi_write_pages.priority_status()), plus the AI's and the human's verdicts
The ranks: -2 promoted (the later paper is from a major publisher), -1 possibly promoted (order unknown),
0 ordinary, 1 possibly by a preprint, 2 the later paper is a preprint. HOW_LEADS_ARE_FLAGGED.md has the rule.

Usage: gi_priority_db.py [--db data/gi.sqlite3] [--out data/priorities.sqlite3]
"""
import argparse
import csv
import os
import sqlite3
from datetime import datetime, timezone

import gi_db
import gi_preprints
import gi_write_pages

LABELS = {gi_preprints.PROMOTED: "promoted", gi_preprints.POSSIBLY_PROMOTED: "possibly promoted",
          gi_preprints.ORDINARY: "ordinary", gi_preprints.POSSIBLY: "possibly by a preprint",
          gi_preprints.LATER: "later paper is a preprint"}

SCHEMA = """
create table doi_class (doi text primary key, preprint_server text, publisher text, publisher_group text,
    promotes integer, venue text, reason text);
create table lead_priority (pair_id integer primary key, pair_lo text, pair_hi text, doi_a text, doi_b text,
    tier integer, lead_rank integer, primary_pair integer, year_a text, year_b text,
    earlier text,            -- 'a', 'b', or null when the years are equal or missing
    priority integer, priority_label text, note text);
create table fulltext_priority (pair_lo text, pair_hi text, doi_a text, doi_b text, run text, page_number integer,
    ft_verdict text, byline text, year_a text, year_b text,
    priority integer, priority_label text, note text,
    ai_decision text, ai_type text, ai_severity text, ai_model text,
    human_decision text, human_type text, human_severity text,
    primary key (pair_lo, pair_hi));
create table build_info (key text primary key, value text);
create index idx_lead_priority on lead_priority(tier, priority);
create index idx_fulltext_priority on fulltext_priority(priority);
"""


def copy_tsv(out, table, path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    rows = [r for r in rows if r and not r[0].startswith("#")]
    head, body = rows[0], rows[1:]
    out.execute(f"create table {table} ({', '.join(f'{c!r} text' for c in head)})")
    out.executemany(f"insert into {table} values ({', '.join('?' * len(head))})",
                    [(r + [""] * len(head))[:len(head)] for r in body])
    return len(body)


def classify(doi):
    server = gi_preprints.is_preprint_doi(doi)
    pub = gi_preprints.major_publisher(doi)
    if not (server or pub):
        return None
    return (doi, server, pub.name if pub else None, pub.group if pub else None,
            int(pub.promote) if pub else None, (pub.venue or None) if pub else None, (pub.reason or None) if pub else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(gi_db.DB_PATH))
    ap.add_argument("--out", default=str(gi_db.DATA / "priorities.sqlite3"))
    args = ap.parse_args()
    src = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=300)
    src.row_factory = sqlite3.Row
    tmp = args.out + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    out = sqlite3.connect(tmp)
    out.executescript(SCHEMA)
    counts = {t: copy_tsv(out, t, p) for t, p in (
        ("preprint_prefixes", gi_preprints.TSV), ("major_publisher_prefixes", gi_preprints.MAJOR_TSV),
        ("major_publisher_excluded_books", gi_preprints.MAJOR_TSV.parent / "major_publisher_excluded_books.tsv"))}

    seen, n = set(), 0
    batch, classes = [], []
    rows = src.execute("""select l.pair_id, l.pair_lo, l.pair_hi, l.doi_a, l.doi_b, l.tier, l.lead_rank, l.primary_pair,
                                 substr(da.pub_date, 1, 4) year_a, substr(db.pub_date, 1, 4) year_b
                          from leads l join pairs p using (pair_id)
                          left join documents da on da.dkey = p.dkey_a left join documents db on db.dkey = p.dkey_b
                          where l.tier is not null""")
    for r in rows:
        earlier = gi_preprints.earlier_by_year(r["year_a"], r["year_b"])
        rank, note = gi_preprints.pair_priority(r["doi_a"], r["doi_b"], earlier)
        batch.append((r["pair_id"], r["pair_lo"], r["pair_hi"], r["doi_a"], r["doi_b"], r["tier"], r["lead_rank"],
                      r["primary_pair"], r["year_a"], r["year_b"], earlier, rank, LABELS[rank], note))
        for d in (r["doi_a"], r["doi_b"]):
            if d and d not in seen:
                seen.add(d)
                c = classify(d)
                if c:
                    classes.append(c)
        if len(batch) >= 100_000:
            out.executemany(f"insert into lead_priority values ({', '.join('?' * 14)})", batch)
            n += len(batch)
            batch = []
            print(f"  {n:,} leads", flush=True)
    out.executemany(f"insert into lead_priority values ({', '.join('?' * 14)})", batch)
    n += len(batch)

    ft = src.execute("""select f.*, a.decision ai_decision, a.type ai_type, a.severity ai_severity, a.model ai_model,
                               r.decision human_decision, r.type human_type, r.severity human_severity
                        from fulltext f
                        left join reviews r on r.pair_lo = f.pair_lo and r.pair_hi = f.pair_hi
                        left join agent_reviews a on a.agent_review_id = (
                            select agent_review_id from agent_reviews x
                            where x.pair_lo = f.pair_lo and x.pair_hi = f.pair_hi and x.type is not null
                            order by x.model like 'local:%', x.agent_review_id desc limit 1)""").fetchall()
    ft_rows = []
    for f in ft:
        rank, note = gi_write_pages.priority_status(f)
        ft_rows.append((f["pair_lo"], f["pair_hi"], f["doi_a"], f["doi_b"], f["run"], f["page_number"],
                        f["ft_verdict"], f["byline"], f["year_a"], f["year_b"], rank, LABELS[rank], note,
                        f["ai_decision"], f["ai_type"], f["ai_severity"], f["ai_model"],
                        f["human_decision"], f["human_type"], f["human_severity"]))
        for d in (f["doi_a"], f["doi_b"]):
            if d and d not in seen:
                seen.add(d)
                c = classify(d)
                if c:
                    classes.append(c)
    out.executemany(f"insert into fulltext_priority values ({', '.join('?' * 20)})", ft_rows)
    out.executemany("insert into doi_class values (?, ?, ?, ?, ?, ?, ?)", classes)
    out.executemany("insert into build_info values (?, ?)", [
        ("built_at", datetime.now(timezone.utc).isoformat(timespec="seconds")), ("source_db", args.db),
        ("enabled_groups", ",".join(sorted(gi_preprints.ENABLED_GROUPS)))])
    out.commit()
    out.close()
    os.replace(tmp, args.out)
    print(f"{args.out}: {n:,} leads, {len(ft_rows):,} fulltext pairs, {len(classes):,} classified DOIs; "
          + ", ".join(f"{t} {c}" for t, c in counts.items()))


if __name__ == "__main__":
    main()

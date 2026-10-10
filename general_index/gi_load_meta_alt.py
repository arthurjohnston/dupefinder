"""Load titles/authors for documents missing from the slice metadata, from the 2022 update file.

doc_info_N.sql.tar.gz has no row at all for about a third of the index's documents. The 2022
update (doc_info.2022.12.update.gz, a tar.gz of doc_meta_alt_N pg_dump tables) has a row for them,
almost never with a DOI, but with a title taken from the document text for ~74% and authors for
~59% (PILOT_FINDINGS.md). That is enough to run gi_filters on them and to look a DOI up by title.

Streams the file over HTTP (never stored) and writes rows into a meta_alt table in META_DB, only
for dkeys whose first hex digit is in --slices and that the meta table doesn't already have.

Usage: gi_load_meta_alt.py META_DB [--slices 0123] [--source URL]
"""
import argparse
import sqlite3
import sys
import time
import urllib.request
import zlib

URL = "https://archive.org/download/GeneralIndex/data/doc_metadata/doc_info.2022.12.update.gz"
NULLS = {"\\N", "N/A", "--", "", "no DOI"}
KEEP = ("dkey", "doi", "title", "author", "pub_date", "doc_size")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("meta_db")
    ap.add_argument("--slices", default="0123")
    ap.add_argument("--source", default=URL)
    args = ap.parse_args()
    slices = tuple(args.slices)

    db = sqlite3.connect(args.meta_db)
    db.execute("create table if not exists meta_alt (dkey text primary key, doi text, title text, "
               "author text, pub_date text, doc_size text)")
    # only the shards being loaded: all 16 shards' keys would be ~70M strings in memory
    known = {r[0] for r in db.execute(
        f"select dkey from meta where substr(dkey, 1, 1) in ({','.join('?' * len(slices))})", slices)}
    print(f"{len(known):,} dkeys already in meta", flush=True)

    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    stream = urllib.request.urlopen(urllib.request.Request(args.source), timeout=120)
    cols, tail, rows, kept, comp, t0 = None, b"", [], 0, 0, time.time()
    while True:
        buf = stream.read(4 << 20)
        if not buf:
            break
        comp += len(buf)
        data = tail + inflater.decompress(buf)
        lines = data.split(b"\n")
        tail = lines.pop()
        for raw in lines:
            if raw.startswith(b"COPY ") or b"COPY docs." in raw[:600]:
                line = raw[raw.index(b"COPY "):].decode("utf-8", "replace")
                cols = line[line.index("(") + 1:line.index(")")].split(", ")
                continue
            if cols is None or raw.startswith(b"\\."):
                if raw.startswith(b"\\."):
                    cols = None
                continue
            p = raw.decode("utf-8", "replace").split("\t")
            if len(p) != len(cols) or not p[0].startswith(slices) or p[0] in known:
                continue
            r = dict(zip(cols, p))
            rows.append(tuple(None if r.get(c, "") in NULLS else r.get(c) for c in KEEP))
            if len(rows) >= 100_000:
                kept += db.executemany("insert or ignore into meta_alt values (?,?,?,?,?,?)", rows).rowcount
                db.commit()
                rows = []
        if comp // (1 << 30) != (comp - len(buf)) // (1 << 30):
            print(f"{comp / 1e9:.1f} GB compressed, {kept:,} rows kept, "
                  f"{comp / (time.time() - t0) / 1e6:.1f} MB/s", flush=True)
    if rows:
        kept += db.executemany("insert or ignore into meta_alt values (?,?,?,?,?,?)", rows).rowcount
        db.commit()
    print(f"done: {comp / 1e9:.1f} GB, {kept:,} rows in meta_alt, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

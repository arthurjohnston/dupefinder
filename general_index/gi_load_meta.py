"""Load General Index doc_info_N.sql.tar.gz metadata into one SQLite lookup (dkey -> doi/title/...).

Usage: gi_load_meta.py OUT_DB doc_info_0.sql.tar.gz [doc_info_1.sql.tar.gz ...]
"""
import sqlite3
import sys
import tarfile

NULLS = {"\\N", "N/A", "--", ""}
COLS = ("dkey", "doi", "journal", "title", "pub_date", "author", "doc_size")


def main():
    db = sqlite3.connect(sys.argv[1])
    db.execute(f"create table if not exists meta({', '.join(c + ' text' for c in COLS)}, primary key (dkey))")
    for path in sys.argv[2:]:
        with tarfile.open(path, "r:gz") as tar:
            member = next(m for m in tar if m.isfile())
            cols, rows, n = None, [], 0
            for raw in tar.extractfile(member):
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if cols is None:
                    if line.startswith("COPY"):
                        cols = line[line.index("(") + 1:line.index(")")].split(", ")
                    continue
                if line == "\\.":
                    break
                p = line.split("\t")
                if len(p) != len(cols):
                    continue
                r = dict(zip(cols, p))
                rows.append(tuple(None if r[c] in NULLS else r[c] for c in COLS))
                n += 1
                if len(rows) >= 200_000:
                    db.executemany(f"insert or ignore into meta values({','.join('?' * len(COLS))})", rows)
                    rows = []
            db.executemany(f"insert or ignore into meta values({','.join('?' * len(COLS))})", rows)
            db.commit()
            print(f"{path}: {n:,} rows", flush=True)


if __name__ == "__main__":
    main()

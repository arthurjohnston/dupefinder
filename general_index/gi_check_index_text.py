"""Does the index's text for a document match the PDF we downloaded for it?

For every full-text pair with both papers (fulltext.retrieved = 'AB') in the given runs: reads each side's
sampled 5-gram hashes from the shard samples (slice*/part_XX.bin, the same hashes the pairing used) and
computes the same hashes from our extracted text of BOTH downloaded PDFs. own = share of the index
document's samples found in its own PDF's text; other = share found in the partner's PDF.

The index lemmatizes (spaCy: "have pulled" -> "have pull", "data" -> "datum") and this doesn't, so a 5-gram
with an inflected word can't match and own stays well under 100% even for the right document. What
matters is own against other: right text -> own clearly above other; the index holding a different
document under that DOI -> own near 0.

Usage: gi_check_index_text.py RUN [RUN ...] [--out data/index_text_check.tsv]
"""
import argparse
import csv
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np

import gi_db

REC = np.dtype([("h", "<u8"), ("d", "<u4")])
WORD = re.compile(r"[a-z0-9]+")


def text_hashes(lib, pid, rate=8):
    words = []
    for (t,) in lib.execute("select text from paragraphs where paper_id = ? order by para_index", (pid,)):
        words += WORD.findall(t.lower())
    out = set()
    for i in range(len(words) - 4):
        h = int.from_bytes(hashlib.blake2b(" ".join(words[i:i + 5]).encode(), digest_size=8).digest(), "little")
        if h % rate == 0:
            out.add(h)
    return out, len(words)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", default=str(gi_db.DATA / "index_text_check.tsv"))
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{gi_db.DB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute(f"select * from fulltext where retrieved = 'AB' and run in ({','.join('?' * len(args.runs))})",
                      args.runs).fetchall()
    sides = []  # (row, side, dkey)
    for r in rows:
        p = db.execute("select p.dkey_a, p.dkey_b from leads l join pairs p using (pair_id) "
                       "where l.pair_lo = ? and l.pair_hi = ? limit 1", (r["pair_lo"], r["pair_hi"])).fetchone()
        da = db.execute("select doi from documents where dkey = ?", (p["dkey_a"],)).fetchone()[0] or ""
        doi_of_a = da.lower() if da else f"gi:{p['dkey_a']}"
        ka, kb = (p["dkey_a"], p["dkey_b"]) if doi_of_a == r["doi_a"].lower() else (p["dkey_b"], p["dkey_a"])
        sides.append((r, ka, kb))
    want = {k for _, ka, kb in sides for k in (ka, kb)}
    # dkey -> (shard, doc_id) from docs.tsv
    loc = {}
    for s in gi_db.available_shards():
        with open(gi_db.shard_dir(s) / "docs.tsv") as f:
            for line in f:
                did, dk, *_ = line.split("\t", 3)
                if dk in want:
                    loc[dk] = (s, int(did))
    print(f"{len(rows)} pairs, {len(want)} index documents, {len(loc)} located in the shard samples", flush=True)
    samples = {k: set() for k in loc}
    for s in sorted({v[0] for v in loc.values()}):
        ids = {v[1]: k for k, v in loc.items() if v[0] == s}
        idarr = np.array(sorted(ids), dtype=np.uint32)
        for part in sorted(gi_db.shard_dir(s).glob("part_*.bin")):
            a = np.fromfile(part, dtype=REC)
            m = a[np.isin(a["d"], idarr)]
            for h, d in zip(m["h"].tolist(), m["d"].tolist()):
                samples[ids[d]].add(h)
        print(f"shard {s} read", flush=True)
    out = []
    libs = {}
    for r, ka, kb in sides:
        lib = libs.setdefault(r["library"], sqlite3.connect(r["library"]))
        ta, na = text_hashes(lib, r["paper_a"])
        tb, nb = text_hashes(lib, r["paper_b"])
        row = {"run": r["run"], "doi_a": r["doi_a"], "doi_b": r["doi_b"], "ft_verdict": r["ft_verdict"],
               "ft_pct_a": r["ft_pct_a"], "ft_pct_b": r["ft_pct_b"], "words_a": na, "words_b": nb}
        for side, k, own, other in (("a", ka, ta, tb), ("b", kb, tb, ta)):
            sm = samples.get(k, set())
            row[f"idx_samples_{side}"] = len(sm)
            row[f"own_{side}"] = round(100 * len(sm & own) / len(sm), 1) if sm else None
            row[f"other_{side}"] = round(100 * len(sm & other) / len(sm), 1) if sm else None
        out.append(row)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]), delimiter="\t")
        w.writeheader()
        w.writerows(out)
    print(f"wrote {len(out)} rows -> {args.out}")


if __name__ == "__main__":
    main()

"""Stream one General Index keywords slice and keep the top keywords for chosen documents.

For identifying documents the index has no metadata for (PILOT_FINDINGS.md: the biggest recall
leak). The keywords zip is streamed like gi_sample_5grams.py streams n-grams, never stored; only
rows for the target dkeys survive, and per document the --top best-scored keywords are kept (YAKE:
lower score = more meaningful).

Usage: gi_keywords.py SOURCE TARGETS_JSON OUT_JSONL [--top 25]
TARGETS_JSON is a JSON list of dkeys. OUT_JSONL gets {"dkey": ..., "keywords": [[phrase, score], ...]}.
"""
import argparse
import heapq
import json
import sys
import time
import zlib

from gi_sample_5grams import compressed_chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("targets_json")
    ap.add_argument("out_jsonl")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    targets = {k.encode() for k in json.load(open(args.targets_json))}
    best = {}  # dkey -> max-heap (by -score) of the top keywords
    inflater, tail, in_copy, rows, comp, t0 = zlib.decompressobj(-15), b"", False, 0, 0, time.time()
    for cbuf in compressed_chunks(args.source):
        comp += len(cbuf)
        data = tail + inflater.decompress(cbuf)
        lines = data.split(b"\n")
        tail = lines.pop()
        for line in lines:
            if not in_copy:
                in_copy = line.startswith(b"COPY ")
                continue
            if line == b"\\.":
                in_copy = False
                continue
            p = line.split(b"\t", 5)
            if len(p) < 5 or p[0] not in targets:
                continue
            rows += 1
            try:
                score = float(p[4])
            except ValueError:
                continue
            h = best.setdefault(p[0], [])
            item = (-score, p[2].decode("utf-8", "replace"))
            if len(h) < args.top:
                heapq.heappush(h, item)
            elif item > h[0]:
                heapq.heapreplace(h, item)
        if comp // (1 << 30) != (comp - len(cbuf)) // (1 << 30):
            print(f"{comp / 1e9:.1f} GB, {rows:,} target rows, {len(best):,} docs, "
                  f"{comp / (time.time() - t0) / 1e6:.1f} MB/s", flush=True)
    with open(args.out_jsonl, "w") as f:
        for k, h in best.items():
            kws = sorted(((-s, w) for s, w in h))
            f.write(json.dumps({"dkey": k.decode(), "keywords": [[w, s] for s, w in kws]}) + "\n")
    print(f"done: {len(best):,} of {len(targets):,} target docs found, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

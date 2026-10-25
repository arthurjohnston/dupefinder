"""Tortured phrases in the General Index: how many 5-grams use one, and whether undoing them finds new pairs.

A tortured phrase is an established term run through a paraphrasing tool ("profound learning" for "deep
learning", "counterfeit consciousness" for "artificial intelligence"), used to hide copied text from
plagiarism checks. The Problematic Paper Screener (Cabanac, Labbé, Magazinov) keeps a list of them, each with
the text it replaced: https://dbrech.irit.fr/pls/apex/f?p=9999:5 ("CSV dump" under Tortured).

A copy disguised that way shares few exact 5-grams with its source, so the pairing (gi_pairs.py) misses it.
Putting the original term back recovers the source's 5-grams: that is what this measures.

  list    the PPS CSV -> data/tortured/phrases.tsv: each tortured phrase and its expected text, lemmatized
          the way the index is (spaCy; the index's 5-grams are lemmas, lower-cased, punctuation dropped).
          A fingerprint's first quoted phrase is the tortured one (its AND / NOT conditions are ignored);
          the expected text loses parentheses, brackets and '?', and 'a/b' gives both a and b; one that is a
          comment (a link, quotes) gives no rewrite. conditional = 1 marks a fingerprint with AND / NOT / OR
          conditions (the phrase alone is often ordinary wording: "differential condition"). Single-word
          tortured phrases are kept for counting but never rewritten (alone, most are ordinary words:
          "bosom" is only a fingerprint together with "breast").
  stream  one n-gram slice (as gi_sample_5grams.py reads it): counts every 5-gram row holding a tortured
          phrase, per document and per phrase, and writes the rewritten 5-grams' hashes, kept on the same
          1-in-SAMPLE rule as the slice samples so they can be joined against them:
            data/tortured/sliceN/hits.tsv    dkey, phrase id, 5-gram rows
            data/tortured/sliceN/norm.bin    (uint64 hash, uint32 doc) for the rewritten, sampled 5-grams
            data/tortured/sliceN/docs.tsv    doc, dkey (documents with a hit only)
            data/tortured/sliceN/stats.json  totals; examples.tsv some rewrites, to check by eye
          A rewrite whose expected text is longer than the tortured phrase gives every 5-word window over
          the replacement; a shorter one can't fill five words from one row and is only counted
          (stats "short_rewrites").
  match   joins every streamed slice's norm.bin against all 16 slice samples: a rewritten hash shared by
          at most --max-group documents (as gi_pairs.py) gives a point to each (tortured doc, other doc)
          pair; pairs with at least --min-shared points are written to data/tortured/matches.tsv, with
          the pair's existing shared count from the `pairs` table (0 = a pair the index pairing never
          found).

Usage: gi_tortured.py list PPS_CSV
       gi_tortured.py stream SLICE [--sample 8] [--limit-gb N]
       gi_tortured.py match [--max-group 100] [--min-shared 5]
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import gi_db

OUT = gi_db.DATA / "tortured"
REC = np.dtype([("h", "<u8"), ("d", "<u4")])
NPART = 64
SLICE_URL = "https://archive.org/download/GeneralIndex.ngrams.{h}/doc_ngrams_{h}.sql.zip"
QUOTED = re.compile(r'["“”]([^"“”]+)["“”]')
WORD = re.compile(r"[a-z0-9][a-z0-9.']*")


def h64(text):
    return int.from_bytes(hashlib.blake2b(text.encode(), digest_size=8).digest(), "little")


# --- list ---------------------------------------------------------------------------------------------

def expected_variants(text):
    """The expected text's plain alternatives; [] when it is a comment rather than a replacement (a link,
    quotes, "the word 'are' as in ..."). Anything after " AND " is a condition, not part of the text."""
    text = text.replace("\xa0", " ").split(" AND ")[0]
    if re.search(r"https?:|[‘’'\"“”:]|\bword\b", text):
        return []
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]|\?", " ", text)
    return [v.strip() for v in text.split("/") if v.strip() and len(v.split()) <= 6]


def lemma_variants(nlp, phrase):
    """The phrase as the index would hold it: spaCy lemmas, lower-cased, punctuation (hyphens too) dropped.
    Lemmas depend on the tag, so the phrase is lemmatized alone and inside a carrier sentence, and both
    kept when they differ; plus the plain lower-cased words."""
    out = []
    for doc, span in ((nlp(phrase), None), (nlp(f"We describe the {phrase} here."), (3, -2))):
        toks = list(doc)[span[0]:span[1]] if span else list(doc)
        out.append(tuple(w for t in toks for w in WORD.findall(t.lemma_.lower())))
    out.append(tuple(WORD.findall(phrase.lower().replace("-", " "))))
    seen = []
    for v in out:
        if v and v not in seen:
            seen.append(v)
    return seen


def cmd_list(args):
    import spacy
    nlp = spacy.load("en_core_web_sm", disable=["ner", "parser"])
    OUT.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8")))
    n = 0
    with open(OUT / "phrases.tsv", "w", encoding="utf-8") as f:
        f.write("id\ttortured\texpected\tpps_papers\tconditional\ttortured_lemmas\texpected_lemmas\n")
        for i, r in enumerate(rows):
            m = QUOTED.findall(r["Fingerprint - Tortured Phrase"])
            if not m:
                continue
            t = m[0].strip()
            exp = expected_variants(r["Expected Text"])
            tl = lemma_variants(nlp, t)
            el = [v for e in exp for v in lemma_variants(nlp, e)]
            el = [v for v in dict.fromkeys(el) if v not in tl]
            papers = int(r["Nb Retrieved Papers"].replace(",", "") or 0)
            cond = int(bool(re.search(r"\b(AND|NOT|OR)\b", r["Fingerprint - Tortured Phrase"])))
            f.write(f"{i}\t{t}\t{' / '.join(exp)}\t{papers}\t{cond}\t{'|'.join(map(' '.join, tl))}\t"
                    f"{'|'.join(map(' '.join, el))}\n")
            n += 1
    print(f"{n} phrases -> {OUT / 'phrases.tsv'}")


def load_phrases():
    """{tortured lemma tuple: [(phrase id, [expected lemma tuples], conditional)]}"""
    table = defaultdict(list)
    with open(OUT / "phrases.tsv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            exp = [tuple(v.split()) for v in r["expected_lemmas"].split("|") if v]
            for t in r["tortured_lemmas"].split("|"):
                if t:
                    table[tuple(t.split())].append((int(r["id"]), exp, r["conditional"] == "1"))
    return table


# --- stream -------------------------------------------------------------------------------------------

def rewrites(words, i, k, exp):
    """The 5-word windows over words with words[i:i+k] replaced by exp, each including some of exp."""
    new = words[:i] + list(exp) + words[i + k:]
    lo, hi = i, i + len(exp)   # where the replacement sits in new
    return [new[s:s + 5] for s in range(0, len(new) - 4) if s < hi and s + 5 > lo]


def cmd_stream(args):
    import gi_sample_5grams as g
    table = load_phrases()
    maxk = max(map(len, table))
    first = {t[0] for t in table}
    first_b = {w.encode() for w in first}   # checked on the raw bytes, before decoding a row
    out = OUT / f"slice{args.slice}"
    out.mkdir(parents=True, exist_ok=True)
    src = SLICE_URL.format(h=f"{args.slice:x}")
    hits = Counter()
    docs = {}
    recs = []
    stats = Counter()
    examples = []
    inflater = zlib.decompressobj(-15)
    tail, in_copy, comp = b"", False, 0
    t0 = time.time()
    limit = args.limit_gb * 1e9 if args.limit_gb else None
    for cbuf in g.compressed_chunks(src):
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
            p = line.split(b"\t")
            if len(p) != 8 or p[3] != b"5":
                continue
            stats["rows"] += 1
            if first_b.isdisjoint(p[2].split()):
                continue
            words = p[2].decode("utf-8", "replace").split()
            found = []
            for i in range(len(words)):
                if words[i] not in first:
                    continue
                for k in range(1, min(maxk, len(words) - i) + 1):
                    for pid, exp, cond in table.get(tuple(words[i:i + k]), ()):
                        found.append((i, k, pid, exp, cond))
            if not found:
                continue
            stats["rows_with_phrase"] += 1
            dkey = p[0].decode()
            d = docs.setdefault(dkey, len(docs))
            multi = plain = False
            for i, k, pid, exp, cond in found:
                hits[(d, pid)] += 1
                if k < 2:
                    continue
                multi = True
                plain = plain or not cond
                for e in exp:
                    if len(e) < k and len(words) - k + len(e) < 5:
                        stats["short_rewrites"] += 1
                        continue
                    for w in rewrites(words, i, k, e):
                        text = " ".join(w)
                        h = h64(text)
                        stats["rewritten_5grams"] += 1
                        if h % args.sample == 0:
                            recs.append((h, d))
                            if len(examples) < 2000:
                                examples.append((dkey, pid, " ".join(words), text))
            stats["rows_with_multiword_phrase"] += multi
            stats["rows_with_plain_multiword_phrase"] += plain   # no AND/NOT conditions to check
        if comp // (1 << 30) != (comp - len(cbuf)) // (1 << 30):
            el = time.time() - t0
            print(f"{comp / 1e9:.1f} GB, {stats['rows']:,} 5-gram rows, {stats['rows_with_phrase']:,} with a "
                  f"phrase, {len(docs):,} docs, {len(recs):,} rewritten samples, {comp / el / 1e6:.1f} MB/s",
                  flush=True)
        if limit and comp >= limit:
            stats["stopped_at_gb"] = round(comp / 1e9, 1)
            break
    stats["docs_with_phrase"] = len(docs)
    stats["compressed_gb"] = round(comp / 1e9, 1)
    np.array(sorted(set(recs)), dtype=REC).tofile(out / "norm.bin")
    with open(out / "docs.tsv", "w") as f:
        for dkey, d in docs.items():
            f.write(f"{d}\t{dkey}\n")
    with open(out / "hits.tsv", "w") as f:
        rev = {d: k for k, d in docs.items()}
        for (d, pid), n in sorted(hits.items()):
            f.write(f"{rev[d]}\t{pid}\t{n}\n")
    with open(out / "examples.tsv", "w", encoding="utf-8") as f:
        for e in examples:
            f.write("\t".join(map(str, e)) + "\n")
    (out / "stats.json").write_text(json.dumps(stats, indent=1) + "\n")
    print(f"done: {json.dumps(stats)} in {time.time() - t0:.0f}s", flush=True)


# --- match --------------------------------------------------------------------------------------------

def sample_dirs():
    """The 16 slice samples, in slice order (slices 0-3 were re-sampled into sliceN_s8)."""
    out = []
    for s in range(16):
        for name in (f"slice{s}_s8", f"slice{s}"):
            d = gi_db.DATA / name
            if (d / "part_00.bin").exists():
                out.append(d)
                break
        else:
            sys.exit(f"no sample for slice {s}")
    return out


def cmd_match(args):
    streamed = sorted(OUT.glob("slice*/norm.bin"))
    if not streamed:
        sys.exit("nothing streamed yet (gi_tortured.py stream SLICE)")
    # the rewritten samples, with their documents as dkeys
    tort = defaultdict(set)   # hash -> tortured dkeys
    for nb in streamed:
        dk = {int(a): b for a, b in (l.rstrip("\n").split("\t") for l in open(nb.parent / "docs.tsv"))}
        for h, d in np.fromfile(nb, dtype=REC):
            tort[int(h)].add(dk[int(d)])
    hashes = np.array(sorted(tort), dtype=np.uint64)
    print(f"{len(hashes):,} rewritten sampled hashes from {len(streamed)} slice(s)", flush=True)
    # every document of the 16 samples holding one of them
    others = defaultdict(set)   # hash -> dkeys holding it unrewritten
    part_of = (hashes >> np.uint64(56)) % np.uint64(NPART)
    for sd in sample_dirs():
        dkeys = np.load(sd / "docs_dkey.npy", mmap_mode="r")
        for p in range(NPART):
            want = hashes[part_of == p]
            if not len(want):
                continue
            r = np.fromfile(sd / f"part_{p:02d}.bin", dtype=REC)
            r = r[np.isin(r["h"], want)]
            for h, d in r:
                others[int(h)].add(dkeys[d].decode())
        print(f"  {sd.name}: {sum(map(len, others.values())):,} hits so far", flush=True)
    points = Counter()
    used = Counter()
    for h, ts in tort.items():
        os_ = others.get(h, set())
        if not os_ or len(ts | os_) > args.max_group:
            continue
        used["hashes"] += 1
        for t in ts:
            for o in os_ - {t}:
                points[(t, o)] += 1
    found = [(t, o, n) for (t, o), n in points.items() if n >= args.min_shared]
    print(f"{used['hashes']:,} rewritten hashes found unrewritten elsewhere (groups <= {args.max_group}); "
          f"{len(points):,} pairs, {len(found):,} with >= {args.min_shared}", flush=True)
    # `pairs` has no index on the dkeys (and either order occurs), so one pass over it rather than a lookup each
    want = {tuple(sorted((t, o))) for t, o, _ in found}
    have = {}
    db = sqlite3.connect(f"file:{gi_db.DB_PATH}?mode=ro", uri=True, timeout=300)
    for pid, a, b, n in db.execute("select pair_id, dkey_a, dkey_b, shared from pairs"):
        key = (a, b) if a < b else (b, a)
        if key in want:
            have[key] = (pid, n)
    with open(OUT / "matches.tsv", "w") as f:
        f.write("tortured_dkey\tother_dkey\trewritten_shared\tpair_shared\tpair_id\n")
        for t, o, n in sorted(found, key=lambda x: -x[2]):
            pid, shared = have.get(tuple(sorted((t, o))), ("", 0))
            f.write(f"{t}\t{o}\t{n}\t{shared}\t{pid}\n")
    print(f"{sum(1 for t, o, _ in found if tuple(sorted((t, o))) not in have):,} of them not in `pairs`")
    print(f"-> {OUT / 'matches.tsv'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("list")
    a.add_argument("csv")
    a = sub.add_parser("stream")
    a.add_argument("slice", type=int)
    a.add_argument("--sample", type=int, default=8)
    a.add_argument("--limit-gb", type=float, help="stop after this many compressed GB (a trial)")
    a = sub.add_parser("match")
    a.add_argument("--max-group", type=int, default=100)
    a.add_argument("--min-shared", type=int, default=5)
    args = ap.parse_args()
    {"list": cmd_list, "stream": cmd_stream, "match": cmd_match}[args.cmd](args)


if __name__ == "__main__":
    main()

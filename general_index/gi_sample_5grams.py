"""Stream one General Index n-gram slice and keep a consistent hash sample of its 5-grams.

The slice is a ~300GB zip holding one pg_dump file. It is never stored: the zip is
read over HTTP, inflated on the fly (raw deflate after the local file header), and
only 5-gram rows survive. Each 5-gram's ngram_lc is hashed (blake2b, 64 bits) and
kept only when hash % SAMPLE == 0 -- the same 5-gram is kept or dropped in every
document, so two documents that share text share proportionally many samples.

Output, under OUT_DIR:
  part_XX.bin   (uint64 hash, uint32 doc_id) records, partitioned by hash top byte % NPART
  docs.tsv      doc_id, dkey, total 5-gram rows, sampled 5-gram rows

Usage: gi_sample_5grams.py SOURCE OUT_DIR [--sample N] [--nparts N]
SOURCE is an http(s) URL or a local .zip path (a truncated zip works, for testing).
"""
import argparse
import hashlib
import os
import struct
import sys
import time
import urllib.request
import zlib

import numpy as np

CHUNK = 4 * 1024 * 1024
# A long-lived connection can slow to a crawl (seen 2026-10-07: two streams under 1 MB/s while a fresh
# connection to the same server got 16-25 MB/s). Below MIN_RATE over SLOW_WINDOW seconds, drop it and
# resume on a new connection at the same byte.
MIN_RATE = 5e6  # was 1.5e6; degraded connections settled at 3-4 MB/s while fresh ones got 8-12 (2026-10-07 evening)
SLOW_WINDOW = 180
REC = np.dtype([("h", "<u8"), ("d", "<u4")])


def open_at(source, offset):
    if source.startswith("http"):
        req = urllib.request.Request(source, headers={"Range": f"bytes={offset}-"})
        return urllib.request.urlopen(req, timeout=120)
    f = open(source, "rb")
    f.seek(offset)
    return f


def total_size(source, stream, offset):
    """The whole file's size in bytes, or None if the server doesn't say. A ranged response carries
    it in Content-Range ("bytes a-b/TOTAL"); a full one in Content-Length."""
    if not source.startswith("http"):
        return os.path.getsize(source)
    rng = stream.headers.get("Content-Range", "")
    if "/" in rng and rng.rsplit("/", 1)[1].isdigit():
        return int(rng.rsplit("/", 1)[1])
    length = stream.headers.get("Content-Length")
    return offset + int(length) if length and length.isdigit() else None


def compressed_chunks(source):
    """Yield the compressed bytes of the single zip member, reconnecting on errors.

    An empty read only counts as the end once every byte of the file has arrived. archive.org can
    close a connection cleanly partway through, which reads exactly like end-of-file; before this
    check, slice 13 (2026-10-07) stopped at 82.2 of 282.3 GB and wrote its docs.tsv as if complete.
    A short read is now treated like any other dropped connection: reconnect and resume at the same
    byte."""
    offset = 0
    header = b""
    retries = 0
    total = None
    while True:
        try:
            stream = open_at(source, offset)
            win_t, win_off = time.time(), offset
            if total is None:
                total = total_size(source, stream, offset)
                if total is None:
                    print("warning: server gave no file size; a dropped connection can't be told "
                          "from the end of the file", file=sys.stderr, flush=True)
            while True:
                # The slow check runs before a read, never between counting a chunk and yielding it,
                # so a reconnect resumes exactly at the first byte not yet passed on.
                now = time.time()
                if now - win_t >= SLOW_WINDOW:
                    rate = (offset - win_off) / (now - win_t)
                    if rate < MIN_RATE and source.startswith("http") and (total is None or offset < total):
                        raise ConnectionError(f"slow connection: {rate / 1e6:.2f} MB/s over {now - win_t:.0f}s")
                    win_t, win_off = now, offset
                buf = stream.read(CHUNK)
                if not buf:
                    if total is not None and offset < total:
                        raise ConnectionError(f"short read: connection closed at {offset:,} of {total:,} bytes")
                    return
                offset += len(buf)
                retries = 0
                if header is not None:
                    header += buf
                    if len(header) < 30:
                        continue
                    nl, el = struct.unpack("<HH", header[26:30])
                    start = 30 + nl + el
                    if len(header) < start:
                        continue
                    assert header[:4] == b"PK\x03\x04", "not a zip local header"
                    assert struct.unpack("<H", header[8:10])[0] == 8, "not deflate"
                    buf, header = header[start:], None
                yield buf
        except (OSError, urllib.error.URLError) as e:
            retries += 1
            if retries > 20:
                raise
            print(f"reconnect at {offset} after {e!r}", file=sys.stderr, flush=True)
            time.sleep(min(60, 5 * retries))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("out_dir")
    ap.add_argument("--sample", type=int, default=4)
    ap.add_argument("--nparts", type=int, default=64)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    parts = [open(os.path.join(args.out_dir, f"part_{i:02d}.bin"), "wb") for i in range(args.nparts)]
    pending = [[] for _ in range(args.nparts)]
    doc_ids, totals, sampled = {}, [], []
    inflater = zlib.decompressobj(-15)
    tail = b""
    in_copy = False
    rows = 0
    t0 = time.time()
    comp_bytes = 0

    def flush(i):
        if pending[i]:
            np.array(pending[i], dtype=REC).tofile(parts[i])
            pending[i] = []

    for cbuf in compressed_chunks(args.source):
        comp_bytes += len(cbuf)
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
            rows += 1
            d = doc_ids.get(p[0])
            if d is None:
                d = doc_ids[p[0]] = len(totals)
                totals.append(0)
                sampled.append(0)
            totals[d] += 1
            h = int.from_bytes(hashlib.blake2b(p[2], digest_size=8).digest(), "little")
            if h % args.sample:
                continue
            sampled[d] += 1
            i = (h >> 56) % args.nparts
            pending[i].append((h, d))
            if len(pending[i]) >= 100_000:
                flush(i)
        if comp_bytes // (1 << 30) != (comp_bytes - len(cbuf)) // (1 << 30):
            el = time.time() - t0
            print(f"{comp_bytes / 1e9:.1f} GB compressed, {rows:,} 5-gram rows, "
                  f"{len(totals):,} docs, {comp_bytes / el / 1e6:.1f} MB/s", flush=True)

    for i in range(args.nparts):
        flush(i)
        parts[i].close()
    with open(os.path.join(args.out_dir, "docs.tsv"), "w") as f:
        for dkey, d in doc_ids.items():
            f.write(f"{d}\t{dkey.decode()}\t{totals[d]}\t{sampled[d]}\n")
    print(f"done: {comp_bytes / 1e9:.1f} GB, {rows:,} 5-gram rows, {len(totals):,} docs, "
          f"{sum(sampled):,} sampled, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

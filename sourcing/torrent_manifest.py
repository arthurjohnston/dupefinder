#!/usr/bin/env python3
"""
Build selective-download manifests for the historical LibGen/SciMag torrents.

Historical SciMag layout:
  - one sm_XXXXXXXX-XXXXXXXX.torrent spans 100,000 SciMag IDs
  - each torrent contains about 100 ZIP files
  - each ZIP spans 1,000 SciMag IDs
  - PDFs inside a ZIP are stored under DOI-derived paths

This script does NOT download anything. It produces:
  - CSV mapping DOI -> SciMag ID -> torrent -> ZIP -> DOI.pdf
  - per-torrent wanted-ZIP lists
  - exact aria2c --select-file commands if local .torrent metadata is supplied

Input CSV must contain ID and DOI columns (case-insensitive).
"""

from __future__ import annotations

import argparse
import csv
import shlex
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

TORRENT_SPAN = 100_000
ZIP_SPAN = 1_000


def normalize_doi(s: str) -> str:
    s = (s or "").strip()
    lower = s.lower()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if lower.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.strip().lower()


def torrent_for_id(scimag_id: int) -> tuple[int, int, str]:
    start = (scimag_id // TORRENT_SPAN) * TORRENT_SPAN
    end = start + TORRENT_SPAN - 1
    return start, end, f"sm_{start:08d}-{end:08d}.torrent"


def zip_for_id(scimag_id: int) -> tuple[int, int, str]:
    start = (scimag_id // ZIP_SPAN) * ZIP_SPAN
    end = start + ZIP_SPAN - 1
    return start, end, f"libgen.scimag{start:08d}-{end:08d}.zip"


def bdecode(data: bytes) -> Any:
    """Minimal bencode decoder sufficient for .torrent metadata."""
    i = 0

    def parse():
        nonlocal i
        if i >= len(data):
            raise ValueError("unexpected end of bencoded data")
        c = data[i:i+1]

        if c == b"i":
            i += 1
            end = data.index(b"e", i)
            value = int(data[i:end])
            i = end + 1
            return value

        if c == b"l":
            i += 1
            out = []
            while data[i:i+1] != b"e":
                out.append(parse())
            i += 1
            return out

        if c == b"d":
            i += 1
            out = {}
            while data[i:i+1] != b"e":
                key = parse()
                out[key] = parse()
            i += 1
            return out

        if b"0" <= c <= b"9":
            colon = data.index(b":", i)
            n = int(data[i:colon])
            i = colon + 1
            value = data[i:i+n]
            i += n
            return value

        raise ValueError(f"invalid bencode token at offset {i}: {c!r}")

    result = parse()
    if i != len(data):
        raise ValueError(f"trailing bytes: {len(data)-i}")
    return result


def decode_text(x: bytes) -> str:
    return x.decode("utf-8", errors="replace")


def torrent_files(path: Path) -> list[dict]:
    """
    Return files in BitTorrent metadata order.
    aria2 --select-file uses 1-based indexes corresponding to this order.
    """
    meta = bdecode(path.read_bytes())
    info = meta.get(b"info")
    if not isinstance(info, dict):
        raise ValueError(f"{path}: missing info dictionary")

    result = []
    if b"files" in info:
        for idx, f in enumerate(info[b"files"], 1):
            parts = [decode_text(p) for p in f.get(b"path", [])]
            result.append({
                "index": idx,
                "path": "/".join(parts),
                "length": int(f.get(b"length", 0)),
            })
    else:
        result.append({
            "index": 1,
            "path": decode_text(info.get(b"name", b"")),
            "length": int(info.get(b"length", 0)),
        })
    return result


def read_scimag_csv(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit(f"{path}: CSV has no header")

        names = {x.lower(): x for x in reader.fieldnames}
        id_col = names.get("id") or names.get("scimag_id")
        doi_col = names.get("doi")
        if not id_col or not doi_col:
            raise SystemExit(
                f"{path}: need ID and DOI columns. Found: {reader.fieldnames}"
            )

        for lineno, row in enumerate(reader, 2):
            raw_id = (row.get(id_col) or "").strip()
            doi = normalize_doi(row.get(doi_col) or "")
            if not raw_id or not doi:
                continue
            try:
                sid = int(raw_id)
            except ValueError:
                print(f"Skipping line {lineno}: invalid ID {raw_id!r}", file=sys.stderr)
                continue

            t_start, t_end, torrent = torrent_for_id(sid)
            z_start, z_end, zip_name = zip_for_id(sid)
            rows.append({
                "scimag_id": sid,
                "doi": doi,
                "torrent_start": t_start,
                "torrent_end": t_end,
                "torrent": torrent,
                "zip_start": z_start,
                "zip_end": z_end,
                "zip_basename": zip_name,
                "pdf_path_in_zip": doi + ".pdf",
            })
    return rows


def cmd_build(args):
    rows = read_scimag_csv(Path(args.scimag_csv))
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    seen = set()
    deduped = []
    for r in rows:
        key = (r["scimag_id"], r["doi"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    rows = deduped

    torrent_dir = Path(args.torrent_dir).expanduser() if args.torrent_dir else None
    file_maps = {}

    if torrent_dir:
        for torrent_name in sorted({r["torrent"] for r in rows}):
            path = torrent_dir / torrent_name
            if not path.exists():
                print(f"Warning: missing {path}", file=sys.stderr)
                continue
            try:
                entries = torrent_files(path)
            except Exception as e:
                print(f"Warning: failed to parse {path}: {e}", file=sys.stderr)
                continue

            by_base = defaultdict(list)
            for e in entries:
                by_base[Path(e["path"]).name].append(e)
            file_maps[torrent_name] = by_base

        for r in rows:
            matches = file_maps.get(r["torrent"], {}).get(r["zip_basename"], [])
            if len(matches) == 1:
                m = matches[0]
                r["torrent_file_index"] = m["index"]
                r["torrent_internal_path"] = m["path"]
                r["zip_size_bytes"] = m["length"]
            else:
                r["torrent_file_index"] = ""
                r["torrent_internal_path"] = ""
                r["zip_size_bytes"] = ""

    manifest = outdir / "torrent_manifest.csv"
    fields = [
        "doi", "scimag_id",
        "torrent", "torrent_start", "torrent_end",
        "zip_basename", "zip_start", "zip_end",
        "pdf_path_in_zip",
    ]
    if torrent_dir:
        fields += ["torrent_file_index", "torrent_internal_path", "zip_size_bytes"]

    with manifest.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    grouped = defaultdict(dict)
    for r in rows:
        grouped[r["torrent"]][r["zip_basename"]] = r

    wanted_dir = outdir / "wanted_zips"
    wanted_dir.mkdir(exist_ok=True)

    for torrent_name, zips in sorted(grouped.items()):
        out = wanted_dir / f"{torrent_name}.wanted.txt"
        with out.open("w", encoding="utf-8") as f:
            for zip_name in sorted(zips):
                r = zips[zip_name]
                if r.get("torrent_file_index"):
                    f.write(
                        f"{r['torrent_file_index']}\t"
                        f"{r.get('torrent_internal_path') or zip_name}\n"
                    )
                else:
                    f.write(zip_name + "\n")

    if torrent_dir:
        aria = outdir / "aria2_selective_commands.sh"
        with aria.open("w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\nset -euo pipefail\n\n")
            f.write("# Generated selective SciMag ZIP downloads. Review before running.\n\n")
            for torrent_name, zips in sorted(grouped.items()):
                indexes = sorted({
                    int(r["torrent_file_index"])
                    for r in zips.values()
                    if r.get("torrent_file_index")
                })
                if len(indexes) != len(zips):
                    missing = len(zips) - len(indexes)
                    f.write(
                        f"# SKIPPED {torrent_name}: {missing} wanted ZIP(s) "
                        "could not be resolved to torrent file indexes.\n"
                    )
                    continue
                path = torrent_dir / torrent_name
                f.write(
                    "aria2c --select-file="
                    + shlex.quote(",".join(map(str, indexes)))
                    + " "
                    + shlex.quote(str(path))
                    + "\n"
                )
        aria.chmod(0o755)

    print(f"Papers mapped:    {len(rows):,}")
    print(f"Unique torrents:  {len(grouped):,}")
    print(f"Unique ZIPs:      {sum(len(v) for v in grouped.values()):,}")
    print(f"Manifest:         {manifest}")
    print(f"Wanted ZIP lists: {wanted_dir}")
    if torrent_dir:
        print(f"aria2 script:     {outdir / 'aria2_selective_commands.sh'}")


def cmd_inspect(args):
    entries = torrent_files(Path(args.torrent))
    print(f"Files: {len(entries):,}")
    print(f"Total bytes: {sum(e['length'] for e in entries):,}")
    print()
    limit = len(entries) if args.limit == 0 else args.limit
    for e in entries[:limit]:
        print(f"{e['index']:>5}\t{e['length']:>12}\t{e['path']}")


def make_parser():
    p = argparse.ArgumentParser(
        description="Generate selective-download manifests for SciMag torrents."
    )
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build")
    b.add_argument("--scimag-csv", required=True, help="CSV containing ID,DOI")
    b.add_argument("--output-dir", default="output/torrents")
    b.add_argument(
        "--torrent-dir",
        help="Optional directory containing local sm_*.torrent metadata files.",
    )
    b.set_defaults(func=cmd_build)

    i = sub.add_parser("inspect")
    i.add_argument("torrent")
    i.add_argument("--limit", type=int, default=20, help="0 = show all")
    i.set_defaults(func=cmd_inspect)
    return p


def main():
    args = make_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

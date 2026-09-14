#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 WANTED_FILE TORRENT_DIR [DOWNLOAD_DIR]" >&2
  exit 2
fi

wanted_file="$1"
torrent_dir="$2"
download_dir="${3:-$PWD}"

[[ -f "$wanted_file" ]] || {
  echo "ERROR: wanted file not found: $wanted_file" >&2
  exit 1
}

[[ -d "$torrent_dir" ]] || {
  echo "ERROR: torrent directory not found: $torrent_dir" >&2
  exit 1
}

mkdir -p "$download_dir"

base="$(basename "$wanted_file")"

if [[ "$base" != *.torrent.wanted.txt ]]; then
  echo "ERROR: expected wanted filename like:" >&2
  echo "  sm_53400000-53499999.torrent.wanted.txt" >&2
  echo "Got: $base" >&2
  exit 1
fi

torrent_name="${base%.wanted.txt}"
torrent_file="$torrent_dir/$torrent_name"

[[ -f "$torrent_file" ]] || {
  echo "ERROR: matching torrent file not found:" >&2
  echo "  $torrent_file" >&2
  exit 1
}

indexes_csv="$(
python3 - "$torrent_file" "$wanted_file" <<'PY'
import sys
from pathlib import Path

torrent_path = Path(sys.argv[1])
wanted_path = Path(sys.argv[2])


def bdecode(data: bytes):
    i = 0

    def parse():
        nonlocal i

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

        raise ValueError(f"Invalid bencode token at byte {i}")

    result = parse()

    if i != len(data):
        raise ValueError("Trailing bytes in torrent metadata")

    return result


meta = bdecode(torrent_path.read_bytes())
info = meta[b"info"]

files = info.get(b"files")
if files is None:
    raise SystemExit("ERROR: torrent is not a multi-file torrent")

# aria2 uses 1-based file indexes.
by_basename = {}

for index, entry in enumerate(files, start=1):
    parts = [
        p.decode("utf-8", errors="replace")
        for p in entry[b"path"]
    ]
    full_path = "/".join(parts)
    basename = Path(full_path).name

    if basename in by_basename:
        raise SystemExit(
            f"ERROR: duplicate basename in torrent: {basename}"
        )

    by_basename[basename] = (index, full_path)

wanted = []

for line in wanted_path.read_text(
    encoding="utf-8",
    errors="replace"
).splitlines():
    line = line.strip()

    if not line:
        continue

    # Supports either:
    #   libgen.scimag53401000-53401999.zip
    # or:
    #   12<TAB>libgen.scimag...
    if "\t" in line:
        possible_index, name = line.split("\t", 1)
        line = name.strip()

    wanted.append(Path(line).name)

if not wanted:
    raise SystemExit("ERROR: wanted file contains no ZIP names")

missing = []
indexes = []

for name in wanted:
    match = by_basename.get(name)

    if match is None:
        missing.append(name)
    else:
        indexes.append(match[0])

if missing:
    print(
        "ERROR: these wanted files were not found in the torrent:",
        file=sys.stderr,
    )
    for name in missing:
        print(f"  {name}", file=sys.stderr)
    raise SystemExit(1)

indexes = sorted(set(indexes))
print(",".join(str(i) for i in indexes))
PY
)"

[[ -n "$indexes_csv" ]] || {
  echo "ERROR: no torrent file indexes resolved" >&2
  exit 1
}

echo "Wanted file:   $wanted_file"
echo "Torrent:       $torrent_file"
echo "Download dir:  $download_dir"
echo "File indexes:  $indexes_csv"
echo

exec aria2c \
  --dir="$download_dir" \
  --select-file="$indexes_csv" \
  "$torrent_file"

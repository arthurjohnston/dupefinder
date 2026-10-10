#!/bin/bash
# Metadata for shards 4-15 into gi_meta.sqlite3: each shard's doc_info table (downloaded, loaded, deleted),
# then the 2022 update's rows for those shards (streamed, never stored). Then rebuild gi.sqlite3 documents
# with: ../.venv/bin/python3 gi_db.py import-documents  (after the shard downloads finish).
set -uo pipefail
cd "$(dirname "$0")/data"
P=../../.venv/bin/python3
for h in 4 5 6 7 8 9 a b c d e f; do
  if sqlite3 gi_meta.sqlite3 "select 1 from meta where substr(dkey,1,1)='$h' limit 1" | grep -q 1; then
    echo "$(date +%T) doc_info_$h already loaded"; continue
  fi
  for try in 1 2 3 4 5; do
    curl -sfL -o "doc_info_$h.sql.tar.gz" "https://archive.org/download/GeneralIndex/data/doc_metadata/doc_info_$h.sql.tar.gz" && break
    echo "$(date +%T) doc_info_$h download failed (try $try)"; sleep 60
  done
  $P ../gi_load_meta.py gi_meta.sqlite3 "doc_info_$h.sql.tar.gz" && rm -f "doc_info_$h.sql.tar.gz"
done
echo "$(date +%T) slice metadata loaded; streaming the 2022 update for shards 4-15"
$P ../gi_load_meta_alt.py gi_meta.sqlite3 --slices 456789abcdef
sqlite3 gi_meta.sqlite3 "select substr(dkey,1,1), count(*) from meta group by 1" | tr '\n' ' '; echo
sqlite3 gi_meta.sqlite3 "select substr(dkey,1,1), count(*), count(doi), count(title) from meta_alt group by 1" | tr '\n' ' '; echo
echo "$(date +%T) done"

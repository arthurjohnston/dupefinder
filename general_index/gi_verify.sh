#!/bin/bash
# Step 4: full-text check of a batch of leads, recorded in gi.sqlite3's fulltext table.
#   select (gi_verify.py) -> retrieve -> extract (the project's own pipeline, in an isolated workdir)
#   -> load-text -> measure (gi_verify.py, with years corrected from OpenAlex).
# Usage: gi_verify.sh RUN EMAIL [select options, e.g. --tiers 1 2 --limit 500]
# Everything runs on the internal disk in data/verify_<RUN>/ (PDFs in papers/), each PDF's path recorded in
# gi.sqlite3's local_pdfs, and the PDFs stay there (2026-10-09: the external drive drops off USB under load,
# so it isn't used for working copies). PDF_ROOT=DIR moves them to DIR/<RUN>/ at the end instead; the move
# can also be run later: gi_verify.py move --run RUN --state ... --library ... --dest DIR
# OPENALEX_API_KEY is read from ~/.config/dupefinder/openalex_api_key.
set -euo pipefail
run=$1; email=$2; shift 2
here=$(cd "$(dirname "$0")" && pwd)
D=$(cd "$here/.." && pwd)
P=$D/.venv/bin/python3
work="$here/data/verify_$run"
pdfs="${PDF_ROOT:-}/$run"
[ -r ~/.config/dupefinder/openalex_api_key ] && export OPENALEX_API_KEY="$(cat ~/.config/dupefinder/openalex_api_key)"
$P "$here/gi_verify.py" select --run "$run" "$@"
mkdir -p "$work"
cd "$work"
$P $D/retrieve_papers.py "$here/data/$run.starting.json" --email "$email" --outdir papers --db state.sqlite3 --log-file retrieve.log
$P "$here/gi_verify.py" paths --run "$run" --state "$work/state.sqlite3"
$P $D/extract_papers.py --state-db state.sqlite3 --library-db library.sqlite3 --paragraphs-file paragraphs.jsonl
# text only: the overlap check reads paragraph text, never the embeddings (gi_verify.py load_text)
$P "$here/gi_verify.py" load-text --run "$run" --library "$work/library.sqlite3" --paragraphs-file "$work/paragraphs.jsonl"
$P "$here/gi_verify.py" measure --run "$run" --library "$work/library.sqlite3" --fix-years
if [ -n "${PDF_ROOT:-}" ]; then
  $P "$here/gi_verify.py" move --run "$run" --state "$work/state.sqlite3" --library "$work/library.sqlite3" --dest "$pdfs"
fi

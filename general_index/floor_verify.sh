#!/bin/bash
# Floor experiment (FLOOR_EXPERIMENT.md), part 3: full text for a floor_select.py sample, entirely inside
# data/floor_experiment/ -- never gi.sqlite3. retrieve (Crossref + Unpaywall) -> Unpaywall-only rescue ->
# extract -> load-text (gi_verify.load_text) -> floor_measure.py. No OpenAlex: the key is unset for every step.
# Usage: floor_verify.sh RUN EMAIL
set -euo pipefail
run=$1; email=$2
here=$(cd "$(dirname "$0")" && pwd)
D=$(cd "$here/.." && pwd)
P="env -u OPENALEX_API_KEY nice -n 10 ionice -c3 $D/.venv/bin/python3"
work="$here/data/floor_experiment/verify_$run"
mkdir -p "$work"; cd "$work"
echo "$(date +%T) retrieve"
$P $D/retrieve_papers.py "$here/data/floor_experiment/$run.starting.json" --email "$email" --outdir papers \
  --db state.sqlite3 --log-file retrieve.log --max-workers 2
echo "$(date +%T) rescue";  (cd "$here" && $P floor_rescue.py "$run" --email "$email" --workers 2)
echo "$(date +%T) extract"; $P $D/extract_papers.py --state-db state.sqlite3 --library-db library.sqlite3 \
  --paragraphs-file paragraphs.jsonl --max-workers 4
echo "$(date +%T) load-text"
(cd "$here" && $P -c "import argparse, gi_verify as g; g.load_text(argparse.Namespace(run='$run', library='$work/library.sqlite3', paragraphs_file='$work/paragraphs.jsonl'))")
echo "$(date +%T) measure"; (cd "$here" && $P floor_measure.py --run "$run")
echo "$(date +%T) done"

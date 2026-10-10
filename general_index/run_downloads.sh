#!/bin/bash
# Stream General Index n-gram slices with gi_sample_5grams.py, N at a time, skipping any slice whose
# log already says "done:". Usage: run_downloads.sh "4 5 6 ... 15" [parallel=3] [sample=8]
# Approved by the user 2026-10-06: slices 4-15 at 1-in-8 sampling.
cd "$(dirname "$0")/data"
slices=${1:-"4 5 6 7 8 9 10 11 12 13 14 15"}; par=${2:-3}; sample=${3:-8}
one() {
  s=$1; hex=$(printf '%x' "$s")
  if grep -q '^done:' "slice$s.log" 2>/dev/null; then echo "slice $s already done"; return; fi
  rm -rf "slice$s"
  echo "$(date +%T) start slice $s"
  ../../.venv/bin/python3 ../gi_sample_5grams.py "https://archive.org/download/GeneralIndex.ngrams.$hex/doc_ngrams_$hex.sql.zip" \
    "slice$s" --sample "$2" > "slice$s.log" 2>&1
  echo "$(date +%T) slice $s exit $? : $(tail -1 slice$s.log)"
}
export -f one
printf '%s\n' $slices | xargs -P "$par" -I{} bash -c "one {} $sample"

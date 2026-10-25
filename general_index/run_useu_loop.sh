#!/bin/bash
# 2026-10-22: the user asked for batches to keep running for the next 48 hours, stopping once a round finds no
# pairs. After the first run (useu_s16, started by hand), runs useu2_s16, useu3_s16, ... : each one re-screens the
# US/European list (affiliation_hunt/useu_screen.py; the co-author cache keeps earlier calls, so only new pairs
# cost a call) for 3000 x k survivors, and run_useu_batch.sh then selects the 3,000 not yet in fulltext.
# Stops at the deadline, when the list runs out, or after a round with no real cross-author overlap (real
# overlap, no byline overlap: what gets a review page).
# Usage: run_useu_loop.sh HOURS [FIRST_K]   (log: data/useu_loop.log). Don't edit this while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3; D=$G/data/affiliation_hunt
deadline=$(( $(date +%s) + ${1:-48} * 3600 )); k=${2:-2}
cd $G
found() {   # real cross-author overlap pairs in run $1
  sqlite3 -readonly data/gi.sqlite3 "select count(*) from fulltext where run = '$1'
    and ft_verdict = 'real overlap' and byline = 'no byline overlap'"
}
# wait for a batch already running (the first one) to finish, then check it
while pgrep -f "run_useu_batch[.]sh" > /dev/null; do sleep 60; done
prev=useu$([ "$k" -gt 2 ] && echo $((k - 1)))_s16
n=$(found $prev); echo "$(date '+%F %T') $prev: $n pairs with real cross-author overlap"
[ "$n" -eq 0 ] && { echo "no pairs found, stopping"; exit 0; }
while [ "$(date +%s)" -lt "$deadline" ]; do
  # new leads scored since the list was built (gi_affiliations.py leads --sets rest4, finished): rebuild it
  if ! pgrep -f "gi_affiliations[.]py leads" > /dev/null && [ data/affiliations_rest4.log -nt $D/useu_ranked.pkl ]; then
    echo "$(date '+%F %T') rebuilding the US/European list with the newly scored leads"
    $P affiliation_hunt/useu_find.py $D 2>&1 | grep -v Warning | tail -1
  fi
  echo "$(date '+%F %T') == round $k: screen for $((3000 * k))"
  out=$($P affiliation_hunt/useu_screen.py $D $((3000 * k)) 2>&1 | tail -1); echo "$out"
  case "$out" in *"rate limited"*) echo "OpenAlex rate limit: waiting an hour"; sleep 3600; continue;; esac
  kept=$(echo "$out" | grep -oP 'kept \K[0-9]+' || echo 0)
  ./run_useu_batch.sh useu${k}_s16
  n=$(found useu${k}_s16); echo "$(date '+%F %T') useu${k}_s16: $n pairs with real cross-author overlap"
  [ "$n" -eq 0 ] && { echo "no pairs found, stopping"; break; }
  [ "$kept" -lt $((3000 * k)) ] && { echo "list exhausted after round $k"; break; }
  k=$((k + 1))
done
echo "$(date '+%F %T') loop done"

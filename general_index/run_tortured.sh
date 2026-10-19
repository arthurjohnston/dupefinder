#!/bin/bash
# 2026-10-17: the user asked how many of the index's 5-grams use a PPS tortured phrase, and whether undoing
# them finds new pairs. Streams every n-gram slice again (gi_tortured.py stream: nothing stored but the
# counts and the rewritten 5-grams' hashes), four at a time, then joins the rewrites against the 16 slice
# samples (gi_tortured.py match). Free (archive.org), ~5.5 h a slice at ~16 MB/s, CPU-bound. A slice that
# already has stats.json without "stopped_at_gb" is skipped, so a rerun resumes. Don't edit while it runs.
set -uo pipefail
G=/home/arthur/Sites/dupefinder/general_index; P=$G/../.venv/bin/python3
cd $G
one() {
  s=$1; d=data/tortured/slice$s
  if [ -f $d/stats.json ] && ! grep -q stopped_at_gb $d/stats.json; then echo "slice $s already done"; return; fi
  $P gi_tortured.py stream $s > data/tortured/stream_slice$s.log 2>&1 && echo "$(date +%T) slice $s: $(tail -1 data/tortured/stream_slice$s.log)"
}
export -f one; export P
echo "$(date +%T) == stream 16 slices, 4 at a time"
seq 0 15 | xargs -P 4 -I{} bash -c 'one {}'
echo "$(date +%T) == match"
$P gi_tortured.py match
echo "$(date +%T) all done"

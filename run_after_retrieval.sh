#!/usr/bin/env bash
# Waits for a set of background PIDs to all finish, then runs the rest of the
# pipeline (run_pipeline.py: extract_papers.py -> embed_paragraphs.py ->
# build_dupe_candidates.py -> classify_dupes.py -> write_dupe_reports.py).
#
# Deliberately waits for full completion of every watched PID rather than
# starting as soon as *one* finishes: extract_papers.py/embed_paragraphs.py
# running concurrently with retrieval is exactly the "heavy sustained
# multi-hour write load" pattern todo.md's "System / hardware reliability"
# section documents as having caused a real crash on this machine before.
# One clean handoff after everything being watched is fully done, not
# overlapping heavy stages at once.
#
# Usage: ./run_after_retrieval.sh [pid ...]
#   With PIDs given as arguments: waits on exactly those.
#   With none given: falls back to the original three-job default
#   (/tmp/theses_run.pid, /tmp/lowtier_run.pid, /tmp/broad_run.pid) for
#   backward-compat invocation the same way this script has always been run.
#   For a later round -- a new retrieval job started *after* the first
#   pipeline pass already kicked off -- pass that job's PID plus the still-
#   running run_pipeline.py's own PID, so this waits for both before running
#   run_pipeline.py again, rather than racing it.
set -uo pipefail
cd "$(dirname "$0")"

PIDS=()
if [ "$#" -gt 0 ]; then
    PIDS=("$@")
else
    for f in /tmp/theses_run.pid /tmp/lowtier_run.pid /tmp/broad_run.pid; do
        [ -f "$f" ] && PIDS+=("$(cat "$f")")
    done
fi

LOG=run_after_retrieval.log
echo "$(date -Iseconds) waiting for retrieval PIDs to finish: ${PIDS[*]}" >> "$LOG"

for pid in "${PIDS[@]}"; do
    while kill -0 "$pid" 2>/dev/null; do
        sleep 30
    done
    echo "$(date -Iseconds) pid $pid finished" >> "$LOG"
done

echo "$(date -Iseconds) all retrieval jobs finished -- starting run_pipeline.py" >> "$LOG"
python3 run_pipeline.py >> "$LOG" 2>&1
echo "$(date -Iseconds) run_pipeline.py exited with status $?" >> "$LOG"

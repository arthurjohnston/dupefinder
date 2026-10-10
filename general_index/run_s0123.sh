#!/bin/bash
# Superseded 2026-10-06 by run_gi.sh, which writes gi.sqlite3. The slice 0-3 run of 2026-10-06 is
# recorded in data/run_s0123.log; its outputs were migrated into gi.sqlite3 (gi_db.py migrate).
exec "$(dirname "$0")/run_gi.sh" slice0 slice1 slice2 slice3

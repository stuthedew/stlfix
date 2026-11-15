#!/usr/bin/env bash
# Run the benchmark: bench/run.sh {dev|test} OUT.jsonl METHOD [METHOD ...]
# Methods: stlfix (full pipeline), A, B (single strategies), C (fTetWild), meshlab, admesh
set -euo pipefail
split=$1; out=$2; shift 2
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
python "$here/make_jobs.py" "$split" "$@" > "$work/jobs.txt"
xargs -P "${JOBS:-4}" -L 1 sh -c 'timeout 300 python "'"$here"'/worker.py" $0 $1 $2 "'"$work"'/$0" 2>/dev/null || echo "{\"id\": $0, \"tool\": \"$1\", \"ok\": false, \"error\": \"timeout/crash\"}"' < "$work/jobs.txt" >> "$out"
python "$here/summarize.py" "$out"

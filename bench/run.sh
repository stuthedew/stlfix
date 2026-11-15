#!/usr/bin/env bash
# Run the benchmark: bench/run.sh {dev|test} OUT.jsonl METHOD [METHOD ...]   (resumes if OUT exists)
# Methods: stlfix (full pipeline), A, B (single strategies), C (fTetWild), meshlab, admesh
set -euo pipefail
split=$1; out=$2; shift 2
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
python "$here/make_jobs.py" "$split" "$@" > "$work/all_jobs.txt"
# resume: skip (id, method) pairs already in OUT
python - "$out" "$work/all_jobs.txt" > "$work/jobs.txt" <<'PY'
import json, os, sys
done = set()
if os.path.exists(sys.argv[1]):
    for l in open(sys.argv[1]):
        if l.startswith("{"):
            r = json.loads(l); done.add((str(r["id"]), r["tool"]))
for l in open(sys.argv[2]):
    i, m, _ = l.split(maxsplit=2)
    if (i, m) not in done:
        sys.stdout.write(l)
PY
xargs -P "${JOBS:-4}" -L 1 sh -c 'timeout 300 python "'"$here"'/worker.py" $0 $1 $2 "'"$work"'/$0" 2>/dev/null || echo "{\"id\": $0, \"tool\": \"$1\", \"ok\": false, \"error\": \"timeout/crash\"}"' < "$work/jobs.txt" >> "$out"
python "$here/summarize.py" "$out"

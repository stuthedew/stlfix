"""Summarize benchmark JSONL: python bench/summarize.py results.jsonl"""
import collections, json, sys
import numpy as np
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip().startswith("{")]
by = collections.defaultdict(dict)
for r in rows:
    by[r["id"]][r["tool"]] = r
tools = [t for t in ["stlfix", "A", "B", "C", "meshlab", "admesh"] if any(t in v for v in by.values())]
ids = [i for i in by if all(t in by[i] for t in tools)]
TOL = 0.01
def fixed(r): return bool(r.get("ok") and r.get("lost", 1) < TOL and r.get("added", 1) < TOL)
def damaged(r): return bool("lost" in r and max(r["lost"], r["added"]) >= TOL)
print(f"models: {len(ids)}  fixed = valid solid AND surface moved <1% of bbox diagonal (99th pct)")
for t in tools:
    R = [by[i][t] for i in ids]
    tm = [r["time"] for r in R if "time" in r]
    print(f"  {t:<8} fixed {sum(map(fixed, R)) / len(R):6.1%}   shape damaged {sum(map(damaged, R)) / len(R):6.1%}   "
          f"errors {sum('error' in r for r in R):3}   time median {np.median(tm):5.2f}s  p95 {np.percentile(tm, 95):5.1f}s")
if "stlfix" in tools:
    print("  stlfix picked:", dict(collections.Counter(by[i]["stlfix"].get("pick") for i in ids)))
    print(f"  any method fixed: {sum(any(fixed(by[i][t]) for t in tools) for i in ids) / len(ids):.1%}")

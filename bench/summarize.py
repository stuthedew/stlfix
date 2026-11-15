import json, sys, collections, numpy as np
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip().startswith('{')]
by=collections.defaultdict(dict)
for r in rows: by[r['id']][r['tool']]=r
tools=[t for t in ['A','B','C','AB','ABC','stlfix','meshlab','admesh'] if any(t in v for v in by.values())]
ids=[i for i in by if all(t in by[i] for t in tools)]
TOL=0.01
def good(r): return bool(r.get('ok') and r.get('lost',1)<TOL and r.get('added',1)<TOL)
print(f"models: {len(ids)}   'fixed' = watertight, no non-manifold edges, and <1% of bbox diagonal deviation (99th pct)")
for t in tools:
    R=[by[i][t] for i in ids]
    tm=[r['time'] for r in R if 'time' in r]
    print(f"  {t:<8} fixed {sum(map(good,R))/len(R):6.1%}  | watertight {sum(bool(r.get('ok')) for r in R)/len(R):6.1%}  "
          f"watertight-but-shape-changed {sum(1 for r in R if r.get('ok') and not good(r)):4}  errors/timeouts {sum('error' in r for r in R):3}  "
          f"time median {np.median(tm):.2f}s  max {max(tm):.0f}s")
if 'ABC' in tools:
    print('  ABC picked:', collections.Counter(by[i]['ABC'].get('pick') for i in ids))
    best_any = sum(any(good(by[i][t]) for t in tools) for i in ids); print(f'  any method fixed: {best_any/len(ids):.1%}')

"""Write a job list (id method npz_path) for the benchmark.

Usage: python bench/make_jobs.py {dev|test} METHOD [METHOD ...] > jobs.txt
Requires: pip install thingi10k ; dataset cache dir in $THINGI_DIR (default ./thingi_cache, ~10 GB).
"""
import json, os, sys
import thingi10k
split, methods = sys.argv[1], sys.argv[2:]
cache = os.environ.get("THINGI_DIR", "thingi_cache")
thingi10k.init(variant="npz", cache_dir=cache)
ids = set(json.load(open(os.path.join(os.path.dirname(__file__), f"{split}_ids.json"))))
for x in thingi10k.dataset(edge_manifold=False, num_facets=(None, 100000)):
    if int(x["file_id"]) in ids:
        npz = os.path.join(os.path.abspath(cache), "thingi10k_npz_extracted", "npz", f'{x["file_id"]}.npz')
        for m in methods:
            print(x["file_id"], m, npz)

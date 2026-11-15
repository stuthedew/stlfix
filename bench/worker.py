"""Benchmark one (model, method). Prints one JSON line. Usage: worker.py ID METHOD NPZ_PATH WORKDIR

Methods: stlfix (full pipeline), A, B (single strategies), C (fTetWild, see strategies.py),
         meshlab (pymeshlab filters), admesh (CLI on PATH).
Evaluation (same for all methods) after writing + re-reading the STL:
  ok    = stlfix.problems() is empty (watertight, no non-manifold/open edges, no overlapping parts)
  lost/added = stlfix.deviation(input, output) with a different random seed than the tool uses.
"""
import json, os, subprocess, sys, time, warnings
warnings.filterwarnings("ignore")
import numpy as np, trimesh
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stlfix

if __name__ == "__main__":
    mid, method, path, wdir = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    os.makedirs(wdir, exist_ok=True)
    d = np.load(path)
    inp = os.path.join(wdir, f"{mid}.stl"); out = os.path.join(wdir, f"{mid}_{method}.stl")
    trimesh.Trimesh(d["vertices"], d["facets"], process=False).export(inp)
    rec = dict(id=mid, tool=method)
    t = time.time()
    try:
        o = stlfix.load(inp)
        rec["in_problems"] = stlfix.problems(o)
        if method == "stlfix":
            name, r, _, _ = stlfix.repair(o); rec["pick"] = name; r.export(out)
        elif method in ("A", "B"):
            stlfix.STRATEGIES[method](o).export(out)
        elif method == "C":
            import strategies; strategies.strat_tetwild(o).export(out)
        elif method == "admesh":
            subprocess.run(["admesh", "--fill-holes", "--normal-directions", "--normal-values",
                            f"--write-binary-stl={out}", inp], capture_output=True, timeout=170)
        elif method == "meshlab":
            import pymeshlab
            ms = pymeshlab.MeshSet(); ms.load_new_mesh(inp)
            ms.meshing_remove_duplicate_vertices(); ms.meshing_remove_duplicate_faces(); ms.meshing_remove_null_faces()
            for _ in range(5):
                n = ms.current_mesh().face_number()
                ms.meshing_repair_non_manifold_edges(); ms.meshing_repair_non_manifold_vertices()
                if ms.current_mesh().face_number() == n: break
            ms.meshing_remove_unreferenced_vertices()
            try: ms.meshing_close_holes(maxholesize=1000)
            except Exception: pass
            ms.save_current_mesh(out)
        rec["time"] = round(time.time() - t, 2)
        r = stlfix.load(out)
        rec["problems"] = stlfix.problems(r); rec["ok"] = not rec["problems"]
        rec["lost"], rec["added"] = stlfix.deviation(o, r, seed=1)
    except Exception as e:
        rec.update(ok=False, error=f"{type(e).__name__}: {e}"[:200], time=round(time.time() - t, 2))
    print(json.dumps(rec))
    for f in (out, inp):
        try: os.remove(f)
        except OSError: pass

"""Benchmark one (model, method). Prints one JSON line. Usage: worker.py ID METHOD NPZ_PATH WORKDIR

Methods: stlfix (full pipeline), A, B (single strategies), C (fTetWild, see strategies.py),
         meshlab (pymeshlab filters), admesh (CLI on PATH).
Evaluation (same for all methods) after writing + re-reading the STL:
  ok    = stlfix.problems() is empty (watertight, no non-manifold/open edges, no overlapping parts)
  lost/added = stlfix.deviation(input, output) with a different random seed than the tool uses.
  in_bodies/out_bodies = face-connected component counts of input and output.
  bodies_lost = input bodies whose real boundary surface (material on one side, as in
                stlfix.classify_surface) is mostly (>50% of samples) farther than 1% of the bbox
                diagonal from the output. Unlike the 99th-percentile deviation, this catches small
                bodies that were dropped. Bodies merged into another solid are not counted.
"""
import json, os, subprocess, sys, time, warnings
warnings.filterwarnings("ignore")
import numpy as np, trimesh
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stlfix


def lost_bodies(o, r, per_body=200, cap=200_000, tol=0.01, seed=2):
    """Count input bodies whose boundary surface is missing from the output r."""
    import igl
    oo = stlfix.orient(o)
    bodies = [b for b in oo.split(only_watertight=False) if len(b.faces)]
    if not bodies or len(r.faces) == 0:
        return len(bodies)
    k = max(10, min(per_body, cap // len(bodies)))
    pts, nrm, lab = [], [], []
    for i, b in enumerate(bodies):
        p, fi = trimesh.sample.sample_surface(b, k, seed=seed)
        pts.append(p); nrm.append(b.face_normals[fi]); lab.append(np.full(len(p), i))
    pts, nrm, lab = np.vstack(pts), np.vstack(nrm), np.concatenate(lab)
    diag = float(np.linalg.norm(oo.extents)) or 1.0
    V, F = stlfix._vf(oo)
    eps = 1e-4 * diag
    w = np.abs(igl.fast_winding_number(V, F, np.vstack([pts + eps * nrm, pts - eps * nrm])))
    bnd = (w[:len(pts)] > 0.5) ^ (w[len(pts):] > 0.5)
    far = stlfix._dist(pts, r) / diag > tol
    n = 0
    for i in range(len(bodies)):
        m = (lab == i) & bnd
        if m.sum() >= 5 and far[m].mean() > 0.5:
            n += 1
    return n


if __name__ == "__main__":
    mid, method, path, wdir = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    os.makedirs(wdir, exist_ok=True)
    d = np.load(path)
    inp = os.path.join(wdir, f"{mid}_{method}_in.stl"); out = os.path.join(wdir, f"{mid}_{method}.stl")
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
        rec["in_bodies"], rec["out_bodies"] = int(o.body_count), int(r.body_count)
        rec["bodies_lost"] = lost_bodies(o, r)
    except Exception as e:
        rec.update(ok=False, error=f"{type(e).__name__}: {e}"[:200], time=round(time.time() - t, 2))
    print(json.dumps(rec))
    for f in (out, inp):
        try: os.remove(f)
        except OSError: pass

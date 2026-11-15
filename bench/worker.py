import sys, os, json, time, subprocess, warnings
warnings.filterwarnings("ignore")
import numpy as np, trimesh
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import strategies as S
mid, tool, path, wdir = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
os.makedirs(wdir, exist_ok=True)
d = np.load(path); inp = os.path.join(wdir, f"{mid}.stl"); out = os.path.join(wdir, f"{mid}_{tool}.stl")
trimesh.Trimesh(d["vertices"], d["facets"], process=False).export(inp)
rec = dict(id=mid, tool=tool); t = time.time()
try:
    o = S.clean(trimesh.load(inp, force="mesh"))
    if tool == "A":   S.strat_groups(o).export(out)
    elif tool == "B": S.strat_union(o).export(out)
    elif tool == "C": S.strat_tetwild(o).export(out)
    elif tool in ("AB", "ABC"):
        c = [("orig", o)] if S.is_ok(o) else []
        c += [("A", S.strat_groups(o)), ("B", S.strat_union(o))]
        if tool == "ABC" and not any(S.is_ok(m) and max(S.deviation(o, m)) < 0.01 for _, m in c): c.append(("C", S.strat_tetwild(o)))
        b = S.best(o, c); rec["pick"] = b[4]; b[5].export(out)
    elif tool == "admesh":
        subprocess.run(["admesh", "--fill-holes", "--normal-directions", "--normal-values", f"--write-binary-stl={out}", inp], capture_output=True, timeout=170)
    elif tool == "meshlab":
        import pymeshlab
        ms = pymeshlab.MeshSet(); ms.load_new_mesh(inp)
        ms.meshing_remove_duplicate_vertices(); ms.meshing_remove_duplicate_faces(); ms.meshing_remove_null_faces()
        for _ in range(5):
            n = ms.current_mesh().face_number(); ms.meshing_repair_non_manifold_edges(); ms.meshing_repair_non_manifold_vertices()
            if ms.current_mesh().face_number() == n: break
        ms.meshing_remove_unreferenced_vertices()
        try: ms.meshing_close_holes(maxholesize=1000)
        except Exception: pass
        ms.save_current_mesh(out)
    rec["time"] = round(time.time() - t, 2)
    r = S.clean(trimesh.load(out, force="mesh"))   # re-read from disk, as a slicer would
    nm, op = S.defects(r); rec.update(nonman=nm, open=op, ok=S.is_ok(r))
    rec["lost"], rec["added"] = S.deviation(o, r, seed=1)   # different seed from the tool's own self-check
except Exception as e:
    rec.update(ok=False, error=str(e)[:200], time=round(time.time() - t, 2))
print(json.dumps(rec))
try: os.remove(out)
except OSError: pass

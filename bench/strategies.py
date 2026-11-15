"""Candidate repair strategies + self-check. Imported by the benchmark and later folded into stlfix."""
import numpy as np, trimesh, warnings
from pymeshfix import MeshFix
import manifold3d as m3d
warnings.filterwarnings("ignore")

def clean(m):
    m = m.copy(); m.merge_vertices()
    m.update_faces(m.nondegenerate_faces()); m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices(); return m

def defects(m):
    if len(m.faces) == 0: return 10**9, 10**9
    _, c = np.unique(m.edges_sorted, axis=0, return_counts=True)
    return int((c > 2).sum()), int((c == 1).sum())

def is_ok(m):
    nm, op = defects(m)
    return bool(nm == 0 and op == 0 and m.is_watertight and m.volume > 0)

def meshfix(p, **kw):
    mf = MeshFix(np.asarray(p.vertices, float), np.asarray(p.faces, np.int32)); mf.repair(**kw)
    return trimesh.Trimesh(mf.points, mf.faces, process=True)

def bbox_vol(m): return float(np.prod(np.ptp(m.vertices, axis=0) + 1e-12))

# --- Strategy A: MeshFix per vertex-connected group (stlfix v1) ---
def strat_groups(mesh):
    labels = trimesh.graph.connected_component_labels(mesh.edges_unique, node_count=len(mesh.vertices))
    fg = labels[mesh.faces[:, 0]]; out = []
    for g in np.unique(fg):
        grp = mesh.submesh([np.where(fg == g)[0]], append=True)
        if len(grp.faces) < 4: continue
        f = meshfix(grp)
        if len(f.faces) and bbox_vol(f) >= 0.9 * bbox_vol(grp): out.append(f); continue
        for piece in grp.split(only_watertight=False):
            if len(piece.faces) >= 4:
                f = meshfix(piece); out.append(f if len(f.faces) else piece)
    return finish(out)

# --- Strategy B: close each body separately, then boolean-union them all ---
def to_manifold(t):
    M = m3d.Manifold(m3d.Mesh64(np.ascontiguousarray(t.vertices, dtype=np.float64),
                                np.ascontiguousarray(t.faces, dtype=np.uint64)))
    return M if M.status() == m3d.Error.NoError and not M.is_empty() else None

def strat_union(mesh):
    pieces = mesh.split(only_watertight=False)
    total = mesh.area or 1.0
    solids, leftovers = [], []
    for p in pieces:
        if p.area < 1e-6 * total or len(p.faces) < 4: continue          # dust
        q = p.copy()
        if not q.is_watertight or not q.is_winding_consistent:
            q = meshfix(p)
            if len(q.faces) == 0 or q.area < 0.5 * p.area: leftovers.append(p); continue
        if q.volume < 0: q.invert()
        M = to_manifold(q)
        if M is None:
            q = meshfix(q); M = to_manifold(q) if len(q.faces) else None
        (solids.append(M) if M is not None else leftovers.append(p))
    if not solids: return finish(leftovers)
    U = m3d.Manifold.batch_boolean(solids, m3d.OpType.Add) if len(solids) > 1 else solids[0]
    mm = U.to_mesh64()
    u = trimesh.Trimesh(np.asarray(mm.vert_properties)[:, :3], np.asarray(mm.tri_verts), process=True)
    return finish([u] + leftovers)

def finish(parts):
    parts = [p for p in parts if len(p.faces)]
    if not parts: return trimesh.Trimesh()
    r = trimesh.util.concatenate(parts); r.merge_vertices(); r.fix_normals(); return r

# --- self-check: how far does a result deviate from the input? ---
def deviation(orig, rep, n=4000, seed=0):
    """Fraction of bbox diagonal. lost: input surface missing from output (ignoring surfaces now
    strictly inside the solid, e.g. internal walls removed by a union). added: output surface not in input."""
    if len(rep.faces) == 0: return 1.0, 1.0
    diag = float(np.linalg.norm(orig.extents)) or 1.0
    po, _ = trimesh.sample.sample_surface(orig, n, seed=seed); pr, _ = trimesh.sample.sample_surface(rep, n, seed=seed)
    _, d_lost, _ = trimesh.proximity.closest_point(rep, po)
    if rep.is_watertight:
        inside = rep.contains(po) & (d_lost > 1e-4 * diag)
        d_lost = d_lost[~inside] if (~inside).any() else np.zeros(1)
    _, d_add, _ = trimesh.proximity.closest_point(orig, pr)
    return float(np.percentile(d_lost, 99) / diag), float(np.percentile(d_add, 99) / diag)

def best(orig, cands):
    """cands: list of (name, mesh). Prefer: valid solid > fewest defects; then least deviation."""
    scored = []
    for name, m in cands:
        lost, add = deviation(orig, m)
        nm, op = defects(m)
        scored.append(((0 if is_ok(m) else 1), (0 if max(lost, add) < 0.01 else 1), nm + op, max(lost, add), name, m, lost, add))
    scored.sort(key=lambda s: s[:4])
    return scored[0]

# --- Strategy C: fTetWild envelope remesh (robust fallback; re-triangulates within epsilon of input) ---
def strat_tetwild(mesh, epsilon=2e-4):
    import pytetwild
    v, t = pytetwild.tetrahedralize(np.asarray(mesh.vertices, np.float64), np.asarray(mesh.faces, np.int32),
                                    optimize=False, epsilon=epsilon, coarsen=True, quiet=True, num_threads=1)
    if len(t) == 0: return trimesh.Trimesh()
    f = np.vstack([t[:, [0, 1, 2]], t[:, [0, 1, 3]], t[:, [0, 2, 3]], t[:, [1, 2, 3]]])
    key = np.sort(f, axis=1); _, idx, cnt = np.unique(key, axis=0, return_index=True, return_counts=True)
    surf = trimesh.Trimesh(v, f[idx[cnt == 1]], process=True)
    return finish([surf])

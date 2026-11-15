#!/usr/bin/env python3
"""stlfix: one-command STL repair.

  stlfix model.stl            -> model_fixed.stl
  stlfix *.stl                -> batch
  stlfix model.stl -o out.stl
  stlfix model.stl --check    -> report only
  stlfix model.stl --inplace  -> overwrite original

Repairs non-manifold edges/vertices, holes, self-intersections, overlapping bodies, duplicate and
degenerate faces, and flipped normals.

How: two repair strategies run in parallel, each with a time limit:
  A  MeshFix (Attene 2010, via pymeshfix) on each group of touching pieces
  B  close each body with MeshFix, then merge all bodies with a boolean union (manifold3d)
Each result is checked against the input (watertight? how far did the surface move?) and the
best one is written. A result that changed the shape is never written as "fixed".
"""
import argparse
import multiprocessing as mp
import os
import sys
import warnings

import numpy as np
import trimesh

warnings.filterwarnings("ignore")

TIME_LIMIT = 90        # seconds per strategy
SHAPE_TOL = 0.01       # max allowed surface deviation, fraction of bounding-box diagonal (99th pct)


# ---------------------------------------------------------------- basics
def clean(m):
    m = m.copy()
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    return m


def load(path):
    return clean(trimesh.load(path, force="mesh", process=True))


def edge_counts(m):
    if len(m.faces) == 0:
        return 0, 0
    _, c = np.unique(m.edges_sorted, axis=0, return_counts=True)
    return int((c > 2).sum()), int((c == 1).sum())


def problems(m):
    if len(m.faces) == 0:
        return ["empty mesh"]
    nm, op = edge_counts(m)
    p = []
    if nm:
        p.append(f"{nm} non-manifold edges")
    if op:
        p.append(f"{op} open edges")
    if not p and not m.is_watertight:
        p.append("not watertight")
    if not p and m.volume <= 0:
        p.append("inside-out normals")
    if not p and m.body_count > 1:
        _, _, internal, _ = classify_surface(m, 2000)
        if internal.mean() > 0.005:
            p.append("overlapping parts")
    return p


def orient(m):
    """Make every closed shell face outward, except inside-out shells that sit inside another
    solid: those are cavities and are left as they are. (trimesh's fix_normals fills cavities.)"""
    import igl
    bodies = m.split(only_watertight=False)
    if len(bodies) <= 1:
        r = m.copy()
        trimesh.repair.fix_winding(r)
        if r.is_watertight and r.volume < 0:
            r.invert()
        return r
    pos, neg, other = [], [], []
    for b in bodies:
        trimesh.repair.fix_winding(b)
        if b.is_watertight and b.is_winding_consistent:
            (neg if b.volume < 0 else pos).append(b)
        else:
            other.append(b)
    if neg:
        if pos:
            V, F = _vf(trimesh.util.concatenate(pos))
            for b in neg:
                q = np.asarray(b.vertices[:: max(1, len(b.vertices) // 50)], np.float64)
                if np.median(np.abs(igl.fast_winding_number(V, F, q))) <= 0.5:
                    b.invert()          # not inside anything: just inside-out
        else:
            for b in neg:
                b.invert()
    r = trimesh.util.concatenate(pos + neg + other)
    r.merge_vertices()
    return r


def finish(parts):
    parts = [p for p in parts if len(p.faces)]
    if not parts:
        return trimesh.Trimesh()
    r = trimesh.util.concatenate(parts)
    r.merge_vertices()
    return orient(r)


def meshfix(p):
    from pymeshfix import MeshFix
    mf = MeshFix(np.asarray(p.vertices, float), np.asarray(p.faces, np.int32))
    mf.repair()
    return trimesh.Trimesh(mf.points, mf.faces, process=True)


def bbox_vol(m):
    return float(np.prod(np.ptp(m.vertices, axis=0) + 1e-12))


# ---------------------------------------------------------------- strategy A
def strat_groups(mesh):
    """MeshFix on each group of vertex-connected pieces."""
    labels = trimesh.graph.connected_component_labels(mesh.edges_unique, node_count=len(mesh.vertices))
    fg = labels[mesh.faces[:, 0]]
    out = []
    for g in np.unique(fg):
        grp = mesh.submesh([np.where(fg == g)[0]], append=True)
        if len(grp.faces) < 4:
            continue
        if grp.is_watertight and grp.is_winding_consistent and not edge_counts(grp)[0]:
            out.append(grp)                 # already a clean shell (may be a cavity): keep as is
            continue
        f = meshfix(grp)
        if len(f.faces) and bbox_vol(f) >= 0.9 * bbox_vol(grp):
            out.append(f)
            continue
        for piece in grp.split(only_watertight=False):   # MeshFix dropped part of the group
            if len(piece.faces) >= 4:
                f = meshfix(piece)
                out.append(f if len(f.faces) else piece)
    return finish(out)


# ---------------------------------------------------------------- strategy B
def to_manifold(t):
    import manifold3d as m3d
    M = m3d.Manifold(m3d.Mesh64(np.ascontiguousarray(t.vertices, dtype=np.float64),
                                np.ascontiguousarray(t.faces, dtype=np.uint64)))
    return M if M.status() == m3d.Error.NoError and not M.is_empty() else None


def _m2t(M):
    mm = M.to_mesh64()
    return trimesh.Trimesh(np.asarray(mm.vert_properties)[:, :3], np.asarray(mm.tri_verts), process=True)


def strat_union(mesh):
    """Close each face-connected body, then boolean-union all bodies.
    Inward-facing closed shells that sit inside another body are cavities and are subtracted."""
    import igl
    import manifold3d as m3d
    total = mesh.area or 1.0
    solids, inward, leftovers = [], [], []
    for p in mesh.split(only_watertight=False):
        if p.area < 1e-6 * total or len(p.faces) < 4:
            continue
        clean_shell = p.is_watertight and p.is_winding_consistent
        q = p
        if not clean_shell:
            q = meshfix(p)
            if len(q.faces) == 0 or q.area < 0.5 * p.area:
                leftovers.append(p)
                continue
        q = q.copy()
        was_inward = clean_shell and q.volume < 0
        if q.volume < 0:
            q.invert()
        M = to_manifold(q)
        if M is None:
            q = meshfix(q)
            M = to_manifold(q) if len(q.faces) else None
        if M is None:
            leftovers.append(p)
        else:
            (inward if was_inward else solids).append((M, q))
    if not solids and not inward:
        return finish(leftovers)
    if not solids:                       # everything was inside-out: just flip it
        solids, inward = inward, []
    U = m3d.Manifold.batch_boolean([s[0] for s in solids], m3d.OpType.Add) if len(solids) > 1 else solids[0][0]
    if inward:
        u = _m2t(U)
        V, F = _vf(u)
        cav, extra = [], []
        for M, q in inward:              # cavity if its points are inside the merged solid
            w = igl.fast_winding_number(V, F, np.asarray(q.vertices[:: max(1, len(q.vertices) // 50)], np.float64))
            (cav if np.median(np.abs(w)) > 0.5 else extra).append(M)
        if extra:
            U = m3d.Manifold.batch_boolean([U] + extra, m3d.OpType.Add)
        if cav:
            U = m3d.Manifold.batch_boolean([U] + cav, m3d.OpType.Subtract)
    return finish([_m2t(U)] + leftovers)


STRATEGIES = {"A": strat_groups, "B": strat_union}


# ---------------------------------------------------------------- run with time limits
def _child(name, v, f, q):
    try:
        r = STRATEGIES[name](trimesh.Trimesh(v, f, process=False))
        q.put((name, np.asarray(r.vertices), np.asarray(r.faces)))
    except Exception:
        q.put((name, None, None))


def run_strategies(mesh, time_limit=TIME_LIMIT):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = {n: ctx.Process(target=_child, args=(n, mesh.vertices, mesh.faces, q), daemon=True)
             for n in STRATEGIES}
    for p in procs.values():
        p.start()
    results = {}
    import queue as _q
    import time
    deadline = time.time() + time_limit
    while len(results) < len(procs) and time.time() < deadline:
        try:
            n, v, f = q.get(timeout=max(0.1, deadline - time.time()))
            results[n] = None if v is None else trimesh.Trimesh(v, f, process=True)
        except _q.Empty:
            break
    for p in procs.values():
        if p.is_alive():
            p.terminate()
        p.join(1)
    return {n: m for n, m in results.items() if m is not None and len(m.faces)}


# ---------------------------------------------------------------- self-check
def _vf(m):
    return np.asarray(m.vertices, np.float64), np.asarray(m.faces, np.int64)


def classify_surface(m, n=4000, seed=0):
    """Sample the surface and use the generalized winding number (Jacobson et al. 2013) to tell
    which side of each sample is solid material:
      boundary = material on exactly one side (real outer/cavity surface)
      internal = material on both sides (walls between overlapping bodies)
      stray    = material on neither side (loose fins, sheets, debris)
    Returns (points, boundary, internal, stray); boolean arrays. Works on broken meshes."""
    import igl
    o = orient(m)
    pts, fi = trimesh.sample.sample_surface(o, n, seed=seed)
    nrm = o.face_normals[fi]
    eps = 1e-4 * (float(np.linalg.norm(o.extents)) or 1.0)
    V, F = _vf(o)
    w = np.abs(igl.fast_winding_number(V, F, np.vstack([pts + eps * nrm, pts - eps * nrm])))
    a, b = w[:len(pts)] > 0.5, w[len(pts):] > 0.5
    return pts, a ^ b, a & b, ~a & ~b


def _dist(pts, m):
    import igl
    V, F = _vf(m)
    return np.sqrt(igl.point_mesh_squared_distance(np.asarray(pts, np.float64), V, F)[0])


def deviation(orig, rep, n=4000, seed=0):
    """How far the repair moved the solid's surface, as a fraction of the bbox diagonal (99th pct).
    lost  = input boundary surface (see classify_surface) that is not on the output.
            Internal walls and stray fins are excluded: removing them is the repair.
            A filled-in cavity or a deleted body counts as lost.
    added = output surface that is not near any input surface."""
    if len(rep.faces) == 0:
        return 1.0, 1.0
    diag = float(np.linalg.norm(orig.extents)) or 1.0
    pts, bnd, _, _ = classify_surface(orig, n, seed)
    if bnd.mean() < 0.3:        # winding numbers unreliable (e.g. mostly open sheets): compare everything
        bnd[:] = True
    pr, _ = trimesh.sample.sample_surface(rep, n, seed=seed)
    return (float(np.percentile(_dist(pts[bnd], rep), 99) / diag),
            float(np.percentile(_dist(pr, orig), 99) / diag))


def choose(orig, cands):
    """Return (name, mesh, fixed, shape_ok). Prefers fixed+shape-preserved, then shape-preserved with
    fewest defects. Shape-changing results are only used if nothing else exists (and reported)."""
    scored = []
    for name, m in cands.items():
        dev = max(deviation(orig, m))
        nm, op = edge_counts(m)
        fixed = not problems(m)
        shape_ok = dev < SHAPE_TOL
        scored.append((not (fixed and shape_ok), not shape_ok, nm + op, dev, name, m, fixed, shape_ok))
    scored.sort(key=lambda s: s[:4])
    s = scored[0]
    return s[4], s[5], s[6], s[7]


def repair(mesh, time_limit=TIME_LIMIT):
    cands = run_strategies(mesh, time_limit)
    cands["input"] = finish([mesh])      # untouched (normals fixed) as the safe fallback
    return choose(mesh, cands)


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(prog="stlfix", description="Repair STL files for 3D printing.")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--output", help="output path (one input only)")
    ap.add_argument("--inplace", action="store_true", help="overwrite the input file")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    ap.add_argument("--time-limit", type=float, default=TIME_LIMIT, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.output and len(a.inputs) > 1:
        sys.exit("-o works with one input only")

    for path in a.inputs:
        try:
            mesh = load(path)
        except Exception as e:
            print(f"{path}: could not read ({e})")
            continue
        before = problems(mesh)
        if a.check:
            print(f"{path}: " + (", ".join(before) if before else "OK"))
            continue
        if not before:
            print(f"{path}: no problems found, nothing written")
            continue
        name, fixed, _, shape_ok = repair(mesh, a.time_limit)
        out = path if a.inplace else (a.output or os.path.splitext(path)[0] + "_fixed.stl")
        fixed.export(out, file_type="stl")
        after = problems(load(out))   # re-read from disk, as a slicer would
        was = ", ".join(before)
        if not after and shape_ok:
            print(f"{path}: {was} -> fixed -> {out}")
        elif shape_ok:
            print(f"{path}: {was} -> partly fixed -> {out}  STILL HAS: {', '.join(after)}\n"
                  "  (often parts touching only at an edge/point: overlap or separate them in CAD;\n"
                  "   most slicers will still print it)")
        else:
            print(f"{path}: {was} -> {out}  WARNING: repair changed the shape; check it before printing")


if __name__ == "__main__":
    main()

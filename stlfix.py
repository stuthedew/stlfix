#!/usr/bin/env python3
"""stlfix: one-command STL repair.

  stlfix model.stl            -> model_fixed.stl
  stlfix *.stl                -> batch
  stlfix model.stl -o out.stl
  stlfix model.stl --check    -> report only
  stlfix model.stl --inplace  -> overwrite original (kept as model.orig.stl)

Repairs non-manifold edges/vertices, holes, self-intersections, overlapping bodies, duplicate and
degenerate faces, and flipped normals.

How: two repair strategies run in parallel, each with a time limit:
  A  MeshFix (Attene 2010, via pymeshfix) on each group of touching pieces
  B  close each body with MeshFix, then merge all bodies with a boolean union (manifold3d)
Each result is checked against the input (watertight? how far did the surface move?) and the
best one is written. A result that changed the shape is never written; if no repair is safe the
file is left alone and nothing is written.
"""
import argparse
import multiprocessing as mp
import os
import shutil
import sys
import warnings

import numpy as np
import trimesh

warnings.filterwarnings("ignore")

TIME_LIMIT = 90        # seconds per strategy
SHAPE_TOL = 0.005      # max surface deviation the tool accepts, fraction of bbox diagonal (99th pct);
                       # half the benchmark's 1% so random-sampling noise cannot push a result over


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


def bodies(m):
    """Face-connected pieces (a mesh with one piece is returned as is)."""
    return m.split(only_watertight=False) if m.body_count > 1 else [m]


def self_intersecting_faces(m):
    """Number of triangles that cut through another triangle of the same mesh (MeshFix's
    detector, Attene 2010). Touching at a shared vertex/edge position does not count."""
    from pymeshfix import PyTMesh
    if len(m.faces) < 4:
        return 0
    t = PyTMesh()
    t.set_quiet(True)
    t.load_array(np.asarray(m.vertices, np.float64), np.asarray(m.faces, np.int32))
    return len(t.select_intersecting_triangles(justproper=True))


def self_intersecting_bodies(m):
    """Bodies that intersect themselves. Checked per body: separate bodies that overlap each
    other are a different problem ("overlapping parts")."""
    return sum(1 for b in bodies(m) if self_intersecting_faces(b))


def _inside_any(b, V, F):
    """True if body b sits inside the solid (V, F), by the generalized winding number."""
    import igl
    q = np.asarray(b.vertices[:: max(1, len(b.vertices) // 50)], np.float64)
    return bool(np.median(np.abs(igl.fast_winding_number(V, F, q))) > 0.5)


def inside_out_bodies(m):
    """Closed bodies with inward normals that are not cavities inside another body."""
    bs = bodies(m)
    pos = [b for b in bs if b.volume > 0]
    neg = [b for b in bs if b.volume <= 0]
    if not neg:
        return 0
    if not pos:
        return len(neg)
    V, F = _vf(trimesh.util.concatenate(pos))
    return sum(1 for b in neg if not _inside_any(b, V, F))


def problems(m):
    if len(m.faces) == 0:
        return ["empty mesh"]
    nm, op = edge_counts(m)
    p = []
    if nm:
        p.append(f"{nm} non-manifold edges")
    if op:
        p.append(f"{op} open edges")
    if not p and not m.is_winding_consistent:
        p.append("inconsistent winding (some triangles face the wrong way)")
    if not p and not m.is_watertight:
        p.append("not watertight")
    if not p:
        n = inside_out_bodies(m)
        if n:
            p.append("inside-out normals" if m.body_count == 1 else f"{n} inside-out bodies")
    if not p:
        n = self_intersecting_bodies(m)
        if n:
            p.append("self-intersecting surface" + (f" ({n} bodies)" if n > 1 else ""))
    if not p and m.body_count > 1:
        _, _, internal, _ = classify_surface(m, 2000)
        if internal.mean() > 0.005:
            p.append("overlapping parts")
    return p


def orient(m):
    """Make every closed shell face outward, except inside-out shells that sit inside another
    solid: those are cavities and are left as they are. (trimesh's fix_normals fills cavities.)"""
    pieces = m.split(only_watertight=False)
    if len(pieces) <= 1:
        r = m.copy()
        trimesh.repair.fix_winding(r)
        if r.is_watertight and r.volume < 0:
            r.invert()
        return r
    pos, neg, other = [], [], []
    for b in pieces:
        trimesh.repair.fix_winding(b)
        if b.is_watertight and b.is_winding_consistent:
            (neg if b.volume < 0 else pos).append(b)
        else:
            other.append(b)
    if neg:
        if pos:
            V, F = _vf(trimesh.util.concatenate(pos))
            for b in neg:
                if not _inside_any(b, V, F):
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
        if (grp.is_watertight and grp.is_winding_consistent and not edge_counts(grp)[0]
                and not self_intersecting_bodies(grp)):
            out.append(grp)                 # already a clean shell (may be a cavity): keep as is
            continue
        f = meshfix(grp)
        # MeshFix keeps only the largest piece of a group: accept its result only if no body of
        # the group went missing (a bbox test alone misses small bodies next to a big one)
        if len(f.faces) and bbox_vol(f) >= 0.9 * bbox_vol(grp) and not lost_bodies(grp, f):
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
        if len(p.faces) < 4 or (p.area < 1e-6 * total and not p.is_watertight):
            continue                     # loose debris that encloses nothing; tiny closed bodies are kept
        clean_shell = p.is_watertight and p.is_winding_consistent and not self_intersecting_faces(p)
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
        q.put((name, np.asarray(r.vertices), np.asarray(r.faces), None))
    except Exception as e:
        q.put((name, None, None, f"{type(e).__name__}: {e}"))


def run_strategies(mesh, time_limit=TIME_LIMIT):
    """Run every strategy in its own process. Returns (results, errors): errors maps a strategy
    name to why it gave no result (exception text, "timed out ...", or "returned an empty mesh")."""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = {n: ctx.Process(target=_child, args=(n, mesh.vertices, mesh.faces, q), daemon=True)
             for n in STRATEGIES}
    for p in procs.values():
        p.start()
    results, errors = {}, {}
    import queue as _q
    import time
    deadline = time.time() + time_limit
    while len(results) + len(errors) < len(procs) and time.time() < deadline:
        try:
            n, v, f, err = q.get(timeout=max(0.1, deadline - time.time()))
        except _q.Empty:
            break
        if err:
            errors[n] = err
        elif v is None or len(f) == 0:
            errors[n] = "returned an empty mesh"
        else:
            results[n] = trimesh.Trimesh(v, f, process=True)
    for n, p in procs.items():
        if n not in results and n not in errors:
            errors[n] = (f"timed out after {time_limit:g} s" if p.is_alive()
                         else f"stopped without a result (exit code {p.exitcode})")
        if p.is_alive():
            p.terminate()
        p.join(1)
    return results, errors


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


def lost_bodies(orig, rep, n_per_body=200, seed=0):
    """Number of input bodies whose real surface is missing from rep.

    deviation() uses the 99th percentile over the whole model, so a deleted body with less than
    ~1% of the surface area slips through it. Here every body gets its own samples. A body counts
    as lost when more than half of its boundary samples (material on one side only, see
    classify_surface) are farther from rep than 5% of that body's own size. Stray fins and
    internal walls have no boundary samples, so removing them is not counted."""
    import igl
    pieces = [b for b in bodies(orig) if len(b.faces) >= 4 and b.area > 0]
    if not pieces:
        return 0
    if len(rep.faces) == 0:
        return len(pieces)
    o = orient(orig)
    V, F = _vf(o)
    eps = 1e-4 * (float(np.linalg.norm(o.extents)) or 1.0)
    pts, nrm, owner, size = [], [], [], []
    for i, b in enumerate(pieces):
        p, fi = trimesh.sample.sample_surface(b, n_per_body, seed=seed)
        pts.append(p)
        nrm.append(b.face_normals[fi])
        owner.append(np.full(len(p), i))
        size.append(float(np.linalg.norm(b.extents)))
    pts, nrm, owner = np.vstack(pts), np.vstack(nrm), np.concatenate(owner)
    w = np.abs(igl.fast_winding_number(V, F, np.vstack([pts + eps * nrm, pts - eps * nrm])))
    a, b = w[:len(pts)] > 0.5, w[len(pts):] > 0.5
    bnd = a ^ b
    if bnd.mean() < 0.3:        # winding numbers unreliable (e.g. open sheets): use every sample
        bnd[:] = True
    far = np.zeros(len(pts), bool)
    far[bnd] = _dist(pts[bnd], rep) > 0.05 * np.asarray(size)[owner[bnd]]
    lost = 0
    for i in range(len(pieces)):
        mine = bnd & (owner == i)
        if mine.sum() >= 10 and far[mine].mean() > 0.5:
            lost += 1
    return lost


def choose(orig, cands):
    """Return a list of (name, mesh, fixed, shape_ok, why) best first. Prefers fixed and
    shape-preserved, then shape-preserved with the fewest defects. "input" is the untouched input
    and is always shape-preserved; a shape-changing result is never preferred over it."""
    scored = []
    for name, m in cands.items():
        nm, op = edge_counts(m)
        if name == "input":
            scored.append((True, False, nm + op, 0.0, name, m, False, True, ""))
            continue
        dev = max(deviation(orig, m))
        lost = lost_bodies(orig, m)
        fixed = not problems(m)
        shape_ok = dev < SHAPE_TOL and not lost
        why = []
        if lost:
            why.append(f"lost {lost} of {len(bodies(orig))} parts")
        if dev >= SHAPE_TOL:
            why.append(f"surface moved {100 * dev:.1f}% of the model size")
        scored.append((not (fixed and shape_ok), not shape_ok, nm + op, dev, name, m, fixed, shape_ok,
                       "; ".join(why)))
    scored.sort(key=lambda s: s[:4])
    return [s[4:] for s in scored]


def repair_detailed(mesh, time_limit=TIME_LIMIT):
    """Returns dict(name, mesh, fixed, shape_ok, errors, rejected).
    name == "input" means no strategy improved the file without changing its shape; mesh is then
    the input object itself, unmodified, and nothing should be written."""
    cands, errors = run_strategies(mesh, time_limit)
    cands["input"] = mesh                # truly untouched fallback
    ranked = choose(mesh, cands)
    name, m, fixed, shape_ok, _ = ranked[0]
    rejected = {n: why for n, _, _, ok, why in ranked if not ok}
    return dict(name=name, mesh=m, fixed=fixed, shape_ok=shape_ok, errors=errors, rejected=rejected)


def repair(mesh, time_limit=TIME_LIMIT):
    """(name, mesh, fixed, shape_ok); see repair_detailed."""
    r = repair_detailed(mesh, time_limit)
    return r["name"], r["mesh"], r["fixed"], r["shape_ok"]


# ---------------------------------------------------------------- CLI
def backup_path(path):
    """model.stl -> model.orig.stl (model.orig-2.stl, ... if that already exists)."""
    root, ext = os.path.splitext(path)
    cand, i = f"{root}.orig{ext}", 2
    while os.path.exists(cand):
        cand, i = f"{root}.orig-{i}{ext}", i + 1
    return cand


def fix_file(path, a):
    """Repair one file. Returns True if the file is OK or was fully fixed."""
    try:
        mesh = load(path)
    except Exception as e:
        print(f"{path}: could not read ({type(e).__name__}: {e})")
        return False
    before = problems(mesh)
    if a.check:
        print(f"{path}: " + (", ".join(before) if before else "OK"))
        return not before
    if not before:
        print(f"{path}: no problems found, nothing written")
        return True
    was = ", ".join(before)
    r = repair_detailed(mesh, a.time_limit)
    notes = [f"  note: repair method {n} {e}" if e.startswith(("timed", "stopped", "returned"))
             else f"  note: repair method {n} failed ({e})" for n, e in sorted(r["errors"].items())]
    notes += [f"  note: repair method {n} rejected: {why}" for n, why in sorted(r["rejected"].items()) if why]
    if r["name"] == "input":
        print(f"{path}: {was} -> NOT repaired, nothing written (every repair would have changed the "
              "shape or did not help)")
        if notes:
            print("\n".join(notes))
        return False
    out = a.output or os.path.splitext(path)[0] + "_fixed.stl"
    backup = None
    if a.inplace or os.path.abspath(out) == os.path.abspath(path):
        out = path
        backup = backup_path(path)
        shutil.copy2(path, backup)       # original bytes kept before anything is overwritten
    r["mesh"].export(out, file_type="stl")
    after = problems(load(out))          # re-read from disk, as a slicer would
    kept = f"  (original saved as {backup})" if backup else ""
    if not after:
        print(f"{path}: {was} -> fixed -> {out}{kept}")
    else:
        print(f"{path}: {was} -> partly fixed -> {out}{kept}  STILL HAS: {', '.join(after)}\n"
              "  (often parts touching only at an edge/point: overlap or separate them in CAD;\n"
              "   most slicers will still print it)")
    if notes:
        print("\n".join(notes))
    return not after


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stlfix", description="Repair STL files for 3D printing.")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--output", help="output path (one input only)")
    ap.add_argument("--inplace", action="store_true",
                    help="overwrite the input file (the original is kept as NAME.orig.stl)")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    ap.add_argument("--time-limit", type=float, default=TIME_LIMIT, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.output and a.inplace:
        ap.error("use either -o or --inplace, not both")
    if a.output and len(a.inputs) > 1:
        ap.error("-o works with one input only")

    ok = True
    for path in a.inputs:
        try:
            ok &= fix_file(path, a)
        except Exception as e:          # one bad file must not stop a batch, and must not be silent
            print(f"{path}: ERROR, nothing written ({type(e).__name__}: {e})")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

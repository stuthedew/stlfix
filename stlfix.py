#!/usr/bin/env python3
"""stlfix: one-command STL repair.

  stlfix model.stl            -> model_fixed.stl
  stlfix *.stl                -> batch
  stlfix model.stl -o out.stl
  stlfix model.stl --check    -> report only
  stlfix model.stl --inplace  -> overwrite original

Repairs non-manifold edges/vertices, holes, self-intersections, duplicate and
degenerate faces, and flipped normals. Engine: MeshFix (Attene 2010) via pymeshfix.
"""
import argparse
import os
import sys
import warnings

import numpy as np
import trimesh
from pymeshfix import MeshFix

warnings.filterwarnings("ignore", category=RuntimeWarning)


def load(path):
    m = trimesh.load(path, force="mesh", process=True)  # merges duplicate vertices
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    return m


def problems(m):
    _, c = np.unique(m.edges_sorted, axis=0, return_counts=True)
    p = []
    if (c > 2).sum():
        p.append(f"{int((c > 2).sum())} non-manifold edges")
    if (c == 1).sum():
        p.append(f"{int((c == 1).sum())} open edges")
    if not p and m.volume <= 0:
        p.append("inside-out normals")
    return p


def meshfix(p):
    mf = MeshFix(np.asarray(p.vertices, float), np.asarray(p.faces, np.int32))
    mf.repair()
    return trimesh.Trimesh(mf.points, mf.faces, process=True)


def bbox_vol(m):
    return float(np.prod(np.ptp(m.vertices, axis=0) + 1e-12))


def repair(mesh):
    """Repair each group of touching pieces as one solid; keep separate objects separate."""
    labels = trimesh.graph.connected_component_labels(mesh.edges_unique, node_count=len(mesh.vertices))
    face_group = labels[mesh.faces[:, 0]]
    out, warned = [], False
    for g in np.unique(face_group):
        grp = mesh.submesh([np.where(face_group == g)[0]], append=True)
        if len(grp.faces) < 4:
            continue  # stray sliver
        fixed = meshfix(grp)
        if len(fixed.faces) and bbox_vol(fixed) >= 0.9 * bbox_vol(grp):
            out.append(fixed)
            continue
        # MeshFix threw away part of the group (pieces touching only at an edge/point):
        # repair each face-connected piece separately so nothing is lost.
        warned = True
        for piece in grp.split(only_watertight=False):
            if len(piece.faces) >= 4:
                f = meshfix(piece)
                out.append(f if len(f.faces) else piece)
    r = trimesh.util.concatenate(out)
    r.merge_vertices()
    r.fix_normals()
    return r, warned


def main():
    ap = argparse.ArgumentParser(prog="stlfix", description="Repair STL files.")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--output", help="output path (one input only)")
    ap.add_argument("--inplace", action="store_true", help="overwrite the input file")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
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
        fixed, _ = repair(mesh)
        out = path if a.inplace else (a.output or os.path.splitext(path)[0] + "_fixed.stl")
        fixed.export(out, file_type="stl")
        after = problems(load(out))  # re-read from disk, as a slicer would
        was = ", ".join(before) if before else "no problems found"
        if after:
            print(f"{path}: {was} -> {out}  STILL HAS: {', '.join(after)}\n"
                  "  (pieces touching only along an edge/point; overlap or separate them in CAD)")
        else:
            print(f"{path}: {was} -> fixed -> {out}")

if __name__ == "__main__":
    main()

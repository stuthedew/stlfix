"""Synthetic broken meshes with known correct outcomes. Run: pytest -q"""
import os, subprocess, sys
import numpy as np
import pytest
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import stlfix  # noqa: E402

box = trimesh.creation.box


def case_fin():  # cube + stray triangle on one edge (3 faces per edge)
    a = box(); v = np.vstack([a.vertices, [[0, 0, 2]]]); e = a.edges_unique[0]
    return trimesh.Trimesh(v, np.vstack([a.faces, [[e[0], e[1], len(v) - 1]]]), process=False)

def case_hole():  # sphere with faces deleted
    s = trimesh.creation.icosphere(3); s.update_faces(np.arange(len(s.faces)) > 10); return s

def case_inside_out():  # two separate cubes, one with flipped normals
    a = box(); c = box(); c.apply_translation([3, 0, 0]); c.invert(); return trimesh.util.concatenate([a, c])

def case_internal_wall():  # box with a dangling internal wall
    b = box(extents=[2, 1, 1]).subdivide().subdivide()
    w = trimesh.Trimesh([[0, -.5, -.5], [0, .5, -.5], [0, .5, .5], [0, -.5, .5]], [[0, 1, 2], [0, 2, 3]])
    return trimesh.util.concatenate([b, w])

def case_stacked():  # two boxes sharing a full face
    a = box(); c = box(); c.apply_translation([0, 0, 1]); return trimesh.util.concatenate([a, c])

def case_edge_touch():  # two cubes touching along one edge: non-manifold by geometry, not fixable
    a = box(); c = box(); c.apply_translation([1, 1, 0]); return trimesh.util.concatenate([a, c])


FIXABLE = {  # name: (builder, expected volume)
    "fin": (case_fin, 1.0),
    "hole": (case_hole, None),
    "inside_out": (case_inside_out, 2.0),
    "internal_wall": (case_internal_wall, 2.0),
    "stacked": (case_stacked, 2.0),
}


def run(tmp_path, mesh):
    src = tmp_path / "in.stl"; out = tmp_path / "in_fixed.stl"
    mesh.export(src)
    subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "stlfix.py"), str(src)], check=True, capture_output=True)
    return stlfix.load(str(out))


@pytest.mark.parametrize("name", sorted(FIXABLE))
def test_fixable(tmp_path, name):
    build, vol = FIXABLE[name]
    m = run(tmp_path, build())
    assert stlfix.problems(m) == [], stlfix.problems(m)
    assert m.is_watertight
    if vol is not None:
        assert m.volume == pytest.approx(vol, rel=1e-3)


def test_edge_touch_keeps_both_parts(tmp_path):
    m = run(tmp_path, case_edge_touch())
    assert m.area == pytest.approx(12.0, rel=1e-3)  # nothing deleted; still flagged as non-manifold
    assert stlfix.problems(m)


def test_clean_file_unchanged(tmp_path):
    m = run(tmp_path, box())
    assert stlfix.problems(m) == [] and m.volume == pytest.approx(1.0)

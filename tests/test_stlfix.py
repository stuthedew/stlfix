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


def test_clean_file_not_rewritten(tmp_path):
    src = tmp_path / "in.stl"; box().export(src)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "stlfix.py"), str(src)], capture_output=True, text=True)
    assert "no problems found" in r.stdout and not (tmp_path / "in_fixed.stl").exists()


def test_overlapping_bodies_merged(tmp_path):  # two overlapping cubes -> one solid, union volume
    a = box(); c = box(); c.apply_translation([0.5, 0, 0])
    m = run(tmp_path, trimesh.util.concatenate([a, c]))
    assert stlfix.problems(m) == [] and m.volume == pytest.approx(1.5, rel=1e-3)


def test_hollow_part_keeps_cavity(tmp_path):  # cube with an internal cavity + a stray fin to force a repair
    outer = box(extents=[2, 2, 2]); inner = box(extents=[1, 1, 1]); inner.invert()
    hollow = trimesh.util.concatenate([outer, inner])
    v = np.vstack([hollow.vertices, [[0, 0, 3]]]); e = outer.edges_unique[0]
    broken = trimesh.Trimesh(v, np.vstack([hollow.faces, [[e[0], e[1], len(v) - 1]]]), process=False)
    m = run(tmp_path, broken)
    assert stlfix.problems(m) == []
    assert m.volume == pytest.approx(7.0, rel=1e-3)   # 8 - 1: cavity not filled

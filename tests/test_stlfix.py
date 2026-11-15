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


SCRIPT = os.path.join(os.path.dirname(HERE), "stlfix.py")


def cli(*args):
    return subprocess.run([sys.executable, SCRIPT, *map(str, args)], capture_output=True, text=True)


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


def test_edge_touch_not_rewritten(tmp_path):
    # no repair can fix this without changing the shape: the input must be left alone, not
    # re-exported with "fixed" normals, and the run must say so and exit non-zero
    src = tmp_path / "in.stl"; case_edge_touch().export(src); before = src.read_bytes()
    r = cli(src)
    assert r.returncode == 1 and "NOT repaired, nothing written" in r.stdout
    assert not (tmp_path / "in_fixed.stl").exists() and src.read_bytes() == before


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


# ---------------------------------------------------------------- "never change good meshes"
def write(tmp_path, mesh, name="in.stl"):
    p = tmp_path / name; mesh.export(p); return p


def test_inplace_keeps_backup(tmp_path):  # finding 1
    src = write(tmp_path, case_fin()); original = src.read_bytes()
    r = cli(src, "--inplace")
    assert r.returncode == 0, r.stdout + r.stderr
    backup = tmp_path / "in.orig.stl"
    assert backup.read_bytes() == original and "original saved as" in r.stdout
    assert stlfix.problems(stlfix.load(str(src))) == []
    # a second run must not overwrite the first backup
    src.write_bytes(original)
    cli(src, "--inplace")
    assert backup.read_bytes() == original and (tmp_path / "in.orig-2.stl").read_bytes() == original


def test_output_onto_input_keeps_backup(tmp_path):  # finding 1: -o pointing at the input
    src = write(tmp_path, case_fin()); original = src.read_bytes()
    assert cli(src, "-o", src).returncode == 0
    assert (tmp_path / "in.orig.stl").read_bytes() == original


def test_small_lost_body_is_caught():  # finding 2
    big = box(extents=[10, 10, 10]); small = box(extents=[0.5, 0.5, 0.5]); small.apply_translation([8, 0, 0])
    orig = trimesh.util.concatenate([big, small])        # small body = 0.25% of the area
    dropped = big.copy()
    # the whole-model deviation check alone does not notice the missing body ...
    assert max(stlfix.deviation(orig, dropped)) < stlfix.SHAPE_TOL
    # ... the per-body check does, and choose() then rejects the candidate
    assert stlfix.lost_bodies(orig, dropped) == 1
    assert stlfix.lost_bodies(orig, orig) == 0
    ranked = stlfix.choose(orig, {"input": orig, "X": dropped})
    assert ranked[0][0] == "input"
    assert [r for r in ranked if r[0] == "X"][0][3] is False


def test_removing_fin_is_not_a_lost_body():  # the per-body check must not block normal repairs
    assert stlfix.lost_bodies(stlfix.clean(case_fin()), box()) == 0


def test_flipped_face_detected():  # finding 3: watertight, positive volume, but bad winding
    c = box(); f = c.faces.copy(); f[0] = f[0][::-1]
    m = trimesh.Trimesh(c.vertices, f, process=False)
    assert m.is_watertight and m.volume > 0
    assert any("winding" in p for p in stlfix.problems(m))


def test_self_intersection_detected():  # finding 3: one closed body that cuts through itself
    b = box(extents=[2, 2, 2]).subdivide(); v = b.vertices.copy()
    v[np.where(np.all(np.isclose(v, [0, 0, 1]), axis=1))[0][0]] = [0, 0, -1.5]  # top centre poked through the bottom
    m = trimesh.Trimesh(v, b.faces, process=False)
    assert m.is_watertight and m.is_winding_consistent and m.volume > 0
    assert any("self-intersecting" in p for p in stlfix.problems(m))


def test_inside_out_body_next_to_good_body_detected():  # finding 3: per body, not whole-file volume
    a = box(extents=[3, 3, 3]); c = box(); c.apply_translation([5, 0, 0]); c.invert()
    m = trimesh.util.concatenate([a, c])
    assert m.volume > 0
    assert any("inside-out" in p for p in stlfix.problems(m))


@pytest.mark.parametrize("name", ["cube", "two_cubes", "hollow", "sphere"])
def test_good_meshes_pass_untouched(tmp_path, name):  # no false positives from the new checks
    outer = box(extents=[2, 2, 2]); inner = box(); inner.invert(); far = box(); far.apply_translation([3, 0, 0])
    mesh = {"cube": box(), "two_cubes": trimesh.util.concatenate([box(), far]),
            "hollow": trimesh.util.concatenate([outer, inner]),
            "sphere": trimesh.creation.icosphere(4)}[name]
    assert stlfix.problems(stlfix.clean(mesh)) == []
    src = write(tmp_path, mesh); before = src.read_bytes()
    r = cli(src)
    assert r.returncode == 0 and "no problems found" in r.stdout
    assert src.read_bytes() == before and not (tmp_path / "in_fixed.stl").exists()


def test_fallback_is_the_untouched_input():  # finding 4
    m = stlfix.clean(case_edge_touch())
    v, f = m.vertices.copy(), m.faces.copy()
    r = stlfix.repair_detailed(m, time_limit=0.01)       # strategies cannot finish: only the fallback is left
    assert r["name"] == "input" and r["mesh"] is m
    assert np.array_equal(m.vertices, v) and np.array_equal(m.faces, f)


def test_timeout_is_reported(tmp_path):  # findings 5 and 6
    src = write(tmp_path, case_fin())
    r = cli(src, "--time-limit", "0.01")
    assert r.returncode == 1
    assert "NOT repaired, nothing written" in r.stdout and "timed out" in r.stdout
    assert not (tmp_path / "in_fixed.stl").exists()


def test_strategy_exception_is_reported():  # finding 6
    import queue
    q = queue.Queue()
    stlfix._child("no-such-strategy", box().vertices, box().faces, q)
    name, v, f, err = q.get_nowait()
    assert v is None and err.startswith("KeyError")


def test_bad_file_does_not_stop_batch(tmp_path):  # finding 6
    good = write(tmp_path, case_fin(), "good.stl"); bad = tmp_path / "bad.stl"; bad.write_text("not an stl")
    r = cli(bad, good)
    assert r.returncode == 1 and "bad.stl" in r.stdout and (tmp_path / "good_fixed.stl").exists()


def test_output_and_inplace_rejected(tmp_path):  # finding 7
    src = write(tmp_path, case_fin())
    r = cli(src, "--inplace", "-o", tmp_path / "x.stl")
    assert r.returncode == 2 and "not both" in r.stderr
    assert not (tmp_path / "x.stl").exists() and not (tmp_path / "in.orig.stl").exists()

# stlfix

One command to repair STL files for 3D printing: non-manifold edges and vertices, holes, self-intersections, duplicate and degenerate faces, inside-out normals. Runs locally; nothing is uploaded.

```
$ stlfix collet-screw.stl
collet-screw.stl: 2 non-manifold edges, 1 open edges -> fixed -> collet-screw_fixed.stl
```

## Install (macOS)

```bash
brew install uv
uv tool install git+ssh://git@github.com/stuthedew/stlfix
uv tool update-shell   # then open a new terminal
```

(Use `git+https://github.com/stuthedew/stlfix` instead if you authenticate to GitHub over HTTPS, e.g. via `gh auth login`.)

Update later with `uv tool upgrade stlfix`.

## Use

```bash
stlfix model.stl            # -> model_fixed.stl
stlfix *.stl                # batch
stlfix model.stl -o out.stl
stlfix model.stl --inplace  # overwrite original
stlfix model.stl --check    # report only
```

The output is re-read from disk after writing (as a slicer would read it) and the result line reports anything still wrong.

## What it can't fix

Parts that touch only along an edge or at a single point are non-manifold by geometry. stlfix keeps them and flags them; fix in CAD by overlapping (~0.01 mm) or separating them.

## How it works

MeshFix ([pymeshfix](https://pypi.org/project/pymeshfix); Attene, *The Visual Computer* 2010, doi:10.1007/s00371-010-0416-3) does the repair; [trimesh](https://trimesh.org) handles I/O and checks. stlfix adds: pre-cleaning, per-part repair so multi-part models keep all parts (MeshFix alone keeps only the largest), a guard against MeshFix deleting geometry, normal fixing, and post-write verification.

## Development

```bash
pip install -e . pytest && pytest -q        # synthetic broken-mesh tests
pip install -r bench/requirements.txt       # benchmark deps (~10 GB dataset download)
bench/run.sh dev results.jsonl A B AB meshlab admesh
```

See `CLAUDE.md` for project status, benchmark method and results.

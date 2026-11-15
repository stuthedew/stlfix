# stlfix

One command to repair STL files for 3D printing: non-manifold edges and vertices, holes, self-intersections, duplicate and degenerate faces, inside-out normals. Runs locally; nothing is uploaded.

```
$ stlfix collet-screw.stl
collet-screw.stl: 2 non-manifold edges, 1 open edges -> fixed -> collet-screw_fixed.stl
```

## Install (macOS)

```bash
brew install uv
uv tool install git+https://github.com/stuthedew/stlfix
uv tool update-shell   # then open a new terminal
```

Update later with `uv tool upgrade stlfix`.

## Use

```bash
stlfix model.stl            # -> model_fixed.stl
stlfix *.stl                # batch
stlfix model.stl -o out.stl
stlfix model.stl --inplace  # overwrite original; the original is kept as model.orig.stl
stlfix model.stl --check    # report only
```

The output is re-read from disk after writing (as a slicer would read it) and the result line reports anything still wrong.

Good files are never changed. A file with no problems is reported as OK and nothing is written. If every repair would change the shape, stlfix writes nothing and says why (`NOT repaired, nothing written`). If a repair method fails or hits its time limit, a `note:` line says so. The exit status is 0 only when every file is OK or fully fixed, so batch scripts can tell.

What counts as a problem: non-manifold or open edges, triangles facing the wrong way, inside-out bodies (cavities inside another body are fine), a body that cuts through itself, and separate bodies that overlap.

## What it can't fix

Parts that touch only along an edge or at a single point are non-manifold by geometry. stlfix keeps them and flags them; fix in CAD by overlapping (~0.01 mm) or separating them.

## How it works

Two repair strategies run in parallel with a time limit, and the best verified result is written:

- **A**: MeshFix ([pymeshfix](https://pypi.org/project/pymeshfix); Attene, *The Visual Computer* 2010, doi:10.1007/s00371-010-0416-3) on each group of touching pieces. Already-clean pieces are left untouched.
- **B**: close each body with MeshFix, then merge overlapping bodies with a boolean union ([manifold3d](https://github.com/elalish/manifold)). Internal cavities are kept.

Each result is checked against the input: it must be a closed solid, the surface must not move by more than 0.5% of the model's size, and every part of the input must still be there (checked part by part, so a deleted small part is caught even when it is under 1% of the surface). Which side of each input surface is solid is decided with the generalized winding number (Jacobson et al., SIGGRAPH 2013, via [libigl](https://libigl.github.io)), so removing internal walls and stray fins counts as repair, while filling a cavity or deleting a part counts as damage. A result that changes the shape is never reported as fixed. The output is re-read from disk before the result line is printed.

## License

Not chosen yet. Note that the repair engine, pymeshfix, is AGPL-3.0 (PyPI classifier), which constrains how stlfix can be licensed if it is redistributed with it.

## Development

```bash
pip install -e . pytest && pytest -q        # synthetic broken-mesh tests
pip install -r bench/requirements.txt       # benchmark deps (~10 GB dataset download)
bench/run.sh dev results.jsonl A B AB meshlab admesh
```

See `CLAUDE.md` for project status, benchmark method and results.

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
stlfix model.stl --inplace  # overwrite original
stlfix model.stl --check    # report only
```

The output is re-read from disk after writing (as a slicer would read it) and the result line reports anything still wrong.

## What it can't fix

Parts that touch only along an edge or at a single point are non-manifold by geometry. stlfix keeps them and flags them; fix in CAD by overlapping (~0.01 mm) or separating them.

## How it works

Two repair strategies run in parallel with a time limit, and the best verified result is written:

- **A**: MeshFix ([pymeshfix](https://pypi.org/project/pymeshfix); Attene, *The Visual Computer* 2010, doi:10.1007/s00371-010-0416-3) on each group of touching pieces. Already-clean pieces are left untouched.
- **B**: close each body with MeshFix, then merge overlapping bodies with a boolean union ([manifold3d](https://github.com/elalish/manifold)). Internal cavities are kept.

Each result is checked against the input: it must be a closed solid, and the surface must not move by more than 1% of the model's size. Which side of each input surface is solid is decided with the generalized winding number (Jacobson et al., SIGGRAPH 2013, via [libigl](https://libigl.github.io)), so removing internal walls and stray fins counts as repair, while filling a cavity or deleting a part counts as damage. A result that changes the shape is never reported as fixed. The output is re-read from disk before the result line is printed.

## License

Not chosen yet. Note that the repair engine, pymeshfix, is AGPL-3.0 (PyPI classifier), which constrains how stlfix can be licensed if it is redistributed with it.

## Development

```bash
pip install -e . pytest && pytest -q        # synthetic broken-mesh tests
pip install -r bench/requirements.txt       # benchmark deps (~10 GB dataset download)
bench/run.sh dev results.jsonl A B AB meshlab admesh
```

See `CLAUDE.md` for project status, benchmark method and results.

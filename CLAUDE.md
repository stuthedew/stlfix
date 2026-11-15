# stlfix — project notes for Claude sessions

## Goal (from the user)
A local, zero-troubleshooting equivalent of https://www.formware.co/onlinestlrepair : one command
(`stlfix model.stl`) that fixes non-manifold STLs for 3D printing on macOS. No tuning, no GUI required.
The user is not a mesh-processing developer; output must be one plain line per file.

## Why a custom tool (researched, Nov 2026)
- No Homebrew formula/cask provides a CLI that fixes non-manifold edges. Checked all 8,731 formulas and
  7,834 casks for mesh/STL tools.
- `admesh` (brew): reported the user's collet-screw.stl as clean and left its 2 non-manifold edges.
- MeshLab (cask): its filters fix the collet screw, but it is GUI-only; `meshlabserver` was dropped in
  2020.12 in favour of PyMeshLab.
- PrusaSlicer's Netfabb repair is Windows-only; its `--repair` CLI flag is unreliable per forum reports.
- Formware's page uploads to their server; no client-side code. Paid API from 100 EUR/month.

## Repo / license
- Public repo github.com/stuthedew/stlfix, branch main. No LICENSE file: the user has not chosen one.
  pymeshfix is AGPL-3.0; manifold3d Apache-2.0; libigl MPL-2.0; trimesh MIT. Don't add a license
  without asking. Keep meshes/datasets out of git (.gitignore); never commit the user's own STLs.

## Layout
- `stlfix.py` — the CLI, single module. v2 pipeline: clean -> detect problems -> run strategies A and B
  in parallel child processes (90 s limit each) -> self-check each result + the untouched input ->
  write the best -> re-read from disk and report. Never reports a shape-changing result as "fixed".
  - A `strat_groups`: MeshFix per vertex-connected group; clean closed shells are kept untouched.
  - B `strat_union`: MeshFix each face-connected body, manifold3d boolean union; inward shells inside
    another solid are cavities and are subtracted.
  - `orient()`: own normal fixing. Do NOT use trimesh `fix_normals()`: it flips cavity shells outward
    and fills hollow parts (verified: hollow cube volume 7 -> 9).
  - `classify_surface()` / `deviation()`: self-check, see below.
- `tests/test_stlfix.py` — synthetic broken meshes with known outcomes (`pytest -q`).
- `bench/` — benchmark on Thingi10K (real Thingiverse files): `run.sh`, `make_jobs.py`, `worker.py`,
  `summarize.py`. `strategies.py` holds experimental strategy C (fTetWild), not in the pipeline.
- `bench/results/*.jsonl` — raw per-model results.

## Benchmark method
- Corpus: Thingi10K (Zhou & Jacobson 2016, arXiv:1605.04797) via `pip install thingi10k`; all 1,218
  edge-non-manifold models with <=100k faces. Fixed random split (seed 42): 150 dev (`dev_ids.json`)
  for tuning, 1,068 held-out test (`test_ids.json`) — only evaluate the final pipeline on test.
- No ground-truth repairs exist for these, so "fixed" = after writing and re-reading the STL:
  `stlfix.problems()` empty (watertight, 0 non-manifold/open edges, positive volume, no overlapping
  parts) AND shape preserved: 99th-percentile surface deviation < 1% of bbox diagonal both ways.
- Deviation uses the generalized winding number (Jacobson et al. 2013, libigl fast_winding_number) to
  classify each input surface sample: material on exactly one side = real boundary (must be kept);
  both sides = internal wall between overlapping bodies; neither = stray fin/sheet (removing these is
  the repair). Only real-boundary samples count toward "lost", so a filled cavity or deleted body is
  caught while internal walls/fins are not penalised. Distances via libigl point_mesh_squared_distance
  (trimesh closest_point ran out of memory on long sliver triangles, e.g. the collet screw).
- Caveat: the tool selects among candidates with the same metric family it is scored on (different
  random seed). It is a consistency check, not independent ground truth; spot-check renders of failures.
- Baselines: admesh, MeshLab filters (pymeshlab).

## Findings so far
- User's collet-screw.stl (2 stray 0.0001 mm^2 triangles on the surface): v1 fixes it, all other
  vertices bit-identical; Formware's fix shifts every vertex up to 0.0037 mm. Same printable result.
- Run 1 (v1, old metric without the "inside" exclusion), first 95 models: v1 watertight 60% but deleted
  real geometry in ~1/3 of those. Root cause: MeshFix assumes one closed solid; on multi-body or
  "triangle soup" files it keeps the largest component (sometimes ~10% of the area).
- Most failures are self-intersecting multi-body models (81/95), typical of Thingiverse exports.
- Strategy B (close each face-connected body with MeshFix, then manifold3d boolean union) fixes several
  of those without loss. Strategy C (fTetWild via pytetwild) was slow (~30 s+/model) and did not fix
  the first hard cases; dropped from the dev run, may revisit only for models A and B both fail.
- MeshFix can take 100-200 s on some models; v2 needs a per-strategy time budget.

## Dev results (150 models, bench/results/dev_v2.jsonl, internal tolerance was 1% for this run)
| method | fixed | shape damaged | median time |
|---|---|---|---|
| stlfix v2 | 57.3% | 1.3% | 3.3 s |
| A only (MeshFix per group) | 54.0% | 30.7% | 0.4 s |
| B only (close + union) | 50.7% | 16.7% | 0.3 s |
| MeshLab filters | 22.0% | 3.3% | 0.2 s |
| admesh | 16.0% | 2.7% | 0.05 s |
24/150 have no problems after basic cleanup (merge duplicate vertices / drop duplicate faces).
stlfix kept the input in 31/150 (every repair damaged the shape). Of stlfix's valid outputs, 80/87
deviate <0.1%; the 2 "damaged" were borderline (1.08%, 1.4%) -> internal tolerance tightened to 0.5%.

What the failures look like (rendered with bench/render.py):
- 41082 factory: many overlapping open pieces that only together enclose a solid; MeshFix keeps one
  chimney (6% of area). 209796 car kit: MeshFix caps the open cockpit. Self-check rejects both.
- fTetWild (pytetwild, strategy C) keeps the shape within ~0.01% on these, but its tet-boundary surface
  has many non-manifold edges; post-processing with A or B either destroys it or leaves them. Also
  30-75 s on larger models and pytetwild only ships macOS 15 arm64 wheels. Dropped.
- Possible next idea for the soup/open-pieces class: winding-number solid extraction with a mesh
  arrangement (e.g. libigl copyleft/cgal remesh_self_intersections + extract by winding number), if
  available in Python bindings; not investigated.

## Status / next steps
1. DONE: v2 pipeline in `stlfix.py` with time limits, cavity-safe orientation, winding-number
   self-check; 9 synthetic tests pass; collet screw fixed in ~4 s with all vertices unchanged.
2. DONE: dev run, see above.
3. IN PROGRESS: held-out test run (1,068 models: stlfix, meshlab, admesh) -> bench/results/test_v2.jsonl.

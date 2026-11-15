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

## Layout
- `stlfix.py` — the CLI (v1: MeshFix per vertex-connected group, bbox-shrink guard, re-read verification).
- `tests/test_stlfix.py` — synthetic broken meshes with known outcomes (`pytest -q`).
- `bench/` — benchmark on Thingi10K (real Thingiverse files). `strategies.py` holds candidate repair
  strategies (A, B, C) and the self-check (`deviation`, `best`) that are being evaluated for v2.
- `bench/results/*.jsonl` — raw per-model results.

## Benchmark method
- Corpus: Thingi10K (Zhou & Jacobson 2016, arXiv:1605.04797) via `pip install thingi10k`; all 1,218
  edge-non-manifold models with <=100k faces. Fixed random split (seed 42): 150 dev (`dev_ids.json`)
  for tuning, 1,068 held-out test (`test_ids.json`) — only evaluate the final pipeline on test.
- No ground-truth repairs exist for these, so "fixed" = after writing and re-reading the STL: watertight,
  0 non-manifold edges, positive volume, AND shape preserved: 99th-percentile surface deviation
  < 1% of bbox diagonal in both directions (input->output "lost", output->input "added").
  "lost" ignores input surface that ends up strictly inside the output solid (internal walls removed by
  a union are correct, not loss).
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

## Status / next steps
1. Finish dev run (A, B, AB, meshlab, admesh) -> pick pipeline (likely AB: run both, keep the best
   self-checked result, never write a silently damaged file).
2. Add time budget, fold the chosen pipeline into `stlfix.py` (add manifold3d + rtree deps).
3. Evaluate once on the held-out test split; record numbers here and in README.
4. Keep `tests/` passing.

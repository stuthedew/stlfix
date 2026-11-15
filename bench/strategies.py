"""Experimental strategies not (yet) in stlfix.py."""
import numpy as np, trimesh
import stlfix


def strat_tetwild(mesh, epsilon=2e-4):
    """fTetWild envelope remesh (Hu et al. 2020) via pytetwild. Re-triangulates the whole model within
    epsilon * bbox diagonal of the input. Slow (~30 s+/model single-threaded); on the first hard dev cases
    it did not produce valid solids, so it is not in the main pipeline."""
    import pytetwild
    v, t = pytetwild.tetrahedralize(np.asarray(mesh.vertices, np.float64), np.asarray(mesh.faces, np.int32),
                                    optimize=False, epsilon=epsilon, coarsen=True, quiet=True, num_threads=1)
    if len(t) == 0:
        return trimesh.Trimesh()
    f = np.vstack([t[:, [0, 1, 2]], t[:, [0, 1, 3]], t[:, [0, 2, 3]], t[:, [1, 2, 3]]])
    _, idx, cnt = np.unique(np.sort(f, axis=1), axis=0, return_index=True, return_counts=True)
    return stlfix.finish([trimesh.Trimesh(v, f[idx[cnt == 1]], process=True)])

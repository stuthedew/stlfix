"""Render input vs repaired mesh side by side to eyeball a result (matplotlib, no GPU needed).

Usage: python bench/render.py input.stl output.stl out.png
"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
import trimesh


def draw(ax, m, title):
    tri = m.triangles
    light = np.array([0.4, 0.3, 0.85]); light /= np.linalg.norm(light)
    shade = 0.35 + 0.65 * np.clip(np.abs(m.face_normals @ light), 0, 1)
    ax.add_collection3d(Poly3DCollection(tri, facecolors=np.c_[shade * .55, shade * .7, shade * .9], linewidths=0))
    lo, hi = m.bounds; c = (lo + hi) / 2; r = (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_axis_off(); ax.set_title(title, fontsize=9); ax.view_init(25, 35)


if __name__ == "__main__":
    a, b, out = sys.argv[1:4]
    fig = plt.figure(figsize=(10, 5), dpi=110)
    for k, (path, t) in enumerate([(a, "input"), (b, "repaired")]):
        draw(fig.add_subplot(1, 2, k + 1, projection="3d"), trimesh.load(path, force="mesh"), f"{t}: {path.split('/')[-1]}")
    fig.tight_layout(); fig.savefig(out)

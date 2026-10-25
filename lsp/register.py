"""Step 2: translation-only tile registration on the low-res copy.

Pairwise 3-D phase correlation on each overlap (band-passed + tapered), a
quality score (Pearson r after alignment), outlier rejection, then a weighted
global least-squares solve. Tiles with no reliable link keep stage positions.
"""
from __future__ import annotations

import numpy as np
import zarr
from scipy import ndimage as ndi
from scipy.signal.windows import tukey
from skimage.registration import phase_cross_correlation

from .convert import tile_paths


def _bandpass(v, zs):
    v = v.astype(np.float32)
    sig = np.array([zs, 1, 1], np.float32)
    return ndi.gaussian_filter(v, 1.0 * sig) - ndi.gaussian_filter(v, 8.0 * sig)


def _taper(shape, alpha=0.3):
    wz, wy, wx = (tukey(n, alpha) if n > 2 else np.ones(n) for n in shape)
    return (wz[:, None, None] * wy[None, :, None] * wx[None, None, :]).astype(np.float32)


def pair_shift(args: dict) -> dict:
    """Return the extra displacement (z,y,x; low-res px) that moves tile j onto tile i."""
    i, j = args["i"], args["j"]
    (zi0, zi1, yi0, yi1, xi0, xi1) = args["box_i"]
    (zj0, zj1, yj0, yj1, xj0, xj1) = args["box_j"]
    A = zarr.open(args["path_i"], "r")[zi0:zi1, yi0:yi1, xi0:xi1]
    B = zarr.open(args["path_j"], "r")[zj0:zj1, yj0:yj1, xj0:xj1]
    zs = args["zscale"]
    fa, fb = _bandpass(A, zs), _bandpass(B, zs)
    res = dict(i=i, j=j, shift=[0.0, 0.0, 0.0], q=0.0, ok=False)
    if fa.std() < 1e-3 or fb.std() < 1e-3:
        return res
    t = _taper(fa.shape)
    s, _, _ = phase_cross_correlation(fa * t, fb * t, upsample_factor=4, normalization="phase")
    s = np.asarray(s, float)
    shifted = ndi.shift(fb, s, order=1, mode="constant")
    m = np.ceil(np.abs(s)).astype(int) + 1
    sl = tuple(slice(k, n - k) if n > 2 * k + 2 else slice(None) for k, n in zip(m, fa.shape))
    a, b = fa[sl].ravel(), shifted[sl].ravel()
    q = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else 0.0
    res.update(shift=s.tolist(), q=q)
    lim = np.array(args["max_shift"], float)
    res["ok"] = bool(np.all(np.abs(s) <= lim) and q >= args["min_q"])
    return res


def build_pairs(proj, tiles, nz, ds, channel, tol=10):
    """Neighbouring tile pairs (same row or column) + overlap boxes in tile-local low-res px."""
    pairs = []
    for a in range(len(tiles)):
        for b in range(a + 1, len(tiles)):
            ta, tb = tiles[a], tiles[b]
            dx, dy = tb["x"] - ta["x"], tb["y"] - ta["y"]
            ox0, ox1 = max(ta["x"], tb["x"]), min(ta["x"] + ta["w"], tb["x"] + tb["w"])
            oy0, oy1 = max(ta["y"], tb["y"]), min(ta["y"] + ta["h"], tb["y"] + tb["h"])
            if ox1 <= ox0 or oy1 <= oy0 or not (abs(dx) <= tol or abs(dy) <= tol):
                continue

            def box(t):
                return (0, nz,
                        int((oy0 - t["y"]) // ds), int(np.ceil((oy1 - t["y"]) / ds)),
                        int((ox0 - t["x"]) // ds), int(np.ceil((ox1 - t["x"]) / ds)))
            bi, bj = box(ta), box(tb)
            # force identical sizes
            sz = [min(bi[k + 1] - bi[k], bj[k + 1] - bj[k]) for k in (0, 2, 4)]
            bi = (0, sz[0], bi[2], bi[2] + sz[1], bi[4], bi[4] + sz[2])
            bj = (0, sz[0], bj[2], bj[2] + sz[1], bj[4], bj[4] + sz[2])
            pairs.append(dict(
                i=ta["m"], j=tb["m"], box_i=bi, box_j=bj,
                path_i=str(tile_paths(proj.tiles_dir, ta["m"], channel)[1]),
                path_j=str(tile_paths(proj.tiles_dir, tb["m"], channel)[1]),
                zscale=proj.info()["px_xy_um"] * ds / proj.info()["px_z_um"],
            ))
    return pairs


def solve_global(n, results, prior=0.01, reject_k=3.0, min_resid=2.0):
    """Weighted least squares for per-tile deltas; one round of outlier rejection."""
    keep = [r for r in results if r["ok"]]
    # Drop the single worst-fitting link at a time until all links agree (loop consistency).
    while True:
        D = _lstsq(n, keep, prior)
        if not keep:
            break
        resid = np.array([np.linalg.norm(D[r["j"]] - D[r["i"]] - np.array(r["shift"])) for r in keep])
        worst = int(np.argmax(resid))
        if resid[worst] <= min_resid * 2:
            break
        keep.pop(worst)
    kept = {(r["i"], r["j"]) for r in keep}
    for r in results:
        r["used"] = (r["i"], r["j"]) in kept
    return D


def _lstsq(n, edges, prior):
    rows, rhs, w = [], [], []
    for r in edges:
        row = np.zeros(n)
        row[r["j"]], row[r["i"]] = 1, -1
        rows.append(row); rhs.append(r["shift"]); w.append(max(r["q"], 0.05))
    for k in range(n):
        row = np.zeros(n); row[k] = 1
        rows.append(row); rhs.append([0, 0, 0]); w.append(prior)
    A, b, W = np.array(rows), np.array(rhs), np.sqrt(np.array(w))[:, None]
    D, *_ = np.linalg.lstsq(A * W, b * W, rcond=None)
    return D

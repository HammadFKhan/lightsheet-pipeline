"""Candidate denoisers for (z, y, x) volumes. All take/return float32 numpy arrays; GPU (CuPy) is used where it helps.

Voxel anisotropy: z is ~2.9x coarser than xy here, so filters take per-axis settings.
"""
from __future__ import annotations

import numpy as np

from . import gpu


def gaussian(v, sigma_zyx, use_gpu=False):
    xp, ndi, on = gpu.backend(use_gpu)
    out = ndi.gaussian_filter(xp.asarray(v, dtype=xp.float32), sigma_zyx, mode="reflect")
    return gpu.to_cpu(out)


def median(v, size_zyx, use_gpu=False):
    xp, ndi, on = gpu.backend(use_gpu)
    out = ndi.median_filter(xp.asarray(v, dtype=xp.float32), size=size_zyx, mode="reflect")
    return gpu.to_cpu(out)


def perona_malik(v, iters=5, kappa=30.0, gamma=0.12, spacing=(2.88, 1.0, 1.0), use_gpu=False, presmooth=0.6):
    """Edge-preserving anisotropic diffusion: smooths flat areas, stops at edges where |gradient| >> kappa.
    presmooth (Catte regularisation): the edge test uses a lightly smoothed copy, so isolated noise specks are
    not mistaken for edges (plain Perona-Malik leaves them untouched)."""
    xp, ndi, on = gpu.backend(use_gpu)
    u = xp.asarray(v, dtype=xp.float32).copy()
    for _ in range(iters):
        upd = xp.zeros_like(u)
        us = ndi.gaussian_filter(u, presmooth) if presmooth else u
        for ax, sp in enumerate(spacing):
            d = xp.diff(u, axis=ax)
            ds = xp.diff(us, axis=ax) if presmooth else d
            f = xp.exp(-(ds / kappa) ** 2) * d                      # flux; zero-flux boundaries
            lo = [slice(None)] * 3; hi = [slice(None)] * 3
            lo[ax] = slice(0, -1); hi[ax] = slice(1, None)
            upd[tuple(lo)] += f / sp ** 2
            upd[tuple(hi)] -= f / sp ** 2
        u += gamma * upd
    return gpu.to_cpu(u)


def tv(v, weight, iters=60):
    from skimage.restoration import denoise_tv_chambolle
    return denoise_tv_chambolle(np.asarray(v, np.float32), weight=weight, max_num_iter=iters).astype(np.float32)


def tv_chambolle(v, weight, iters=60, use_gpu=False):
    """Total-variation denoising (Chambolle's dual projection), NumPy/CuPy port of the skimage algorithm with a
    fixed iteration count. `weight` is in units of the input (normalise by the noise sigma first)."""
    xp, _, on = gpu.backend(use_gpu)
    img = xp.asarray(v, dtype=xp.float32)
    nd = img.ndim
    p = xp.zeros((nd,) + img.shape, dtype=xp.float32)
    g = xp.zeros_like(p)
    tau = 1.0 / (2.0 * nd)
    d = xp.zeros_like(img)
    out = img
    for i in range(iters):
        if i > 0:
            d = -p.sum(axis=0)
            for ax in range(nd):
                sd = [slice(None)] * nd; sp = [slice(None)] * (nd + 1)
                sd[ax] = slice(1, None); sp[ax + 1] = slice(0, -1); sp[0] = ax
                d[tuple(sd)] += p[tuple(sp)]
            out = img + d
        for ax in range(nd):
            sg = [slice(None)] * (nd + 1)
            sg[ax + 1] = slice(0, -1); sg[0] = ax
            g[tuple(sg)] = xp.diff(out, axis=ax)             # last slice along ax is never written: stays 0
        norm = xp.sqrt((g ** 2).sum(axis=0))[None]
        norm *= tau / weight
        norm += 1.0
        p -= tau * g
        p /= norm
    d = -p.sum(axis=0)
    for ax in range(nd):
        sd = [slice(None)] * nd; sp = [slice(None)] * (nd + 1)
        sd[ax] = slice(1, None); sp[ax + 1] = slice(0, -1); sp[0] = ax
        d[tuple(sd)] += p[tuple(sp)]
    res = img + d
    return gpu.to_cpu(res)


def tv_volume(vol, sigma, weight, use_gpu=True, iters=60, block=1024, halo=16):
    """Block-wise TV denoise of a (z, y, x) volume held in memory. Noise-normalised: weight is in units of sigma
    (typical 0.15 light ... 0.26 medium ... 0.34 strong). In-plane blocks overlap by `halo` px so seams vanish."""
    vol = np.asarray(vol, dtype=np.float32)
    out = np.empty_like(vol)
    Z, Y, X = vol.shape
    for y0 in range(0, Y, block):
        for x0 in range(0, X, block):
            ya, yb = max(0, y0 - halo), min(Y, y0 + block + halo)
            xa, xb = max(0, x0 - halo), min(X, x0 + block + halo)
            r = tv_chambolle(vol[:, ya:yb, xa:xb] / sigma, weight, iters, use_gpu) * sigma
            y1, x1 = min(Y, y0 + block), min(X, x0 + block)
            out[:, y0:y1, x0:x1] = r[:, y0 - ya:y1 - ya, x0 - xa:x1 - xa]
    gpu.free_gpu_memory()
    return out


def estimate_tissue_sigma(v, offset=207.0, thresh=50.0):
    """Noise sigma in tissue (pixels clearly above the camera floor): MAD of 2nd differences along x. Noise grows
    with brightness here (5 counts in the dark, ~20-25 in tissue), so the whole-image median would underestimate it."""
    v = np.asarray(v, np.float32)
    d2 = v[:, :, 2:] - 2 * v[:, :, 1:-1] + v[:, :, :-2]
    sm = gpu_free_smooth(v)[:, :, 1:-1]
    sel = sm > offset + thresh
    if sel.sum() < 1000:
        sel = sm > np.percentile(sm, 50)
    dd = d2[sel]
    return float(1.4826 * np.median(np.abs(dd - np.median(dd))) / np.sqrt(6))


def gpu_free_smooth(v, s=1.0):
    from scipy import ndimage as _ndi
    return _ndi.gaussian_filter(v, s)


def wavelet(v, wavelet_name="db2", sigma=None):
    from skimage.restoration import denoise_wavelet
    return denoise_wavelet(np.asarray(v, np.float32), wavelet=wavelet_name, mode="soft", method="BayesShrink",
                           sigma=sigma, rescale_sigma=True).astype(np.float32)


def nlm(v, sigma, h_factor=0.8, patch=3, dist=4):
    from skimage.restoration import denoise_nl_means
    return denoise_nl_means(np.asarray(v, np.float32), patch_size=patch, patch_distance=dist,
                            h=h_factor * sigma, sigma=sigma, fast_mode=True, preserve_range=True).astype(np.float32)


def estimate_noise_sigma(v):
    """Robust white-noise sigma from 2nd differences (median-based, insensitive to sparse structure)."""
    v = np.asarray(v, np.float32)
    s = []
    for ax in range(3):
        d = np.diff(v, n=2, axis=ax)
        s.append(1.4826 * np.median(np.abs(d - np.median(d))) / np.sqrt(6))
    return float(np.median(s))

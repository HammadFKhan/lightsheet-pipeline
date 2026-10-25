"""Per-tile preprocessing: plane brightness (z) correction -> flat-field -> destripe -> Richardson-Lucy deconvolution.

All stages share `process_block`, which works on whole planes (so FFT destriping is
identical in the region test and in the full run) and runs on GPU (CuPy) or CPU.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import zarr
from scipy import ndimage as sndi

from . import convert as cv
from . import gpu
from . import psf as psfmod

DEFAULT_PARAMS = dict(
    plane_gain=True, pg_offset=None, pg_window=61, pg_pct=50,     # pg_offset None = estimate camera floor per tile
    flatfield=True, destripe=True, deconv=False, gpu=True,
    ff_smooth=40.0, ff_offset=0.0,
    stripe_dir="y", stripe_sigma=1.5, stripe_keep=12.0, stripe_strength=1.0,
    rl_iters=10, psf_lat_um=None, psf_ax_um=None,
)


def params_key(p: dict) -> str:
    q = {k: v for k, v in p.items() if k != "gpu"}      # GPU on/off doesn't change the result materially
    return hashlib.md5(json.dumps(q, sort_keys=True, default=str).encode()).hexdigest()[:8]


# ---- plane brightness (z) correction ---------------------------------------
def estimate_offset(low) -> float:
    """Camera floor (counts with no signal) from a low-res tile: 0.1th percentile of ~20 sampled planes."""
    nz = low.shape[0]
    zs = np.linspace(0, nz - 1, min(20, nz)).astype(int)
    return float(np.median([np.percentile(np.asarray(low[int(z)]), 0.1) for z in zs]))


def channel_floor(raw_dir, tile_ids, c) -> float:
    """Camera floor of a channel = 20th percentile of the per-tile estimates. The floor is a property of the camera,
    so tiles that are entirely tissue (no empty background of their own) borrow it from the other tiles."""
    offs = [estimate_offset(zarr.open(str(cv.tile_paths(Path(raw_dir), m, c)[1]), "r")) for m in tile_ids]
    return float(np.percentile(offs, 20))


def plane_gains(low_path, offset=None, window=61, pct=50, clip=(0.9, 2.2), grid=(4, 6), min_signal=15.0):
    """Per-plane gain MAPS g(z, y, x) that flatten narrow brightness dips along z (laser/illumination dropouts).

    The dimming is not uniform across the field of view (in the test data its depth varied from ~10% to ~50% across
    one tile), so gains are estimated per block of a coarse grid (default 4 x 6) on the low-res tile and later
    interpolated smoothly to full size. Per block, the reference level is a running median (61 planes) of the
    block's mean signal (mean - camera floor) along z: a dip ~10-15 planes wide is well under half the window, so it
    cannot drag its own reference down, and the median is unbiased on slow trends; slow real changes along z are
    left untouched. Blocks with too little signal (empty background) and
    planes within 1% of the reference get g = 1. Returns (gains of shape (nz, gy, gx), offset).
    """
    low = zarr.open(str(low_path), "r")
    off = estimate_offset(low) if offset is None else float(offset)
    nz, lh, lw = low.shape
    gy, gx = grid
    bh, bw = lh // gy, lw // gx
    blocks = np.concatenate([
        np.asarray(low[z:z + 64])[:, :gy * bh, :gx * bw].astype(np.float32).reshape(-1, gy, bh, gx, bw).mean(axis=(2, 4))
        for z in range(0, nz, 64)])                                            # (nz, gy, gx) block means
    s = blocks - off
    size = min(window, max(3, nz // 2 * 2 - 1))
    base = sndi.percentile_filter(s, pct, size=(size, 1, 1), mode="nearest")
    base = sndi.gaussian_filter1d(base, 3, axis=0, mode="nearest")
    g = np.clip(base / np.maximum(s, 1.0), clip[0], clip[1])
    g[np.abs(g - 1) < 0.01] = 1.0
    g[:, np.median(base, axis=0) < min_signal] = 1.0                            # background-only blocks: nothing to fix
    return g.astype(np.float32), float(off)


# ---- flat-field ------------------------------------------------------------
def flatfield_path(work_dir: Path, c: int) -> Path:
    return Path(work_dir) / f"flatfield_c{c}.npy"


def estimate_flatfield(raw_paths, z0, z1, smooth=40.0, per_tile=6):
    """Median over sampled planes from all tiles -> smooth -> normalise to mean 1 (shape h,w)."""
    stack = []
    zs = np.linspace(0, (z1 - z0) - 1, per_tile).astype(int)
    for p in raw_paths:
        a = zarr.open(str(p), "r")
        for z in zs:
            stack.append(np.asarray(a[int(z)], dtype=np.float32))
    prof = np.median(np.stack(stack), axis=0)
    prof = sndi.gaussian_filter(prof, smooth, mode="nearest")
    return (prof / prof.mean()).astype(np.float32)


# ---- core ------------------------------------------------------------------
def destripe_planes(v, p, xp):
    """Suppress stripe energy in the Fourier plane (ky~0 row for stripes running along y)."""
    n, h, w = v.shape
    F = xp.fft.rfft2(v, axes=(1, 2))
    ky = xp.fft.fftfreq(h)[:, None] * h            # frequency in bins, axis 1 (y)
    kx = xp.fft.rfftfreq(w)[None, :] * w           # frequency in bins, axis 2 (x)
    if p["stripe_dir"] == "y":                     # stripes constant along y: energy near ky = 0
        narrow, keep = ky, kx
    else:                                          # stripes constant along x: energy near kx = 0
        narrow, keep = kx, ky
    att = xp.exp(-(narrow ** 2) / (2 * p["stripe_sigma"] ** 2)) * (1 - xp.exp(-(keep ** 2) / (2 * p["stripe_keep"] ** 2)))
    H = (1 - p["stripe_strength"] * att).astype(xp.float32)
    return xp.fft.irfft2(F * H[None], s=(h, w), axes=(1, 2))


def richardson_lucy(v, kernel, iters, xp, ndi):
    """FFT-based RL: cost per iteration is independent of PSF size (circular edges, trimmed by the z halo)."""
    if xp is np:
        import scipy.fft as fftm
        rfftn = lambda a: fftm.rfftn(a, workers=-1)
        irfftn = lambda a, s: fftm.irfftn(a, s=s, workers=-1)
    else:
        rfftn, irfftn = xp.fft.rfftn, (lambda a, s: xp.fft.irfftn(a, s=s))
    shape = v.shape
    k = np.asarray(kernel, np.float32)
    for ax, (ks, n) in enumerate(zip(k.shape, shape)):          # kernel can't exceed the block: trim centred
        if ks > n:
            t = (ks - n) // 2
            k = np.take(k, range(t, t + n), axis=ax)
    k = k / k.sum()
    pad = np.zeros(shape, np.float32)
    pad[: k.shape[0], : k.shape[1], : k.shape[2]] = k
    pad = np.roll(pad, tuple(-(s // 2) for s in k.shape), axis=(0, 1, 2))   # PSF centre at the origin
    otf = rfftn(xp.asarray(pad))
    otf_c = xp.conj(otf)
    obs = xp.maximum(v, 0) + 1e-3
    est = obs.copy()
    for _ in range(iters):
        conv = irfftn(rfftn(est) * otf, shape)
        est *= irfftn(rfftn(obs / (conv + 1e-6)) * otf_c, shape)
    return est


def deconv_halo(p, kernel):
    return int(min(16, max(2, kernel.shape[0] // 2 * 2 * p["rl_iters"] // 2)))


def make_kernel(p, info, voxel_um_zyx):
    th = psfmod.theoretical_fwhm_um(info)
    lat = p.get("psf_lat_um") or th["lateral"]
    ax = p.get("psf_ax_um") or th["axial"]
    return psfmod.gaussian_kernel(lat, ax, voxel_um_zyx)


def process_block(block, p, ff, kernel, use_gpu, gain=None, pg_offset=0.0):
    """block: (n,h,w) uint16/float array -> processed uint16 array (numpy).
    gain: per-plane gains for exactly these n planes (plane brightness correction), applied about pg_offset."""
    xp, ndi, _ = gpu.backend(use_gpu)
    v = xp.asarray(block, dtype=xp.float32)
    if p.get("plane_gain") and gain is not None:
        g = xp.asarray(gain, dtype=xp.float32)
        if g.ndim == 1:
            g = g[:, None, None]
        elif g.shape[1:] != v.shape[1:]:                      # coarse gain map -> smooth full-size map (bilinear)
            g = ndi.zoom(g, (1, v.shape[1] / g.shape[1], v.shape[2] / g.shape[2]), order=1, mode="nearest", grid_mode=True)
            g = g[:, :v.shape[1], :v.shape[2]]
        v = (v - pg_offset) * g + pg_offset
    if p["flatfield"] and ff is not None:
        off = p["ff_offset"]
        v = (v - off) / xp.asarray(ff)[None] + off
    if p["destripe"]:
        v = destripe_planes(v, p, xp)
    if p["deconv"]:
        v = richardson_lucy(v, kernel, p["rl_iters"], xp, ndi)
    out = xp.clip(v, 0, 65535).astype(xp.uint16)
    res = gpu.to_cpu(out)
    if xp is not np:
        gpu.free_gpu_memory()
    return res


# ---- full-tile task --------------------------------------------------------
def pp_task(a: dict) -> str:
    m, c, ds = a["m"], a["c"], a["ds"]
    z0, z1 = a["z0"], a["z1"]
    p = a["params"]
    raw_dir, pp_dir = Path(a["raw_dir"]), Path(a["pp_dir"])
    pp_dir.mkdir(exist_ok=True)
    raw_p = cv.tile_paths(raw_dir, m, c)[0]
    out_p, low_p, done = cv.tile_paths(pp_dir, m, c)
    mark = f"z{z0}-{z1}-ds{ds}-pp{params_key(p)}"
    if done.exists() and done.read_text() == mark:
        return f"tile {m} ch {c}: already preprocessed"
    src = zarr.open(str(raw_p), "r")
    nz, h, w = src.shape
    lh, lw = h // ds, w // ds
    dst = zarr.open(str(out_p), mode="w", shape=src.shape, dtype="u2", chunks=cv.TILE_CHUNKS, compressor=cv.CODEC)
    low = zarr.open(str(low_p), mode="w", shape=(nz, lh, lw), dtype="u2",
                    chunks=(64, min(lh, 128), min(lw, 128)), compressor=cv.CODEC)
    ff = np.load(a["ff_path"]) if p["flatfield"] and a.get("ff_path") else None
    kernel = np.asarray(a["kernel"], np.float32) if p["deconv"] else None
    halo = deconv_halo(p, kernel) if p["deconv"] else 0
    gains, pg_off = None, 0.0
    if p.get("plane_gain"):
        gains, pg_off = plane_gains(cv.tile_paths(raw_dir, m, c)[1], p.get("pg_offset"), p.get("pg_window", 61), p.get("pg_pct", 50))
        np.save(pp_dir / f"gains_tile{m:02d}_c{c}.npy", gains)         # kept for transparency / QC
    prog = Path(a["prog"]) if a.get("prog") else None
    step = cv.TILE_CHUNKS[0]
    for zs in range(0, nz, step):
        ze = min(zs + step, nz)
        lo, hi = max(0, zs - halo), min(nz, ze + halo)
        res = process_block(src[lo:hi], p, ff, kernel, p["gpu"], None if gains is None else gains[lo:hi], pg_off)[zs - lo: zs - lo + (ze - zs)]
        dst[zs:ze] = res
        b = res[:, : lh * ds, : lw * ds].reshape(ze - zs, lh, ds, lw, ds)
        low[zs:ze] = b.mean(axis=(2, 4), dtype=np.float32).astype(np.uint16)
        if prog is not None:
            prog.write_text(str(ze))
    done.write_text(mark)
    return f"tile {m} ch {c}: preprocessed"


# ---- region test -----------------------------------------------------------
def preview_region(raw_path, p, ff, kernel, z0, z1, y0, y1, x0, x1, gains=None, pg_offset=0.0):
    """Process whole planes z0:z1 (+halo) of one tile and return (raw_crop, processed_crop).
    gains: per-plane gains for the whole tile (from plane_gains), indexed like the tile's planes."""
    src = zarr.open(str(raw_path), "r")
    nz = src.shape[0]
    halo = deconv_halo(p, kernel) if p["deconv"] else 0
    lo, hi = max(0, z0 - halo), min(nz, z1 + halo)
    blk = np.asarray(src[lo:hi])
    res = process_block(blk, p, ff, kernel, p["gpu"], None if gains is None else gains[lo:hi], pg_offset)
    sl = slice(z0 - lo, z0 - lo + (z1 - z0))
    return blk[sl, y0:y1, x0:x1], res[sl, y0:y1, x0:x1]

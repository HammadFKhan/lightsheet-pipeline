"""Export a fused OME-Zarr volume to size-reduced BigTIFF stacks (one file per channel).

Size levers: pyramid level (downsample), 8-bit scaling, lossless compression, crop.
Written plane by plane from z-slabs; files are written as *.partial and renamed when complete.
"""
from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import tifffile
import zarr

SLAB = 32                       # matches the zarr z-chunk so each chunk is decoded once
COMPRESSIONS = {"Deflate (lossless, recommended)": "zlib", "LZW (lossless)": "lzw", "None": None}


@dataclass
class ExportOptions:
    zarr_path: str
    out_dir: str
    stem: str
    level: int = 1
    channels: list[int] = field(default_factory=lambda: [0])
    bit8: bool = True
    limits: dict = field(default_factory=dict)          # {channel: (lo, hi)} used when bit8
    compression: str | None = "zlib"
    crop: tuple | None = None                           # (z0,z1,y0,y1,x0,x1) in LEVEL voxel coordinates
    channel_names: list[str] = field(default_factory=list)
    tv_weight: float | None = None                      # display-copy TV denoise strength in noise-sigma units (None = off)
    use_gpu: bool = True


def level_table(zpath):
    """[{level, shape(c,z,y,x), voxel_um(z,y,x), uncompressed_gb_16bit}] for every pyramid level."""
    g = zarr.open_group(str(zpath), "r")
    ms = g.attrs["multiscales"][0]["datasets"]
    rows = []
    for d in ms:
        a = g[d["path"]]
        sc = d["coordinateTransformations"][0]["scale"][1:]
        rows.append(dict(level=int(d["path"]), shape=a.shape, voxel_um=tuple(sc),
                         gb16=float(np.prod(a.shape[1:])) * 2 / 1e9))
    return rows


def auto_limits(zpath, channel, lo_pct=0.35, hi_pct=99.65):
    """ImageJ-style auto range from the coarsest pyramid level (ignores empty canvas voxels)."""
    g = zarr.open_group(str(zpath), "r")
    n = len(g.attrs["multiscales"][0]["datasets"]) - 1
    a = np.asarray(g[str(n)][channel]).ravel()
    a = a[a > 0]
    if a.size == 0:
        return 0.0, 65535.0
    lo, hi = np.percentile(a, [lo_pct, hi_pct])
    return float(lo), float(max(hi, lo + 1))


def _scale8(slab, lo, hi):
    f = (slab.astype(np.float32) - lo) * (255.0 / max(hi - lo, 1e-6))
    return np.clip(f, 0, 255).astype(np.uint8)


def _tiff_kwargs(comp):
    kw = dict(photometric="minisblack")
    if comp:
        kw["compression"] = comp
        if comp in ("zlib", "lzw"):
            kw["predictor"] = True
        if comp == "zlib":
            kw["compressionargs"] = {"level": 6}
    return kw


def _crop(opts, shape):
    _, nz, ny, nx = shape
    return opts.crop or (0, nz, 0, ny, 0, nx)


TV_HALO_Z = 4                    # planes of context above/below each slab so TV has no seams along z


def camera_floor(zpath, channel):
    """Camera floor (no-signal counts): 0.1th percentile of the non-empty voxels of the coarsest pyramid level."""
    g = zarr.open_group(str(zpath), "r")
    n = len(g.attrs["multiscales"][0]["datasets"]) - 1
    a = np.asarray(g[str(n)][channel]).ravel()
    a = a[a > 0]
    return float(np.percentile(a, 0.1)) if a.size else 207.0


def tissue_sigma(arr, channel, crop, offset, n_patches=24, seed=0):
    """Median noise sigma over random full-resolution patches that contain tissue."""
    from . import denoise as dn
    z0, z1, y0, y1, x0, x1 = crop
    rng = np.random.default_rng(seed)
    est = []
    for _ in range(n_patches * 4):
        if len(est) >= n_patches:
            break
        z = int(rng.integers(z0, max(z0 + 1, z1 - 16))); y = int(rng.integers(y0, max(y0 + 1, y1 - 256)))
        x = int(rng.integers(x0, max(x0 + 1, x1 - 256)))
        p = np.asarray(arr[channel, z:z + 16, y:y + 256, x:x + 256]).astype(np.float32)
        if p.shape[0] < 3 or (p > offset + 50).mean() < 0.5:
            continue
        est.append(dn.estimate_tissue_sigma(p, offset))
    return float(np.median(est)) if est else 20.0


def _tv_slab(arr, c, za, zb, y0, y1, x0, x1, sigma, weight, use_gpu):
    from . import denoise as dn
    v = np.asarray(arr[c, za:zb, y0:y1, x0:x1]).astype(np.float32)
    return dn.tv_volume(v, sigma, weight, use_gpu)


def estimate(opts: ExportOptions, n_samples=3):
    """Per-channel compressed size estimate (bytes) from a few sample planes using the real settings."""
    g = zarr.open_group(opts.zarr_path, "r")
    arr = g[str(opts.level)]
    z0, z1, y0, y1, x0, x1 = _crop(opts, arr.shape)
    zs = np.linspace(z0, z1 - 1, n_samples + 2)[1:-1].astype(int)
    out = {}
    for c in opts.channels:
        tot = 0
        sigma = None
        if opts.tv_weight:
            sigma = tissue_sigma(arr, c, (z0, z1, y0, y1, x0, x1), camera_floor(opts.zarr_path, c))
        for z in zs:
            if opts.tv_weight:     # TV makes the data far more compressible, so estimate on the denoised planes
                za, zb = max(0, int(z) - TV_HALO_Z), min(arr.shape[1], int(z) + TV_HALO_Z + 1)
                p = np.clip(np.rint(_tv_slab(arr, c, za, zb, y0, y1, x0, x1, sigma, opts.tv_weight, opts.use_gpu)[int(z) - za]), 0, 65535).astype(np.uint16)
            else:
                p = np.asarray(arr[c, int(z), y0:y1, x0:x1])
            if opts.bit8:
                lo, hi = opts.limits.get(c) or auto_limits(opts.zarr_path, c)
                p = _scale8(p, lo, hi)
            bio = io.BytesIO()
            with tifffile.TiffWriter(bio, bigtiff=True) as tw:
                tw.write(p, **_tiff_kwargs(opts.compression))
            tot += bio.tell()
        out[c] = tot / len(zs) * (z1 - z0)
    return out


def _voxel_um(zpath, level):
    g = zarr.open_group(str(zpath), "r")
    return g.attrs["multiscales"][0]["datasets"][level]["coordinateTransformations"][0]["scale"][1:]


def export_iter(opts: ExportOptions, cancel):
    """Generator yielding (pages_done, pages_total, msg, status); raises RuntimeError on cancel."""
    g = zarr.open_group(opts.zarr_path, "r")
    arr = g[str(opts.level)]
    z0, z1, y0, y1, x0, x1 = _crop(opts, arr.shape)
    dz, dy, dx = _voxel_um(opts.zarr_path, opts.level)
    out_dir = Path(opts.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    total = len(opts.channels) * (z1 - z0)
    done, t0 = 0, time.time()
    names = opts.channel_names or [f"ch{c}" for c in range(arr.shape[0])]
    meta = dict(source=opts.zarr_path, level=opts.level, shape_zyx=[z1 - z0, y1 - y0, x1 - x0],
                voxel_um_zyx=[dz, dy, dx], bit_depth=8 if opts.bit8 else 16, compression=opts.compression,
                crop_level_voxels=[z0, z1, y0, y1, x0, x1], created=datetime.now().isoformat(timespec="seconds"),
                limits={}, files=[])
    kw = _tiff_kwargs(opts.compression)
    for c in opts.channels:
        lo, hi = (opts.limits.get(c) or auto_limits(opts.zarr_path, c)) if opts.bit8 else (None, None)
        if opts.bit8:
            meta["limits"][names[c]] = [lo, hi]
        final = out_dir / f"{opts.stem}_{names[c]}.tif"
        tmp = final.with_suffix(".tif.partial")
        desc = f"ImageJ=1.53t\nunit=um\nspacing={dz}\nfinterval={dz}\n"
        sigma = None
        if opts.tv_weight:
            sigma = tissue_sigma(arr, c, (z0, z1, y0, y1, x0, x1), camera_floor(opts.zarr_path, c))
            meta.setdefault("tv", {})[names[c]] = dict(weight=opts.tv_weight, noise_sigma=round(sigma, 2),
                                                       note="display copy; source data unchanged")
            yield done, total, f"{names[c]}: TV denoise weight {opts.tv_weight} (noise sigma {sigma:.1f} counts)", None
        try:
            with tifffile.TiffWriter(tmp, bigtiff=True) as tw:
                first = True
                for zs in range(z0, z1, SLAB):
                    if cancel.is_set():
                        raise RuntimeError("cancelled")
                    ze = min(zs + SLAB, z1)
                    if opts.tv_weight:
                        za, zb = max(0, zs - TV_HALO_Z), min(arr.shape[1], ze + TV_HALO_Z)
                        den = _tv_slab(arr, c, za, zb, y0, y1, x0, x1, sigma, opts.tv_weight, opts.use_gpu)
                        slab = np.clip(np.rint(den[zs - za: zs - za + (ze - zs)]), 0, 65535).astype(np.uint16)
                    else:
                        slab = np.asarray(arr[c, zs:ze, y0:y1, x0:x1])
                    if opts.bit8:
                        slab = _scale8(slab, lo, hi)
                    for k in range(slab.shape[0]):
                        extra = dict(description=desc) if first else {}
                        tw.write(slab[k], metadata=None, resolution=(1.0 / dx, 1.0 / dy),
                                 resolutionunit="NONE", maxworkers=8, **kw, **extra)
                        first = False
                    done += slab.shape[0]
                    el = time.time() - t0
                    rate = done / max(el, 1e-6)
                    eta = (total - done) / rate if done > 8 else None
                    yield done, total, None, (
                        f"{100 * done / total:.1f}% | {names[c]} plane {done}/{total} | "
                        f"elapsed {_fmt(el)} | ETA {_fmt(eta) if eta is not None else 'estimating...'} | "
                        f"{tmp.stat().st_size / 1e9:.1f} GB written")
            tmp.replace(final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        meta["files"].append(dict(file=final.name, gb=round(final.stat().st_size / 1e9, 2)))
        yield done, total, f"wrote {final} ({final.stat().st_size / 1e9:.1f} GB)", None
    (out_dir / f"{opts.stem}_export.json").write_text(json.dumps(meta, indent=2))
    yield total, total, f"Export complete: {sum(f['gb'] for f in meta['files']):.1f} GB total in {out_dir}", None


def _fmt(s):
    s = int(max(s, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"

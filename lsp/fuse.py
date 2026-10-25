"""Steps 3-4: lazy, blockwise linear-blend fusion + multiscale pyramid (OME-Zarr layout)."""
from __future__ import annotations

import json
from pathlib import Path

import dask.array as da
import numpy as np
import zarr
from numcodecs import Blosc

CODEC = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)


def _taper(n, r):
    i = np.arange(n, dtype=np.float32)
    return np.minimum(1.0, np.minimum(i + 0.5, n - i - 0.5) / max(r, 1)).astype(np.float32)


def canvas_shape(offsets, tile_shape):
    nz, h, w = tile_shape
    return (max(o[0] for o in offsets.values()) + nz,
            max(o[1] for o in offsets.values()) + h,
            max(o[2] for o in offsets.values()) + w)


def fuse_task(a: dict) -> str:
    """Fuse one output block (z0:z1, y0:y1, x0:x1) for one channel and write it."""
    out = zarr.open(a["out"], "r+")[a["level0"]]
    z0, z1, y0, y1, x0, x1 = a["block"]
    num = np.zeros((z1 - z0, y1 - y0, x1 - x0), np.float32)
    den = np.zeros_like(num)
    nz, h, w = a["tile_shape"]
    ry, rx = a["ramp"]
    wy_full, wx_full = _taper(h, ry), _taper(w, rx)
    for m, (oz, oy, ox) in a["offsets"].items():
        tz0, tz1 = max(z0, oz), min(z1, oz + nz)
        ty0, ty1 = max(y0, oy), min(y1, oy + h)
        tx0, tx1 = max(x0, ox), min(x1, ox + w)
        if tz0 >= tz1 or ty0 >= ty1 or tx0 >= tx1:
            continue
        src = zarr.open(a["tiles"][m], "r")[tz0 - oz:tz1 - oz, ty0 - oy:ty1 - oy, tx0 - ox:tx1 - ox]
        wgt = wy_full[ty0 - oy:ty1 - oy, None] * wx_full[None, tx0 - ox:tx1 - ox]
        sl = (slice(tz0 - z0, tz1 - z0), slice(ty0 - y0, ty1 - y0), slice(tx0 - x0, tx1 - x0))
        num[sl] += src.astype(np.float32) * wgt[None]
        den[sl] += wgt[None]
    fused = np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0)
    out[(a["c"], slice(z0, z1), slice(y0, y1), slice(x0, x1))] = np.clip(fused, 0, 65535).astype(np.uint16)
    return f"c{a['c']} block z{z0}-{z1} y{y0}-{y1}"


def make_output(path: Path, shape_czyx, chunks=(1, 32, 256, 256)):
    g = zarr.open_group(str(path), mode="w")
    g.create_dataset("0", shape=shape_czyx, chunks=chunks, dtype="u2", compressor=CODEC, fill_value=0)
    return g


def blocks(shape_zyx, bz, by):
    Z, Y, X = shape_zyx
    return [(z, min(z + bz, Z), y, min(y + by, Y), 0, X)
            for z in range(0, Z, bz) for y in range(0, Y, by)]


def build_pyramid(path: Path, factors, voxel_um, names=None, log=lambda *_: None):
    """Add levels 1..n (generator: yields (level, n_levels, shape) per level); writes OME-NGFF 0.4
    metadata last, so an interrupted pyramid is never mistaken for a finished one."""
    g = zarr.open_group(str(path), mode="r+")
    prev = "0"
    shapes = [g["0"].shape]
    cum = [(1, 1, 1)]
    for k, f in enumerate(factors, start=1):
        src = da.from_zarr(g[prev])
        Z, Y, X = src.shape[1:]
        fz, fy, fx = f
        src = src[:, : Z // fz * fz, : Y // fy * fy, : X // fx * fx]
        lvl = da.coarsen(np.mean, src, {1: fz, 2: fy, 3: fx}).astype(np.uint16)
        lvl = lvl.rechunk((1, 32, 256, 256))
        lvl.to_zarr(str(path), component=str(k), compressor=CODEC, overwrite=True)
        prev = str(k)
        shapes.append(g[prev].shape)
        cum.append(tuple(c * s for c, s in zip(cum[-1], f)))
        yield k, len(factors), shapes[-1]
    dz, dy, dx = voxel_um
    datasets = [dict(path=str(k), coordinateTransformations=[
        dict(type="scale", scale=[1.0, dz * c[0], dy * c[1], dx * c[2]])])
        for k, c in enumerate(cum)]
    g.attrs["multiscales"] = [dict(
        version="0.4", name=path.stem,
        axes=[dict(name="c", type="channel"),
              dict(name="z", type="space", unit="micrometer"),
              dict(name="y", type="space", unit="micrometer"),
              dict(name="x", type="space", unit="micrometer")],
        datasets=datasets)]
    g.attrs["lsp"] = dict(voxel_um_zyx=list(voxel_um), channel_names=names or [])

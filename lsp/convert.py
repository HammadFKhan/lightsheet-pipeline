"""Step 1: stream CZI tiles into chunked Zarr (full-res + xy-downsampled copy)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import zarr
from numcodecs import Blosc

CODEC = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
TILE_CHUNKS = (32, 256, 256)


def tile_paths(tiles_dir: Path, m: int, c: int):
    return (tiles_dir / f"tile{m:02d}_c{c}.zarr",
            tiles_dir / f"tile{m:02d}_c{c}_low.zarr",
            tiles_dir / f".tile{m:02d}_c{c}.done")


def marker(z0, z1, ds):
    return f"z{z0}-{z1}-ds{ds}"


def is_done(done_path: Path, z0, z1, ds) -> bool:
    """A tile counts as converted only if it was converted for the same z-range and downsample."""
    try:
        return done_path.read_text() == marker(z0, z1, ds)
    except OSError:
        return False


def convert_task(a: dict) -> str:
    from aicspylibczi import CziFile

    m, c, ds = a["m"], a["c"], a["ds"]
    z0, z1, h, w = a["z0"], a["z1"], a["h"], a["w"]
    full_p, low_p, done = tile_paths(Path(a["tiles_dir"]), m, c)
    if is_done(done, z0, z1, ds):
        return f"tile {m} ch {c}: already done"

    nz, lh, lw = z1 - z0, h // ds, w // ds
    full = zarr.open(str(full_p), mode="w", shape=(nz, h, w), dtype="u2",
                     chunks=TILE_CHUNKS, compressor=CODEC)
    low = zarr.open(str(low_p), mode="w", shape=(nz, lh, lw), dtype="u2",
                    chunks=(64, min(lh, 128), min(lw, 128)), compressor=CODEC)
    cz = CziFile(a["czi"])
    step = TILE_CHUNKS[0]
    prog = Path(a["prog"]) if a.get("prog") else None
    for zs in range(0, nz, step):
        ze = min(zs + step, nz)
        buf = np.empty((ze - zs, h, w), dtype=np.uint16)
        for k, z in enumerate(range(zs, ze)):
            img, _ = cz.read_image(M=m, Z=z0 + z, C=c)
            buf[k] = np.squeeze(img)
            if prog is not None:
                prog.write_text(str(z + 1))
        full[zs:ze] = buf
        b = buf[:, : lh * ds, : lw * ds].reshape(ze - zs, lh, ds, lw, ds)
        low[zs:ze] = b.mean(axis=(2, 4), dtype=np.float32).astype(np.uint16)
    done.write_text(marker(z0, z1, ds))
    return f"tile {m} ch {c}: done"

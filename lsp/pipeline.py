"""Pipeline steps as generators yielding (done, total, message, status).

message is a log line or None for a pure progress tick; status is a one-line
"37% | ... | elapsed | ETA" string (or None).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import numpy as np

from . import convert as cv
from . import fuse as fu
from . import register as rg
from .project import Project, inspect_czi


class Cancelled(Exception):
    pass


def fmt_time(s):
    s = int(max(s, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def run_pool(fn, tasks, workers, cancel: threading.Event, unit="tasks", unit_bytes=0, progress=False):
    """Run tasks in processes. Yields (units_done, units_total, result_or_None, status).

    Tasks may carry "units" (default 1). With progress=True each task gets a "prog" file path
    and may write its running unit count there, giving smooth progress inside long tasks.
    """
    units = [t.get("units", 1) for t in tasks]
    total = sum(units)
    pdir = Path(tempfile.mkdtemp(prefix="lsp_prog_")) if progress else None
    if pdir:
        for i, t in enumerate(tasks):
            t["prog"] = str(pdir / f"{i}.txt")
    ex = ProcessPoolExecutor(max_workers=workers)
    t0 = time.time()
    hist = deque([(t0, 0)])
    finished_units = 0
    try:
        fut_idx = {ex.submit(fn, t): i for i, t in enumerate(tasks)}
        pending = set(fut_idx)
        done_idx = set()
        while pending:
            if cancel.is_set():
                raise Cancelled()
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            results = []
            for f in done:
                i = fut_idx[f]
                results.append(f.result())          # re-raises worker exceptions
                done_idx.add(i)
                finished_units += units[i]
            cur = finished_units
            if progress:
                for i in range(len(tasks)):
                    if i in done_idx:
                        continue
                    try:
                        cur += min(int(Path(tasks[i]["prog"]).read_text() or 0), units[i])
                    except (OSError, ValueError):
                        pass
            now = time.time()
            hist.append((now, cur))
            while len(hist) > 2 and now - hist[0][0] > 45:
                hist.popleft()
            status = _status(cur, total, now - t0, hist, unit, unit_bytes)
            if results:
                for r in results:
                    yield cur, total, r, status
            else:
                yield cur, total, None, status
    finally:
        if cancel.is_set():
            for p in list(getattr(ex, "_processes", {}).values()):
                p.terminate()
        ex.shutdown(wait=False, cancel_futures=True)
        if pdir:
            shutil.rmtree(pdir, ignore_errors=True)


def _status(cur, total, elapsed, hist, unit, unit_bytes):
    pct = 100.0 * cur / max(total, 1)
    (ta, ca), (tb, cb) = hist[0], hist[-1]
    rate = (cb - ca) / (tb - ta) if tb > ta else 0.0
    eta = fmt_time((total - cur) / rate) if rate > 0 and elapsed > 5 else "estimating..."
    s = f"{pct:.1f}% | {cur:,}/{total:,} {unit} | elapsed {fmt_time(elapsed)} | ETA {eta}"
    if unit_bytes and rate > 0:
        s += f" | {rate * unit_bytes / 1e6:.0f} MB/s"
    elif rate > 0:
        s += f" | {rate:.2f} {unit}/s"
    return s


def _relay(gen):
    """Turn run_pool events into (done, total, msg, status) for steps that log string results."""
    for d, t, res, st in gen:
        yield d, t, (res if isinstance(res, str) else None), st


def project_status(proj: Project) -> str:
    """One-line summary of what already exists in the work folder."""
    try:
        info = proj.info() if proj.info_path.exists() else None
    except Exception:
        info = None
    parts = []
    if info:
        n = info["ntiles"] * len(proj.channel_list())
        z0, z1 = proj.z_range()
        done = sum(cv.is_done(p, z0, z1, proj.ds) for p in proj.raw_dir.glob(".tile*.done"))
        parts.append(f"converted {done}/{n} tile-channels for z {z0}-{z1}")
        if proj.pp_dir.exists():
            parts.append(f"preprocessed {len(list(proj.pp_dir.glob('.tile*.done')))}/{n}"
                         f"{' (in use)' if proj.use_pp else ' (not in use)'}")
    else:
        parts.append("not inspected yet")
    parts.append("registered" if proj.reg_path.exists() else "not registered")
    for k in ("preview", "full"):
        parts.append(f"{k} {'ready' if (proj.fused_dir / fused_name(proj, k) / '.zattrs').exists() else '-'}"
                     f"{' (pp)' if proj.use_pp else ''}")
    return " | ".join(parts)


def already_done(key: str, proj: Project) -> str:
    """'done' if this step's output exists and matches the current settings, 'needs_register' if a
    fuse step has no registration to use, else ''. Convert has its own per-tile skipping."""
    try:
        reg = json.loads(proj.reg_path.read_text()) if proj.reg_path.exists() else None
        z0, z1 = proj.z_range()
        reg_ok = bool(reg) and (reg["z0"], reg["z1"], reg["ds"], reg["reg_channel"], reg.get("use_pp", False)) == (
            z0, z1, proj.ds, proj.reg_channel, proj.use_pp)
        if key == "register":
            return "done" if reg_ok else ""
        if key in ("preview", "full"):
            if not reg_ok:
                return "needs_register"
            marker = proj.fused_dir / fused_name(proj, key) / ".zattrs"
            fresh = marker.exists() and marker.stat().st_mtime >= proj.reg_path.stat().st_mtime
            return "done" if fresh else ""
    except Exception:
        pass
    return ""


def step_inspect(proj: Project, cancel):
    proj.save()
    info = inspect_czi(proj)
    yield 1, 1, (f"{info['ntiles']} tiles, {info['nc']} ch {info['channel_names']}, "
                 f"{info['nz']} z, px {info['px_xy_um']:.3f}/{info['px_z_um']:.3f} um (xy/z), "
                 f"overlap {info['overlap']}"), None


def step_convert(proj: Project, cancel):
    proj.save()
    info = proj.info()
    z0, z1 = proj.z_range()
    tasks = [dict(czi=proj.czi, tiles_dir=str(proj.raw_dir), m=t["m"], c=c, ds=proj.ds,
                  z0=z0, z1=z1, h=t["h"], w=t["w"], units=z1 - z0)
             for t in info["tiles"] for c in proj.channel_list()]
    todo = [t for t in tasks if not cv.is_done(cv.tile_paths(proj.raw_dir, t["m"], t["c"])[2], z0, z1, proj.ds)]
    skipped = len(tasks) - len(todo)
    if skipped:
        yield 0, 1, f"{skipped} of {len(tasks)} tile-channels already converted - skipping them", None
    if not todo:
        yield 1, 1, "Nothing to convert.", "100% | already converted"
        return
    t0 = info["tiles"][0]
    yield from _relay(run_pool(cv.convert_task, todo, proj.workers, cancel, unit="planes",
                               unit_bytes=t0["h"] * t0["w"] * 2, progress=True))


def fused_name(proj: Project, kind: str) -> str:
    """Fusions of preprocessed tiles go to *_pp.zarr so the raw-tile results are never overwritten."""
    return f"{kind}{'_pp' if proj.use_pp else ''}.zarr"


def pp_params(proj: Project) -> dict:
    from .preprocess import DEFAULT_PARAMS
    return {**DEFAULT_PARAMS, **(proj.pp or {})}


def step_preprocess(proj: Project, cancel):
    """Flat-field (estimated from the data) -> destripe -> optional deconvolution, per tile."""
    from . import preprocess as pp
    from .gpu import gpu_info
    info = proj.info()
    p = pp_params(proj)
    z0, z1 = proj.z_range()
    chans = proj.channel_list()
    # Measured: GPU gives no speedup for flat-field/destripe (disk + codec bound) but ~30x for deconvolution,
    # so the GPU is only used when deconvolution is ticked.
    use_gpu = bool(p["gpu"] and p["deconv"] and gpu_info())
    p["gpu"] = use_gpu
    if not any(p[k] for k in ("plane_gain", "flatfield", "destripe", "deconv")):
        yield 1, 1, "No preprocessing stage is ticked.", None
        return
    ffs = {}
    if p["flatfield"]:
        for c in chans:
            if cancel.is_set():
                raise Cancelled()
            fp = pp.flatfield_path(proj.work_dir, c)
            yield 0, 1, f"estimating flat-field for channel {c} ...", f"Estimating flat-field (channel {c})"
            raws = [cv.tile_paths(proj.raw_dir, t["m"], c)[0] for t in info["tiles"]]
            np.save(fp, pp.estimate_flatfield(raws, z0, z1, p["ff_smooth"]))
            ffs[c] = str(fp)
    t0 = info["tiles"][0]
    voxel = (info["px_z_um"], info["px_xy_um"], info["px_xy_um"])
    kernel = pp.make_kernel(p, info, voxel) if p["deconv"] else None
    floors = {}
    if p["plane_gain"]:
        for c in chans:
            floors[c] = p["pg_offset"] if p.get("pg_offset") is not None else pp.channel_floor(proj.raw_dir, [t["m"] for t in info["tiles"]], c)
        yield 0, 1, "camera floor (counts): " + ", ".join(f"channel {c}: {v:.0f}" for c, v in floors.items()), None
    tasks = [dict(m=t["m"], c=c, ds=proj.ds, z0=z0, z1=z1, params={**p, "pg_offset": floors.get(c, p.get("pg_offset"))},
                  raw_dir=str(proj.raw_dir), pp_dir=str(proj.pp_dir), ff_path=ffs.get(c),
                  kernel=None if kernel is None else kernel.tolist(), units=z1 - z0) for t in info["tiles"] for c in chans]
    workers = min(proj.workers, 4) if use_gpu else proj.workers
    yield 0, 1, (f"preprocessing {len(tasks)} tile-channels on {'GPU' if use_gpu else 'CPU'} "
                 f"({workers} workers): plane_gain={p['plane_gain']} flatfield={p['flatfield']} "
                 f"destripe={p['destripe']} deconv={p['deconv']}"), None
    yield from _relay(run_pool(pp.pp_task, tasks, workers, cancel, unit="planes",
                               unit_bytes=t0["h"] * t0["w"] * 2, progress=True))
    proj.use_pp = True
    proj.save()
    yield 1, 1, "Preprocessing complete. Register and fuse will now use the preprocessed tiles.", None


def step_register(proj: Project, cancel, min_q=0.2, max_shift=(40, 40, 40)):
    info = proj.info()
    ds = proj.ds
    z0, z1 = proj.z_range()
    nz = z1 - z0
    tiles = info["tiles"]
    assert [t["m"] for t in tiles] == list(range(len(tiles))), "non-contiguous tile indices"
    pairs = rg.build_pairs(proj, tiles, nz, ds, proj.reg_channel)
    for p in pairs:
        p.update(max_shift=max_shift, min_q=min_q)
    results = []
    for d, total, r, st in run_pool(rg.pair_shift, pairs, min(proj.workers, 6), cancel, unit="links"):
        if r is None:
            yield d, total, None, st
            continue
        results.append(r)
        yield d, total, (f"pair {r['i']}-{r['j']}: shift {np.round(r['shift'], 1).tolist()} "
                         f"q={r['q']:.2f} {'ok' if r['ok'] else 'REJECTED'}"), st
    D = rg.solve_global(len(tiles), results)
    x0 = min(t["x"] for t in tiles); y0 = min(t["y"] for t in tiles)
    off = np.array([[0, t["y"] - y0, t["x"] - x0] for t in tiles], float)
    off += np.column_stack([D[:, 0], D[:, 1] * ds, D[:, 2] * ds])
    off = np.round(off - off.min(axis=0)).astype(int)
    reg = dict(ds=ds, z0=z0, z1=z1, reg_channel=proj.reg_channel, use_pp=proj.use_pp,
               offsets={str(t["m"]): off[k].tolist() for k, t in enumerate(tiles)},
               edges=[dict(i=r["i"], j=r["j"], shift=r["shift"], q=r["q"], ok=r["ok"], used=r["used"]) for r in results])
    proj.reg_path.write_text(json.dumps(reg, indent=1))
    used = sum(r["used"] for r in results)
    yield 1, 1, f"registration saved: {used}/{len(results)} links used, max correction {np.abs(D * [1, ds, ds]).max():.0f} px", None


def step_fuse(proj: Project, cancel, kind="preview"):
    info = proj.info()
    reg = json.loads(proj.reg_path.read_text())
    ds = proj.ds
    preview = kind == "preview"
    chans = proj.channel_list()
    t0 = info["tiles"][0]
    f = ds if preview else 1
    nz = reg["z1"] - reg["z0"]
    tile_shape = (nz, t0["h"] // f, t0["w"] // f)
    offsets = {int(m): (o[0], int(round(o[1] / f)), int(round(o[2] / f))) for m, o in reg["offsets"].items()}
    ov = info["overlap"] or 0.2
    ramp = (max(1, int(t0["h"] * ov / f)), max(1, int(t0["w"] * ov / f)))
    cz, cy, cx = fu.canvas_shape(offsets, tile_shape)
    out = proj.fused_dir / fused_name(proj, kind)
    if (out / ".zattrs").exists():
        (out / ".zattrs").unlink()          # mark incomplete until the pyramid is finished
    chunks = (1, 32, 128, 128) if preview else (1, 32, 256, 256)
    fu.make_output(out, (len(chans), cz, cy, cx), chunks)
    blk = fu.blocks((cz, cy, cx), 64, 512 if preview else 1024)
    tasks = []
    for ci, c in enumerate(chans):
        paths = {m: str(cv.tile_paths(proj.tiles_dir, m, c)[1 if preview else 0]) for m in offsets}
        tasks += [dict(out=str(out), level0="0", c=ci, block=b, tiles=paths, offsets=offsets,
                       tile_shape=tile_shape, ramp=ramp) for b in blk]
    yield from _relay(run_pool(fu.fuse_task, tasks, proj.workers if not preview else min(proj.workers, 6),
                               cancel, unit="blocks"))
    voxel = (info["px_z_um"], info["px_xy_um"] * f, info["px_xy_um"] * f)
    factors = [(2, 2, 2)] * 4 if preview else [(1, 2, 2), (1, 2, 2), (2, 2, 2), (2, 2, 2)]
    names = [info["channel_names"][c] for c in chans]
    for k, nlev, shape in fu.build_pyramid(out, factors, voxel, names=names):
        if cancel.is_set():
            raise Cancelled()
        yield k, nlev, f"pyramid level {k}/{nlev} {shape}", f"Building pyramid level {k}/{nlev} (can take minutes for full-res)"
    yield 1, 1, f"{kind} fusion complete: {out}", None

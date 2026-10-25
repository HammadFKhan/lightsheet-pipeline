"""Self-contained checks (synthetic data, no microscope files needed).  Run:  python tests/test_core.py"""
import sys, tempfile, threading, shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import tifffile
import zarr
from numcodecs import Blosc

from lsp import denoise as dn, export as ex, fuse as fu, gpu, preprocess as pp

OK, BAD = "PASS", "FAIL"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{OK if cond else BAD}] {name} {detail}")


def make_low_zarr(path, nz=200, floor=205.0, dip=(100, 110), depth_left=0.5, depth_right=0.1):
    """Low-res tile with signal 150 above the camera floor and a multiplicative dip that is stronger on the left."""
    rng = np.random.default_rng(0)
    a = zarr.open(str(path), mode="w", shape=(nz, 300, 480), dtype="u2", chunks=(64, 150, 160))
    x = np.linspace(0, 1, 480)[None, :]
    for z in range(nz):
        f = 1.0
        if dip[0] <= z < dip[1]:
            ramp = np.exp(-((z - np.mean(dip)) ** 2) / (2 * 3.0 ** 2))
            f = 1 - ramp * (depth_left + (depth_right - depth_left) * x)
        sig = 150.0 * f * np.ones((300, 480)); sig[:, :40] = 0                 # 40 empty columns = camera floor only
        a[z] = (floor + sig + rng.normal(0, 2, (300, 480))).astype(np.uint16)


def test_plane_gains(tmp):
    p = tmp / "low.zarr"
    make_low_zarr(p)
    g, off = pp.plane_gains(p)
    check("camera floor estimated near truth", abs(off - 205) < 12, f"(got {off:.0f}, true 205)")
    # an all-tissue tile has no floor of its own: the channel-level estimate borrows it from the other tiles
    full = tmp / "tiles"; full.mkdir(exist_ok=True)
    from lsp import convert as cv
    for m in range(5):
        make_low_zarr(cv.tile_paths(full, m, 0)[1], nz=70) if m < 4 else None
    zt = zarr.open(str(cv.tile_paths(full, 4, 0)[1]), mode="w", shape=(70, 300, 480), dtype="u2", chunks=(64, 150, 160))
    zt[:] = np.full((70, 300, 480), 355, np.uint16)
    check("channel floor is robust to an all-tissue tile", abs(pp.channel_floor(full, range(5), 0) - 205) < 12,
          f"(got {pp.channel_floor(full, range(5), 0):.0f})")
    low = np.asarray(zarr.open(str(p), "r")[:]).astype(np.float32)
    params = {**pp.DEFAULT_PARAMS, "flatfield": False, "destripe": False, "deconv": False, "gpu": False}
    out = np.concatenate([pp.process_block(low[z:z + 64], params, None, None, False, g[z:z + 64], off) for z in range(0, 200, 64)]).astype(np.float32)
    sig = lambda v: (v - 205).mean(axis=(1, 2))
    dip_before = 1 - sig(low)[95:115].min() / np.median(sig(low)[:80])
    dip_after = 1 - sig(out)[95:115].min() / np.median(sig(out)[:80])
    check("plane dip flattened", dip_after < 0.25 * dip_before, f"(whole-plane dip {100 * dip_before:.0f}% -> {100 * dip_after:.0f}%)")
    left_b = 1 - low[105, :, :60].mean() / low[:80, :, :60].mean(); left_a = 1 - out[105, :, :60].mean() / out[:80, :, :60].mean()
    check("spatially varying dip corrected (strong side)", left_a < 0.3 * left_b, f"(left side {100 * left_b:.0f}% -> {100 * left_a:.0f}%)")
    untouched = np.abs(out[:80] - low[:80]).mean() / low[:80].mean()
    check("planes without a dip are left alone", untouched < 0.01, f"(mean change {100 * untouched:.2f}%)")


def test_tv_parity():
    rng = np.random.default_rng(1)
    base = np.zeros((16, 96, 96), np.float32); base[:, 20:70, 20:70] = 3.0
    v = base + rng.standard_normal(base.shape).astype(np.float32)
    ref = dn.tv(v, 0.26, iters=200)
    cpu = dn.tv_chambolle(v, 0.26, iters=200, use_gpu=False)
    check("TV numpy port matches skimage", np.abs(cpu - ref).max() < 0.1, f"(max diff {np.abs(cpu - ref).max():.3f} sigma)")
    if gpu.gpu_info():
        g = dn.tv_chambolle(v, 0.26, iters=200, use_gpu=True)
        check("TV GPU matches CPU", np.abs(g - cpu).max() < 1e-3, f"(max diff {np.abs(g - cpu).max():.1e}; {gpu.gpu_info()})")
    else:
        print("[SKIP] no GPU / CuPy - GPU parity not tested (everything still runs on CPU)")
    out = dn.tv_volume(np.tile(v, (1, 2, 2)), 1.0, 0.26, use_gpu=False, block=100, halo=16)
    one = dn.tv_volume(np.tile(v, (1, 2, 2)), 1.0, 0.26, use_gpu=False, block=4096)
    check("block-wise TV has no seams", np.abs(out - one).max() < 0.02, f"(max diff {np.abs(out - one).max():.1e})")


def test_destripe():
    rng = np.random.default_rng(2)
    img = rng.normal(300, 5, (4, 200, 300)).astype(np.float32)
    stripes = np.zeros(300, np.float32); stripes[rng.choice(300, 25, replace=False)] = rng.normal(0, 25, 25)
    noisy = img + stripes[None, None, :]
    p = {**pp.DEFAULT_PARAMS, "plane_gain": False, "flatfield": False, "deconv": False, "gpu": False}
    out = pp.process_block(noisy, p, None, None, False).astype(np.float32)
    col = lambda a: np.abs(a.mean(axis=1) - np.apply_along_axis(lambda r: np.convolve(r, np.ones(15) / 15, "same"), 1, a.mean(axis=1)))[:, 10:-10].mean()
    check("vertical stripes suppressed", col(out) < 0.4 * col(noisy), f"(stripe metric {col(noisy):.2f} -> {col(out):.2f})")


def test_deconvolution():
    rng = np.random.default_rng(3)
    v = (rng.random((12, 64, 64)) * 1000 + 200).astype(np.float32)
    delta = np.zeros((3, 3, 3), np.float32); delta[1, 1, 1] = 1
    out = pp.richardson_lucy(v, delta, 5, np, None)
    check("RL with a delta PSF returns the input", np.abs(out - (np.maximum(v, 0) + 1e-3)).max() < 1, "")
    if gpu.gpu_info():
        import cupy as cp
        k = np.ones((3, 3, 3), np.float32) / 27
        a = pp.richardson_lucy(v, k, 5, np, None); b = cp.asnumpy(pp.richardson_lucy(cp.asarray(v), k, 5, cp, None))
        check("RL GPU matches CPU", np.abs(a - b).max() < 0.5, f"(max diff {np.abs(a - b).max():.2e})")


def test_export(tmp):
    path = tmp / "vol.zarr"
    fu.make_output(path, (2, 40, 128, 128), chunks=(1, 32, 64, 64))
    g = zarr.open_group(str(path), "r+")
    rng = np.random.default_rng(4)
    vol = (rng.random((2, 40, 128, 128)) * 800 + 207).astype(np.uint16)
    g["0"][:] = vol
    list(fu.build_pyramid(path, [(1, 2, 2), (2, 2, 2)], (7.32, 2.54, 2.54), names=["A", "B"]))
    o = ex.ExportOptions(zarr_path=str(path), out_dir=str(tmp / "out"), stem="t", level=1, channels=[0, 1],
                         channel_names=["A", "B"], bit8=True, compression="zlib")
    list(ex.export_iter(o, threading.Event()))
    f = tmp / "out" / "t_A.tif"
    with tifffile.TiffFile(f) as tf:
        check("export writes a BigTIFF stack", tf.is_bigtiff and len(tf.pages) == 40 and tf.pages[0].dtype == np.uint8,
              f"({len(tf.pages)} pages, {tf.pages[0].shape})")
    check("export leaves no .partial files", not list((tmp / "out").glob("*.partial")))
    o2 = ex.ExportOptions(zarr_path=str(path), out_dir=str(tmp / "out2"), stem="tv", level=0, channels=[0], channel_names=["A", "B"],
                          bit8=False, compression="zlib", tv_weight=0.26, use_gpu=False)
    list(ex.export_iter(o2, threading.Event()))
    a = tifffile.imread(tmp / "out2" / "tv_A.tif")
    check("TV export keeps shape/dtype (16-bit)", a.shape == (40, 128, 128) and a.dtype == np.uint16)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="lsp_test_"))
    try:
        print("GPU:", gpu.gpu_info() or "none (CPU only)")
        test_plane_gains(tmp); test_tv_parity(); test_destripe(); test_deconvolution(); test_export(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed" + (f"; FAILED: {bad}" if bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

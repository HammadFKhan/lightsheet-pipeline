"""Synthetic ground-truth benchmark: real-data noise model + embedded features of several sizes.

Noise: Poisson-Gaussian fitted on real preprocessed tissue (2nd-difference MAD per brightness bin),
plus the mild z-correlation (~0.15) measured in the data. Features: point-like (sigma 0.6 px), medium (1.2),
coarse (2.0), close pairs, lines, an edge. Each method is scored over several noise realisations.
"""
import os
from pathlib import Path
import time, json, csv, sys
import numpy as np, zarr
from scipy import ndimage as ndi
from scipy.optimize import curve_fit
from scipy.special import erf
from lsp import denoise as dn

W = os.path.join(os.environ["LSP_WORK"], "fused", "full_pp.zarr")
OUT = str(Path(__file__).resolve().parent / "results"); os.makedirs(OUT, exist_ok=True)
OFF = 207.0
SEEDS = (11, 12, 13, 14)
SHAPE = (33, 288, 256)


def fit_noise_model():
    """var = b + a*(I-OFF), fitted jointly on three real crops (tissue x2, dark background); bins above
    ~450 counts are excluded because bright textured tissue inflates 2nd-difference noise estimates."""
    g = zarr.open_group(W, "r")["0"]
    crops = [g[0, 300:364, 3200:3456, 1700:2200], g[0, 100:900, 3300:3340, 1500:3000][60:-60], g[0, 500:520, 5000:5200, 600:1000]]
    xs, ys = [], []
    for c in crops:
        v = np.asarray(c).astype(np.float32)
        d2 = v[:, :, 2:] - 2 * v[:, :, 1:-1] + v[:, :, :-2]
        mm = ndi.gaussian_filter(v, 1.0)[:, :, 1:-1]
        qs = np.percentile(mm, np.linspace(2, 98, 9))
        for lo, hi in zip(qs[:-1], qs[1:]):
            sel = (mm >= lo) & (mm < hi)
            if mm[sel].mean() > 450:
                continue
            dd = d2[sel]
            xs.append(mm[sel].mean() - OFF); ys.append((1.4826 * np.median(np.abs(dd - np.median(dd))) / np.sqrt(6)) ** 2)
    a, b = np.polyfit(xs, ys, 1)
    return float(max(a, 0.0)), float(max(b, 1.0))


def make_clean(L=115.0):                 # typical tissue ~320 counts (offset 207 + 115)
    Z, Y, X = SHAPE
    zc = Z // 2
    zp = np.exp(-((np.arange(Z) - zc) ** 2) / (2 * 0.45 ** 2))
    yy, xx = np.mgrid[0:Y, 0:X].astype(np.float32)
    feat = np.zeros((Y, X), np.float32)
    info = dict(zc=zc, classes={}, pairs=[])

    def blob(cy, cx, A, s):
        nonlocal feat
        feat += A * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s))
    ys = [18, 56, 94, 132, 170, 208]
    classes = {"fine A60 (s0.6)": (0.6, 60.0, ys, (20, 52, 84)),
               "medium A40 (s1.2)": (1.2, 40.0, [y + 19 for y in ys], (20, 52, 84)),
               "coarse A25 (s2.0)": (2.0, 25.0, ys, (116, 148, 180)),
               "fine A25 (s0.6)": (0.6, 25.0, [y + 19 for y in ys], (116, 148, 180))}
    for name, (s, A, yl, xl) in classes.items():
        info["classes"][name] = dict(s=s, A=A, centers=[(y, x) for y in yl for x in xl])
        for y in yl:
            for x in xl:
                blob(y, x, A, s)
    for k in range(6):                                           # pairs, sigma 0.9, separation 3.0 px
        y = 14 + 17 * k
        blob(y, 222 - 1.5, 60.0, 0.9); blob(y, 222 + 1.5, 60.0, 0.9); info["pairs"].append((y, 222))

    def line(p0, p1, A, s=0.8):
        nonlocal feat
        n = int(np.hypot(p1[0] - p0[0], p1[1] - p0[1]) * 2)
        for t in np.linspace(0, 1, n):
            cy, cx = p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1])
            feat = np.maximum(feat, A * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s)))
    line((146, 196), (146, 246), 40.0); line((156, 222), (206, 222), 40.0)
    info["line_h"] = (146, 196, 246)
    edge = np.zeros((Y, X), np.float32); edge[250:286, 128:] = 60.0
    edge = ndi.gaussian_filter(edge, 0.8)
    feat += edge
    clean = OFF + L + zp[:, None, None] * feat[None]
    fm = ndi.binary_dilation(feat > 0.5, iterations=8)
    fm |= ndi.binary_dilation(np.abs(np.gradient(edge, axis=1)) > 0.5, iterations=8)
    info["empty"] = ~fm
    return clean.astype(np.float32), info


def noisy_from(clean, a, b, seed):
    rg = np.random.default_rng(seed)
    sig = np.sqrt(np.maximum(a * (clean - OFF) + b, 1.0))
    n = rg.standard_normal(clean.shape).astype(np.float32)
    n = np.sqrt(1 - 0.15 ** 2) * n + 0.15 * np.roll(n, 1, axis=0)
    return (clean + sig * n).astype(np.float32)


def fwhm(profile):
    p = np.asarray(profile, float); p = p - p.min()
    pk = p.max(); half = pk / 2
    i = int(np.argmax(p)); l = i
    while l > 0 and p[l] > half: l -= 1
    r = i
    while r < len(p) - 1 and p[r] > half: r += 1
    xl = l + (half - p[l]) / (p[l + 1] - p[l]) if p[l + 1] != p[l] else l
    xr = r - 1 + (p[r - 1] - half) / (p[r - 1] - p[r]) if p[r - 1] != p[r] else r
    return xr - xl


def edge_width(profile):
    x = np.arange(len(profile), dtype=float)
    f = lambda x, lo, hi, x0, s: lo + (hi - lo) * 0.5 * (1 + erf((x - x0) / (np.sqrt(2) * abs(s) + 1e-6)))
    try:
        p, _ = curve_fit(f, x, profile, p0=[profile[:8].mean(), profile[-8:].mean(), len(x) / 2, 1.0], maxfev=4000)
        return 2.563 * abs(p[3])
    except Exception:
        return float("nan")


def metrics(den, clean, info):
    zc = info["zc"]; emp = info["empty"]
    m = {"noise_sd": float(den[zc][emp].std()),
         "rmse": float(np.sqrt(((den[zc - 1:zc + 2] - clean[zc - 1:zc + 2]) ** 2).mean()))}
    ring = np.hypot(*np.mgrid[-9:10, -9:10])
    for cname, c in info["classes"].items():
        pk, cut = [], []
        for (y, x) in c["centers"]:
            sub = den[zc, y - 9:y + 10, x - 9:x + 10]
            bg = sub[(ring > 6) & (ring < 9)].mean()
            pk.append(sub[9, 9] - bg); cut.append(sub - bg)
        m["ret " + cname] = float(np.mean(pk) / c["A"])
        if cname.startswith("fine A60"):
            cm = np.mean(cut, 0); m["fwhm_xy fine"] = float((fwhm(cm[9, :]) + fwhm(cm[:, 9])) / 2)
            zp = np.mean([den[zc - 4:zc + 5, y, x] - den[zc - 4:zc + 5, y - 9:y + 10, x - 9:x + 10].reshape(9, -1).mean(1)
                          for (y, x) in c["centers"]], axis=0)
            m["fwhm_z fine"] = float(fwhm(zp))
    prof = np.mean([den[zc, y, 222 - 5:222 + 6] for (y, _) in info["pairs"]], axis=0)
    m["pair_dip"] = float(max(1 - prof[5] / max(prof[3:5].max(), prof[6:8].max()), 0))
    y, x0, x1 = info["line_h"]
    m["line_ret"] = float((den[zc, y, x0 + 5:x1 - 5].mean() - den[zc, y - 8:y - 4, x0 + 5:x1 - 5].mean()) / 40.0)
    m["edge_w"] = float(edge_width(den[zc, 252:284, 100:160].mean(0)))
    return m


def method_table(S, use_gpu=True):
    M = {"none": lambda v: v}
    for s in (0.4, 0.6, 0.8):
        M[f"gauss xy s={s}"] = lambda v, s=s: dn.gaussian(v, (0, s, s), use_gpu)
    M["gauss 3D z=0.3 xy=0.6"] = lambda v: dn.gaussian(v, (0.3, 0.6, 0.6), use_gpu)
    M["gauss z only s=0.5"] = lambda v: dn.gaussian(v, (0.5, 0, 0), use_gpu)
    M["median 1x3x3"] = lambda v: dn.median(v, (1, 3, 3), use_gpu)
    M["median 3x3x3"] = lambda v: dn.median(v, (3, 3, 3), use_gpu)
    for it, kf in ((4, 2.5), (8, 2.5), (8, 5.0)):
        M[f"aniso-diff it={it} k={kf}s"] = lambda v, it=it, kf=kf: dn.perona_malik(v, it, kf * S, 0.12, (2.88, 1, 1), use_gpu)
    for w in (0.1, 0.2, 0.35):
        M[f"TV w={w}"] = lambda v, w=w: dn.tv(v / S, w) * S
    M["wavelet BayesShrink db2"] = lambda v: dn.wavelet(v)
    for h in (0.4, 0.7):
        M[f"NLM h={h}s"] = lambda v, h=h: dn.nlm(v, S, h)
    return M


def main():
    a, b = fit_noise_model()
    print(f"noise model (real data): var = {a:.3f}*(I-{OFF:.0f}) + {b:.1f}  -> sigma at I=322 = {np.sqrt(a * (322 - OFF) + b):.1f} counts "
          f"(real tissue measured 15-24; dark background 5.4)", flush=True)
    clean, info = make_clean()
    noisies = [noisy_from(clean, a, b, s) for s in SEEDS]
    S = float(np.mean([dn.estimate_noise_sigma(n) for n in noisies]))
    print(f"phantom {clean.shape}, {len(SEEDS)} noise realisations, estimated sigma {S:.1f}", flush=True)
    ref = metrics(clean, clean, info)
    print("ground truth (noise-free):", {k: round(float(v), 2) for k, v in ref.items()}, flush=True)
    rows = []
    for name, fn in method_table(S).items():
        t = time.time()
        ms = [metrics(fn(n), clean, info) for n in noisies]
        m = {k: float(np.nanmean([x[k] for x in ms])) for k in ms[0]}
        m["method"] = name; m["sec"] = round((time.time() - t) / len(SEEDS), 1)
        rows.append(m)
        print(f"{name:26s} noise {m['noise_sd']:5.1f} rmse {m['rmse']:5.1f} | peak kept: fine60 {100 * m['ret fine A60 (s0.6)']:4.0f}% "
              f"med40 {100 * m['ret medium A40 (s1.2)']:4.0f}% coarse25 {100 * m['ret coarse A25 (s2.0)']:4.0f}% fine25 {100 * m['ret fine A25 (s0.6)']:4.0f}% | "
              f"fwhm xy {m['fwhm_xy fine']:.2f} z {m['fwhm_z fine']:.2f} | pair {m['pair_dip']:.2f} line {100 * m['line_ret']:3.0f}% edge {m['edge_w']:.2f} ({m['sec']}s)", flush=True)
    keys = ["method"] + [k for k in rows[0] if k not in ("method",)]
    with open(OUT + r"\benchmark.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); [w.writerow(r) for r in rows]
    np.save(OUT + r"\phantom_clean.npy", clean); np.save(OUT + r"\phantom_noisy.npy", noisies[0])
    json.dump(dict(a=a, b=b, S=S, ref={k: float(v) for k, v in ref.items()}), open(OUT + r"\bench_meta.json", "w"))


if __name__ == "__main__":
    main()

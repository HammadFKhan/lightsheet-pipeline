import os
from pathlib import Path
import numpy as np, zarr
from scipy import ndimage as ndi
from PIL import Image

W = os.path.join(os.environ["LSP_WORK"], "fused")
OUT = str(Path(__file__).resolve().parent / "results"); os.makedirs(OUT, exist_ok=True)


def mad_sigma(d):
    return 1.4826 * np.median(np.abs(d - np.median(d)))


def main():
    for name in ("full", "full_pp"):
        g = zarr.open_group(f"{W}\\{name}.zarr", "r")
        L0, L2 = g["0"], g["2"]
        print(f"=== {name}: level0 {L0.shape}, level2 {L2.shape}")
        # --- 1. plane-to-plane variation at z resolution (level 2 keeps every z plane) ---
        nz = L2.shape[1]
        roi = L2[0, :, 500:1200, 300:900]                       # tissue-rich ROI at 10 um xy
        med = np.median(roi.reshape(nz, -1), axis=1).astype(float)
        p20 = np.percentile(roi.reshape(nz, -1), 20, axis=1).astype(float)
        for lab, s in (("median", med), ("20th pct", p20)):
            sm = ndi.gaussian_filter1d(s, 6)
            r = s / np.maximum(sm, 1)
            core = slice(30, nz - 30)
            print(f"  plane-to-plane variation ({lab}): std of (plane / smoothed) = {100 * r[core].std():.2f}%  "
                  f"| p5-p95 range {100 * (np.percentile(r[core], 95) - np.percentile(r[core], 5)):.2f}%")
        # --- 2. coronal noise anisotropy at full resolution (robust, MAD of 2nd differences) ---
        y0, y1, x0, x1 = 3300, 3340, 1500, 3000
        v = np.asarray(L0[0, :, y0:y1, x0:x1]).astype(np.float32)   # (z, y, x)
        z_ok = slice(60, v.shape[0] - 60)
        v = v[z_ok]
        d2 = lambda a, ax: np.diff(a, n=2, axis=ax)
        s_z, s_y, s_x = (mad_sigma(d2(v, ax)) / np.sqrt(6) for ax in (0, 1, 2))
        print(f"  noise sigma (MAD, 2nd diff) along z={s_z:.2f}  y={s_y:.2f}  x={s_x:.2f}   -> z/x = {s_z / s_x:.2f}, z/y = {s_z / s_y:.2f}")
        # correlation of adjacent-plane / adjacent-pixel high-pass residual
        hp = v - ndi.uniform_filter(v, 5)
        cz = np.corrcoef(hp[:-1].ravel(), hp[1:].ravel())[0, 1]
        cx = np.corrcoef(hp[..., :-1].ravel(), hp[..., 1:].ravel())[0, 1]
        cy = np.corrcoef(hp[:, :-1].ravel(), hp[:, 1:].ravel())[0, 1]
        print(f"  residual correlation between neighbours: z={cz:.2f}  y={cy:.2f}  x={cx:.2f}")
        # row (per-z) mean profile inside this coronal slab -> banding amplitude vs local noise
        rowmean = v.mean(axis=(1, 2))
        band = (rowmean - ndi.gaussian_filter1d(rowmean, 4)).std()
        print(f"  per-plane mean fluctuation inside coronal slab: {band:.2f} counts (typical pixel noise {s_z:.1f})")
        # --- 3. images: coronal (x vs z) and horizontal crops, same contrast ---
        cor = np.asarray(L0[0, 300:700, 3320, 1800:2600]).astype(np.float32)          # (z, x)
        hor = np.asarray(L0[0, 500, 3000:3600, 1800:2600]).astype(np.float32)         # (y, x)
        lo, hi = np.percentile(cor, [1, 99.7])
        for tag, a, zstretch in (("coronal", cor, 7.32 / 2.54), ("horizontal", hor, 1.0)):
            img = (np.clip((a - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
            im = Image.fromarray(img)
            if zstretch != 1.0:
                im = im.resize((im.width, int(im.height * zstretch)), Image.NEAREST)
            im.save(f"{OUT}\\diag_{name}_{tag}.png")
    print("saved diagnostic images")


if __name__ == "__main__":
    main()

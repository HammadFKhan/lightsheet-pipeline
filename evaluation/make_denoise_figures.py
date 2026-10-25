"""Denoiser comparison figures for the guide (coronal and horizontal-zoom montages), on real preprocessed tile data.
Usage: python make_denoise_figures.py <work folder> <output folder>"""
import os
import json, os, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from lsp import preprocess as pp, denoise as dn, convert as cv

WORK = sys.argv[1] if len(sys.argv) > 1 else os.environ["LSP_WORK"]
OUT = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).resolve().parents[1] / "docs" / "images")
FONT = ImageFont.load_default(size=20)


def tile(a, lo, hi, zstretch=1.0, scale=1.0, text=""):
    im = Image.fromarray((np.clip((a - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8))
    im = im.resize((int(im.width * scale), int(im.height * scale * zstretch)), Image.NEAREST).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, d.textlength(text, font=FONT) + 14, 30], fill=(0, 0, 0)); d.text((7, 4), text, fill=(255, 255, 255), font=FONT)
    return im


def montage(ims, ncol, gap=8):
    w, h = ims[0].size
    rows = (len(ims) + ncol - 1) // ncol
    M = Image.new("RGB", (ncol * w + gap * (ncol - 1), rows * h + gap * (rows - 1)), (255, 255, 255))
    for i, t in enumerate(ims):
        M.paste(t, ((i % ncol) * (w + gap), (i // ncol) * (h + gap)))
    return M


def main():
    info = json.load(open(f"{WORK}\\info.json"))
    W = Path(WORK)
    m, c = 10, 0
    z0, z1, y0, y1, x0, x1 = 380, 520, 300, 900, 500, 1100
    low_p = cv.tile_paths(W / "tiles", m, c)[1]
    p = {**pp.DEFAULT_PARAMS, "gpu": True}
    gains, off = pp.plane_gains(low_p, pp.channel_floor(W / "tiles", [t["m"] for t in info["tiles"]], c))
    _, out = pp.preview_region(cv.tile_paths(W / "tiles", m, c)[0], p, np.load(W / f"flatfield_c{c}.npy"), None, z0, z1, y0, y1, x0, x1, gains, off)
    out = out.astype(np.float32)
    S = dn.estimate_tissue_sigma(out, off)
    print("tissue noise sigma", round(S, 1))
    V = {"Preprocessed (no denoise)": out,
         "Gaussian xy s=0.45 (1.35x)": dn.gaussian(out, (0, 0.45, 0.45), True),
         "Gaussian xy s=0.6 (2x)": dn.gaussian(out, (0, 0.6, 0.6), True),
         "TV w=0.15 (light)": dn.tv_volume(out, S, 0.15),
         "TV w=0.26 (medium, 2x)": dn.tv_volume(out, S, 0.26),
         "TV w=0.34 (strong, 2.8x)": dn.tv_volume(out, S, 0.34)}
    zst = info["px_z_um"] / info["px_xy_um"]
    lo, hi = np.percentile(out, [1, 99.7])
    montage([tile(v[:, 300, :], lo, hi, zst, 1.0, n) for n, v in V.items()], 2).save(f"{OUT}\\fig_denoise_coronal.png")
    iz = 70
    montage([tile(v[iz, 330:450, 190:370], lo, hi, 1.0, 3.0, n) for n, v in V.items()], 2).save(f"{OUT}\\fig_denoise_zoom.png")
    print("saved denoise figures")


if __name__ == "__main__":
    main()

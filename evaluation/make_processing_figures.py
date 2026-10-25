"""Before/after figures for the guide: raw tile region -> preprocessed -> + TV display denoise.
Usage: python make_processing_figures.py <work folder> <output folder>"""
import os
from pathlib import Path
import json, os, sys
import numpy as np, zarr
from PIL import Image, ImageDraw, ImageFont
from lsp import preprocess as pp, denoise as dn, convert as cv, gpu

WORK = sys.argv[1] if len(sys.argv) > 1 else os.environ["LSP_WORK"]
OUT = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).resolve().parents[1] / "docs" / "images")
FONT = ImageFont.load_default(size=20)
os.makedirs(OUT, exist_ok=True)


def tile_img(a, lo, hi, zstretch=1.0, scale=1.0):
    im = Image.fromarray((np.clip((a - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8))
    w, h = int(im.width * scale), int(im.height * scale * zstretch)
    return im.resize((w, h), Image.NEAREST if scale >= 1 else Image.BICUBIC).convert("RGB")


def label(im, text):
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, d.textlength(text, font=FONT) + 14, 30], fill=(0, 0, 0))
    d.text((7, 4), text, fill=(255, 255, 255), font=FONT)
    return im


def row(panels, names, lo, hi, zstretch=1.0, scale=1.0, gap=8):
    ims = [label(tile_img(p, lo, hi, zstretch, scale), n) for p, n in zip(panels, names)]
    W = sum(i.width for i in ims) + gap * (len(ims) - 1)
    out = Image.new("RGB", (W, max(i.height for i in ims)), (255, 255, 255))
    x = 0
    for i in ims:
        out.paste(i, (x, 0)); x += i.width + gap
    return out


def main():
    run(10, "")
    run(7, "_tile7")


def run(m, sfx):
    info = json.load(open(f"{WORK}\\info.json"))
    c = 0
    z0, z1, y0, y1, x0, x1 = 380, 520, 300, 900, 500, 1100
    raw_p = cv.tile_paths(f"{WORK}\\tiles", m, c)[0] if False else cv.tile_paths(__import__("pathlib").Path(WORK) / "tiles", m, c)[0]
    low_p = cv.tile_paths(__import__("pathlib").Path(WORK) / "tiles", m, c)[1]
    p = {**pp.DEFAULT_PARAMS, "gpu": True}
    ff = np.load(f"{WORK}\\flatfield_c{c}.npy")
    gains, off = pp.plane_gains(low_p, pp.channel_floor(Path(WORK) / "tiles", [t["m"] for t in info["tiles"]], c))
    raw, out = pp.preview_region(raw_p, p, ff, None, z0, z1, y0, y1, x0, x1, gains, off)
    sig = dn.estimate_tissue_sigma(out, off)
    tv = np.clip(dn.tv_volume(out.astype(np.float32), sig, 0.26, True), 0, 65535)
    print(f"region {raw.shape}; camera floor {off:.0f}; tissue noise sigma {sig:.1f}; plane gains in range {gains[z0:z1].min():.2f}-{gains[z0:z1].max():.2f}")
    zst = info["px_z_um"] / info["px_xy_um"]
    lo, hi = np.percentile(raw, [1, 99.7])
    names = ["Raw tile", "+ plane-gain, flat-field, destripe", "+ TV display denoise"]
    # coronal (x-z) slice and a zoomed crop of it
    iy = 300
    cor = [a[:, iy, :] for a in (raw, out, tv)]
    row(cor, names, lo, hi, zstretch=zst, scale=1.0).save(f"{OUT}\\fig_processing_coronal{sfx}.png")
    if sfx:
        pr, po = raw.reshape(raw.shape[0], -1).mean(1), out.reshape(out.shape[0], -1).mean(1)
        print(f"tile {m}: raw dip depth {pr[60:75].min() - np.median(pr[85:110]):+.0f} vs corrected {po[60:75].min() - np.median(po[85:110]):+.0f}; "
              f"gains {gains[z0:z1].min():.2f}-{gains[z0:z1].max():.2f}")
        return
    # horizontal (x-y) plane
    iz = 70
    hor = [a[iz] for a in (raw, out, tv)]
    row(hor, names, lo, hi, scale=1.0).save(f"{OUT}\\fig_processing_horizontal.png")
    # horizontal zoom (pixel scale) to see resolution
    zoom = [a[iz, 330:450, 190:370] for a in (raw, out, tv)]
    row(zoom, names, lo, hi, scale=3.0).save(f"{OUT}\\fig_processing_zoom.png")
    # plane brightness profile: mean per plane in the region (raw vs corrected) - the dip fix
    pr, po = raw.reshape(raw.shape[0], -1).mean(1), out.reshape(out.shape[0], -1).mean(1)
    print(f"mean brightness change outside the dips (planes {z0}-{z0 + 25}, {z0 + 90}-{z1}): "
          f"{np.concatenate([po[:25] - pr[:25], po[90:] - pr[90:]]).mean():+.1f} counts; at the dip (plane 447): {po[67] - pr[67]:+.1f}; "
          f"raw dip depth {pr[60:75].min() - np.median(pr[85:110]):+.0f} vs corrected {po[60:75].min() - np.median(po[85:110]):+.0f}")
    W_, H_ = 900, 360
    im = Image.new("RGB", (W_, H_), (255, 255, 255)); d = ImageDraw.Draw(im)
    allv = np.concatenate([pr, po]); vmin, vmax = allv.min() - 5, allv.max() + 5
    def pt(i, v):
        return (60 + i / (len(pr) - 1) * (W_ - 90), H_ - 50 - (v - vmin) / (vmax - vmin) * (H_ - 90))
    for arr, col in ((pr, (200, 40, 40)), (po, (30, 110, 200))):
        d.line([pt(i, v) for i, v in enumerate(arr)], fill=col, width=3)
    d.line([(60, H_ - 50), (W_ - 30, H_ - 50)], fill=(0, 0, 0), width=1); d.line([(60, 30), (60, H_ - 50)], fill=(0, 0, 0), width=1)
    d.text((70, 8), "Mean brightness of each z-plane (tile 10)", fill=(0, 0, 0), font=FONT)
    d.text((W_ - 330, 40), "raw: dip at plane 447", fill=(200, 40, 40), font=FONT)
    d.text((W_ - 330, 66), "after plane-gain correction", fill=(30, 110, 200), font=FONT)
    d.text((60, H_ - 40), f"z plane {z0}", fill=(0, 0, 0), font=FONT); d.text((W_ - 140, H_ - 40), f"{z1}", fill=(0, 0, 0), font=FONT)
    d.text((6, 30), f"{vmax:.0f}", fill=(0, 0, 0), font=FONT); d.text((6, H_ - 80), f"{vmin:.0f}", fill=(0, 0, 0), font=FONT)
    im.save(f"{OUT}\\fig_plane_brightness.png")
    print("saved processing figures")


if __name__ == "__main__":
    main()

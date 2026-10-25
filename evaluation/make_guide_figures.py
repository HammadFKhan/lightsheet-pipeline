"""Figures for Lightsheet_Guide.md, generated from a processed work folder.
Usage:  python make_guide_figures.py <work_folder> <output_folder>
"""
import os
from pathlib import Path
import json, os, sys
import numpy as np, zarr
from PIL import Image, ImageDraw, ImageFont

WORK = sys.argv[1] if len(sys.argv) > 1 else os.environ["LSP_WORK"]
OUT = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).resolve().parents[1] / "docs" / "images")
os.makedirs(OUT, exist_ok=True)
FONT = ImageFont.load_default(size=20)
FONT_S = ImageFont.load_default(size=15)


def norm(a, lo=None, hi=None, plo=1, phi=99.7, gamma=1.0):
    a = a.astype(np.float32)
    if lo is None:
        lo, hi = np.percentile(a[a > 0] if (a > 0).any() else a, [plo, phi])
    return (np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1) ** gamma * 255).astype(np.uint8)


def label(im, text, size=FONT):
    d = ImageDraw.Draw(im)
    w = d.textlength(text, font=size)
    d.rectangle([0, 0, w + 14, size.size + 10], fill=(0, 0, 0))
    d.text((7, 4), text, fill=(255, 255, 255), font=size)
    return im


def taper(n, r):
    i = np.arange(n, dtype=np.float32)
    return np.minimum(1.0, np.minimum(i + 0.5, n - i - 0.5) / max(r, 1))


def blend(tiles, offsets, th, tw, ry, rx):
    H = max(o[0] for o in offsets.values()) + th; Wd = max(o[1] for o in offsets.values()) + tw
    num = np.zeros((H, Wd), np.float32); den = np.zeros_like(num)
    w = taper(th, ry)[:, None] * taper(tw, rx)[None]
    for m, (oy, ox) in offsets.items():
        num[oy:oy + th, ox:ox + tw] += tiles[m].astype(np.float32) * w
        den[oy:oy + th, ox:ox + tw] += w
    return np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0)


def main():
    info = json.load(open(f"{WORK}\\info.json"))
    reg = json.load(open(f"{WORK}\\registration.json"))
    ds = reg["ds"]
    tiles = {t["m"]: t for t in info["tiles"]}
    x0 = min(t["x"] for t in tiles.values()); y0 = min(t["y"] for t in tiles.values())
    nominal = {m: ((t["y"] - y0) // ds, (t["x"] - x0) // ds) for m, t in tiles.items()}
    regd = {int(m): (o[1] // ds, o[2] // ds) for m, o in reg["offsets"].items()}
    zoff = {int(m): o[0] for m, o in reg["offsets"].items()}
    th, tw = tiles[0]["h"] // ds, tiles[0]["w"] // ds
    ry, rx = int(th * 0.2), int(tw * 0.2)

    # ---- Fig 2: tile layout on the stitched preview plane ----
    g = zarr.open_group(f"{WORK}\\fused\\preview.zarr", "r")
    prev = g["0"]
    zc = 500
    plane = np.asarray(prev[0, zc])
    im = Image.fromarray(norm(plane, plo=2, phi=99.8, gamma=0.8)).convert("RGB")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for m, (oy, ox) in regd.items():
        row, col = divmod(m, 3)
        c = (255, 120, 60) if (row + col) % 2 == 0 else (60, 170, 255)
        d.rectangle([ox, oy, ox + tw - 1, oy + th - 1], fill=c + (46,), outline=c + (255,), width=2)
    im = Image.alpha_composite(im.convert("RGBA"), ov)
    d = ImageDraw.Draw(im)
    for m, (oy, ox) in regd.items():
        d.text((ox + tw // 2 - 8, oy + th // 2 - 8), str(m), fill=(255, 255, 255, 255), font=FONT)
    im = label(im.convert("RGB"), "21 tiles (3 x 7); where two colours overlap = the ~20% overlap that is registered")
    im.thumbnail((1000, 1400)); im.save(f"{OUT}\\fig_tile_layout.png")

    # ---- Fig 3: red/green overlay of the overlap strip of the pair with the largest registration correction ----
    used = [e for e in reg["edges"] if e["used"] and e["q"] > 0.85]
    e = max(used, key=lambda e: np.hypot(e["shift"][1], e["shift"][2]))
    i, j = e["i"], e["j"]
    zz = 500
    lows = {m: zarr.open(f"{WORK}\\tiles\\tile{m:02d}_c0_low.zarr", "r") for m in (i, j)}

    def strip(offs, zi, zj):
        (yi, xi), (yj, xj) = offs[i], offs[j]
        oy0, oy1 = max(yi, yj), min(yi, yj) + th; ox0, ox1 = max(xi, xj), min(xi, xj) + tw
        A = np.asarray(lows[i][zi])[oy0 - yi:oy1 - yi, ox0 - xi:ox1 - xi].astype(np.float32)
        B = np.asarray(lows[j][zj])[oy0 - yj:oy1 - yj, ox0 - xj:ox1 - xj].astype(np.float32)
        return A, B

    zi_n = zj_n = zz
    A0, B0 = strip(nominal, zi_n, zj_n)
    A1, B1 = strip(regd, zz - zoff[i], zz - zoff[j])
    lo, hi = np.percentile(np.concatenate([A1.ravel(), B1.ravel()]), [2, 99.8])

    def overlay(A, B):
        r = norm(A, lo, hi); g = norm(B, lo, hi)
        return np.dstack([r, g, np.minimum(r, g)])
    panels = []
    for (A, B), name in (((A0, B0), "Stage positions only"), ((A1, B1), "After registration")):
        o = Image.fromarray(overlay(A, B)).resize((A.shape[1] * 4, A.shape[0] * 4), Image.BICUBIC)
        panels.append(label(o, name))
    horizontal_strip = panels[0].width > panels[0].height
    fw = max(p.width for p in panels) * (1 if horizontal_strip else 2) + (0 if horizontal_strip else 10)
    fh = sum(p.height for p in panels) + 10 if horizontal_strip else max(p.height for p in panels)
    fig = Image.new("RGB", (fw, fh), (255, 255, 255))
    if horizontal_strip:
        fig.paste(panels[0], (0, 0)); fig.paste(panels[1], (0, panels[0].height + 10))
    else:
        fig.paste(panels[0], (0, 0)); fig.paste(panels[1], (panels[0].width + 10, 0))
    fig = label(fig, f"Tiles {i} (red) and {j} (green), overlap strip. Aligned = yellow/white", FONT_S) if False else fig
    fig.save(f"{OUT}\\fig_registration_before_after.png")
    print(f"registration overlay: tiles {i}-{j}, correction (z,y,x lowres px) {np.round(e['shift'], 1).tolist()} q={e['q']:.2f}; "
          f"= {np.round(np.array(e['shift'][1:]) * ds).tolist()} px at full resolution; strip {A0.shape}")

    # ---- Fig 4: final stitched plane (both channels), no seams ----
    pl1 = np.asarray(prev[0, 520]); pl2 = np.asarray(prev[1, 520]) if prev.shape[0] > 1 else None
    imgs = [label(Image.fromarray(norm(p, plo=2, phi=99.8, gamma=0.8)).convert("RGB"), n) for p, n in ((pl1, info["channel_names"][0]), (pl2, info["channel_names"][1])) if p is not None]
    for i in imgs:
        i.thumbnail((700, 1000))
    fig = Image.new("RGB", (sum(i.width for i in imgs) + 10 * (len(imgs) - 1), max(i.height for i in imgs)), (255, 255, 255))
    x = 0
    for i in imgs:
        fig.paste(i, (x, 0)); x += i.width + 10
    fig.save(f"{OUT}\\fig_stitched_plane.png")
    print("saved fig_tile_layout, fig_registration_before_after, fig_stitched_plane")


if __name__ == "__main__":
    main()

import os
from pathlib import Path
import sys, csv
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark as B
from lsp import denoise as dn


def main():
    a, b = B.fit_noise_model()
    clean, info = B.make_clean()
    noisies = [B.noisy_from(clean, a, b, s) for s in B.SEEDS]
    S = float(np.mean([dn.estimate_noise_sigma(n) for n in noisies]))
    fam = {}
    for s in (0.45, 0.5, 0.55, 0.6, 0.65, 0.7):
        fam[f"gauss xy s={s}"] = lambda v, s=s: dn.gaussian(v, (0, s, s), True)
    for w in (0.22, 0.26, 0.30, 0.34):
        fam[f"TV w={w}"] = lambda v, w=w: dn.tv(v / S, w) * S
    for it, kf in ((1, 3.0), (2, 3.0), (2, 5.0), (3, 5.0), (2, 8.0), (3, 8.0)):
        fam[f"aniso-reg it={it} k={kf}s"] = lambda v, it=it, kf=kf: dn.perona_malik(v, it, kf * S, 0.12, (2.88, 1, 1), True, 0.6)
    for it, kf in ((4, 2.5), (5, 2.5), (6, 2.5)):
        fam[f"aniso-plain it={it} k={kf}s"] = lambda v, it=it, kf=kf: dn.perona_malik(v, it, kf * S, 0.12, (2.88, 1, 1), True, 0)
    rows = []
    print(f"baseline noise {S:.1f}.  Target: ~2x noise reduction (noise_sd ~10)")
    print(f"{'method':26s} noise  fine60 med40 coarse25 fine25 | fwhm_xy edge_w pair line")
    for name, fn in fam.items():
        ms = [B.metrics(fn(n), clean, info) for n in noisies]
        m = {k: float(np.nanmean([x[k] for x in ms])) for k in ms[0]}
        rows.append((name, m))
        print(f"{name:26s} {m['noise_sd']:5.1f}  {100*m['ret fine A60 (s0.6)']:5.0f}% {100*m['ret medium A40 (s1.2)']:4.0f}% {100*m['ret coarse A25 (s2.0)']:6.0f}% {100*m['ret fine A25 (s0.6)']:5.0f}% | "
              f"{m['fwhm_xy fine']:.2f}   {m['edge_w']:.2f}  {m['pair_dip']:.2f} {100*m['line_ret']:4.0f}%", flush=True)


if __name__ == "__main__":
    main()

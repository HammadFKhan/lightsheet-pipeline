"""Headless runner:  python -m lsp.cli <inspect|convert|register|preview|full|all> --czi X --work Y"""
from __future__ import annotations

import argparse
import threading
import time

from .project import Project
from . import pipeline as pl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["inspect", "convert", "register", "preview", "full", "all"])
    ap.add_argument("--czi", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--z-start", type=int, default=0)
    ap.add_argument("--z-stop", type=int, default=None)
    ap.add_argument("--channels", type=int, nargs="*", default=None)
    a = ap.parse_args()
    p = Project(a.czi, a.work, workers=a.workers, z_start=a.z_start, z_stop=a.z_stop, channels=a.channels)
    steps = {"inspect": pl.step_inspect, "convert": pl.step_convert, "register": pl.step_register,
             "preview": lambda p, c: pl.step_fuse(p, c, "preview"),
             "full": lambda p, c: pl.step_fuse(p, c, "full")}
    order = ["inspect", "convert", "register", "preview", "full"] if a.step == "all" else [a.step]
    ev = threading.Event()
    for s in order:
        print(f"== {s}")
        last = 0.0
        for d, t, msg, status in steps[s](p, ev):
            if msg:
                print(f"[{d}/{t}] {msg}", flush=True)
            elif status and time.time() - last > 15:
                last = time.time()
                print(f"   {status}", flush=True)


if __name__ == "__main__":
    main()

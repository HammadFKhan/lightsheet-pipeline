"""napari-based GUI: run each pipeline step, view the result, analyse, export."""
from __future__ import annotations

import json
import os
import threading
import traceback
from pathlib import Path

import dask.array as da
import numpy as np
import zarr
from napari.qt.threading import thread_worker
from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                            QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
                            QMessageBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from . import analyze as an
from . import pipeline as pl
from .project import Project
from .widgets import HistogramWidget, ZBar

CMAPS = ["green", "magenta", "cyan", "yellow"]


def _browse_row(edit: QLineEdit, folder=False, filt="CZI (*.czi)"):
    b = QPushButton("Browse")

    def go():
        if folder:
            p = QFileDialog.getExistingDirectory(None, "Select folder", edit.text() or "")
        else:
            p, _ = QFileDialog.getOpenFileName(None, "Select file", edit.text() or "", filt)
        if p:
            edit.setText(p)
    b.clicked.connect(go)
    row = QHBoxLayout(); row.addWidget(edit); row.addWidget(b)
    w = QWidget(); w.setLayout(row); row.setContentsMargins(0, 0, 0, 0)
    return w


class PipelineWidget(QWidget):
    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.cancel = threading.Event()
        self.worker = None

        # last-used folders are remembered between sessions in a small file in the user's home folder
        self._qs = QSettings(str(Path.home() / ".lightsheet_pipeline.ini"), QSettings.IniFormat)
        self.czi = QLineEdit(str(self._qs.value("czi", "")))
        self.czi.setPlaceholderText("Folder containing the .czi (or the .czi file itself)")
        self.work = QLineEdit(str(self._qs.value("work", "")))
        self.work.setPlaceholderText("Work folder for this sample (created if missing; needs ~2-3x the CZI size free)")
        self.workers = QSpinBox(); self.workers.setRange(1, 64)
        self.workers.setValue(int(self._qs.value("workers", max(2, min(16, (os.cpu_count() or 8) // 2)))))
        self.zs = QSpinBox(); self.zs.setRange(0, 100000); self.zs.setValue(0)
        self.ze = QSpinBox(); self.ze.setRange(0, 100000); self.ze.setValue(0)
        self.ze.setSpecialValueText("all")
        self.regch = QSpinBox(); self.regch.setRange(0, 7)
        self.ds = QSpinBox(); self.ds.setRange(2, 16); self.ds.setValue(4)

        form = QFormLayout()
        form.addRow("CZI file", _browse_row(self.czi))
        form.addRow("Work folder", _browse_row(self.work, folder=True))
        form.addRow("CPU workers", self.workers)
        form.addRow("Z start / stop", self._pair(self.zs, self.ze))
        form.addRow("Registration channel", self.regch)
        form.addRow("Registration downsample (xy)", self.ds)
        box = QGroupBox("1. Setup"); box.setLayout(form)

        steps = QVBoxLayout()
        self.btns = {}
        for key, label, tip in [
            ("inspect", "A. Inspect CZI", "Read tile layout, voxel size, channels (seconds)"),
            ("convert", "B. Convert to chunked Zarr", "Stream every tile out of the CZI (resumable)"),
            ("register", "C. Register tiles", "Phase-correlation on low-res overlaps + global solve"),
            ("preview", "D. Fuse preview (low-res) + view", "Fast stitched overview - check seams here first"),
            ("full", "E. Fuse full resolution + view", "Writes the full stitched multiscale volume"),
        ]:
            b = QPushButton(label); b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.run(k))
            steps.addWidget(b); self.btns[key] = b
        row = QHBoxLayout()
        lp = QPushButton("Load preview"); lp.clicked.connect(lambda: self.load("preview"))
        lf = QPushButton("Load full-res"); lf.clicked.connect(lambda: self.load("full"))
        stop = QPushButton("Stop"); stop.clicked.connect(self.stop)
        for w in (lp, lf, stop):
            row.addWidget(w)
        steps.addLayout(row)
        sbox = QGroupBox("2. Pipeline"); sbox.setLayout(steps)

        self.bar = QProgressBar(); self.bar.setRange(0, 1)
        self.status = QLabel("Idle"); self.status.setWordWrap(True)
        self.status.setStyleSheet("font-weight:bold")
        self.pstatus = QLabel(""); self.pstatus.setWordWrap(True); self.pstatus.setStyleSheet("color:#9ab")
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(110)

        lay = QVBoxLayout(self)
        for w in (box, sbox, self.pstatus, self.bar, self.status, self.log):
            lay.addWidget(w)
        self.work.editingFinished.connect(self.refresh_status)
        self.refresh_status()

    def refresh_status(self):
        try:
            self.pstatus.setText("Work folder: " + pl.project_status(self.project()))
        except Exception:
            self.pstatus.setText("Work folder: (set CZI + work folder)")

    @staticmethod
    def _pair(a, b):
        r = QHBoxLayout(); r.addWidget(a); r.addWidget(b); r.setContentsMargins(0, 0, 0, 0)
        w = QWidget(); w.setLayout(r); return w

    # ---- project ---------------------------------------------------------
    def project(self) -> Project:
        czi = self.czi.text().strip()
        if not czi:
            raise RuntimeError("Choose the CZI file (or its folder) first.")
        if Path(czi).is_dir():                       # allow picking the folder
            files = sorted(Path(czi).glob("*.czi"))
            if not files:
                raise RuntimeError("No .czi file in that folder")
            czi = str(files[0]); self.czi.setText(czi)
        if not self.work.text().strip():             # default: <czi folder>_work next to the CZI's folder
            self.work.setText(str(Path(czi).parent.with_name(Path(czi).parent.name + "_work")))
        self._qs.setValue("czi", czi); self._qs.setValue("work", self.work.text().strip())
        self._qs.setValue("workers", self.workers.value())
        pre = getattr(self, "pre", None)
        return Project(czi=czi, work=self.work.text().strip(), ds=self.ds.value(), workers=self.workers.value(),
                       z_start=self.zs.value(), z_stop=(self.ze.value() or None), reg_channel=self.regch.value(),
                       use_pp=bool(pre and pre.use_pp.isChecked()), pp=(pre.params() if pre else None))

    def say(self, msg):
        self.log.appendPlainText(msg)

    # ---- running ---------------------------------------------------------
    def run(self, key, fn=None):
        if self.worker is not None:
            self.say("A step is already running - press Stop first."); return
        try:
            proj = self.project()
        except Exception as e:
            self.say(f"ERROR: {e}"); return
        verdict = pl.already_done(key, proj)
        if verdict == "needs_register":
            self.say("Run C. Register tiles first."); return
        if verdict == "done":
            ask = QMessageBox.question(
                self, "Already done",
                f"'{key}' already finished for these settings.\n\nRe-run it anyway? (No = keep the existing result"
                f"{' and load it' if key in ('preview', 'full') else ''}.)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ask != QMessageBox.Yes:
                self.say(f"{key}: already done - kept existing result.")
                if key in ("preview", "full"):
                    self.load(key)
                return
        fn = fn or {"inspect": pl.step_inspect, "convert": pl.step_convert, "register": pl.step_register,
                    "preprocess": pl.step_preprocess,
                    "preview": lambda p, c: pl.step_fuse(p, c, "preview"),
                    "full": lambda p, c: pl.step_fuse(p, c, "full")}[key]
        self.cancel = threading.Event()
        cancel = self.cancel

        @thread_worker
        def job():
            for item in fn(proj, cancel):
                yield item

        self.say(f"--- {key} ---")
        w = job()
        w.yielded.connect(self._progress)
        w.errored.connect(self._error)
        w.finished.connect(lambda k=key: self._finished(k))
        self.worker = w
        w.start()

    def _progress(self, item):
        d, t, msg, status = item
        self.bar.setRange(0, max(t, 1)); self.bar.setValue(d)
        if status:
            self.status.setText(status)
        if msg:
            self.say(f"[{d}/{t}] {msg}")

    def _error(self, e):
        if isinstance(e, pl.Cancelled):
            self.say("Stopped.")
            self.status.setText("Stopped")
        else:
            self.say("ERROR: " + "".join(traceback.format_exception(e)))
            self.status.setText("Error - see log")
        self.worker = None
        self.refresh_status()

    def _finished(self, key):
        ok = self.worker is not None       # an error handler clears worker first
        self.worker = None
        self.refresh_status()
        if ok:
            self.say(f"{key}: finished")
            self.status.setText(f"{key}: finished")
            if key == "preprocess" and getattr(self, "pre", None) is not None:
                self.pre.use_pp.setChecked(True)
                self.refresh_status()
            if key in ("preview", "full"):
                self.load(key)

    def stop(self):
        if self.worker is not None:
            self.status.setText("Stopping...")
        self.cancel.set()

    # ---- viewing ---------------------------------------------------------
    def load(self, kind):
        try:
            proj = self.project()
            kind = f"{kind}_pp" if proj.use_pp and not kind.endswith("_pp") else kind
            path = proj.fused_dir / f"{kind}.zarr"
            g = zarr.open_group(str(path), mode="r")
        except Exception as e:
            self.say(f"Nothing to load yet ({e})"); return
        meta = g.attrs["multiscales"][0]
        info = g.attrs["lsp"]
        scale0 = meta["datasets"][0]["coordinateTransformations"][0]["scale"][1:]
        n_ch = g["0"].shape[0]
        for lyr in [l for l in self.viewer.layers if l.name.startswith(f"{kind}")]:
            self.viewer.layers.remove(lyr)
        names = info.get("channel_names") or [f"ch{c}" for c in range(n_ch)]
        for c in range(n_ch):
            levels = [da.from_zarr(g[d["path"]])[c] for d in meta["datasets"]]
            coarse = np.asarray(levels[-1])
            lyr = self.viewer.add_image(levels, multiscale=True, name=f"{kind}_{names[c]}", scale=scale0,
                                        colormap=CMAPS[c % 4], blending="additive",
                                        contrast_limits=(float(np.percentile(coarse, 1)), float(np.percentile(coarse, 99.8))))
            lyr.contrast_limits_range = (0, 65535)
            try:
                lyr.units = ("um", "um", "um")
            except Exception:
                pass
        self.viewer.scale_bar.visible = True
        zbar = getattr(self, "zbar", None)
        if zbar is not None:
            zbar.apply_view()               # keep the chosen plane (horizontal / coronal / sagittal)
        else:
            self.viewer.reset_view()
        self.say(f"Loaded {path} ({n_ch} channels, {len(levels)} levels, voxel {scale0} um zyx)")


class AnalysisWidget(QWidget):
    """ROI -> extract -> save TIFF / segment+count -> CSV. Coordinates are level-0 voxels."""

    def __init__(self, viewer, pipe: PipelineWidget):
        super().__init__()
        self.viewer, self.pipe = viewer, pipe
        self.roi = {k: QSpinBox() for k in ("z0", "z1", "y0", "y1", "x0", "x1")}
        for k, s in self.roi.items():
            s.setRange(0, 1_000_000)
        for k, v in dict(z0=0, z1=64, y0=0, y1=1024, x0=0, x1=1024).items():
            self.roi[k].setValue(v)
        self.src = QLineEdit("full")
        self.ch = QSpinBox(); self.ch.setRange(0, 7)
        self.sigma = QDoubleSpinBox(); self.sigma.setValue(1.5)
        self.thr = QDoubleSpinBox(); self.thr.setRange(0, 65535); self.thr.setValue(0)
        self.thr.setSpecialValueText("Otsu")
        self.minv = QSpinBox(); self.minv.setRange(1, 10_000_000); self.minv.setValue(20)
        self.out = QLineEdit()

        form = QFormLayout()
        form.addRow("Source (preview/full)", self.src)
        form.addRow("Channel", self.ch)
        for lab, a, b in (("Z range", "z0", "z1"), ("Y range", "y0", "y1"), ("X range", "x0", "x1")):
            form.addRow(lab, PipelineWidget._pair(self.roi[a], self.roi[b]))
        form.addRow("Smoothing sigma (xy px)", self.sigma)
        form.addRow("Threshold", self.thr)
        form.addRow("Min object voxels", self.minv)
        form.addRow("Output folder", _browse_row(self.out, folder=True))
        box = QGroupBox("ROI / Analysis"); box.setLayout(form)

        lay = QVBoxLayout(self); lay.addWidget(box)
        for label, fn in (("Show ROI box", self.show_box), ("Extract ROI into viewer", self.extract),
                          ("Save ROI as TIFF", self.save_tiff), ("Segment + count objects (ROI)", self.count),
                          ("Save screenshot", self.shot)):
            b = QPushButton(label); b.clicked.connect(fn); lay.addWidget(b)
        lay.addStretch()
        self._roi_arr = None

    def _g(self):
        p = self.pipe.project().fused_dir / f"{self.src.text()}.zarr"
        g = zarr.open_group(str(p), mode="r")
        scale = g.attrs["multiscales"][0]["datasets"][0]["coordinateTransformations"][0]["scale"][1:]
        return g, scale

    def _slices(self):
        r = {k: s.value() for k, s in self.roi.items()}
        return (slice(r["z0"], r["z1"]), slice(r["y0"], r["y1"]), slice(r["x0"], r["x1"]))

    def show_box(self):
        g, sc = self._g()
        z, y, x = self._slices()
        corners = np.array([[z.start, y.start, x.start], [z.start, y.start, x.stop],
                            [z.start, y.stop, x.stop], [z.start, y.stop, x.start]]) * sc
        for n in ("ROI box",):
            if n in self.viewer.layers:
                self.viewer.layers.remove(n)
        self.viewer.add_shapes([corners], shape_type="polygon", name="ROI box", edge_color="yellow",
                               face_color="transparent", edge_width=20)

    def _read(self):
        g, sc = self._g()
        path = self.pipe.project().fused_dir / f"{self.src.text()}.zarr"
        return an.read_roi(path, 0, self.ch.value(), self._slices()), sc

    def extract(self):
        arr, sc = self._read()
        self._roi_arr, self._sc = arr, sc
        z, y, x = self._slices()
        if "ROI" in self.viewer.layers:
            self.viewer.layers.remove("ROI")
        self.viewer.add_image(arr, name="ROI", scale=sc, translate=(z.start * sc[0], y.start * sc[1], x.start * sc[2]),
                              colormap="gray")
        self.pipe.say(f"ROI extracted {arr.shape}")

    def save_tiff(self):
        if self._roi_arr is None:
            self.extract()
        out = Path(self.out.text() or self.pipe.project().work_dir) / "roi.tif"
        an.save_tiff(self._roi_arr, str(out), self._sc)
        self.pipe.say(f"Saved {out}")

    def count(self):
        if self._roi_arr is None:
            self.extract()
        thr = self.thr.value() or None
        lab, props, used = an.segment_count(self._roi_arr, self.sigma.value(), thr, self.minv.value(), self._sc)
        z, y, x = self._slices(); sc = self._sc
        if "ROI objects" in self.viewer.layers:
            self.viewer.layers.remove("ROI objects")
        self.viewer.add_labels(lab, name="ROI objects", scale=sc,
                               translate=(z.start * sc[0], y.start * sc[1], x.start * sc[2]))
        out = Path(self.out.text() or self.pipe.project().work_dir) / "roi_objects.csv"
        an.save_table(props, str(out), sc)
        self.pipe.say(f"{len(props['label'])} objects (threshold {used:.1f}) -> {out}")

    def shot(self):
        out = Path(self.out.text() or self.pipe.project().work_dir) / "screenshot.png"
        self.viewer.screenshot(str(out), canvas_only=True)
        self.pipe.say(f"Saved {out}")


class PreprocessWidget(QWidget):
    """Flat-field / destripe / deconvolution settings, a quick region test, and the full run."""

    def __init__(self, viewer, pipe: PipelineWidget):
        super().__init__()
        from .gpu import gpu_info
        from . import preprocess as pp
        from . import psf
        self.viewer, self.pipe, self._pp, self._psf = viewer, pipe, pp, psf
        pipe.pre = self
        d = pp.DEFAULT_PARAMS
        gname = gpu_info()

        self.gpu = QCheckBox(f"Use GPU for deconvolution: {gname}" if gname else "GPU not available - CPU only")
        self.gpu.setChecked(bool(gname)); self.gpu.setEnabled(bool(gname))
        self.gpu.setToolTip("Measured: GPU gives ~30x for deconvolution but no speedup for flat-field/destripe "
                            "(those are disk/codec bound), so the GPU is only used when deconvolution is ticked.")

        def spin(val, lo, hi, dec=1, step=None):
            s = QDoubleSpinBox(); s.setDecimals(dec); s.setRange(lo, hi); s.setValue(val)
            if step:
                s.setSingleStep(step)
            return s

        th = psf.theoretical_fwhm_um({})
        self.pg = QCheckBox("0. Plane brightness (z) correction"); self.pg.setChecked(True)
        self.pg.setToolTip("Flattens narrow brightness dips along z (laser/illumination dropouts, seen as a dark band in "
                           "coronal/sagittal views). Changes only plane brightness - no loss of resolution.")
        self.ff = QCheckBox("1. Flat-field / shading correction"); self.ff.setChecked(True)
        self.ff_smooth = spin(d["ff_smooth"], 1, 500, 0)
        self.ff_off = spin(d["ff_offset"], 0, 5000, 0)
        self.ds = QCheckBox("2. Destripe"); self.ds.setChecked(True)
        self.ds_dir = QComboBox(); self.ds_dir.addItems(["y  (vertical streaks)", "x  (horizontal streaks)"])
        self.ds_sigma = spin(d["stripe_sigma"], 0.3, 20, 1)
        self.ds_keep = spin(d["stripe_keep"], 1, 200, 0)
        self.ds_str = spin(d["stripe_strength"], 0, 1, 2, 0.1)
        self.dc = QCheckBox("3. Deconvolution (Richardson-Lucy)")
        self.dc_it = QSpinBox(); self.dc_it.setRange(1, 100); self.dc_it.setValue(d["rl_iters"])
        self.dc_lat = spin(round(th["lateral"], 2), 0.1, 50, 2)
        self.dc_ax = spin(round(th["axial"], 2), 0.1, 100, 2)
        self.note = QLabel(); self.note.setWordWrap(True); self.note.setStyleSheet("color:#c9a")
        self.dc_lat.valueChanged.connect(self._psf_note); self.dc_ax.valueChanged.connect(self._psf_note)

        form = QFormLayout()
        form.addRow(self.pg)
        form.addRow(self.ff); form.addRow("  smoothing (px) / offset", PipelineWidget._pair(self.ff_smooth, self.ff_off))
        form.addRow(self.ds); form.addRow("  streak direction", self.ds_dir)
        form.addRow("  width (bins) / keep low-freq (bins)", PipelineWidget._pair(self.ds_sigma, self.ds_keep))
        form.addRow("  strength (0-1)", self.ds_str)
        form.addRow(self.dc); form.addRow("  iterations", self.dc_it)
        form.addRow("  PSF FWHM lateral / axial (um)", PipelineWidget._pair(self.dc_lat, self.dc_ax))
        form.addRow(self.note)
        sbox = QGroupBox("Stages (run in this order)"); sbox.setLayout(form)

        self.tile = QSpinBox(); self.tile.setRange(0, 999); self.tile.setValue(10)
        self.ch = QSpinBox(); self.ch.setRange(0, 7)
        self.z0 = QSpinBox(); self.z0.setRange(0, 100000); self.z0.setValue(420)
        self.nz = QSpinBox(); self.nz.setRange(1, 400); self.nz.setValue(64)
        self.tv_prev = QCheckBox("Also preview display denoise (TV)")
        self.tv_prev.setToolTip("Adds a third panel: the preprocessed region with TV denoising (display copy only).")
        self.tv_w = spin(0.26, 0.05, 1.0, 2, 0.02)
        self.tv_w.setToolTip("TV strength in noise-sigma units: 0.15 light (~1.3x less noise), 0.26 medium (~2x), 0.34 strong (~2.8x)")
        self.y0, self.y1 = QSpinBox(), QSpinBox(); self.x0, self.x1 = QSpinBox(), QSpinBox()
        for s, v in ((self.y0, 300), (self.y1, 900), (self.x0, 500), (self.x1, 1300)):
            s.setRange(0, 5000); s.setValue(v)
        rform = QFormLayout()
        rform.addRow("Tile / channel", PipelineWidget._pair(self.tile, self.ch))
        rform.addRow("Start plane / # planes", PipelineWidget._pair(self.z0, self.nz))
        rform.addRow("Y range", PipelineWidget._pair(self.y0, self.y1))
        rform.addRow("X range", PipelineWidget._pair(self.x0, self.x1))
        rform.addRow(self.tv_prev); rform.addRow("  TV strength", self.tv_w)
        rbox = QGroupBox("Region test (before / after, side by side)"); rbox.setLayout(rform)

        self.use_pp = QCheckBox("Use preprocessed tiles for Register / Fuse")
        try_b = QPushButton("Try on region"); try_b.clicked.connect(self.try_region)
        clr_b = QPushButton("Clear test layers"); clr_b.clicked.connect(self.clear_test)
        run_b = QPushButton("F. Run preprocessing on all tiles"); run_b.clicked.connect(lambda: pipe.run("preprocess"))
        row = QHBoxLayout(); row.addWidget(try_b); row.addWidget(clr_b)
        lay = QVBoxLayout(self)
        for w in (self.gpu, sbox, rbox):
            lay.addWidget(w)
        lay.addLayout(row)
        for w in (run_b, self.use_pp):
            lay.addWidget(w)
        lay.addStretch()
        self._psf_note()

    def params(self) -> dict:
        return dict(
            plane_gain=self.pg.isChecked(),
            flatfield=self.ff.isChecked(), destripe=self.ds.isChecked(), deconv=self.dc.isChecked(),
            gpu=self.gpu.isChecked(), ff_smooth=self.ff_smooth.value(), ff_offset=self.ff_off.value(),
            stripe_dir="y" if self.ds_dir.currentIndex() == 0 else "x", stripe_sigma=self.ds_sigma.value(),
            stripe_keep=self.ds_keep.value(), stripe_strength=self.ds_str.value(),
            rl_iters=self.dc_it.value(), psf_lat_um=self.dc_lat.value(), psf_ax_um=self.dc_ax.value())

    def _voxel(self):
        try:
            info = self.pipe.project().info()
            return (info["px_z_um"], info["px_xy_um"], info["px_xy_um"]), info
        except Exception:
            return (7.32, 2.541, 2.541), {}

    def _psf_note(self):
        vox, _ = self._voxel()
        z, y, x = self._psf.fwhm_in_voxels(self.dc_lat.value(), self.dc_ax.value(), vox)
        txt = f"PSF FWHM in voxels (z, y, x): {z:.2f}, {y:.2f}, {x:.2f}."
        if max(z, y, x) < 1.2:
            txt += (" The PSF is about a voxel or smaller, so deconvolution will change little. "
                    "Only raise the FWHM if you have measured a broader blur.")
        self.note.setText(txt)

    # ---- region test -----------------------------------------------------
    def try_region(self):
        try:
            proj = self.pipe.project()
            info = proj.info()
            p = self.params()
            p["gpu"] = bool(p["gpu"])
            m, c = self.tile.value(), self.ch.value()
            raws = [self._tile_raw(proj, t["m"], c) for t in info["tiles"]]
        except Exception as e:
            self.pipe.say(f"Region test needs converted tiles first: {e}"); return
        vox, _ = self._voxel()
        z0_all, z1_all = proj.z_range()
        z0 = min(self.z0.value(), max(z1_all - z0_all - 3, 0)); z1 = min(z0 + self.nz.value(), z1_all - z0_all)
        box = (self.y0.value(), self.y1.value(), self.x0.value(), self.x1.value())
        pp, workdir = self._pp, proj.work_dir
        kernel = pp.make_kernel(p, info, vox) if p["deconv"] else None
        tvw = self.tv_w.value() if self.tv_prev.isChecked() else None
        from .convert import tile_paths
        low_path = tile_paths(proj.raw_dir, m, c)[1]

        @thread_worker
        def job():
            from . import denoise as dn
            ff = None
            if p["flatfield"]:
                fp = pp.flatfield_path(workdir, c)
                ff = np.load(fp) if fp.exists() else pp.estimate_flatfield(raws, z0_all, z1_all, p["ff_smooth"])
            gains, off = (pp.plane_gains(low_path, p.get("pg_offset") if p.get("pg_offset") is not None
                                         else pp.channel_floor(proj.raw_dir, [t["m"] for t in info["tiles"]], c),
                                         p.get("pg_window", 61), p.get("pg_pct", 50))
                          if p["plane_gain"] else (None, 0.0))
            raw, out = pp.preview_region(raws[m], p, ff, kernel, z0, z1, box[0], box[1], box[2], box[3], gains, off)
            tvo = None
            if tvw:
                floor = off if p["plane_gain"] else pp.estimate_offset(zarr.open(str(low_path), "r"))
                sig = dn.estimate_tissue_sigma(out, floor)
                tvo = np.clip(dn.tv_volume(out.astype(np.float32), sig, tvw, bool(p["gpu"]) and dn.gpu.gpu_info() is not None), 0, 65535)
            gmin, gmax = (float(gains[z0:z1].min()), float(gains[z0:z1].max())) if gains is not None else (1.0, 1.0)
            return raw, out, tvo, (gmin, gmax)

        self.pipe.say(f"Region test: tile {m} ch {c}, planes {z0}-{z1}"
                      f"{', + TV preview' if tvw else ''} ({'GPU' if p['gpu'] else 'CPU'}; "
                      f"{'flat-field estimate may take a few seconds' if p['flatfield'] else 'no flat-field'})...")
        w = job()
        w.returned.connect(lambda res, z0=z0, vox=vox, box=box: self._show(res, z0, vox, box))
        w.errored.connect(lambda e: self.pipe.say("Region test error: " + "".join(traceback.format_exception(e))))
        w.start(); self._w = w

    @staticmethod
    def _tile_raw(proj, m, c):
        from .convert import tile_paths
        p = tile_paths(proj.raw_dir, m, c)[0]
        if not p.exists():
            raise RuntimeError(f"tile {m} channel {c} not converted")
        return p

    TEST_LAYERS = ("raw_region", "preprocessed_region", "tv_display_region")

    def _show(self, res, z0, vox, box):
        raw, out, tvo, (gmin, gmax) = res
        self.clear_test()
        lo, hi = float(np.percentile(raw, 1)), float(np.percentile(raw, 99.8))
        tr = (z0 * vox[0], box[0] * vox[1], box[2] * vox[2])
        panels = [("raw_region", raw), ("preprocessed_region", out)] + ([("tv_display_region", tvo)] if tvo is not None else [])
        # take the stitched layers out of the list while testing (hiding them would still stretch the view to the whole
        # canvas); "Clear test layers" puts them back
        self._stash = [l for l in list(self.viewer.layers) if l.name not in self.TEST_LAYERS]
        for l in self._stash:
            self.viewer.layers.remove(l)
        for name, arr in panels:
            try:
                lyr = self.viewer.add_image(arr, name=name, scale=vox, translate=tr, colormap="gray",
                                            contrast_limits=(lo, hi), units=("um", "um", "um"))
            except TypeError:                               # older napari without the units argument
                lyr = self.viewer.add_image(arr, name=name, scale=vox, translate=tr, colormap="gray",
                                            contrast_limits=(lo, hi))
            lyr.contrast_limits_range = (0, 65535)
        try:
            self.viewer.grid.enabled = True; self.viewer.grid.shape = (1, len(panels))
        except Exception:
            pass
        self.viewer.reset_view()
        # jump every axis to the middle of the test region so any view (horizontal/coronal/sagittal) shows it at once
        centre_um = ((z0 + raw.shape[0] / 2) * vox[0], (box[0] + box[1]) / 2 * vox[1], (box[2] + box[3]) / 2 * vox[2])
        for ax, val in enumerate(centre_um):
            try:
                self.viewer.dims.set_point(ax, val)
            except Exception:
                pass
        gtxt = f" Plane gains in this range: {gmin:.2f}-{gmax:.2f}." if (gmin, gmax) != (1.0, 1.0) else ""
        self.pipe.say(f"Region test done: mean {raw.mean():.0f} -> {out.mean():.0f}.{gtxt} "
                      f"Panels (same contrast): raw | preprocessed{' | + TV (display copy)' if tvo is not None else ''}. "
                      "Use the View box (bottom) to switch to coronal / sagittal.")

    def clear_test(self):
        for n in self.TEST_LAYERS:
            if n in self.viewer.layers:
                self.viewer.layers.remove(n)
        for l in getattr(self, "_stash", []):                 # restore the stitched layers removed for the test
            if l not in self.viewer.layers:
                self.viewer.layers.append(l)
        self._stash = []
        try:
            self.viewer.grid.enabled = False
        except Exception:
            pass


class ExportWidget(QWidget):
    """Export a fused volume to size-reduced BigTIFF stacks (one file per channel)."""

    def __init__(self, viewer, pipe: PipelineWidget):
        super().__init__()
        from . import export as ex
        self.ex, self.viewer, self.pipe = ex, viewer, pipe
        self.src = QComboBox(); self.src.currentIndexChanged.connect(self._fill_levels)
        refresh = QPushButton("Refresh"); refresh.clicked.connect(self.refresh)
        self.level = QComboBox(); self.level.currentIndexChanged.connect(self._level_changed)
        self.chk_ch = []
        self.chbox = QHBoxLayout()
        self.bits = QComboBox(); self.bits.addItems(["8-bit (smaller)", "16-bit (full range)"])
        self.scale = QComboBox(); self.scale.addItems(["Auto contrast (0.35-99.65%)", "Match viewer contrast"])
        self.comp = QComboBox(); self.comp.addItems(list(ex.COMPRESSIONS))
        self.tv = QComboBox()
        for label, w in (("Off - data as acquired", None), ("Light TV  (w 0.15, ~1.3x less noise)", 0.15),
                         ("Medium TV  (w 0.26, ~2x less noise)", 0.26), ("Strong TV  (w 0.34, ~2.8x less noise)", 0.34)):
            self.tv.addItem(label, w)
        self.tv.setToolTip("Total-variation denoising applied to the EXPORTED COPY only (the data on disk is unchanged). "
                           "Great for figures and viewing; keep an un-denoised export for quantification.")
        from .gpu import gpu_info
        self.has_gpu = bool(gpu_info())
        self.tv_note = QLabel("GPU: " + (gpu_info() or "none - TV on CPU is slow") + ". Denoised copy for viewing; keep the "
                              "data as acquired for quantification.")
        self.tv_note.setWordWrap(True); self.tv_note.setStyleSheet("color:#9ab")
        self.crop = QCheckBox("Crop to range (in this level's voxels)")
        self.cs = {k: QSpinBox() for k in ("z0", "z1", "y0", "y1", "x0", "x1")}
        for s in self.cs.values():
            s.setRange(0, 1_000_000)
        self.out = QLineEdit(); self.stem = QLineEdit("HFK001")
        self.est = QLabel("Estimate: click 'Estimate size'"); self.est.setWordWrap(True)
        self.info = QLabel(""); self.info.setWordWrap(True); self.info.setStyleSheet("color:#9ab")

        form = QFormLayout()
        row = QHBoxLayout(); row.addWidget(self.src, 1); row.addWidget(refresh)
        w = QWidget(); w.setLayout(row); row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Source volume", w)
        form.addRow("Resolution level", self.level)
        cw = QWidget(); cw.setLayout(self.chbox); self.chbox.setContentsMargins(0, 0, 0, 0)
        form.addRow("Channels", cw)
        form.addRow("Bit depth", self.bits)
        form.addRow("8-bit scaling", self.scale)
        form.addRow("Compression", self.comp)
        form.addRow("Display denoise", self.tv)
        form.addRow(self.tv_note)
        form.addRow(self.crop)
        form.addRow("Z", PipelineWidget._pair(self.cs["z0"], self.cs["z1"]))
        form.addRow("Y", PipelineWidget._pair(self.cs["y0"], self.cs["y1"]))
        form.addRow("X", PipelineWidget._pair(self.cs["x0"], self.cs["x1"]))
        form.addRow("Output folder", _browse_row(self.out, folder=True))
        form.addRow("File name stem", self.stem)
        box = QGroupBox("Export to TIFF"); box.setLayout(form)
        est_b = QPushButton("Estimate size"); est_b.clicked.connect(self.estimate)
        go_b = QPushButton("Export TIFF"); go_b.clicked.connect(self.export)
        lay = QVBoxLayout(self)
        for wdg in (box, self.info, est_b, self.est, go_b):
            lay.addWidget(wdg)
        lay.addStretch()
        self.refresh()

    # ---- source / level handling ------------------------------------------
    def refresh(self):
        self.src.blockSignals(True); self.src.clear()
        try:
            fd = self.pipe.project().fused_dir
            for n in ("full", "full_pp", "preview", "preview_pp"):
                if (fd / f"{n}.zarr" / ".zattrs").exists():
                    self.src.addItem(n, str(fd / f"{n}.zarr"))
        except Exception:
            pass
        self.src.blockSignals(False)
        self._fill_levels()

    def _path(self):
        return self.src.currentData()

    def _fill_levels(self):
        self.level.blockSignals(True); self.level.clear()
        p = self._path()
        if p:
            self.rows = self.ex.level_table(p)
            for r in self.rows:
                c, z, y, x = r["shape"]
                self.level.addItem(f"L{r['level']}: {x} x {y} x {z} | voxel {r['voxel_um'][1]:.1f} x {r['voxel_um'][0]:.1f} um | 16-bit raw {r['gb16']:.1f} GB/ch", r["level"])
            g = zarr.open_group(p, "r")
            names = g.attrs["lsp"].get("channel_names") or [f"ch{i}" for i in range(self.rows[0]["shape"][0])]
            for cb in self.chk_ch:
                self.chbox.removeWidget(cb); cb.deleteLater()
            self.chk_ch = []
            for i, n in enumerate(names):
                cb = QCheckBox(n); cb.setChecked(i == 0); self.chbox.addWidget(cb); self.chk_ch.append(cb)
            self.names = names
            default = min(1, len(self.rows) - 1)
            self.level.setCurrentIndex(default)
        self.level.blockSignals(False)
        self._level_changed()

    def _level_changed(self):
        if not self._path() or self.level.currentIndex() < 0:
            return
        _, z, y, x = self.rows[self.level.currentIndex()]["shape"]
        for k, v in (("z0", 0), ("z1", z), ("y0", 0), ("y1", y), ("x0", 0), ("x1", x)):
            self.cs[k].setValue(v)

    # ---- options -----------------------------------------------------------
    def _opts(self):
        p = self._path()
        if not p:
            raise RuntimeError("No fused volume found - run Fuse first (or press Refresh).")
        chans = [i for i, cb in enumerate(self.chk_ch) if cb.isChecked()]
        if not chans:
            raise RuntimeError("Tick at least one channel.")
        lvl = self.level.currentData()
        bit8 = self.bits.currentIndex() == 0
        limits = {}
        if bit8 and self.scale.currentIndex() == 1:
            stem = Path(p).stem
            for c in chans:
                lyr = self.viewer.layers[f"{stem}_{self.names[c]}"] if f"{stem}_{self.names[c]}" in self.viewer.layers else None
                if lyr is not None:
                    limits[c] = tuple(float(v) for v in lyr.contrast_limits)
        crop = tuple(self.cs[k].value() for k in ("z0", "z1", "y0", "y1", "x0", "x1")) if self.crop.isChecked() else None
        out = self.out.text() or str(self.pipe.project().work_dir / "exports")
        return self.ex.ExportOptions(
            zarr_path=p, out_dir=out,
            stem=f"{self.stem.text() or 'volume'}_{Path(p).stem}_L{lvl}{'_TV' if self.tv.currentData() else ''}",
            level=lvl, channels=chans, bit8=bit8, limits=limits,
            compression=self.ex.COMPRESSIONS[self.comp.currentText()], crop=crop, channel_names=self.names,
            tv_weight=self.tv.currentData(), use_gpu=self.has_gpu)

    def estimate(self):
        try:
            o = self._opts()
        except Exception as e:
            self.pipe.say(str(e)); return
        self.est.setText("Estimating from sample planes...")

        @thread_worker
        def job():
            return self.ex.estimate(o)

        def show(res):
            tot = sum(res.values())
            import shutil
            Path(o.out_dir).mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(o.out_dir).free
            per = ", ".join(f"{self.names[c]} ~{v / 1e9:.1f} GB" for c, v in res.items())
            warn = "  WARNING: more than free disk space!" if tot > free * 0.95 else ""
            self.est.setText(f"Estimated: {per} | total ~{tot / 1e9:.1f} GB (free on target: {free / 1e9:.0f} GB){warn}")
        w = job(); w.returned.connect(show)
        w.errored.connect(lambda e: self.est.setText(f"Estimate failed: {e}"))
        w.start(); self._w = w

    def export(self):
        try:
            o = self._opts()
        except Exception as e:
            self.pipe.say(str(e)); return

        def fn(proj, cancel):
            try:
                yield from self.ex.export_iter(o, cancel)
            except RuntimeError as e:
                if str(e) == "cancelled":
                    raise pl.Cancelled()
                raise
        self.pipe.say(f"Exporting {o.stem}: level {o.level}, {'8' if o.bit8 else '16'}-bit, "
                      f"{o.compression or 'no'} compression -> {o.out_dir}")
        self.pipe.run("export", fn)


def main():
    import napari
    viewer = napari.Viewer(title="Lightsheet CZI pipeline")
    build_layout(viewer)
    napari.run()


def build_layout(viewer):
    """One compact right-hand column (tabs + histogram), a slim Z bar below the canvas,
    narrower left panels, so the image canvas gets most of the window."""
    pipe = PipelineWidget(viewer)
    tabs = QTabWidget()
    tabs.addTab(pipe, "Pipeline")
    tabs.addTab(PreprocessWidget(viewer, pipe), "Preprocess")
    tabs.addTab(AnalysisWidget(viewer, pipe), "Analyze")
    tabs.addTab(ExportWidget(viewer, pipe), "Export TIFF")
    split = QSplitter(Qt.Vertical)
    split.addWidget(tabs); split.addWidget(HistogramWidget(viewer))
    split.setSizes([620, 300]); split.setMinimumWidth(360)
    win = viewer.window
    right = win.add_dock_widget(split, name="Controls", area="right")
    pipe.zbar = ZBar(viewer)
    zdock = win.add_dock_widget(pipe.zbar, name="Z", area="bottom")
    try:
        zdock.setMaximumHeight(52)
        qt = win._qt_window
        qt.resizeDocks([right], [400], Qt.Horizontal)
        qv = win._qt_viewer
        for d in (qv.dockLayerControls, qv.dockLayerList):
            d.setMaximumWidth(250)
        qt.resizeDocks([qv.dockLayerControls], [230], Qt.Horizontal)
        qv.dims.setVisible(False)          # our Z bar replaces napari's slider
        qt.resize(1750, 980)
    except Exception:
        pass
    return pipe


if __name__ == "__main__":
    main()

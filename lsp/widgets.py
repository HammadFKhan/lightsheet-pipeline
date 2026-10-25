"""Histogram (ImageJ-style, draggable display limits) and Z-playback widgets for napari."""
from __future__ import annotations

import numpy as np
from qtpy.QtCore import QPointF, QRectF, Qt, QTimer
from qtpy.QtGui import QColor, QFont, QPainter, QPen
from qtpy.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
                            QSlider, QSpinBox, QVBoxLayout, QWidget)

MAX_SAMPLES = 4_000_000

# Volume axes are (z, y, x). napari's dims.order lists the axes with the last two displayed;
# the first one is what the slider scrolls. Names assume this sample's orientation
# (z = dorsal->ventral, y = anterior-posterior, x = left-right); edit here if yours differs.
VIEWS = [
    ("Horizontal (XY, scroll Z)", (0, 1, 2)),
    ("Coronal (XZ, scroll Y)", (1, 0, 2)),
    ("Sagittal (YZ, scroll X)", (2, 0, 1)),
]


def _active_image(viewer):
    lyr = viewer.layers.selection.active
    if lyr is not None and hasattr(lyr, "contrast_limits"):
        return lyr
    for l in reversed(viewer.layers):
        if hasattr(l, "contrast_limits") and l.visible:
            return l
    return None


def _current_slice(layer):
    """The 2-D data currently displayed for this layer (best effort), as a float32 sample."""
    arr = None
    try:
        dv = layer._data_view
        if dv is not None and np.size(dv) > 0:
            arr = np.asarray(dv)
    except Exception:
        pass
    if arr is None:
        data = layer.data[-1] if layer.multiscale else layer.data
        arr = np.asarray(data[data.shape[0] // 2]) if data.ndim > 2 else np.asarray(data)
    if arr.ndim > 2:
        arr = arr.reshape(-1, arr.shape[-1])
    step = max(1, int(np.ceil(np.sqrt(arr.size / MAX_SAMPLES))))
    return arr[::step, ::step].astype(np.float32, copy=False)


class _HistCanvas(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.o = owner
        self.setMinimumHeight(130)
        self.setMouseTracking(True)
        self._grab = None

    def _plot_rect(self):
        return QRectF(8, 6, self.width() - 16, self.height() - 26)

    def _x_to_val(self, x):
        r = self._plot_rect()
        t = min(max((x - r.left()) / max(r.width(), 1), 0), 1)
        return self.o.lo + t * (self.o.hi - self.o.lo)

    def _val_to_x(self, v):
        r = self._plot_rect()
        span = max(self.o.hi - self.o.lo, 1e-9)
        return r.left() + (v - self.o.lo) / span * r.width()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(24, 26, 30))
        r = self._plot_rect()
        o = self.o
        if o.counts is not None:
            c = np.log1p(o.counts) if o.log.isChecked() else o.counts.astype(float)
            c = c / max(c.max(), 1e-9)
            n = len(c)
            bw = r.width() / n
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(170, 175, 185))
            for i, v in enumerate(c):
                h = v * r.height()
                p.drawRect(QRectF(r.left() + i * bw, r.bottom() - h, max(bw, 1), h))
            if o.layer is not None:
                lo, hi = o.layer.contrast_limits
                xl, xh = self._val_to_x(lo), self._val_to_x(hi)
                p.fillRect(QRectF(r.left(), r.top(), max(0, min(xl, r.right()) - r.left()), r.height()), QColor(0, 0, 0, 110))
                p.fillRect(QRectF(max(xh, r.left()), r.top(), max(0, r.right() - xh), r.height()), QColor(0, 0, 0, 110))
                p.setPen(QPen(QColor(0, 200, 255), 2)); p.drawLine(QPointF(xl, r.top()), QPointF(xl, r.bottom()))
                p.setPen(QPen(QColor(255, 210, 0), 2)); p.drawLine(QPointF(xh, r.top()), QPointF(xh, r.bottom()))
        p.setPen(QColor(150, 155, 165)); p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(r.left(), r.bottom() + 3, 120, 14), Qt.AlignLeft, f"{o.lo:.0f}")
        p.drawText(QRectF(r.right() - 120, r.bottom() + 3, 120, 14), Qt.AlignRight, f"{o.hi:.0f}")
        p.end()

    def mousePressEvent(self, e):
        if self.o.layer is None:
            return
        lo, hi = self.o.layer.contrast_limits
        x = e.position().x() if hasattr(e, "position") else e.x()
        self._grab = "lo" if abs(x - self._val_to_x(lo)) <= abs(x - self._val_to_x(hi)) else "hi"
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e):
        if self._grab is None or self.o.layer is None:
            return
        x = e.position().x() if hasattr(e, "position") else e.x()
        v = self._x_to_val(x)
        lo, hi = self.o.layer.contrast_limits
        eps = max((self.o.hi - self.o.lo) / 1000, 1e-6)
        if self._grab == "lo":
            lo = min(v, hi - eps)
        else:
            hi = max(v, lo + eps)
        self.o.set_limits(lo, hi)

    def mouseReleaseEvent(self, _):
        self._grab = None


class HistogramWidget(QWidget):
    """Histogram of the plane being displayed for the active layer; drag the lines to set contrast."""

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.layer = None
        self.counts = None
        self.lo, self.hi = 0.0, 1.0
        self.title = QLabel("Histogram"); self.title.setStyleSheet("font-weight:bold")
        self.canvas = _HistCanvas(self)
        self.log = QCheckBox("Log"); self.log.setChecked(True); self.log.toggled.connect(self.canvas.update)
        self.stats = QLabel(""); self.stats.setStyleSheet("color:#aab")
        self.smin = QDoubleSpinBox(); self.smax = QDoubleSpinBox()
        for s in (self.smin, self.smax):
            s.setRange(-1e9, 1e9); s.setDecimals(0); s.setKeyboardTracking(False)
        self.smin.valueChanged.connect(lambda v: self._spin(0, v))
        self.smax.valueChanged.connect(lambda v: self._spin(1, v))
        auto = QPushButton("Auto"); auto.setToolTip("ImageJ-style auto: 0.35%-99.65% of this plane")
        auto.clicked.connect(self.auto)
        mm = QPushButton("Min-Max"); mm.clicked.connect(self.minmax)
        refresh = QPushButton("Refresh"); refresh.clicked.connect(self.refresh)
        row1 = QHBoxLayout()
        for w in (QLabel("Min"), self.smin, QLabel("Max"), self.smax):
            row1.addWidget(w)
        row2 = QHBoxLayout()
        for w in (auto, mm, refresh, self.log):
            row2.addWidget(w)
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4)
        for w in (self.title, self.canvas, self.stats):
            lay.addWidget(w)
        lay.addLayout(row1); lay.addLayout(row2)

        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        viewer.layers.selection.events.active.connect(lambda e=None: self._schedule())
        viewer.layers.events.inserted.connect(lambda e=None: self._schedule())
        viewer.dims.events.current_step.connect(lambda e=None: self._schedule())
        self._schedule()

    def _schedule(self):
        self._timer.start()

    def _connect(self, layer):
        if layer is self.layer:
            return
        if self.layer is not None:
            try:
                self.layer.events.contrast_limits.disconnect(self._limits_changed)
            except Exception:
                pass
        self.layer = layer
        if layer is not None:
            layer.events.contrast_limits.connect(self._limits_changed)

    def refresh(self):
        layer = _active_image(self.viewer)
        self._connect(layer)
        if layer is None:
            self.counts = None; self.title.setText("Histogram (no image layer)"); self.canvas.update(); return
        try:
            a = _current_slice(layer)
        except Exception as e:  # data not ready
            self.title.setText(f"Histogram - {e}"); return
        lo = float(np.min(a))
        hi = float(np.percentile(a, 99.99))
        if hi <= lo:
            hi = lo + 1
        self.lo, self.hi = lo, hi
        self.counts, _ = np.histogram(a, bins=256, range=(lo, hi))
        self.title.setText(f"Histogram: {layer.name}")
        self.title.setToolTip("Current plane; the top 0.01% of values is clipped from the plot only")
        self._p = (np.percentile(a, 0.35), np.percentile(a, 99.65), float(a.min()), float(a.max()))
        self.stats.setText(f"min {a.min():.0f}  max {a.max():.0f}  mean {a.mean():.1f}  std {a.std():.1f}")
        self._limits_changed()

    def _limits_changed(self, *_):
        if self.layer is None:
            return
        lo, hi = self.layer.contrast_limits
        for s, v in ((self.smin, lo), (self.smax, hi)):
            s.blockSignals(True); s.setValue(v); s.blockSignals(False)
        self.canvas.update()

    def set_limits(self, lo, hi):
        lay = self.layer
        if lay is None:
            return
        r0, r1 = lay.contrast_limits_range
        lay.contrast_limits_range = (min(r0, lo), max(r1, hi))
        lay.contrast_limits = (lo, hi)

    def _spin(self, which, v):
        if self.layer is None:
            return
        lo, hi = self.layer.contrast_limits
        lo, hi = (v, hi) if which == 0 else (lo, v)
        if hi > lo:
            self.set_limits(lo, hi)

    def auto(self):
        if self.counts is not None:
            self.set_limits(float(self._p[0]), float(self._p[1]))

    def minmax(self):
        if self.counts is not None:
            self.set_limits(float(self._p[2]), float(self._p[3]))


class ZBar(QWidget):
    """Slider + play/pause + fps + jump box controlling the first (z) dimension."""

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.slider = QSlider(Qt.Horizontal)
        self.play = QPushButton("Play"); self.play.setCheckable(True); self.play.setFixedWidth(60)
        self.prev = QPushButton("<"); self.next = QPushButton(">")
        for b in (self.prev, self.next):
            b.setFixedWidth(28)
        self.fps = QSpinBox(); self.fps.setRange(1, 60); self.fps.setValue(10); self.fps.setSuffix(" fps")
        self.jump = QSpinBox(); self.jump.setKeyboardTracking(False)
        self.label = QLabel("z -"); self.label.setMinimumWidth(190)
        self.view = QComboBox()
        for name, _ in VIEWS:
            self.view.addItem(name)
        self.view.setToolTip("Plane shown on screen. The slider scrolls through the remaining axis.")
        self.view.currentIndexChanged.connect(lambda _=None: self.apply_view())
        lay = QHBoxLayout(self); lay.setContentsMargins(6, 2, 6, 2)
        lay.addWidget(QLabel("View")); lay.addWidget(self.view); lay.addSpacing(8)
        lay.addWidget(self.prev); lay.addWidget(self.play); lay.addWidget(self.next)
        lay.addWidget(self.slider, 1); lay.addWidget(self.jump); lay.addWidget(self.fps); lay.addWidget(self.label)

        self.timer = QTimer(self)
        self.slider.valueChanged.connect(self._set)
        self.jump.valueChanged.connect(self._set)
        self.prev.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        self.play.toggled.connect(self._toggle)
        self.fps.valueChanged.connect(lambda v: self.timer.setInterval(int(1000 / v)))
        self.timer.timeout.connect(lambda: self._step(1, wrap=True))
        self.timer.setInterval(100)
        viewer.dims.events.current_step.connect(lambda e=None: self._from_viewer())
        viewer.dims.events.range.connect(lambda e=None: self._from_viewer())
        viewer.dims.events.ndisplay.connect(lambda e=None: self._from_viewer())
        viewer.dims.events.order.connect(lambda e=None: self._from_viewer())
        self._from_viewer()

    # The slider scrolls whichever axis is NOT displayed: z for XY, y for XZ, x for YZ.
    def _axis(self):
        try:
            nd = self.viewer.dims.not_displayed
            return int(nd[0]) if len(nd) else 0
        except Exception:
            return 0

    def _n(self):
        try:
            return int(self.viewer.dims.nsteps[self._axis()])
        except Exception:
            return 1

    def _from_viewer(self):
        ax = self._axis()
        n = self._n()
        i = int(self.viewer.dims.current_step[ax]) if self.viewer.dims.ndim else 0
        active = self.viewer.dims.ndisplay == 2 and n > 1
        for w in (self.slider, self.jump, self.play, self.prev, self.next):
            w.setEnabled(active)
        for w in (self.slider, self.jump):
            w.blockSignals(True)
            w.setRange(0, max(n - 1, 0)); w.setValue(min(i, max(n - 1, 0)))
            w.blockSignals(False)
        lyr = _active_image(self.viewer)
        name = "zyx"[ax] if ax < 3 else "?"
        um = f" - {i * lyr.scale[ax]:.0f} um" if lyr is not None and ax < len(lyr.scale) else ""
        self.label.setText(f"{name} {i} / {max(n - 1, 0)}{um}" if active else "(3-D view)")

    def _set(self, v):
        self.viewer.dims.set_current_step(self._axis(), int(v))

    def _step(self, d, wrap=False):
        n = self._n()
        ax = self._axis()
        i = int(self.viewer.dims.current_step[ax]) + d
        if wrap:
            i %= max(n, 1)
        i = min(max(i, 0), n - 1)
        self.viewer.dims.set_current_step(ax, i)

    def apply_view(self, reset=True):
        """Show the plane chosen in the dropdown: reorder axes so the displayed plane changes."""
        order = VIEWS[self.view.currentIndex()][1]
        dims = self.viewer.dims
        try:
            if dims.ndim == 3:
                dims.axis_labels = ("z", "y", "x")
                dims.order = order
                if reset:
                    self.viewer.reset_view()
        except Exception:
            pass
        self._from_viewer()

    def _toggle(self, on):
        self.play.setText("Pause" if on else "Play")
        if on:
            self.timer.start()
        else:
            self.timer.stop()

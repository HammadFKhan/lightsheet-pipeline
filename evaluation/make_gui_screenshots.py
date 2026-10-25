"""Screenshots of the GUI on a processed work folder (for the guide).
Usage: python make_gui_screenshots.py <czi folder> <work folder> <output folder>"""
import os
import sys, time
from pathlib import Path
import napari
from qtpy.QtWidgets import QApplication, QTabWidget
from lsp.gui import build_layout

CZI = sys.argv[1] if len(sys.argv) > 1 else os.environ["LSP_CZI"]
WORK = sys.argv[2] if len(sys.argv) > 2 else os.environ["LSP_WORK"]
OUT = Path(sys.argv[3] if len(sys.argv) > 3 else str(Path(__file__).resolve().parents[1] / "docs" / "images"))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    v = napari.Viewer(show=True)
    pipe = build_layout(v)
    pipe.czi.setText(CZI); pipe.work.setText(WORK)
    pipe.project(); pipe.refresh_status()
    tabs = [w for w in app.allWidgets() if isinstance(w, QTabWidget)][0]

    def pump(n):
        for _ in range(n):
            app.processEvents(); time.sleep(0.1)

    def shot(name):
        v.window._qt_window.grab().save(str(OUT / name))

    # 1) Pipeline tab with the stitched preview loaded
    tabs.setCurrentIndex(0)
    pipe.load("preview"); pump(60)
    v.dims.set_current_step(0, 500); pump(20)
    shot("gui_pipeline_tab.png")
    # 2) Preprocess region test on real tissue (tile 10, ch 0) incl. TV, horizontal then coronal
    pre = pipe.pre
    tabs.setCurrentIndex(1)
    pre.z0.setValue(380); pre.nz.setValue(140)
    pre.y0.setValue(300); pre.y1.setValue(900); pre.x0.setValue(500); pre.x1.setValue(1300)
    pre.tv_prev.setChecked(True)
    pre.try_region()
    for _ in range(900):
        app.processEvents(); time.sleep(0.1)
        if "tv_display_region" in v.layers:
            break
    pump(40)
    print(pipe.log.toPlainText()[-500:])
    pump(20)
    shot("gui_region_test_horizontal.png")
    pipe.zbar.view.setCurrentIndex(1); pump(40)
    shot("gui_region_test_coronal.png")
    # 3) Export tab with a size estimate
    pre.clear_test(); pipe.zbar.view.setCurrentIndex(0)
    ew = [w for w in app.allWidgets() if type(w).__name__ == "ExportWidget"][0]
    tabs.setCurrentIndex(3); ew.refresh()
    ew.level.setCurrentIndex(min(1, ew.level.count() - 1)); ew.tv.setCurrentIndex(0)
    ew.estimate()
    for _ in range(600):
        app.processEvents(); time.sleep(0.1)
        if ew.est.text().startswith("Estimated"):
            break
    print(ew.est.text())
    pump(10)
    shot("gui_export_tab.png")
    v.close()


if __name__ == "__main__":
    main()

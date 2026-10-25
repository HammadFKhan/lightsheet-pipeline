# Lightsheet pipeline

Turn a huge tiled light-sheet `.czi` (ZEISS Lightsheet Z.1, tested on a 182 GB, 21-tile, 2-channel cleared brain) into a clean,
stitched, zoomable 3-D volume, with a GUI for every step. Python + [napari](https://napari.org); no coding needed.

**Start here: [Lightsheet_Guide.md](Lightsheet_Guide.md)** (step-by-step, with pictures).

![Stitched preview of a whole brain](docs/images/fig_stitched_plane.jpg)

## What it does

| Step | What | Notes |
|---|---|---|
| A. Inspect | Read tile layout, voxel size, channels | seconds, reads only the file's table of contents |
| B. Convert | Stream tiles into chunked Zarr | resumable, progress bar with ETA |
| Preprocess | Plane-brightness (z) correction, flat-field, destripe, optional deconvolution | writes a copy; raw tiles untouched; GPU optional |
| C. Register | 3-D phase correlation of every tile overlap + global solve | outlier-robust; quality score per link |
| D/E. Fuse | Blend tiles into a multiscale volume (preview, then full resolution) | built block by block, never in RAM |
| View | Horizontal / coronal / sagittal, Z-play, histogram with draggable contrast | |
| Analyze | ROI extraction, TIFF, generic threshold segmentation | starting point only |
| Export TIFF | Level, 8-bit, Deflate, optional TV display denoise, crop | BigTIFF per channel + settings JSON |

## Install and run (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File setup_env.ps1            # creates .venv, installs, self-tests
powershell -ExecutionPolicy Bypass -File setup_env.ps1 -Gpu cuda13 # optional NVIDIA GPU support (or cuda12)
```

Then double-click **`run_gui.bat`**. Needs Python 3.10 to 3.12; about 1.5 to 2× the `.czi` size in free disk space for the work folder.

## Repository layout

```
Lightsheet_Guide.md   the user guide
lsp/                  the library (project, convert, register, fuse, preprocess, denoise, export, GUI)
tests/test_core.py    self-contained checks on synthetic data (python tests/test_core.py)
evaluation/           scripts behind the numbers and figures in the guide (set LSP_WORK to a work folder)
docs/images/          figures used in the guide
```

Headless use (no GUI): `python -m lsp.cli all --czi FILE --work DIR` (steps: inspect, convert, register, preview, full).

## Honest limits

One time point; ZEN "bounding grid" tile numbering 0…N-1; translation-only registration; the deconvolution PSF is an approximation
(and for a 5×/0.16 objective the blur is about one voxel, so gains are small); denoised exports are for display, not measurement.

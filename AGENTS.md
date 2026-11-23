# Agent context: lightsheet-pipeline

Orientation for AI agents (and humans) picking up this repository. Read `README.md` for the overview and
`Lightsheet_Guide.md` for the user guide and technical appendix (section 10). This file adds goals, state, constraints and open items.

## Goal
End-to-end, GUI-driven pipeline that turns a very large (~200 GB) tiled ZEISS Lightsheet Z.1 `.czi` into a clean, stitched,
zoomable 3-D volume that the user can view and save, without loading the file into RAM. Dual-sided illumination is already fused by
the acquisition software, so sides are not fused again. Longer-term: analysis on the stitched volume (the user has not yet said
which analysis they want).

## User and data context (as stated by the user)
- Windows machine, 124 GB RAM. CPU, GPU and free disk are not recorded; ask or have the user run `nvidia-smi` / `Get-PSDrive D`.
- Data lives at `D:\Ephys\Practice\lightsheet\HFK001` (not in this repo; exact file name(s) not confirmed). Overlap, pixel size and
  tile count are in the CZI metadata: read them with the Inspect step, do not assume.
- The README reports a tested dataset: 182 GB, 21 tiles (3x7), 2 channels, 1041 z-planes, ~20 % overlap, cleared mouse brain.
- Fiji with the Bio-Formats CZI plugin was not confirmed installed. The pipeline does not need Fiji; TIFF export is for opening in Fiji.
- User preference: a GUI where data is loaded, processed and the result shown and saved. Python or ImageJ both acceptable.

## Current state (from reading the repo; the code was not run in the session that wrote this)
Implemented in `lsp/`:
- `project.py` (Project state, CZI inspect), `convert.py` (CZI -> chunked Zarr, resumable), `preprocess.py` (plane-gain, flat-field,
  destripe, optional deconvolution), `psf.py`, `register.py` (3-D phase correlation per overlap + weighted least-squares solve with
  outlier rejection), `fuse.py` (lazy blockwise linear-blend fusion + multiscale OME-NGFF pyramid), `denoise.py` (TV, display only),
  `export.py` (BigTIFF), `analyze.py` (ROI / threshold segmentation, a starting point only), `pipeline.py` (steps as generators
  yielding progress), `gpu.py` (optional CuPy), `cli.py` (headless), `gui.py` + `widgets.py` (napari app, tabs: Pipeline, Preprocess,
  Analyze, Export TIFF).
- `tests/test_core.py` runs on synthetic data: `python tests/test_core.py`.
- `evaluation/` holds the scripts behind the guide's numbers and figures (set `LSP_WORK` to a work folder).
- `docs/Acquisition_Guide.md` covers acquiring the data on a ZEISS Lightsheet 7 (ZEN 3.1).
- Entry points: `run_gui.bat` / `run_gui.py` (GUI), `python -m lsp.cli {inspect|convert|register|preview|full|all} --czi FILE --work DIR`.
- Pinned, tested stack in `requirements.txt` (Windows 11, Python 3.10; supports 3.10-3.12): zarr 2.18.3, dask, napari 0.7.1,
  aicspylibczi, pylibCZIrw. Install with `setup_env.ps1` (`-Gpu cuda13|cuda12` optional).

## Known limits (also in the README)
One time point only; ZEN "bounding grid" tile numbering 0..N-1; translation-only registration (offsets rounded to whole voxels for
fusion); approximate Gaussian PSF for deconvolution; no per-tile intensity matching beyond plane-gain and flat-field; denoised
exports are for display, not measurement. Needs about 1.5-2x the .czi size in free disk for the work folder.

## Ideas discussed but NOT implemented
These came up in the original design brainstorm; check the code before assuming any exist.
- Segmentation with Cellpose/StarDist/ilastik run blockwise (Dask `map_overlap` with halos, label merging across chunk borders).
- Atlas registration (brainreg/ANTs) if whole-brain mapping is wanted; vessel/neuron tracing.
- Non-translation (affine) tile registration; multi-band blending; BasicPy-style flat-field.
- Using `multiview-stitcher` or BigStitcher as a cross-check for registration quality.
- Support for multiple time points and other tile numbering schemes.

## Open questions for the user
1. Which analysis is the end goal (viewing only, cell counts, vasculature, atlas mapping, morphology)?
2. Is the full-resolution fused volume needed, or an overview plus full-resolution ROIs? (Drives disk use and run time.)
3. GPU model and free disk on the work drive.
4. Is `HFK001` one .czi or a folder of files?

## Rules for working in this repo
- Treat the original `.czi` as read-only. Cleaning steps write a second copy of the tiles.
- Never load a whole volume into RAM; read chunks/planes, keep steps resumable and cancellable (see the generator pattern in `pipeline.py`).
- Register on the low-resolution copy, apply to full resolution.
- Record parameters in the work folder (`project.json`, `registration.json`, export JSONs) so runs stay reproducible.
- Test on synthetic data (`tests/test_core.py`) or a z-subset (`Project.z_start/z_stop`) before running on the full file.
- Do not claim results on the user's real data unless you actually ran on it; the cloud session used to write this had no access to it.

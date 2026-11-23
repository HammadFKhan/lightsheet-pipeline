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
- The lab acquires on a **ZEISS Lightsheet 7** with **ZEN 3.1 (black edition)**, using the lab's managed preset **`rg5x`**
  (full procedure: `docs/Acquisition_Guide.md`). A `.czi` acquired that way is meant to work in the pipeline **out of the box**,
  with no conversion or manual settings. The README's "Lightsheet Z.1" wording predates this; same ZEISS `.czi` format.
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
- `docs/Acquisition_Guide.md` covers acquiring the data on a ZEISS Lightsheet 7 (ZEN 3.1 black, `rg5x` preset); see below.
- Entry points: `run_gui.bat` / `run_gui.py` (GUI), `python -m lsp.cli {inspect|convert|register|preview|full|all} --czi FILE --work DIR`.
- Pinned, tested stack in `requirements.txt` (Windows 11, Python 3.10; supports 3.10-3.12): zarr 2.18.3, dask, napari 0.7.1,
  aicspylibczi, pylibCZIrw. Install with `setup_env.ps1` (`-Gpu cuda13|cuda12` optional).

## Acquisition workflow (as stated by the user; details in `docs/Acquisition_Guide.md`)
Written iteratively with the user from a facility training session, then committed to `main`.
- **`rg5x` preset** preloads everything: 488 nm (~70 %) + 561 nm (~5 %) in one track, two cameras (Cam1 BP 505-545,
  Cam2 LP 585, SBS LP 560 splitter), **16-bit**, single-side illumination from the left, sheet 7.32 um, pivot scan on,
  zoom 0.36 -> 2.54 um pixels, 1920 x 1200 px frames, **z interval 7.320 um** (ignore ZEN's 3.515 um "optimal").
- Matching solution RI 1.51-1.52 (ZEN set to 1.52); 5x detection (EC Plan-Neofluar 5x/0.16 foc) and 5x illumination objectives.
  Swapping beam-forming objectives only matters if someone else changed them.
- **Light-sheet calibration** (Adjust Light Sheet wizard, chamber with medium only) every time the system boots: adjust x/y for
  both sides until a thin, high-intensity band sits near the crosshairs. The resulting Left/Right values are arbitrary per
  session; never copy them.
- **Tiling:** bounding grid from ~4 marked points (anterior, posterior, left and right at the widest part); typically
  **3 x 6 = 18 tiles, ~35 min**; overlap **10-25 %** (recent runs 25 %); bi-directional off. Saved as **one `.czi`**
  (no separate files), one time point.
- Typical run: **~1,274 slices, ~100 GB**. Saved to a portable hard drive, then moved into that mouse's folder.
- **File naming:** `<MouseID>_<Recording>_<YYYYMMDD>.czi`, e.g. `MMTC001_DIQB_20261123.czi`
  (`DIQB` = DiI-coated probes + quad-base Neuropixels). The user's own recordings will use `NP2`.
- Cleanup: remove the chamber in reverse order, rinse under warm water ~1 min, let dry, exit ZEN, turn off the system.

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
5. Acquisition guide, step 6: the user will supply a photo of a good (and bad) calibration band.

## Rules for working in this repo
- Treat the original `.czi` as read-only. Cleaning steps write a second copy of the tiles.
- Never load a whole volume into RAM; read chunks/planes, keep steps resumable and cancellable (see the generator pattern in `pipeline.py`).
- Register on the low-resolution copy, apply to full resolution.
- Record parameters in the work folder (`project.json`, `registration.json`, export JSONs) so runs stay reproducible.
- Test on synthetic data (`tests/test_core.py`) or a z-subset (`Project.z_start/z_stop`) before running on the full file.
- Guides are for non-programmers: numbered steps, pictures, plain language. The repo is **public**: crop out credentials,
  asset tags and personal info from photos and re-encode images to strip metadata before committing.
- Do not claim results on the user's real data unless you actually ran on it; the cloud session used to write this had no access to it.

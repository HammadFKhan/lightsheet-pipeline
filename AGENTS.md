# Agent context: lightsheet-pipeline

Orientation for AI agents (and humans) picking up this repository. Read `README.md` for the overview and
`Lightsheet_Guide.md` for the user guide and technical appendix (section 10). This file adds goals, state, how the pipeline was
built (and why), constraints and open items.

## Goal
End-to-end, GUI-driven pipeline that turns a very large (~200 GB) tiled ZEISS Lightsheet Z.1 `.czi` into a clean, stitched,
zoomable 3-D volume that the user can view and save, without loading the file into RAM. Dual-sided illumination is already fused by
the acquisition software, so sides are not fused again. Longer-term: analysis on the stitched volume (the user has not yet said
which analysis they want).

## User and data context (as stated by the user)
- Development workstation: Windows 11, Ryzen 9 9950X3D (16 cores), 125 GB RAM, RTX 5070 Ti 16 GB (CuPy `cuda13x`), NVMe SSD.
  The network inspects SSL, so `pip` needs `truststore` and command-line `git` needs `http.sslBackend=schannel`.
- Data (not in this repo) is under `D:\Ephys\WT\lightsheet\` (moved from `D:\Ephys\Practice\lightsheet\`): one `.czi` at
  `HFK001\HFK001_20261015_laser488_poswer70_exp40ms_dualsizefusion.czi`, work folder `HFK001_work` (`info.json` and `project.json`
  there still record the old path). Overlap, pixel size and tile count are in the CZI metadata: read them with the Inspect step.
- Tested dataset: 182 GB, 21 tiles of 1920 x 1200 px (3 columns x 7 rows), 2 channels (Cam1, Cam2), **1011 z-planes per tile**
  (about 1040 in the stitched volume because tiles are offset in z; the README's "1041" is the stitched figure), 20 % overlap,
  voxel 2.54 x 2.54 x 7.32 um, 5x/0.16, cleared mouse brain.
- The lab acquires on a **ZEISS Lightsheet 7** with **ZEN 3.1 (black edition)**, using the lab's managed preset **`rg5x`**
  (full procedure: `docs/Acquisition_Guide.md`). A `.czi` acquired that way is meant to work in the pipeline **out of the box**,
  with no conversion or manual settings. The README's "Lightsheet Z.1" wording predates this; same ZEISS `.czi` format.
- No Fiji on the development machine: plain ImageJ 1.54g plus the Bio-Formats plugin (needed for the BigTIFF exports). The
  pipeline itself needs neither.
- User preference: a GUI where data is loaded, processed and the result shown and saved. Python or ImageJ both acceptable.

## Current state
Built and run on the tested dataset on the development workstation (2026-10-19 to 10-25). Verification status is at the end of the
build history below. Implemented in `lsp/`:
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

## How the pipeline was built

### Order of work
1. **Survey (10-19).** Checked the machine and the CZI first. Chose pure Python (no Fiji/Java dependency). Streamed tiles with
   `aicspylibczi` (metadata via `pylibCZIrw`), so the 182 GB file is never loaded into RAM.
2. **Core on a subset (10-19).** Convert -> register -> fuse on 128 planes (z 400-528), checked a stitched plane by eye, then built the
   napari GUI.
3. **Usability (10-20).** Plane-level progress with ETA, immediate Stop, Z slider/play, horizontal/coronal/sagittal views,
   draggable histogram, compact dock layout, "already done" prompt.
4. **Preprocessing (10-20).** Flat-field, destripe and optional Richardson-Lucy deconvolution, with a "try on a region" test before
   committing to a full run. Writes `tiles_pp/`; raw tiles stay untouched.
5. **Export (10-20).** TIFF export tab with level, bit depth, compression, crop and size estimate.
6. **Image quality (10-20 to 10-25).** Investigated noise in coronal views, benchmarked denoisers, then added plane-gain correction and
   display-only TV denoise.
7. **Release (10-25).** Wrote `Lightsheet_Guide.md`, made install portable (`setup_env.ps1`, `run_gui.bat`), first commit `f1c2f0d`.
8. **Later (11-23).** A cloud session added `docs/Acquisition_Guide.md` and this file.

### Design decisions and why
| Area | What was built | Why |
|---|---|---|
| Storage | Zarr v2, blosc-zstd; tiles in 32x256x256 chunks plus a 4x xy-downsampled `_low` copy | Block access without loading volumes; cheap registration input |
| Resumability | Convert writes a done-marker holding the z range and downsample; fused outputs write `.zattrs` last | A subset or interrupted run is never mistaken for a finished one |
| Parallelism | `ProcessPoolExecutor`; steps are generators; workers report planes through progress files | Live ETA and cancel that terminates workers at once |
| Registration | Own translation-only solver: DoG + Tukey taper, 3-D phase correlation, Pearson *q*, weighted least squares | The first plan proposed `multiview-stitcher`; the built code does not use it |
| Outlier links | Drop the single worst-fitting link, re-solve, repeat until consistent (the `solve_global` docstring still says "one round") | One bad link must not drag its neighbours |
| Fusion | Linear feathering, block by block, then a mean-pooled pyramid (OME-NGFF 0.4) | Bounded memory |
| Preprocess order | plane-gain -> flat-field -> destripe -> deconvolution | Fix brightness before the spatial filters |
| GPU | Deconvolution and TV only, with CPU fallback | Measured ~130 vs 4.2 Mvox/s for deconvolution and ~37 Mvox/s for TV; convert and fuse are disk/codec bound |
| Deconvolution | FFT-based Richardson-Lucy, approximate Gaussian PSF, off by default | Blur is about one voxel at 5x/0.16, so expected gain is small |
| Denoising | TV (Chambolle), strength in units of tissue noise sigma, export/display only | See below |
| Export | BigTIFF per channel + settings JSON, written as `.partial` then renamed | Single file over 4 GB; a cancelled export leaves nothing half-written |

### What the data showed
- **Registration** (latest full run, preprocessed tiles): 30 of 32 neighbour links used, median *q* about 0.99. Both rejected links
  touch tile 18, which therefore sits at its stage position. The largest accepted pairwise correction was about 127 px (~320 um) in x,
  against a search limit of 160 px (`max_shift=(40, 40, 40)` in low-res units). On a new dataset, many rejected links may mean the
  limit is too tight.
- **Noise** is isotropic and brightness dependent: about 5 counts in dark background, 15-29 in tissue; camera floor about 205.
- **Dark coronal bands** were z-plane illumination dips that vary across a tile, so **one gain per plane overshot**. The fix is a
  smooth per-plane gain map (4 x 6 blocks, 61-plane running median reference). A 75th-percentile reference biased gains upward. The
  main dip in tile 7 went from -57 to -16 counts: much reduced, not gone.
- **Camera floor** estimates fail on a tile with no background, so `channel_floor` takes the 20th percentile across tiles.
- **Denoising** at ~2x noise reduction: TV (w 0.26) kept 66 % of the finest features without widening them; Gaussian (sigma 0.6) kept
  55 % and widened them 28 %. Median, wavelet and non-local means were not recommended. No single-image filter recovers buried
  detail, so quantify on un-denoised data. Full table: guide section 6 and `evaluation/results/benchmark.csv`.
- **Benchmarking pitfalls:** compare methods at matched noise reduction, average several noise seeds, and estimate noise from second
  differences (an earlier fit was inflated by image structure).
- **Destripe** on the first full preprocessing run: column-streak metric 2.51 -> 0.03 on one checked tile, mean brightness unchanged.
- **Timings and disk use:** guide section 8.

### Problems hit while building
- `pip` and `git` certificate errors: use `truststore` / `schannel`; never disable verification.
- Windows multiprocessing hangs without an `if __name__ == "__main__"` guard. A helper script named `inspect.py` shadowed the stdlib.
- napari: hidden layers still stretch the view, so the region test removes stitched layers from the list and restores them on Clear.
- Plain ImageJ cannot read BigTIFF. A "classic TIFF in parts" option was tried, then reverted at the user's request once
  Bio-Formats was installed.
- `setup_env.ps1`: a PowerShell function returned pip output together with the exit code; piping to `Out-Host` fixed it.

### Verification status
- `tests/test_core.py` passed 14/14 with a GPU (12 checks without) when the pipeline was moved into this repo.
- The existing `tiles_pp/` and `full_pp.zarr` in `HFK001_work` **predate plane-gain correction** (no `gains_*.npy`). To apply it, re-run
  Preprocess -> Register -> Fuse.
- Not done: a full-dataset run with the final code, a GUI re-check on the full dataset after the last edits, and an install test on a
  clean machine. No license file yet.

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
These came up in design discussions; check the code before assuming any exist.
- Self-supervised deep denoising (Noise2Void / CAREamics).
- Block-level resume for full-resolution fusion (an interrupted fusion restarts).
- Segmentation with Cellpose/StarDist/ilastik run blockwise (Dask `map_overlap` with halos, label merging across chunk borders).
- Atlas registration (brainreg/ANTs) if whole-brain mapping is wanted; vessel/neuron tracing.
- Non-translation (affine) tile registration; multi-band blending; BasicPy-style flat-field.
- Using `multiview-stitcher` or BigStitcher as a cross-check for registration quality.
- Support for multiple time points and other tile numbering schemes.

## Open questions for the user
1. Which analysis is the end goal (viewing only, cell counts, vasculature, atlas mapping, morphology)?
2. Is the full-resolution fused volume needed, or an overview plus full-resolution ROIs? (Drives disk use and run time.)
3. Acquisition guide, step 6: the user will supply a photo of a good (and bad) calibration band.

## Rules for working in this repo
- Treat the original `.czi` as read-only. Cleaning steps write a second copy of the tiles.
- Never load a whole volume into RAM; read chunks/planes, keep steps resumable and cancellable (see the generator pattern in `pipeline.py`).
- Register on the low-resolution copy, apply to full resolution.
- Record parameters in the work folder (`project.json`, `registration.json`, export JSONs) so runs stay reproducible.
- Test on synthetic data (`tests/test_core.py`) or a z-subset (`Project.z_start/z_stop`) before running on the full file.
- Guides are for non-programmers: numbered steps, pictures, plain language. The repo is **public**: crop out credentials,
  asset tags and personal info from photos and re-encode images to strip metadata before committing.
- Do not claim results on the user's real data unless you actually ran on it (the 11-23 cloud session had no access to it).
- Never end all `python.exe` processes: the user's GUI or a long run may be active. Stop only processes you started.

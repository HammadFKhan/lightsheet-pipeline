"""Approximate (Gaussian) light-sheet PSF from acquisition metadata.

Lateral FWHM ~ 0.51*lambda/NA (detection objective). Axial FWHM ~ light-sheet thickness
(the sheet, not the detection objective, sets axial resolution in light-sheet imaging).
This is an approximation: real PSFs in cleared tissue are usually broader (RI mismatch,
scattering), so FWHM values are editable in the GUI and a measured PSF would be better.
"""
from __future__ import annotations

import math

import numpy as np

DEFAULT_NA = 0.16          # EC Plan-Neofluar 5x/0.16
DEFAULT_EM_NM = 498.0
DEFAULT_SHEET_UM = 7.32


def theoretical_fwhm_um(info: dict | None = None):
    info = info or {}
    na = info.get("na") or DEFAULT_NA
    em = info.get("emission_nm") or DEFAULT_EM_NM
    sheet = info.get("sheet_um") or DEFAULT_SHEET_UM
    return dict(lateral=0.51 * em / 1000.0 / na, axial=float(sheet))


def fwhm_in_voxels(fwhm_lat_um, fwhm_ax_um, voxel_um_zyx):
    dz, dy, dx = voxel_um_zyx
    return fwhm_ax_um / dz, fwhm_lat_um / dy, fwhm_lat_um / dx


def gaussian_kernel(fwhm_lat_um, fwhm_ax_um, voxel_um_zyx, max_radius=(8, 12, 12)):
    """3-D Gaussian PSF integrated over each voxel (so sub-voxel PSFs become ~delta). Sum = 1."""
    sig_vox = [f / 2.3548 for f in fwhm_in_voxels(fwhm_lat_um, fwhm_ax_um, voxel_um_zyx)]
    axes = []
    for s, rmax in zip(sig_vox, max_radius):
        r = int(min(max(1, math.ceil(3.0 * s)), rmax))
        i = np.arange(-r, r + 1, dtype=np.float64)
        if s < 1e-3:
            w = (i == 0).astype(np.float64)
        else:
            cdf = np.vectorize(lambda t: 0.5 * (1 + math.erf(t / (s * math.sqrt(2)))))
            w = cdf(i + 0.5) - cdf(i - 0.5)
        axes.append(w / w.sum())
    k = axes[0][:, None, None] * axes[1][None, :, None] * axes[2][None, None, :]
    return (k / k.sum()).astype(np.float32)

"""ROI extraction, export and a simple generic segmentation/counting tool."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import zarr
from scipy import ndimage as ndi
from skimage import filters, measure


def read_roi(zpath, level, c, zyx_slices):
    g = zarr.open_group(str(zpath), mode="r")
    return np.asarray(g[str(level)][(c,) + tuple(zyx_slices)])


def save_tiff(arr, path, voxel_um):
    import tifffile
    dz, dy, dx = voxel_um
    tifffile.imwrite(path, arr, imagej=True, photometric="minisblack",
                     resolution=(1 / dx, 1 / dy),
                     metadata=dict(axes="ZYX", spacing=dz, unit="um"))


def segment_count(vol, sigma=1.5, threshold=None, min_voxels=20, voxel_um=(1, 1, 1)):
    """Gaussian smooth -> threshold (Otsu if None) -> connected components -> per-object table."""
    v = ndi.gaussian_filter(vol.astype(np.float32), (sigma * voxel_um[1] / voxel_um[0], sigma, sigma))
    thr = filters.threshold_otsu(v) if threshold is None else threshold
    lab, n = ndi.label(v > thr)
    sizes = np.bincount(lab.ravel())
    small = np.where(sizes < min_voxels)[0]
    small = small[small > 0]
    if len(small):
        lab[np.isin(lab, small)] = 0
        lab, _ = ndi.label(lab > 0)
    props = measure.regionprops_table(lab, intensity_image=vol,
                                      properties=("label", "area", "centroid", "mean_intensity", "max_intensity"))
    return lab.astype(np.int32), props, float(thr)


def save_table(props, path, voxel_um):
    dz, dy, dx = voxel_um
    keys = list(props)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(keys + ["volume_um3", "z_um", "y_um", "x_um"])
        for r in range(len(props["label"])):
            row = [props[k][r] for k in keys]
            w.writerow(row + [props["area"][r] * dz * dy * dx, props["centroid-0"][r] * dz,
                              props["centroid-1"][r] * dy, props["centroid-2"][r] * dx])

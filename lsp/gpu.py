"""Optional GPU backend (CuPy). Everything works on CPU if CuPy/CUDA is unavailable."""
from __future__ import annotations

import numpy as np
import scipy.ndimage as _ndi


def gpu_info() -> str | None:
    """Device name if a usable CUDA GPU + CuPy is present, else None."""
    try:
        import cupy as cp
        if cp.cuda.runtime.getDeviceCount() < 1:
            return None
        name = cp.cuda.runtime.getDeviceProperties(0)["name"]
        return name.decode() if isinstance(name, bytes) else str(name)
    except Exception:
        return None


def backend(use_gpu: bool):
    """Return (xp, ndi, is_gpu). Falls back to NumPy/SciPy when the GPU is off or unavailable."""
    if use_gpu and gpu_info():
        import cupy as cp
        from cupyx.scipy import ndimage as cndi
        return cp, cndi, True
    return np, _ndi, False


def to_cpu(a):
    try:
        import cupy as cp
        if isinstance(a, cp.ndarray):
            return cp.asnumpy(a)
    except Exception:
        pass
    return np.asarray(a)


def free_gpu_memory():
    try:
        import cupy as cp
        cp.get_default_memory_pool().free_all_blocks()
    except Exception:
        pass

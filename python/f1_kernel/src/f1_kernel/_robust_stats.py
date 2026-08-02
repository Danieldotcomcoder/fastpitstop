"""Precisely-typed wrappers around the handful of scipy robust-regression
calls the kernel uses (Theil-Sen in `fuel.py` and `degradation.py`).

scipy's own signatures for these are only partially annotated (no
`py.typed` marker, and what stubs exist leave the return type a bare
`Unknown`/`object`), which under pyright's STRICT mode would otherwise leak
`Unknown` through every line downstream of a call. Confining the
`# type: ignore` comments to this one module keeps that noise from
spreading into the actual analytics.
"""

from __future__ import annotations

import numpy as np
from scipy import stats


def theilslopes(y: np.ndarray, x: np.ndarray, alpha: float) -> tuple[float, float, float, float]:
    """`(slope, intercept, ci_low, ci_high)` from `scipy.stats.theilslopes`,
    as plain Python floats."""
    slope, intercept, ci_low, ci_high = stats.theilslopes(y, x, alpha=alpha)  # type: ignore[arg-type]
    return float(slope), float(intercept), float(ci_low), float(ci_high)  # type: ignore[arg-type]

"""Fit the screening regressions and calculate observed-to-expected stay ratios."""
import warnings
import numpy as np


def lstsq(X, y):
    X, y = np.asarray(X, float), np.asarray(y, float)
    if X.ndim != 2 or y.ndim != 1 or X.shape[0] != len(y):
        raise ValueError('Expected a design matrix and matching outcome vector')
    n, k = X.shape
    if k == 0 or n < k:
        raise ValueError('Empty or underdetermined screening design')
    if not np.isfinite(X).all() or not np.isfinite(y).all():
        raise ValueError('Nonfinite screening input')
    beta, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    if rank != k:
        raise ValueError('Screening design matrix is rank deficient')
    if not np.isfinite(beta).all():
        raise ValueError('Nonfinite screening coefficients')
    return beta


def components(observed_alos, observed_dtc, expected_alos, expected_dtc):
    arrays = [np.asarray(v, float) for v in
              (observed_alos, observed_dtc, expected_alos, expected_dtc)]
    if any(a.ndim != 1 or a.size == 0 for a in arrays) or len({a.shape for a in arrays}) != 1:
        raise ValueError('Screening observations and predictions must be matching nonempty vectors')
    if not all(np.isfinite(a).all() for a in arrays):
        raise ValueError('Nonfinite screening observations or predictions')
    yA, yD, eA, eD = arrays
    if np.any(eA <= 0):
        raise ValueError('Expected length of stay must be positive to calculate the observed-to-expected ratio')
    with np.errstate(over='ignore', divide='ignore', invalid='ignore'):
        ratio, residual = yA / eA, yD - eD
    if not np.isfinite(ratio).all() or not np.isfinite(residual).all():
        raise ValueError('Nonfinite screening ratio or residual')
    return ratio, residual


def r_squared(y, expected):
    y, expected = np.asarray(y, float), np.asarray(expected, float)
    if y.ndim != 1 or y.size == 0 or expected.shape != y.shape:
        raise ValueError('R-squared requires matching nonempty vectors')
    if not np.isfinite(y).all() or not np.isfinite(expected).all():
        raise ValueError('Nonfinite R-squared input')
    if np.all(y == y[0]):
        warnings.warn('R-squared is undefined for a constant outcome; returning NaN', RuntimeWarning)
        return np.nan
    with np.errstate(over='ignore', divide='ignore', invalid='ignore'):
        sse, sst = ((y - expected) ** 2).sum(), ((y - y.mean()) ** 2).sum()
        result = 1 - sse / sst
    if not np.isfinite(sse) or not np.isfinite(sst) or not np.isfinite(result):
        raise ValueError('Nonfinite R-squared calculation')
    return result

"""Strict CR1 input/output checks for current analyses; legacy engine stays unchanged.
No row/term removal or estimator redesign. Zero weights require explicit upstream handling.
Rank uses numpy's default tolerance. Full-rank weighted designs are solved directly by SVD;
CR1 uses cluster-summed observation influences, avoiding a squared-condition Gram inverse.
This does not eliminate intrinsic near-collinearity or establish small-cluster validity.
"""

import warnings
from numbers import Real

import numpy as np
import pandas as pd

import irf_common as legacy


def ols_cluster(y, X, groups, w=None):
    """OLS / WLS with CR1 cluster-robust covariance. Returns beta, se, dof (G-1), r2."""
    y, X = np.asarray(y, float), np.asarray(X, float)
    groups = np.asarray(groups)
    if y.ndim != 1 or X.ndim != 2 or groups.ndim != 1:
        raise ValueError(
            "Expected y/groups vectors and a two-dimensional design matrix"
        )
    n, k = X.shape
    if len(y) != n or len(groups) != n:
        raise ValueError("Outcome, design and cluster lengths must match")
    if k == 0 or n <= k:
        raise ValueError("CR1 requires a nonempty design and n > k")
    if not np.isfinite(y).all() or not np.isfinite(X).all():
        raise ValueError("Outcome and design must contain only finite values")
    if pd.isna(groups).any() or any(
        isinstance(g, str) and not g.strip() for g in groups
    ):
        raise ValueError("Cluster identifiers must not be missing or blank")
    if any(isinstance(g, Real) and not np.isfinite(g) for g in groups):
        raise ValueError("Numeric cluster identifiers must be finite")
    if len(pd.unique(groups)) < 2:
        raise ValueError("CR1 requires at least two clusters")
    w = np.ones(n) if w is None else np.asarray(w, float)
    if w.ndim != 1 or len(w) != n:
        raise ValueError("Weights must be a vector matching the observation count")
    if not np.isfinite(w).all() or np.any(w <= 0):
        raise ValueError(
            "Weights must be finite and strictly positive; zero-weight policy requires explicit handling"
        )
    sw = np.sqrt(w)
    Xw, yw = X * sw[:, None], y * sw
    if not np.isfinite(Xw).all() or not np.isfinite(yw).all():
        raise ValueError("Weighted inputs overflowed")
    if np.linalg.matrix_rank(Xw) != k:
        raise ValueError(
            "Rank-deficient weighted design; do not silently remove model terms"
        )
    # Do not square the condition number or apply a second, incompatible cutoff.
    # The full-rank check above uses NumPy's existing matrix_rank tolerance.
    U, singular, Vt = np.linalg.svd(Xw, full_matrices=False)
    inverse = (Vt.T / singular) @ U.T
    beta = inverse @ yw
    resid = y - X @ beta
    # Each row is that observation's contribution to the coefficient error.
    influences = (inverse * (sw * resid)[None, :]).T
    G = pd.DataFrame(influences).groupby(np.asarray(groups)).sum().to_numpy()
    n, k = X.shape
    g = G.shape[0]
    c = (g / (g - 1)) * ((n - 1) / (n - k))
    V = c * (G.T @ G)
    if not np.isfinite(beta).all() or not np.isfinite(V).all():
        raise ValueError("Nonfinite coefficient or covariance output")
    variance = np.diag(V)
    if np.any(variance < 0):
        raise ValueError(
            "Negative coefficient variance; investigate numerical stability"
        )
    ybar = np.average(y, weights=w)
    sse = np.sum(w * resid**2)
    sst = np.sum(w * (y - ybar) ** 2)
    if not np.isfinite(sse) or not np.isfinite(sst):
        raise ValueError("Nonfinite residual or total sum of squares")
    if np.all(y == y[0]) or sst == 0:
        warnings.warn(
            "R-squared is undefined for a constant outcome; returning NaN",
            RuntimeWarning,
        )
        r2 = np.nan
    else:
        r2 = 1 - sse / sst
        if not np.isfinite(r2):
            raise ValueError("Nonfinite R-squared")
    return beta, np.sqrt(variance), g - 1, r2


def fit(s, ycol, terms, state_fe=True, w=None):
    if len(terms) != len(set(terms)):
        raise ValueError("Duplicate model terms")
    if isinstance(w, pd.Series) and not w.index.equals(s.index):
        raise ValueError("Weight index must match sample index")
    X, names = legacy.design(s, terms, state_fe)
    beta, se, dof, r2 = ols_cluster(
        s[ycol].to_numpy(float), X, s["state"].to_numpy(), w
    )
    return legacy.tidy(names, beta, se, dof).set_index("term"), r2, dof + 1

"""Post-results functional-form sensitivity for the group/concurrent therapy share."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.interpolate import CubicSpline
from scipy.stats import t
from statsmodels.stats.multitest import multipletests

from irf_common import design, imp_terms
from mode_mix_post_audit import ADJUST, EXPOSURES, OUTCOMES

KNOTS = np.array([0.0, 5.0, 15.0, 30.0])
MODEL = "A_complete_composition"
NAMES = {
    "DTC": "Community discharge",
    "RSRR": "Readmission",
    "PAY": "Standardized payment",
}


def basis(share):
    """Truncated powers with linear tails. Inputs are percentage points."""
    x = np.asarray(share) / 10
    knots = KNOTS / 10
    cubes = np.maximum(x[:, None] - knots, 0) ** 3
    fractions = (knots[-1] - knots[:2]) / (knots[-1] - knots[-2])
    bends = cubes[:, :2] - cubes[:, [-2]] * fractions + cubes[:, [-1]] * (fractions - 1)
    return np.column_stack([x, bends])


def cardinal(share):
    """Independent natural cubic interpolation of values at the four knots."""
    spline = CubicSpline(KNOTS, np.eye(len(KNOTS)), bc_type="natural")
    boundary = np.clip(share, KNOTS[0], KNOTS[-1])
    values = spline(boundary) + (share - boundary)[:, None] * spline(boundary, 1)
    return values[:, 1:]  # the intercept supplies the remaining constant dimension


def clustered_fit(y, X, groups):
    if np.linalg.matrix_rank(X) != X.shape[1]:
        raise ValueError("Rank-deficient design")
    result = sm.OLS(y, X).fit(
        cov_type="cluster",
        cov_kwds={"groups": groups, "use_correction": True},
        use_t=True,
    )
    np.testing.assert_equal(result.df_resid_inference, len(np.unique(groups)) - 1)
    return result


def contrast(fit, vectors):
    # The first three columns span the exposure curve. All other columns cancel.
    coef = fit.params[:3]
    cov = fit.cov_params()[:3, :3]
    means = vectors @ coef
    variances = np.einsum("ij,jk,ik->i", vectors, cov, vectors)
    if (variances < -1e-10).any():
        raise ValueError("Negative contrast variance")
    errors = t.ppf(0.975, fit.df_resid_inference) * np.sqrt(np.maximum(variances, 0))
    return np.column_stack([means, means - errors, means + errors])


def curvature_test(fit, constraints):
    matrix = np.zeros((2, len(fit.params)))
    matrix[:, :3] = constraints
    if np.linalg.matrix_rank(matrix @ fit.cov_params() @ matrix.T) != 2:
        raise ValueError("Non-estimable curvature test")
    test = fit.f_test(matrix)
    return float(test.fvalue), float(test.pvalue)


def curve_vectors(build, grid):
    at = build(np.array([0.0, 10.0, 20.0, 30.0]))
    low, high = at[1] - at[0], at[3] - at[2]
    return np.vstack([low, high, high - low, build(grid) - at[0]])


def main():
    results_dir = Path("data/processed/mode_mix_post_audit")
    cohort = pd.read_csv(
        results_dir / "cohort_private.csv", dtype={"ccn": str}
    ).set_index("ccn")
    members = pd.read_csv(
        results_dir / "model_membership_private.csv", dtype={"ccn": str}
    )
    primary = pd.read_csv(results_dir / "primary_12.csv").set_index(["outcome", "term"])
    if (
        not cohort.index.is_unique
        or not primary.index.is_unique
        or not primary.model.eq(MODEL).all()
    ):
        raise ValueError("Invalid source records")
    terms = EXPOSURES + ADJUST + imp_terms(cohort)
    tests, effects, support = [], [], []
    fig, axes = plt.subplots(
        2,
        3,
        figsize=(12, 5.7),
        sharex="col",
        height_ratios=[3, 1],
        layout="constrained",
    )
    for i, (outcome, ycol) in enumerate(OUTCOMES.items()):
        ids = members.loc[
            (members.model == MODEL) & (members.outcome == outcome), "ccn"
        ]
        if not ids.is_unique or set(ids) != set(
            cohort.dropna(subset=[ycol, "state", *terms]).index
        ):
            raise ValueError(f"Sample differs from complete Model A for {outcome}")
        s = cohort.loc[ids]
        x = 10 * s.group10.to_numpy()
        if x.min() != 0 or x.max() < KNOTS[-1]:
            raise ValueError(
                "The planned endpoints require observations at zero and through 30%"
            )
        y, groups = s[ycol].to_numpy(), s.state.to_numpy()
        X, names = design(s, terms, True)
        linear = clustered_fit(y, X, groups)
        pos = names.index("group10")
        saved = primary.loc[(outcome, "group10")]
        np.testing.assert_allclose(
            [linear.params[pos], linear.bse[pos], *linear.conf_int()[pos]],
            saved[["coef", "se", "ci_low", "ci_high"]].astype(float),
            atol=1e-6,
            rtol=0,
        )
        np.testing.assert_equal([len(s), s.state.nunique()], [saved.n, saved.clusters])
        other = np.delete(X, pos, axis=1)
        fit = clustered_fit(y, np.column_stack([basis(x), other]), groups)
        check = clustered_fit(y, np.column_stack([cardinal(x), other]), groups)
        np.testing.assert_allclose(
            fit.fittedvalues, check.fittedvalues, atol=1e-7, rtol=0
        )
        fval, p = curvature_test(fit, np.eye(3)[1:])
        slopes = np.diff(cardinal(KNOTS), axis=0) / np.diff(KNOTS)[:, None]
        np.testing.assert_allclose(
            [fval, p], curvature_test(check, np.diff(slopes, axis=0)), atol=1e-7, rtol=0
        )
        tests.append((outcome, len(s), s.state.nunique(), fval, p))
        grid = np.linspace(0.0, x.max(), 251)
        estimates = contrast(fit, curve_vectors(basis, grid))
        np.testing.assert_allclose(
            estimates, contrast(check, curve_vectors(cardinal, grid)), atol=1e-7, rtol=0
        )
        factor, digits = (1000, 0) if outcome == "PAY" else (1, 3)
        cells = []
        for mean, lo, hi in estimates[:3] * factor:
            cells.append(f"{mean:+.{digits}f} [{lo:+.{digits}f}, {hi:+.{digits}f}]")
        effects.append("| " + " | ".join([outcome, *cells]) + " |")
        bins = [
            np.sum(x == 0),
            *[
                np.sum((x > lo) & (x <= hi))
                for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 30), (30, np.inf)]
            ],
        ]
        np.testing.assert_equal(sum(bins), len(s))
        support.append(
            "| " + " | ".join(map(str, [outcome, *bins])) + f" | {x.max():.2f} |"
        )
        ax, hist = axes[:, i]
        mean, low, high = (estimates[3:] * factor).T
        ax.fill_between(
            grid, low, high, color="#a5d5d4", alpha=0.65, label="Pointwise 95% CI"
        )
        ax.plot(grid, mean, color="#12696b", lw=2.3, label="Spline")
        ax.plot(
            grid,
            grid / 10 * linear.params[pos] * factor,
            "--",
            color="#8c492a",
            label="Linear",
        )
        ax.axhline(0, color="0.4", lw=0.7)
        ax.set(
            title=NAMES[outcome],
            ylabel="Dollars per stay" if outcome == "PAY" else "Percentage points",
        )
        if i == 0:
            ax.legend(frameon=False, fontsize=8)
        hist.hist(x, bins=np.arange(0, x.max() + 2, 2), color="#577586")
        hist.set(
            xlabel="Group/concurrent share (%)",
            ylabel="Facilities",
            xlim=(-1, np.ceil(x.max())),
        )
    fig.suptitle(
        "Adjusted difference relative to a facility at 0% group/concurrent therapy"
    )
    figpath = Path("reports/figures/group_share_nonlinearity.png")
    figpath.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figpath, dpi=170, facecolor="white")
    plt.close(fig)

    holm = multipletests([row[-1] for row in tests], method="holm")[1]
    rows = [
        f"| {outcome} | {n} | {g} | {fval:.3f} | {p:.4g} | {adj:.4g} |"
        for (outcome, n, g, fval, p), adj in zip(tests, holm)
    ]
    report = [
        "# Does the association change across the group/concurrent therapy range?",
        "",
        "This post-results sensitivity analysis fits a restricted cubic spline in place of Model A’s linear "
        "group/concurrent term. The 0%, 5%, 15% and 30% knots were fixed before fitting the curves. "
        "Each fit uses the original facilities, other therapy shares, covariates, state fixed effects and outcome scale.",
        "",
        "| Outcome | Facilities | State groups | Curvature F | Raw p | Holm p |",
        "|---|---:|---:|---:|---:|---:|",
        *rows,
        "",
        "The state-clustered CR1 Wald F test compares the curve with a straight line by jointly testing two additional "
        "terms (2 numerator degrees of freedom, number of state groups minus one denominator degrees of freedom). "
        "Holm adjustment covers these three curvature tests. The original BH family covers the primary linear associations.",
        "",
        "## Two ten-point comparisons",
        "",
        "| Outcome | 0% to 10% [95% CI] | 20% to 30% [95% CI] | Second change minus first [95% CI] |",
        "|---|---:|---:|---:|",
        *effects,
        "",
        "The entries are adjusted facility-level differences. DTC and RSRR use percentage points. PAY uses dollars "
        "per stay. Each interval uses a state-clustered t critical value and the covariance of the full contrast. "
        "These pointwise intervals are unadjusted for multiple comparisons.",
        "",
        "![Adjusted curves, pointwise intervals, linear comparators and exposure histograms](../reports/figures/group_share_nonlinearity.png)",
        "",
        "## Exposure support",
        "",
        "| Outcome | Exactly 0% | (0, 5]% | (5, 10]% | (10, 20]% | (20, 30]% | Above 30% | Maximum (%) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *support,
        "",
        "The curve is continuous at zero and constrained to be linear above 30%. The zero-width interval at 0% follows "
        "from using 0% as the reference. Histograms show the observed facilities in two-percentage-point bins. "
        "Sparse upper-tail data limit interpretation. The selected knots do not establish clinical thresholds.",
        "",
        "Reproduce from the repository root with `python src/group_share_nonlinearity.py`. "
        "Checks compare the original linear estimate and interval with Model A and reproduce spline fits, curvature tests "
        "and contrasts using an independent natural-cubic interpolation basis.",
        "",
    ]
    Path("docs/group_share_nonlinearity.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print("\n".join(rows + effects + support))


if __name__ == "__main__":
    main()

"""Compare linear and spline fits for the share of group or concurrent therapy."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import statsmodels.api as sm
from scipy.stats import t
from statsmodels.stats.multitest import multipletests

from irf_common import design, imp_terms
from mode_mix_io import load_inputs, load_outcome_sample
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


def clustered_fit(y, X, groups):
    if np.linalg.matrix_rank(X) != X.shape[1]:
        raise ValueError("Rank-deficient design")
    result = sm.OLS(y, X).fit(
        cov_type="cluster",
        cov_kwds={"groups": groups, "use_correction": True},
        use_t=True,
    )
    if result.df_resid_inference != len(np.unique(groups)) - 1:
        raise ValueError("Unexpected clustered degrees of freedom")
    return result


def contrast(fit, vectors):
    # The first three columns span the exposure curve. All other columns cancel.
    coef = fit.params[:3]
    cov = fit.cov_params()[:3, :3]
    means = vectors @ coef
    variances = np.sum((vectors @ cov) * vectors, axis=1)
    if (variances < -1e-10).any():
        raise ValueError("Negative contrast variance")
    errors = t.ppf(0.975, fit.df_resid_inference) * np.sqrt(np.maximum(variances, 0))
    return np.column_stack([means, means - errors, means + errors])


def curvature_test(fit):
    matrix = np.zeros((2, len(fit.params)))
    matrix[:, 1:3] = np.eye(2)  # Jointly test the two nonlinear spline terms.
    if np.linalg.matrix_rank(matrix @ fit.cov_params() @ matrix.T) != 2:
        raise ValueError("Non-estimable curvature test")
    test = fit.f_test(matrix)
    return float(test.fvalue), float(test.pvalue)


def curve_vectors(grid):
    at = basis(np.array([0.0, 10.0, 20.0, 30.0]))
    low, high = at[1] - at[0], at[3] - at[2]
    return np.vstack([low, high, high - low, basis(grid) - at[0]])


def main():
    cohort, members, primary = load_inputs()
    if not primary.model.eq(MODEL).all():
        raise ValueError("Expected Model A primary estimates")
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
        s = load_outcome_sample(cohort, members, MODEL, outcome)
        eligible = cohort.dropna(subset=[ycol, "state", *terms])
        if set(s.index) != set(eligible.index):
            raise ValueError(f"Sample differs from complete Model A for {outcome}")
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
        if not np.allclose(
            [linear.params[pos], linear.bse[pos], *linear.conf_int()[pos]],
            saved[["coef", "se", "ci_low", "ci_high"]].astype(float),
            atol=1e-6,
            rtol=0,
        ):
            raise ValueError(f"Linear baseline differs from Model A for {outcome}")
        if (len(s), s.state.nunique()) != (saved.n, saved.clusters):
            raise ValueError(f"Model A sample dimensions differ for {outcome}")
        other = np.delete(X, pos, axis=1)
        fit = clustered_fit(y, np.column_stack([basis(x), other]), groups)
        fval, p = curvature_test(fit)
        tests.append((outcome, len(s), s.state.nunique(), fval, p))
        grid = np.linspace(0.0, x.max(), 251)
        estimates = contrast(fit, curve_vectors(grid))
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
        if sum(bins) != len(s):
            raise ValueError(f"Exposure bins do not cover every facility for {outcome}")
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
        "Adjusted facility-level differences are reported in percentage points for DTC and RSRR and in dollars per stay "
        "for PAY. The pointwise intervals use the state-clustered covariance of the full contrast and a t critical value "
        "with degrees of freedom equal to the number of state groups minus one. There is no multiple-comparison adjustment "
        "for these intervals.",
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
        "Run `python src/group_share_nonlinearity.py` to reproduce the table and figure.",
        "",
    ]
    Path("docs/group_share_nonlinearity.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print("\n".join(rows + effects + support))


if __name__ == "__main__":
    main()

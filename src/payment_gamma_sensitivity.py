"""Compare Model A payment estimates with a Gamma GLM on the same facilities.

Run from the project root: python src/payment_gamma_sensitivity.py
"""

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import t

from irf_common import imp_terms
from mode_mix_post_audit import ADJUST, EXPOSURES, OUTCOMES, checked_fit

SOURCE = Path("data/processed/mode_mix_post_audit")
MODEL = "A_complete_composition"
LABELS = {
    "group10": "Group/concurrent (+10 pp)",
    "cotreat5": "Co-treatment (+5 pp)",
    "pt5": "PT share (+5 pp)",
    "slp5": "SLP share (+5 pp)",
}


def check_covariance(X, y, mu, groups, covariance):
    # Gamma log-link score and observed information. Dispersion cancels.
    ratio = y / mu
    scores = X * (ratio - 1)[:, None]
    cluster_scores = pd.DataFrame(scores).groupby(groups).sum().to_numpy()
    information = X.T @ (ratio[:, None] * X)
    influence = np.linalg.solve(information, cluster_scores.T)
    n, k = X.shape
    g = len(cluster_scores)
    expected = (g / (g - 1)) * ((n - 1) / (n - k)) * (influence @ influence.T)
    np.testing.assert_allclose(covariance, expected, rtol=1e-6, atol=1e-10)


def dollar_contrast(fit, X, j, critical):
    shifted = X.copy()
    shifted[:, j] += 1
    base = fit.fittedvalues
    higher = fit.predict(shifted)
    diff = 1000 * np.mean(higher - base)
    gradient = 1000 * np.mean(higher[:, None] * shifted - base[:, None] * X, axis=0)

    # With a log link and no interactions, a one-unit change multiplies all means equally.
    beta = fit.params

    def contrast(b):
        return 1000 * np.mean(np.exp(X @ b)) * np.expm1(b[j])

    np.testing.assert_allclose(diff, contrast(beta), rtol=1e-10, atol=1e-8)
    step = 1e-20
    directions = np.eye(len(beta))
    numerical = [contrast(beta + 1j * step * e).imag / step for e in directions]
    np.testing.assert_allclose(gradient, numerical, rtol=1e-8, atol=1e-7)
    se = np.sqrt(gradient @ fit.cov_params() @ gradient)
    if not np.isfinite(se) or se <= 0:
        raise ValueError("Invalid dollar-contrast standard error")
    return diff, diff - critical * se, diff + critical * se


def interval(values):
    estimate, low, high = values
    return f"{estimate:+,.0f} [{low:+,.0f}, {high:+,.0f}]"


def main():
    results_dir = SOURCE
    d = pd.read_csv(results_dir / "cohort_private.csv", dtype={"ccn": str}).set_index(
        "ccn"
    )
    members = pd.read_csv(
        results_dir / "model_membership_private.csv", dtype={"ccn": str}
    )
    ids = members.loc[(members.model == MODEL) & (members.outcome == "PAY"), "ccn"]
    ycol = OUTCOMES["PAY"]
    terms = EXPOSURES + ADJUST + imp_terms(d)
    complete = d.dropna(subset=[ycol, "state", *terms])
    if not d.index.is_unique or not ids.is_unique or set(ids) != set(complete.index):
        raise ValueError("Inconsistent Model A payment membership")
    sample = d.loc[ids]
    y = sample[ycol].to_numpy()
    if np.any(y <= 0):
        raise ValueError("Gamma payment analysis requires positive payments")
    (ols, _, g), X, names = checked_fit(sample, ycol, terms)
    primary = pd.read_csv(results_dir / "primary_12.csv")
    primary = primary.loc[
        (primary.model == MODEL) & (primary.outcome == "PAY")
    ].set_index("term")
    if (
        len(primary) != len(EXPOSURES)
        or set(primary.index) != set(EXPOSURES)
        or not primary.n.eq(len(y)).all()
        or not primary.clusters.eq(g).all()
    ):
        raise ValueError("Inconsistent source payment estimates")
    columns = ["coef", "ci_low", "ci_high"]
    np.testing.assert_allclose(
        ols.loc[EXPOSURES, columns], primary.loc[EXPOSURES, columns], rtol=0, atol=1e-6
    )

    groups = sample.state.to_numpy()
    model = sm.GLM(y, X, family=sm.families.Gamma(link=sm.families.links.Log()))
    fit = model.fit(
        maxiter=200,
        tol=1e-11,
        cov_type="cluster",
        cov_kwds={"groups": groups, "use_correction": True},
        use_t=True,
    )
    if not fit.converged or not np.isfinite(fit.params).all():
        raise RuntimeError("The Gamma model did not produce a converged finite fit")
    check_covariance(X, y, fit.fittedvalues, groups, fit.cov_params())

    rows = []
    critical = t.ppf(0.975, g - 1)
    for term in EXPOSURES:
        gamma = dollar_contrast(fit, X, names.index(term), critical)
        linear = primary.loc[term, columns].to_numpy(float) * 1000
        rows.append(f"| {LABELS[term]} | {interval(linear)} | {interval(gamma)} |")

    report = [
        "# Sensitivity of Medicare payment estimates to the choice of model",
        "",
        f"Model A compared standardized Medicare payment per stay across {len(sample):,} facilities in {g} state-level groups. "
        "Its payment residuals had a long right tail and greater spread at high fitted values. "
        "This post-results sensitivity analysis uses the same positive payments, facilities, state fixed effects and adjustment "
        "variables. A Gamma model with a log link allows proportional changes in mean payment. "
        "[Original residual diagnostics](model_a_diagnostics.md)",
        "",
        "## Adjusted difference in dollars per stay",
        "",
        "| Higher share of recorded therapy minutes | Original OLS [95% CI] | Gamma with log link [95% CI] |",
        "|---|---:|---:|",
        *rows,
        "",
        "Each row raises the named therapy share by the stated number of percentage points (pp), holds the other model "
        "columns fixed and averages the predicted dollar difference equally across the included facilities. "
        "For PT and SLP, occupational therapy is the reference discipline. These modeled contrasts can extend beyond "
        "observed exposure combinations and describe facility-level associations.",
        "",
        "Intervals use state-clustered sandwich covariance with the CR1 correction. Gamma dollar contrasts use the "
        f"delta method conditional on the observed facility profiles and a t critical value with {g - 1} degrees of freedom. "
        "All four intervals are pointwise. The original BH correction covers the primary Model A family only.",
        "",
        "## Reproduction",
        "",
        f"From the repository root run `python src/payment_gamma_sensitivity.py` after building Model A. "
        f"The Gamma fit converged in {fit.fit_history['iteration']} iterations. "
        "The script checks the OLS contrasts against the saved primary table, the Gamma covariance against the clustered "
        "score calculation and the dollar contrasts and gradients against a separate log-link calculation.",
        "",
    ]
    Path("docs/payment_gamma_sensitivity.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print("\n".join(rows))


if __name__ == "__main__":
    main()

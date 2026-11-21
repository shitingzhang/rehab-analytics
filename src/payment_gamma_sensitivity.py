"""Compare Model A payment estimates with a Gamma GLM on the same facilities.

Run from the project root: python src/payment_gamma_sensitivity.py
"""

from pathlib import Path

import numpy as np
import statsmodels.api as sm
from scipy.stats import t

from irf_common import imp_terms
from mode_mix_io import load_inputs, load_outcome_sample
from mode_mix_post_audit import ADJUST, EXPOSURES, OUTCOMES, checked_fit

MODEL = "A_complete_composition"
LABELS = {
    "group10": "Group/concurrent (+10 pp)",
    "cotreat5": "Co-treatment (+5 pp)",
    "pt5": "PT share (+5 pp)",
    "slp5": "SLP share (+5 pp)",
}


def dollar_contrast(fit, X, j, critical):
    shifted = X.copy()
    shifted[:, j] += 1
    base = fit.fittedvalues
    higher = fit.predict(shifted)
    diff = 1000 * np.mean(higher - base)
    # With a log link, the gradient of each predicted mean is that mean times its design row.
    gradient = 1000 * np.mean(higher[:, None] * shifted - base[:, None] * X, axis=0)
    se = np.sqrt(gradient @ fit.cov_params() @ gradient)
    if not np.isfinite(se) or se <= 0:
        raise ValueError("Invalid dollar-contrast standard error")
    return diff, diff - critical * se, diff + critical * se


def interval(values):
    estimate, low, high = values
    return f"{estimate:+,.0f} [{low:+,.0f}, {high:+,.0f}]"


def main():
    d, members, primary = load_inputs()
    sample = load_outcome_sample(d, members, MODEL, "PAY")
    ycol = OUTCOMES["PAY"]
    terms = EXPOSURES + ADJUST + imp_terms(d)
    complete = d.dropna(subset=[ycol, "state", *terms])
    if set(sample.index) != set(complete.index):
        raise ValueError("Inconsistent Model A payment membership")
    y = sample[ycol].to_numpy()
    if np.any(y <= 0):
        raise ValueError("Gamma payment analysis requires positive payments")
    (ols, _, g), X, names = checked_fit(sample, ycol, terms)
    primary = primary.loc[
        (primary.model == MODEL) & (primary.index.get_level_values("outcome") == "PAY")
    ].droplevel("outcome")
    if (
        len(primary) != len(EXPOSURES)
        or set(primary.index) != set(EXPOSURES)
        or not primary.n.eq(len(y)).all()
        or not primary.clusters.eq(g).all()
    ):
        raise ValueError("Inconsistent source payment estimates")
    columns = ["coef", "ci_low", "ci_high"]
    if not np.allclose(ols.loc[EXPOSURES, columns], primary.loc[EXPOSURES, columns], rtol=0, atol=1e-6):
        raise ValueError("Refitted payment estimates differ from Model A")

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

    rows = []
    critical = t.ppf(0.975, g - 1)
    for term in EXPOSURES:
        gamma = dollar_contrast(fit, X, names.index(term), critical)
        linear = primary.loc[term, columns].to_numpy(float) * 1000
        rows.append(f"| {LABELS[term]} | {interval(linear)} | {interval(gamma)} |")

    report = [
        "# Sensitivity of Medicare payment estimates to the choice of model",
        "",
        f"Across the {len(sample):,} facilities in {g} state-level groups, Model A’s payment residuals had a long right tail "
        "and greater spread at higher fitted values. This post-results sensitivity analysis fits a Gamma model with a log "
        "link to allow proportional changes in mean standardized Medicare payment per stay, using the same positive payments, "
        "facilities, state fixed effects and adjustment variables. "
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
        "Intervals use state-clustered sandwich covariance with the CR1 correction. The four pointwise Gamma intervals "
        "use the delta method conditional on the observed facility profiles and a t critical value with "
        f"{g - 1} degrees of freedom. The original BH correction covers the primary Model A family only.",
        "",
        "## Reproduction",
        "",
        "Run `python src/payment_gamma_sensitivity.py` after fitting Model A. "
        f"The Gamma fit converged in {fit.fit_history['iteration']} iterations.",
        "",
    ]
    Path("docs/payment_gamma_sensitivity.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print("\n".join(rows))


if __name__ == "__main__":
    main()

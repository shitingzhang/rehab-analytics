"""Plot Model A estimates and check residuals, collinearity, and facility influence."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

import irf_common as legacy
from mode_mix_post_audit import ADJUST, EXPOSURES, OUTCOMES, checked_fit

ROOT = Path("data/processed/mode_mix_post_audit")
FIG = Path("reports/figures")
MODEL = "A_complete_composition"
TITLES = {
    "DTC": "Discharge to community",
    "RSRR": "Readmission",
    "PAY": "Standardized payment",
}
UNITS = {
    "DTC": "Percentage points",
    "RSRR": "Percentage points",
    "PAY": "$1,000 per stay",
}
LABELS = [
    "Group/concurrent (+10 pp)",
    "Co-treatment (+5 pp)",
    "PT share (+5 pp)",
    "SLP share (+5 pp)",
]
BLUE = "#176373"


def forest_plot(primary):
    fig, axes = plt.subplots(1, 3, figsize=(10.8, 3.2), sharey=True)
    for ax, outcome in zip(axes, OUTCOMES):
        rows = primary.xs(outcome).loc[EXPOSURES]
        for i, row in enumerate(rows.itertuples()):
            ax.plot([row.ci_low, row.ci_high], [i, i], color=BLUE, lw=2)
            ax.plot(row.coef, i, "o", color=BLUE, ms=6)
        ax.axvline(0, color="#657481", lw=1, ls="--", zorder=0)
        ax.set(title=TITLES[outcome], xlabel=UNITS[outcome], ylim=(3.6, -0.6))
        ax.set_yticks(range(4), LABELS)
        ax.grid(axis="x", alpha=0.13)
        ax.tick_params(axis="y", length=0)
        ax.spines[["top", "right", "left"]].set_visible(False)
    fig.suptitle(
        "Therapy composition and facility outcomes",
        x=0.022,
        ha="left",
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.207, right=0.982, top=0.76, bottom=0.24, wspace=0.27)
    fig.text(
        0.022,
        0.035,
        "Points show adjusted estimates. Lines show 95% confidence intervals.   pp = percentage points.",
    )
    fig.savefig(FIG / "model_a_forest.png", dpi=200, facecolor="white")
    plt.close(fig)


def within_state_vif(sample, terms):
    centered = (
        sample[terms] - sample.groupby("state")[terms].transform("mean")
    ).to_numpy()
    values = {}
    for i, term in enumerate(terms):
        y = centered[:, i]
        other = np.delete(centered, i, axis=1)
        coef = np.linalg.lstsq(other, y, rcond=None)[0]
        resid = y - other @ coef
        values[term] = (y @ y) / (resid @ resid)
    return values


def diagnostic_row(axes, outcome, fitted, resid, cook):
    ax, qq, influence = axes
    ax.scatter(fitted, resid, s=7, alpha=0.35, linewidths=0, c=BLUE)
    ax.axhline(0, color="gray", lw=1)
    ax.set(xlabel="Fitted value", ylabel=f"{outcome} residual")
    (normal, ordered), (slope, intercept, _) = stats.probplot(resid)
    qq.scatter(normal, ordered, s=7, c=BLUE, alpha=0.45, linewidths=0)
    qq.plot(normal, slope * normal + intercept, color="gray", lw=1)
    qq.set(xlabel="Theoretical normal quantile", ylabel="Observed residual")
    sorted_d = np.sort(cook)
    influence.plot(np.arange(1, len(cook) + 1), sorted_d, color=BLUE)
    influence.axhline(4 / len(resid), color="gray", ls="--", lw=1)
    influence.set(xlabel="Facility rank by Cook’s D", ylabel="Cook’s D")


def main():
    results_dir = ROOT
    cohort = pd.read_csv(
        results_dir / "cohort_private.csv", dtype={"ccn": str}
    ).set_index("ccn")
    if not cohort.index.is_unique:
        raise ValueError("Multiple cohort rows for one facility")
    members = pd.read_csv(
        results_dir / "model_membership_private.csv", dtype={"ccn": str}
    )
    members = members.loc[members.model == MODEL]
    saved = pd.read_csv(results_dir / "all_model_terms.csv").query("model == @MODEL")
    primary = pd.read_csv(results_dir / "primary_12.csv").set_index(["outcome", "term"])
    expected = pd.MultiIndex.from_product(
        [OUTCOMES, EXPOSURES], names=["outcome", "term"]
    )
    if (
        not primary.index.is_unique
        or set(primary.index) != set(expected)
        or not primary.model.eq(MODEL).all()
    ):
        raise ValueError("Unexpected primary reporting family")
    terms = EXPOSURES + ADJUST + legacy.imp_terms(cohort)
    summary_rows, deletion_rows, vif = [], [], {}
    fig, axes = plt.subplots(3, 3, figsize=(11, 9.5), layout="constrained")
    for ax, title in zip(
        axes[0], ["Residual vs fitted", "Normal Q–Q", "Single-facility influence"]
    ):
        ax.set_title(title, fontweight="bold")

    for i, (outcome, y) in enumerate(OUTCOMES.items()):
        ids = members.loc[members.outcome == outcome, "ccn"]
        sample = cohort.loc[ids]
        eligible = cohort.dropna(subset=[y, "state", *terms])
        if not ids.is_unique or set(ids) != set(eligible.index):
            raise ValueError(f"Model A membership mismatch for {outcome}")
        (fit, _, clusters), X, names = checked_fit(sample, y, terms)
        original = saved.loc[saved.outcome == outcome].set_index("term")
        if not original.index.is_unique or set(original.index) != set(names):
            raise ValueError(f"Model A terms differ for {outcome}")
        columns = ["coef", "se", "ci_low", "ci_high"]
        for reference in (original, primary.xs(outcome)):
            np.testing.assert_allclose(
                fit.loc[reference.index, columns], reference[columns], rtol=0, atol=1e-6
            )
            if not (
                reference.n.eq(len(sample)).all()
                and reference.clusters.eq(clusters).all()
            ):
                raise ValueError("Saved sample dimensions differ")
        n, k = X.shape
        beta = fit.loc[names, "coef"].to_numpy()
        fitted = X @ beta
        resid = sample[y].to_numpy() - fitted
        pinv = np.linalg.pinv(X)
        hat = np.einsum("ij,ji->i", X, pinv)
        valid = (1 - hat) > 1e-10
        mse = resid @ resid / (n - k)
        cook = resid[valid] ** 2 * hat[valid] / (k * mse * (1 - hat[valid]) ** 2)
        quartile = pd.qcut(fitted, 4, labels=False)
        rms = [np.sqrt(np.mean(resid[quartile == j] ** 2)) for j in range(4)]
        summary_rows.append(
            f"| {outcome} | {n} | {clusters} | {stats.skew(resid, bias=False):.2f} | "
            f"{stats.kurtosis(resid, bias=False):.2f} | "
            + " / ".join(f"{x:.2f}" for x in rms)
            + f" | {(cook > 4 / n).sum()} | {cook.max():.3f} | {(~valid).sum()} |"
        )
        vif[outcome] = within_state_vif(sample, terms)
        for term in EXPOSURES:
            j = names.index(term)
            deleted = beta[j] - pinv[j, valid] * resid[valid] / (1 - hat[valid])
            deletion_rows.append(
                f"| {outcome} | {term} | {beta[j]:+.3f} | {deleted.min():+.3f} | {deleted.max():+.3f} |"
            )
        diagnostic_row(axes[i], outcome, fitted, resid, cook)

    FIG.mkdir(parents=True, exist_ok=True)
    forest_plot(primary)
    fig.savefig(FIG / "model_a_residual_influence.png", dpi=160, facecolor="white")
    plt.close(fig)
    vif_rows = [
        "| " + term + " | " + " | ".join(f"{vif[o][term]:.2f}" for o in OUTCOMES) + " |"
        for term in terms
    ]
    report = [
        "# Model A diagnostics",
        "",
        "We examined the residuals and influence of individual facilities after fitting Model A. "
        "The sample and covariates match the [main analysis](../README.md#results).",
        "",
        "## Residuals and influence",
        "",
        "RMSE is the root mean squared residual in successive quartiles of fitted values. DTC and RSRR use percentage points. "
        "PAY uses thousands of dollars per stay. Excess kurtosis is zero for a normal distribution.",
        "",
        "| Outcome | Facilities | State groups | Residual skew | Excess kurtosis | RMSE Q1 / Q2 / Q3 / Q4 | Cook’s D > 4/n | Max D | Unit leverage |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|",
        *summary_rows,
        "",
        "![Residuals, normal Q–Q plots and sorted Cook’s distances by outcome](../reports/figures/model_a_residual_influence.png)",
        "",
        "Cook’s D uses the full OLS design and residual mean square. The dashed line in the right column marks 4/n. "
        "The count identifies observations for closer inspection. Unit-leverage observations have undefined classical deletion "
        "diagnostics and are excluded from the Cook summaries and deletion ranges. All facilities contributed to the reported fit.",
        "",
        "## Single-facility deletion",
        "",
        "Ranges use the exact OLS deletion identity for each facility with leverage below one. "
        "Each range gives the smallest and largest estimates after omitting one facility at a time.",
        "",
        "| Outcome | Exposure | Full estimate | Smallest deletion estimate | Largest deletion estimate |",
        "|---|---|---:|---:|---:|",
        *deletion_rows,
        "",
        "## Collinearity within states",
        "",
        "Each non-state predictor was centered within state and regressed on the other centered predictors. "
        "VIF is the ratio of its within-state sum of squares to its residual sum of squares. "
        "The four therapy terms appear first. PT and SLP use OT as the reference discipline.",
        "",
        "| Predictor | DTC VIF | RSRR VIF | PAY VIF |",
        "|---|---:|---:|---:|",
        *vif_rows,
        "",
        "## Reproduction",
        "",
        "From the repository root run `python src/model_a_diagnostics.py` after running the "
        "main analysis in `src/12_mode_mix_analysis.py`.",
        "",
    ]
    Path("docs/model_a_diagnostics.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    main()

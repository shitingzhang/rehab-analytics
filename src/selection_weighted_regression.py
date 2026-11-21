"""Refit Model A after weighting facilities by setting and stay volume."""

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from irf_common import imp_terms
from mode_mix_io import load_inputs, load_outcome_sample
from mode_mix_post_audit import ADJUST, EXPOSURES, OUTCOMES, checked_fit

A = "A_complete_composition"
B = "B_no_composition_all"
LABELS = {
    "group10": "Group/concurrent (+10 pp)",
    "cotreat5": "Co-treatment (+5 pp)",
    "pt5": "PT share (+5 pp)",
    "slp5": "SLP share (+5 pp)",
}


def selection_weights(target, sample):
    strata = target[["unit", "tot_epsd_stay_cnt"]].copy()
    if strata.isna().any().any() or set(strata.unit) != {0, 1}:
        raise ValueError("Invalid facility setting or stay volume")
    strata["quartile"] = pd.qcut(strata.tot_epsd_stay_cnt, 4, labels=False) + 1
    keys = ["unit", "quartile"]
    retained = strata.loc[sample.index]
    counts = pd.DataFrame(
        {
            "eligible": strata.groupby(keys).size(),
            "retained": retained.groupby(keys).size(),
        }
    )
    if len(counts) != 8 or counts.isna().any().any() or (counts.retained <= 0).any():
        raise ValueError("Every target stratum requires retained facilities")
    counts["weight"] = counts.eligible / counts.retained
    weighted = retained.join(counts.weight, on=keys, validate="many_to_one")
    if not np.allclose(weighted.groupby(keys).weight.sum(), counts.eligible, rtol=0, atol=1e-9):
        raise ValueError("Weighted counts differ from the eligible stratum sizes")
    return weighted.weight, counts


def interval(row, factor, precision):
    return (
        f"{row.coef * factor:+.{precision}f} "
        f"[{row.ci_low * factor:+.{precision}f}, {row.ci_high * factor:+.{precision}f}]"
    )


def main():
    cohort, members, saved = load_inputs()
    if not saved.model.eq(A).all():
        raise ValueError("Expected Model A primary estimates")
    terms = EXPOSURES + ADJUST + imp_terms(cohort)
    comparisons, coverage, cells = [], [], []
    for outcome, y in OUTCOMES.items():
        sample = load_outcome_sample(cohort, members, A, outcome)
        target = load_outcome_sample(cohort, members, B, outcome)
        eligible = cohort.dropna(subset=[y, "state", *EXPOSURES, *ADJUST])
        complete = eligible.dropna(subset=imp_terms(cohort))
        if set(sample.index) != set(complete.index) or set(target.index) != set(eligible.index):
            raise ValueError(f"Inconsistent A/B membership for {outcome}")
        w, counts = selection_weights(target, sample)
        (original, _, groups), X, names = checked_fit(sample, y, terms)
        ref = saved.loc[outcome]
        cols = ["coef", "se", "ci_low", "ci_high"]
        if (
            set(ref.index) != set(EXPOSURES)
            or not (ref.n.eq(len(sample)) & ref.clusters.eq(groups)).all()
        ):
            raise ValueError("Primary dimensions differ")
        if not np.allclose(original.loc[ref.index, cols], ref[cols], rtol=0, atol=1e-6):
            raise ValueError(f"Refitted estimates differ from Model A for {outcome}")
        if not w.index.equals(sample.index):
            raise ValueError("Weights must follow the sample order")
        wls = sm.WLS(sample[y].to_numpy(), X, weights=w.to_numpy()).fit(
            cov_type="cluster",
            cov_kwds={"groups": sample.state.to_numpy(), "use_correction": True},
            use_t=True,
        )
        if wls.df_resid_inference != groups - 1:
            raise ValueError("Unexpected weighted regression degrees of freedom")
        intervals = wls.conf_int()
        if not np.isfinite(wls.params).all() or not np.isfinite(intervals).all():
            raise ValueError(f"Nonfinite weighted estimates for {outcome}")
        weighted = pd.DataFrame(
            {"coef": wls.params, "ci_low": intervals[:, 0], "ci_high": intervals[:, 1]},
            index=names,
        )
        ess = w.sum() ** 2 / (w @ w)
        coverage.append(
            f"| {outcome} | {len(sample)} | {len(target)} | {groups} | {w.min():.3f}–{w.max():.3f} | {ess:.1f} |"
        )
        for (unit, q), cell in counts.iterrows():
            label = "Hospital unit" if unit else "Freestanding"
            cells.append(
                f"| {outcome} | {label} | Q{q} | {cell.retained:.0f} | {cell.eligible:.0f} | {cell.weight:.3f} |"
            )
        factor, digits = (1000, 0) if outcome == "PAY" else (1, 3)
        for term in EXPOSURES:
            source, result = ref.loc[term], weighted.loc[term]
            delta = factor * (result.coef - source.coef)
            comparisons.append(
                f"| {outcome} | {LABELS[term]} | {interval(source, factor, digits)} | "
                f"{interval(result, factor, digits)} | {delta:+.{digits}f} |"
            )

    report = [
        "# How much do treatment-share estimates move after reweighting the sample?",
        "",
        "Facilities with complete impairment data differ in size and setting from the broader eligible population. "
        "This post-results check refits each Model A regression with weights that match the Model B eligible population "
        "on the joint distribution of facility setting and stay-volume quartile.",
        "",
        "## Treatment-share estimates",
        "",
        "DTC (community discharge) and RSRR (readmission) are reported in percentage points, and standardized Medicare "
        "payment (PAY) is reported in dollars per stay. Each change is calculated before rounding. PT and SLP are relative to OT.",
        "",
        "| Outcome | Higher share | Original A [95% CI] | Weighted A [95% CI] | Change |",
        "|---|---|---:|---:|---:|",
        *comparisons,
        "",
        "Both fits use the same retained facilities, adjustment variables and state fixed effects. Pointwise intervals use "
        "state-clustered CR1 standard errors and a t critical value with the number of groups minus one degrees of freedom. "
        "For the weighted fit, these intervals condition on the observed weights. The last column shows how the point "
        "estimate changes for each comparison. The original BH adjustment applies only to the primary Model A estimates.",
        "",
        "## Who is represented by the weights?",
        "",
        "| Outcome | Retained A | Eligible B | State groups in A | Weight range | Kish effective sample size |",
        "|---|---:|---:|---:|---:|---:|",
        *coverage,
        "",
        "For each outcome, stay-volume quartiles within the Model B eligible population were crossed with facility setting "
        "(hospital-based unit or freestanding facility) to form eight strata. Each retained facility was weighted by the "
        "ratio of eligible to retained facilities in its stratum. The weights sum to the eligible count in each stratum, "
        "and Kish’s effective sample size summarizes how much the weights vary.",
        "",
        "| Outcome | Setting | Stay quartile | Retained | Eligible | Weight |",
        "|---|---|---|---:|---:|---:|",
        *cells,
        "",
        "Selection can still depend on patients, facilities or volume differences within these eight groups. "
        "The excluded facilities’ impairment profiles remain unknown. The sample supports facility-level associations.",
        "",
        "## Reproduction",
        "",
        "Run `python src/selection_weighted_regression.py` from the repository root. "
        "[Study design and sample selection](study_design.md)",
        "",
    ]
    Path("docs/selection_weighted_regression.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print("\n".join(comparisons + coverage))


if __name__ == "__main__":
    main()

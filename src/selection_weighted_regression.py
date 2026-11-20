"""Compare Model A with the same facility regressions weighted to Model B's eight strata."""

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from irf_common import design, imp_terms
from mode_mix_post_audit import ADJUST, EXPOSURES, OUTCOMES
from strict_regression import fit

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
    np.testing.assert_allclose(
        weighted.groupby(keys).weight.sum(), counts.eligible, rtol=0, atol=1e-9
    )
    return weighted.weight, counts


def interval(row, factor, precision):
    return (
        f"{row.coef * factor:+.{precision}f} "
        f"[{row.ci_low * factor:+.{precision}f}, {row.ci_high * factor:+.{precision}f}]"
    )


def main():
    results_dir = Path("data/processed/mode_mix_post_audit")
    cohort = pd.read_csv(
        results_dir / "cohort_private.csv", dtype={"ccn": str}
    ).set_index("ccn")
    members = pd.read_csv(
        results_dir / "model_membership_private.csv", dtype={"ccn": str}
    )
    saved = pd.read_csv(results_dir / "primary_12.csv").set_index(["outcome", "term"])
    if (
        not cohort.index.is_unique
        or not saved.index.is_unique
        or not saved.model.eq(A).all()
    ):
        raise ValueError("Inconsistent cohort or primary results")
    terms = EXPOSURES + ADJUST + imp_terms(cohort)
    comparisons, coverage, cells = [], [], []
    for outcome, y in OUTCOMES.items():
        subset = members[members.outcome == outcome]
        a_ids = subset.loc[subset.model == A, "ccn"]
        b_ids = subset.loc[subset.model == B, "ccn"]
        eligible = cohort.dropna(subset=[y, "state", *EXPOSURES, *ADJUST])
        complete = eligible.dropna(subset=imp_terms(cohort))
        if (
            not a_ids.is_unique
            or not b_ids.is_unique
            or set(a_ids) != set(complete.index)
            or set(b_ids) != set(eligible.index)
        ):
            raise ValueError(f"Inconsistent A/B membership for {outcome}")
        sample, target = cohort.loc[a_ids], cohort.loc[b_ids]
        w, counts = selection_weights(target, sample)
        original, _, groups = fit(sample, y, terms)
        weighted, _, _ = fit(sample, y, terms, w=w)
        ref = saved.loc[outcome]
        cols = ["coef", "se", "ci_low", "ci_high"]
        if (
            set(ref.index) != set(EXPOSURES)
            or not (ref.n.eq(len(sample)) & ref.clusters.eq(groups)).all()
        ):
            raise ValueError("Primary dimensions differ")
        np.testing.assert_allclose(
            original.loc[ref.index, cols], ref[cols], rtol=0, atol=1e-6
        )
        X, names = design(sample, terms, True)
        independent = sm.WLS(sample[y].to_numpy(), X, weights=w.to_numpy()).fit(
            cov_type="cluster",
            cov_kwds={"groups": sample.state.to_numpy(), "use_correction": True},
            use_t=True,
        )
        np.testing.assert_equal(independent.df_resid_inference, groups - 1)
        reference = np.column_stack(
            [independent.params, independent.bse, independent.conf_int()]
        )
        np.testing.assert_allclose(
            weighted.loc[names, cols], reference, rtol=0, atol=1e-6
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
        "This post-results check gives each retained facility the inverse of its stratum’s observed retention fraction "
        "and refits the three original Model A regressions. The target for each outcome is its Model B eligible population.",
        "",
        "## Treatment-share estimates",
        "",
        "DTC (community discharge) and RSRR (readmission) are measured in percentage points. PAY is standardized Medicare "
        "payment in dollars per stay. Each change is calculated before rounding. PT and SLP are relative to OT.",
        "",
        "| Outcome | Higher share | Original A [95% CI] | Weighted A [95% CI] | Change |",
        "|---|---|---:|---:|---:|",
        *comparisons,
        "",
        "Both fits use the same retained facilities, adjustment variables and state fixed effects. Pointwise intervals use "
        "state-clustered CR1 standard errors and a t critical value with the number of groups minus one degrees of freedom. "
        "Weighted intervals condition on the observed weights. The last column describes movement of paired point estimates. "
        "The original BH adjustment applies only to the original primary family.",
        "",
        "## Who is represented by the weights?",
        "",
        "| Outcome | Retained A | Eligible B | State groups in A | Weight range | Kish effective sample size |",
        "|---|---:|---:|---:|---:|---:|",
        *coverage,
        "",
        "Stay-volume quartiles are defined within the eligible population for each outcome. Pairing each quartile with "
        "facility setting gives eight strata. Each retained facility receives its stratum’s eligible count divided by its "
        "retained count. Weighted counts reproduce every target cell. The effective sample size summarizes weight dispersion.",
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
        "The script verifies membership and cell counts, reproduces the saved original estimates, and checks every "
        "weighted coefficient, clustered standard error and interval against statsmodels. "
        "[Study design and sample selection](study_design.md)",
        "",
    ]
    Path("docs/selection_weighted_regression.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print("\n".join(comparisons + coverage))


if __name__ == "__main__":
    main()

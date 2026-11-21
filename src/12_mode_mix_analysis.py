"""Fit the three facility-level therapy models and export adjusted estimates."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests

import irf_common as legacy
from mode_mix_post_audit import ADJUST, EXPOSURES, OUTCOMES, checked_fit, construct

OUT = Path("data/processed/mode_mix_post_audit")
MODEL_A = "A_complete_composition"
MODEL_B = "B_no_composition_all"
MODEL_C = "C_no_composition_same_sample"


def build_results(dest):
    d, audit = construct()
    impairment = legacy.imp_terms(d)
    estimates = []
    coefficient_tables = []
    sample_flow = []
    checks = []
    members = []
    sample_characteristics = []
    for outcome, y in OUTCOMES.items():
        available = d[d[y].notna()]
        current = available
        needed = ["state"] + EXPOSURES + ADJUST
        for term in needed + impairment:
            before = len(current)
            current = current[current[term].notna()]
            sample_flow.append(
                {
                    "outcome": outcome,
                    "required_term": term,
                    "before": before,
                    "missing_dropped": before - len(current),
                    "remaining": len(current),
                }
            )
        base = d.dropna(subset=[y] + needed).copy()
        strict = base.dropna(subset=impairment).copy()
        assert set(current.ccn) == set(strict.ccn)
        for model, s, terms in [
            (MODEL_A, strict, EXPOSURES + ADJUST + impairment),
            (MODEL_B, base, EXPOSURES + ADJUST),
            (MODEL_C, strict, EXPOSURES + ADJUST),
        ]:
            (tab, _, clusters), X, names = checked_fit(s, y, terms)
            reference = sm.OLS(s[y].to_numpy(float), X).fit(
                cov_type="cluster",
                cov_kwds={"groups": s.state.to_numpy(), "use_correction": True},
                use_t=True,
            )
            coefficient_error = float(
                np.max(np.abs(tab.loc[names, "coef"].to_numpy() - reference.params))
            )
            se_error = float(
                np.max(np.abs(tab.loc[names, "se"].to_numpy() - reference.bse))
            )
            if not max(coefficient_error, se_error) < 1e-6:
                raise RuntimeError("Independent cross-check failed")
            checks.append(
                {
                    "model": model,
                    "outcome": outcome,
                    "n": len(s),
                    "clusters": clusters,
                    "design_columns": X.shape[1],
                    "coefficient_error": coefficient_error,
                    "se_error": se_error,
                }
            )
            all_terms = tab.reset_index().assign(
                model=model, outcome=outcome, n=len(s), clusters=clusters
            )
            coefficient_tables.append(all_terms)
            for term in EXPOSURES:
                r = tab.loc[term]
                estimates.append(
                    {
                        "model": model,
                        "outcome": outcome,
                        "term": term,
                        "n": len(s),
                        "clusters": clusters,
                        **{
                            c: float(r[c])
                            for c in ["coef", "se", "ci_low", "ci_high", "p"]
                        },
                    }
                )
            members.append(s[["ccn"]].assign(model=model, outcome=outcome))
        for group, s in [
            ("retained_complete", strict),
            ("excluded_unknown", base.loc[~base.index.isin(strict.index)]),
        ]:
            sample_characteristics.append(
                {
                    "outcome": outcome,
                    "group": group,
                    "n": len(s),
                    "median_beneficiaries": s.bene_dstnct_cnt.median(),
                    "median_stays": s.tot_epsd_stay_cnt.median(),
                    "mean_risk": s.risk.mean(),
                    "mean_age": s.age.mean(),
                    "hospital_unit_pct": 100 * s.unit.mean(),
                    "for_profit_pct": 100 * s.for_profit.mean(),
                }
            )
    result = pd.DataFrame(estimates)
    primary = result.model.eq(MODEL_A)
    assert primary.sum() == 12 and len(result) == 36
    result["q_bh_primary_12"] = np.nan
    result.loc[primary, "q_bh_primary_12"] = multipletests(
        result.loc[primary, "p"], method="fdr_bh"
    )[1]
    membership = pd.concat(members, ignore_index=True)
    for _, sample_rows in membership.groupby("outcome"):
        ids = sample_rows.groupby("model")["ccn"].agg(set)
        assert ids[MODEL_A] == ids[MODEL_C] and ids[MODEL_A] <= ids[MODEL_B]
    audit.update(
        {
            "provider_count": len(d),
            "providers_without_condition_record": int(d.condition_record_missing.sum()),
            "providers_with_Not_Available": int(d.dx_n_unavailable_cells.gt(0).sum()),
            "composition_unknown_total": int(d.composition_unknown.sum()),
        }
    )
    result.to_csv(dest / "all_36_estimates.csv", index=False, lineterminator="\n")
    result.loc[primary].to_csv(
        dest / "primary_12.csv", index=False, lineterminator="\n"
    )
    pd.concat(coefficient_tables, ignore_index=True).to_csv(
        dest / "all_model_terms.csv", index=False, lineterminator="\n"
    )
    pd.DataFrame(sample_flow).to_csv(
        dest / "sample_flow.csv", index=False, lineterminator="\n"
    )
    pd.DataFrame(sample_characteristics).to_csv(
        dest / "retained_excluded_characteristics.csv", index=False, lineterminator="\n"
    )
    pd.DataFrame(checks).to_csv(
        dest / "statsmodels_checks.csv", index=False, lineterminator="\n"
    )
    membership.to_csv(
        dest / "model_membership_private.csv", index=False, lineterminator="\n"
    )
    d.to_csv(dest / "cohort_private.csv", index=False, lineterminator="\n")
    (dest / "data_audit.json").write_text(
        json.dumps(audit, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print("A: primary post-results exploratory estimates (all 12)")
    print(
        result.loc[
            primary,
            [
                "outcome",
                "term",
                "n",
                "coef",
                "ci_low",
                "ci_high",
                "p",
                "q_bh_primary_12",
            ],
        ].to_string(index=False)
    )
    print("Results saved to", dest)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    build_results(OUT)

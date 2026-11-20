"""Flag facilities for review using length of stay and community discharge.

The historical rule selects facilities with stays at least 20% longer than
expected and discharge residuals in the bottom quartile. Bootstrap refits
measure how often each facility meets both criteria.
"""
import io
import os
import sys
from pathlib import Path
from _infra.model_publication import publish_batch
from _infra.screening_publication import REQUIRED, validate_screen_files, current_screen_dir

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from irf_common import PAC_FILL, imp_terms, load_facility, prepare, sample_flow  # noqa: E402

AS_PLANNED = "--as-planned" in sys.argv     # v1.2: no fill for suppressed dual / rural %
PAC = None if AS_PLANNED else PAC_FILL
OUT_DIR = "data/processed/screening_as_planned" if AS_PLANNED else "data/processed/screening"
FIG_DIR = "reports/figures/as_planned" if AS_PLANNED else "reports/figures"
FUNNEL_PATH = ("data/processed/analysis_as_planned/funnel_flags.csv" if AS_PLANNED
               else "data/processed/analysis/funnel_flags.csv")
RATIO, DTC_PCT = 1.20, 25            # primary thresholds (pre-specified)
GRID_RATIOS, GRID_PCTS = (1.10, 1.20, 1.30), (10, 25)
B, ROBUST_SHARE, SEED = 500, 0.80, 2026
COV = ["risk", "age", "dual10", "rural10", "log_stays"] + ([] if AS_PLANNED else ["dual_suppressed", "rural_suppressed"])
buf = io.StringIO()


def out(*a):
    print(*a)
    print(*a, file=buf)


from _infra.screening_guards import lstsq, components, r_squared


def jaccard(a, b):
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else float("nan")


def tertile(s, name):
    return pd.qcut(s, 3, labels=[f"{name}: low", f"{name}: mid", f"{name}: high"], duplicates="drop")


def build_results():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(FIG_DIR, exist_ok=True)
    d = prepare(load_facility(), pac_fill=PAC)
    out("Version: " + ("v1.2 AS PLANNED (suppressed dual / rural % left missing)" if AS_PLANNED
                       else f"v1.3 (suppressed dual / rural % filled with {PAC} beneficiaries + indicators)"))
    terms = COV + imp_terms(d)
    s = d.dropna(subset=["alos_days", "i_019_02_dtc_rs_rate", "state"] + terms).copy()
    out(f"Facilities screened: {len(s):,} of {len(d):,} (complete case-mix, ALOS and DTC). "
        f"Case-mix variables in the expected-value models: {terms}")
    sample_flow(d, "i_019_02_dtc_rs_rate", terms + ["alos_days"], out)
    if len(s) < 200:
        raise SystemExit("Too few complete facilities for the screen. Check missing values in the facility table.")

    X = np.column_stack([np.ones(len(s)), s[terms].to_numpy(float)])
    yA, yD = s["alos_days"].to_numpy(float), s["i_019_02_dtc_rs_rate"].to_numpy(float)
    bA, bD = lstsq(X, yA), lstsq(X, yD)
    s["alos_expected"], s["dtc_expected"] = X @ bA, X @ bD
    s["oe_alos"], s["dtc_residual"] = components(yA, yD, s["alos_expected"], s["dtc_expected"])
    q = np.percentile(s["dtc_residual"], DTC_PCT)
    s["flag"] = (s["oe_alos"] >= RATIO) & (s["dtc_residual"] <= q)
    out(f"R-squared of the expected-value models: ALOS {r_squared(yA, X @ bA):.2f}, "
        f"DTC {r_squared(yD, X @ bD):.2f}")
    out(f"Primary rule: O/E ALOS >= {RATIO:.2f} and DTC residual <= {q:.1f} points (the {DTC_PCT}th percentile) "
        f"-> {int(s['flag'].sum())} facilities flagged ({100 * s['flag'].mean():.1f}%)")

    # Refit on resampled facilities and score the full cohort each time.
    rng = np.random.default_rng(SEED)
    n = len(s)
    counts = np.zeros(n)
    for _ in range(B):
        idx = rng.integers(0, n, n)
        try:
            eA, eD = X @ lstsq(X[idx], yA[idx]), X @ lstsq(X[idx], yD[idx])
            ratio, res = components(yA, yD, eA, eD)
        except (ValueError, np.linalg.LinAlgError) as exc:
            raise ValueError(f"Screening bootstrap draw {_ + 1}/{B} failed: {exc}") from exc
        counts += (ratio >= RATIO) & (res <= np.percentile(res, DTC_PCT))
    s["flag_frequency"] = counts / B
    s["robust_flag"] = s["flag_frequency"] >= ROBUST_SHARE
    out(f"Bootstrap ({B} refits): {int(s['robust_flag'].sum())} facilities have flag frequency "
        f">= {int(100 * ROBUST_SHARE)}% (legacy robust_flag); "
        f"{int((s['flag'] & s['robust_flag']).sum())} also meet the original primary rule; "
        f"{int((~s['flag'] & s['robust_flag']).sum())} do not meet the original primary rule. "
        f"{int(((s['flag_frequency'] > 0) & ~s['flag']).sum())} primary-unflagged facilities are flagged in some refits.")
    out("Each refit keeps observed outcomes fixed and recalculates expected outcomes and the discharge cutoff.")

    # Compare alternative screening thresholds.
    rows = []
    for r in GRID_RATIOS:
        for p in GRID_PCTS:
            f = (s["oe_alos"] >= r) & (s["dtc_residual"] <= np.percentile(s["dtc_residual"], p))
            rows.append({"alos_ratio_min": r, "dtc_percentile": p, "flagged": int(f.sum()),
                         "jaccard_with_primary": round(jaccard(f, s["flag"]), 2)})
            s[f"flag_{r:.2f}_{p}"] = f
    grid = pd.DataFrame(rows)
    s["n_definitions_flagged"] = s[[c for c in s.columns if c.startswith("flag_1.")]].sum(axis=1)
    out("\n=== Sensitivity of the screen to its thresholds ===")
    out(grid.to_string(index=False))

    # Compare flags with the readmission funnel and peer-median rule.
    out("\n=== Agreement with other screens ===")
    if os.path.exists(FUNNEL_PATH):
        fl = pd.read_csv(FUNNEL_PATH, dtype={"ccn": str})[["ccn", "above_99_8"]]
        m = s.merge(fl, on="ccn", how="left")
        out(f"Funnel-plot readmission outliers (above the adjusted 99.8% limit): {int(m['above_99_8'].fillna(False).sum())} facilities; "
            f"{int((m['flag'] & m['above_99_8'].fillna(False)).sum())} of them are also flagged by this screen.")
    else:
        out("(run 04_analysis.py" + (" --as-planned" if AS_PLANNED else "")
            + " first to compare with the matching-version funnel-plot outliers)")
    if "review_flag" in s.columns:
        old = s["review_flag"].eq("Priority for Review")
        out(f"Earlier peer-median rule (review_flag in 01): {int(old.sum())} facilities; "
            f"{int((old & s['flag']).sum())} also flagged here; Jaccard = {jaccard(old, s['flag']):.2f}")

    # Summarize flag rates by facility group.
    groups = {"ownership": s["ownership_type"].fillna("Unknown"),
              "setting": s["unit"].map({1.0: "hospital-based unit", 0.0: "free-standing / other"}),
              "dual-eligible %": tertile(s["dual10"], "dual"), "rural %": tertile(s["rural10"], "rural"),  # filled values
              "volume (stays)": tertile(s["tot_epsd_stay_cnt"], "volume")}
    fr = []
    for gname, g in groups.items():
        t = s.assign(_g=g).groupby("_g", observed=True).agg(facilities=("flag", "size"), flagged=("flag", "sum"),
                                                          robust=("robust_flag", "sum")).reset_index()
        t.insert(0, "dimension", gname)
        t = t.rename(columns={"_g": "group"})
        t["flag_rate_%"] = (100 * t["flagged"] / t["facilities"]).round(1)
        t["robust_rate_%"] = (100 * t["robust"] / t["facilities"]).round(1)
        fr.append(t)
    fairness = pd.concat(fr, ignore_index=True)
    out("\n=== Flag rates by facility group ===")
    out(fairness.to_string(index=False))

    s["reason"] = ("ALOS " + ((s["oe_alos"] - 1) * 100).round(0).astype(int).astype(str) + "% above expected; DTC "
                   + s["dtc_residual"].round(1).astype(str) + " points vs expected")
    keep = [c for c in ["ccn", "prvdr_name", "state", "ownership_type", "unit", "tot_epsd_stay_cnt", "alos_days",
                        "alos_expected", "oe_alos", "i_019_02_dtc_rs_rate", "dtc_expected", "dtc_residual",
                        "flag_frequency", "robust_flag", "n_definitions_flagged", "reason"] if c in s.columns]
    lst = s[s["flag"]].sort_values(["flag_frequency", "oe_alos"], ascending=False)[keep]
    lst.round(3).to_csv(f"{OUT_DIR}/priority_review_list.csv", index=False)
    grid.to_csv(f"{OUT_DIR}/sensitivity_grid.csv", index=False)
    fairness.to_csv(f"{OUT_DIR}/fairness_flag_rates.csv", index=False)
    s[["ccn", "oe_alos", "dtc_residual", "flag", "flag_frequency", "robust_flag"]].round(4).to_csv(
        f"{OUT_DIR}/screen_all_facilities.csv", index=False)

    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    ax.scatter(s["oe_alos"], s["dtc_residual"], s=10, c="lightgray", label="not flagged")
    f1 = s[s["flag"] & ~s["robust_flag"]]
    f2 = s[s["robust_flag"]]
    ax.scatter(f1["oe_alos"], f1["dtc_residual"], s=22, c="C1", label=f"flagged ({len(f1)})")
    ax.scatter(f2["oe_alos"], f2["dtc_residual"], s=28, c="C3", label=f"refit frequency >= {int(100 * ROBUST_SHARE)}% ({len(f2)})")
    ax.axvline(RATIO, color="k", ls="--", lw=1)
    ax.axhline(q, color="k", ls="--", lw=1)
    ax.set_xlabel("Observed / expected ALOS (expected from case mix only)")
    ax.set_ylabel("DTC rate minus expected (percentage points)")
    ax.set_title("Priority-for-review screen")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{FIG_DIR}/screening_scatter.png", dpi=150)
    plt.close(fig)

    out("\nFlagged facilities warrant individual review.")
    with open(f"{OUT_DIR}/screening_summary.txt", "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    print(f"\nSaved to {OUT_DIR}/ and {FIG_DIR}/screening_scatter.png")


def main():
    global OUT_DIR, FIG_DIR
    args = sys.argv[1:]
    if any(arg not in ('--as-planned', '--show-current') for arg in args) or len(args) != len(set(args)):
        raise SystemExit('Usage: python src/05_screening.py [--as-planned] [--show-current]')
    root = Path(OUT_DIR)
    if '--show-current' in args:
        print(current_screen_dir(root))
        return
    inputs = [Path(p) for p in ['data/processed/irf_analytics.db', 'src/05_screening.py',
              'src/irf_common.py', 'src/_infra/screening_guards.py', 'src/_infra/screening_publication.py',
              'src/_infra/model_publication.py']]
    funnel_present = Path(FUNNEL_PATH).exists()
    if funnel_present:
        inputs.append(Path(FUNNEL_PATH))
    old_out, old_fig = OUT_DIR, FIG_DIR
    try:
        with publish_batch(root, REQUIRED, inputs) as stage:
            OUT_DIR = FIG_DIR = str(stage)
            build_results()
            if Path(FUNNEL_PATH).exists() != funnel_present:
                raise RuntimeError('Funnel input availability changed during screening')
            validate_screen_files(stage)
    finally:
        OUT_DIR, FIG_DIR = old_out, old_fig
    print('Published complete screening batch:', current_screen_dir(root))


if __name__ == "__main__":
    main()
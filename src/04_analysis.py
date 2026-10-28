"""
04_analysis.py  (implements docs/analysis_plan.md, v1.3)
Run from the project root after 02_build_database.py:   python src/04_analysis.py [--check] [--as-planned]
--as-planned reproduces the v1.2 run (suppressed dual / rural % left missing, complete cases) into separate folders.

RQ-A  Dose : therapy minutes per covered day  ->  DTC rate, readmission RSRR, standardized payment per stay
RQ-B  Mode and mix (exploratory): group/concurrent share, co-treatment share, PT and SLP shares, ownership
Primary model: OLS, state fixed effects, state-clustered (CR1) standard errors, one fixed sample per outcome.
Confirmatory family: the therapy-dose coefficient for the three outcomes, Holm-adjusted.
Everything else is exploratory. Pre-specified sensitivity analyses are run for every outcome.
Facility-level, observational, windows differ by measure: associations, not causal effects.

Outputs: data/processed/analysis/*.csv + analysis_summary.txt, reports/figures/*.png
"""
import io
import os
import sys

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from irf_common import (PAC_FILL, design, fit, holm, imp_terms, label, load_facility, ols_cluster,  # noqa: E402
                        prepare, sample_flow)

CHECK = "--check" in sys.argv
AS_PLANNED = "--as-planned" in sys.argv     # v1.2: no fill for suppressed dual / rural % (sensitivity analysis 10)
PAC = None if AS_PLANNED else PAC_FILL
OUT_DIR = "data/processed/analysis_as_planned" if AS_PLANNED else "data/processed/analysis"
FIG_DIR = "reports/figures/as_planned" if AS_PLANNED else "reports/figures"
LINKAGE_PATH = "data/processed/linkage_audit/linkage_check.csv"
OUTCOMES = {  # key: (column, case-count column used for weights, axis label)
    "DTC": ("i_019_02_dtc_rs_rate", "i_019_02_dtc_volume", "DTC rate (%)"),
    "RSRR": ("i_017_01_ppr_pd_rsrr", "i_017_01_ppr_pd_volume", "Readmission RSRR (%)"),
    "PAY": ("std_pay_k", "tot_epsd_stay_cnt", "Standardized payment per stay ($1,000)"),
}
OWN = ["for_profit", "government", "physician", "unit"]
MODE = ["group10", "cotreat5", "pt5", "slp5"]
SUPP = [] if AS_PLANNED else ["dual_suppressed", "rural_suppressed"]
COV = ["risk", "age", "dual10", "rural10"] + SUPP
buf = io.StringIO()


def out(*a):
    print(*a)
    print(*a, file=buf)


def specs(d):
    imp = imp_terms(d)
    return {"M0 raw": (["therapy10"], False),
            "M1 +ownership/setting": (["therapy10"] + OWN, False),
            "M2 +case mix": (["therapy10"] + OWN + COV + imp, False),
            "M3 +volume & state FE": (["therapy10"] + OWN + COV + imp + ["log_stays"], True),
            "P  primary (+mode & mix)": (["therapy10"] + MODE + OWN + COV + imp + ["log_stays"], True)}


def row(outcome, analysis, term, n, tab):
    t = tab.loc[term]
    return {"outcome": outcome, "analysis": analysis, "term": term, "n": n, "coef": t.coef, "ci_low": t.ci_low,
            "ci_high": t.ci_high, "p": t.p}


# main analyses
def run_outcome(d, key, sd_min):
    ycol, wcol, ylab = OUTCOMES[key]
    sp = specs(d)
    primary_terms = sp["P  primary (+mode & mix)"][0]
    s = d.dropna(subset=[ycol, "state"] + primary_terms).copy()
    out(f"\n=== {ylab}: analysis sample n = {len(s):,} of {d[ycol].notna().sum():,} facilities with the outcome, "
        f"{s['state'].nunique()} states ===")
    sample_flow(d, ycol, primary_terms, out)
    if len(s) < 100 or s["state"].nunique() < 10:
        out("    Too few facilities or states - skipped. Check missing values.")
        return None
    ladder, full = [], None
    for name, (terms, fe) in sp.items():
        tab, r2, g = fit(s, ycol, terms, fe)
        r = row(key, name, "therapy10", len(s), tab)
        r.update(r2=r2, states=g, per_sd=r["coef"] * sd_min / 10)
        ladder.append(r)
        if name.startswith("P"):
            full = tab.reset_index()

    sens = []
    w = s[wcol].where(s[wcol] > 0)
    ok = w.notna()
    sens.append(row(key, "S1 weighted by cases (WLS)", "therapy10", int(ok.sum()),
                    fit(s[ok], ycol, primary_terms, True, w[ok].to_numpy())[0]))
    sens.append(row(key, "S2 state-clustered SE, no state FE", "therapy10", len(s), fit(s, ycol, primary_terms, False)[0]))
    lo_t, hi_t = s["therapy10"].quantile([.01, .99])
    lo_y, hi_y = s[ycol].quantile([.01, .99])
    keep = s["therapy10"].between(lo_t, hi_t) & s[ycol].between(lo_y, hi_y)
    sens.append(row(key, "S3 trim top/bottom 1% (dose and outcome)", "therapy10", int(keep.sum()),
                    fit(s[keep], ycol, primary_terms, True)[0]))
    if "dx_n_suppressed_cells" in s.columns:
        k = s["dx_n_suppressed_cells"] <= 1
        if k.sum() >= 100:
            sens.append(row(key, "S4a at most 1 suppressed impairment cell", "therapy10", int(k.sum()),
                            fit(s[k], ycol, primary_terms, True)[0]))
    for fillv in (0, 10):
        try:
            # fill 0: a facility with every impairment cell suppressed has no defined shares
            alt = prepare(d.loc[s.index], fill=fillv, pac_fill=PAC).dropna(subset=primary_terms)
            sens.append(row(key, f"S4b suppressed cells filled with {fillv}", "therapy10", len(alt),
                            fit(alt, ycol, primary_terms, True)[0]))
        except SystemExit:
            out("    S4b skipped: dx_supp_* columns missing (re-run 01 and 02).")
            break
    terms_nounit = [t for t in primary_terms if t != "unit"]
    for lab, mask in [("S5a free-standing / other only", s["unit"] == 0), ("S5b hospital-based units only", s["unit"] == 1)]:
        if mask.sum() >= 100 and s.loc[mask, "state"].nunique() >= 10:
            sens.append(row(key, lab, "therapy10", int(mask.sum()), fit(s[mask], ycol, terms_nounit, True)[0]))
    s2 = s.assign(therapy10_c=s["therapy10"] - s["therapy10"].mean())
    s2["therapy10_sq"] = s2["therapy10_c"] ** 2
    t_terms = [t if t != "therapy10" else "therapy10_c" for t in primary_terms] + ["therapy10_sq"]
    tab = fit(s2, ycol, t_terms, True)[0]
    sens.append(row(key, "S6a quadratic dose: linear term at mean dose", "therapy10_c", len(s), tab))
    sens.append(row(key, "S6a quadratic dose: squared term", "therapy10_sq", len(s), tab))
    q = pd.qcut(s["therapy10"], 4, labels=False, duplicates="drop")
    s3 = s.assign(**{f"tq{i + 1}": (q == i).astype(float) for i in range(1, 4)})
    tab = fit(s3, ycol, [t for t in primary_terms if t != "therapy10"] + ["tq2", "tq3", "tq4"], True)[0]
    for t in ("tq2", "tq3", "tq4"):
        sens.append(row(key, f"S6b dose quartile {t[2]} vs quartile 1", t, len(s), tab))
    # S9: group/concurrent share is very right-skewed -> use tertiles of the share instead of the linear term
    gt = pd.qcut(s["group_therapy_share"].rank(method="first"), 3, labels=False)
    s4 = s.assign(gt2=(gt == 1).astype(float), gt3=(gt == 2).astype(float))
    tab = fit(s4, ycol, [t for t in primary_terms if t != "group10"] + ["gt2", "gt3"], True)[0]
    for t, lab in (("gt2", "middle"), ("gt3", "highest")):
        sens.append(row(key, f"S9 group/concurrent share, {lab} tertile vs lowest", t, len(s), tab))
    if not AS_PLANNED:   # plan v1.3, analyses 10-12 (suppressed dual / rural %)
        terms_v12 = [t for t in primary_terms if t not in SUPP]
        k = (s["dual_suppressed"] == 0) & (s["rural_suppressed"] == 0)
        sens.append(row(key, "S10 as planned (v1.2): complete cases, no fill", "therapy10", int(k.sum()),
                        fit(s[k], ycol, terms_v12, True)[0]))
        for v in (1, 10):
            alt = prepare(d.loc[s.index], pac_fill=v)
            tab = fit(alt, ycol, primary_terms, True)[0]
            sens.append(row(key, f"S11 suppressed dual / rural filled with {v} beneficiaries", "therapy10", len(alt), tab))
            tab = tab.reset_index()   # all terms kept: fill sensitivity of the exploratory coefficients (06)
            tab[~tab["term"].str.startswith("st_")].round(4).to_csv(f"{OUT_DIR}/s11_fill{v}_model_{key.lower()}.csv",
                                                                     index=False)
        terms_nodr = [t for t in terms_v12 if t not in ("dual10", "rural10")]
        tab = fit(s, ycol, terms_nodr, True)[0]
        sens.append(row(key, "S12 without rural % and dual %", "therapy10", len(s), tab))
        tab = tab.reset_index()   # all terms kept for the three-version comparison (06)
        tab[~tab["term"].str.startswith("st_")].round(4).to_csv(f"{OUT_DIR}/s12_model_{key.lower()}.csv", index=False)
        if os.path.exists(LINKAGE_PATH):   # S13: drop low-confidence and rule-resolved facility links (check_linkage.py)
            lk = pd.read_csv(LINKAGE_PATH, dtype={"ccn": str})
            drop = set(lk.loc[lk["low_confidence"] | lk["link"].eq("crosswalk: ambiguous_resolved"), "ccn"])
            k = ~s["ccn"].isin(drop)
            sens.append(row(key, "S13 excluding low-confidence facility links", "therapy10", int(k.sum()),
                            fit(s[k], ycol, primary_terms, True)[0]))
        else:
            out("    S13 skipped: run src/check_linkage.py first.")
    sx = s.assign(dose_x_for_profit=s["therapy10"] * s["for_profit"], dose_x_government=s["therapy10"] * s["government"])
    tab = fit(sx, ycol, primary_terms + ["dose_x_for_profit", "dose_x_government"], True)[0]
    for t in ("therapy10", "dose_x_for_profit", "dose_x_government"):
        sens.append(row(key, "S8 dose x ownership interaction", t, len(s), tab))
    loso = pd.DataFrame([{"outcome": key, "state_left_out": st,
                          "coef": fit(s[s["state"] != st], ycol, primary_terms, True)[0].loc["therapy10", "coef"]}
                         for st in sorted(s["state"].unique())])

    if CHECK:
        try:
            import statsmodels.api as sm
            X, _ = design(s, primary_terms, True)
            res = sm.OLS(s[ycol].to_numpy(float), X).fit(
                cov_type="cluster", cov_kwds={"groups": pd.factorize(s["state"])[0], "use_correction": True}, use_t=True)
            b, se_, _, _ = ols_cluster(s[ycol], X, s["state"])
            out(f"    statsmodels cross-check: max |coef diff| = {np.abs(np.asarray(res.params) - b).max():.2e}, "
                f"max |SE diff| = {np.abs(np.asarray(res.bse) - se_).max():.2e}")
        except Exception as e:
            out(f"    statsmodels cross-check not run: {e}")
    return pd.DataFrame(ladder), full, pd.DataFrame(sens), loso, len(s)


# funnel plot
def funnel(d):
    y, lo, hi, v = ("i_017_01_ppr_pd_rsrr", "i_017_01_ppr_pd_rsrr_2_5", "i_017_01_ppr_pd_rsrr_97_5", "i_017_01_ppr_pd_volume")
    f = d.dropna(subset=[y, lo, hi, v]).copy()
    f = f[(f[v] > 0) & (f[hi] > f[lo])]
    f["se"] = (f[hi] - f[lo]) / (2 * 1.96)
    w = 1 / f["se"] ** 2
    center = float((w * f[y]).sum() / w.sum())
    z = ((f[y] - center) / f["se"]).to_numpy()
    lo_q, hi_q = np.percentile(z, [5, 95])
    phi = float(np.mean(np.clip(z, lo_q, hi_q) ** 2))
    W = w.to_numpy()
    tau2 = max(0.0, (len(z) * phi - (len(z) - 1)) / (W.sum() - (W ** 2).sum() / W.sum()))
    f["z_adj"] = (f[y] - center) / np.sqrt(f["se"] ** 2 + tau2)
    f["above_99_8"], f["below_99_8"], f["above_95"] = f["z_adj"] > 3.09, f["z_adj"] < -3.09, f["z_adj"] > 1.96
    out(f"\n=== Funnel plot (readmission RSRR): {len(f):,} facilities; reference = precision-weighted mean {center:.2f}% ===")
    out(f"    over-dispersion phi = {phi:.2f}; between-facility SD tau = {np.sqrt(tau2):.2f} points; "
        f"outside adjusted 99.8% limits: above {int(f.above_99_8.sum())}, below {int(f.below_99_8.sum())}")
    f["ownership_type"] = d.loc[f.index, "ownership_type"].fillna("Unknown")
    summ = f.groupby("ownership_type").agg(facilities=(y, "size"), mean_rsrr=(y, "mean"), above_99_8=("above_99_8", "sum"),
                                           below_99_8=("below_99_8", "sum"), above_95=("above_95", "sum")).reset_index()
    summ["pct_above_99_8"] = (100 * summ["above_99_8"] / summ["facilities"]).round(1)
    k = float(np.median(f["se"] * np.sqrt(f[v])))
    grid = np.linspace(f[v].min(), f[v].max(), 300)
    fig, ax = plt.subplots(figsize=(8, 5.5))
    for name, g in f.groupby("ownership_type"):
        ax.scatter(g[v], g[y], s=14, alpha=0.65, label=f"{name} (n={len(g)})")
    for zl, ls in [(1.96, "--"), (3.09, ":")]:
        half = zl * np.sqrt((k / np.sqrt(grid)) ** 2 + tau2)
        ax.plot(grid, center + half, "k" + ls, lw=1, label=("95% limits" if zl == 1.96 else "99.8% limits"))
        ax.plot(grid, center - half, "k" + ls, lw=1)
    ax.axhline(center, color="k", lw=0.8)
    ax.set_xlabel("Cases behind the rate (facility volume)")
    ax.set_ylabel("Risk-standardised readmission rate (%)")
    ax.set_title(f"Funnel plot: 30-day potentially preventable readmission (tau = {np.sqrt(tau2):.2f} pts)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{FIG_DIR}/funnel_readmission.png", dpi=150)
    plt.close(fig)
    return f, summ


# figures
def figures(ladder, sens):
    n = ladder["outcome"].nunique()
    fig, axes = plt.subplots(1, n, figsize=(4.4 * n, 4.2), squeeze=False)
    for ax, (key, g) in zip(axes[0], ladder.groupby("outcome", sort=False)):
        yy = np.arange(len(g))[::-1]
        ax.errorbar(g["coef"], yy, xerr=[g["coef"] - g["ci_low"], g["ci_high"] - g["coef"]], fmt="o", capsize=3)
        ax.axvline(0, color="k", lw=0.8)
        ax.set_yticks(yy)
        ax.set_yticklabels(g["analysis"], fontsize=7)
        ax.set_title(OUTCOMES[key][2], fontsize=9)
        ax.set_xlabel("Change per +10 min/day (95% CI)", fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{FIG_DIR}/therapy_dose_ladder.png", dpi=150)
    plt.close(fig)

    sub = sens[sens["term"].isin(["therapy10", "therapy10_c"]) & ~sens["analysis"].str.startswith("S8")]
    fig, axes = plt.subplots(1, n, figsize=(5.4 * n, 5.5), squeeze=False)
    for ax, (key, g) in zip(axes[0], sub.groupby("outcome", sort=False)):
        yy = np.arange(len(g))[::-1]
        ax.errorbar(g["coef"], yy, xerr=[g["coef"] - g["ci_low"], g["ci_high"] - g["coef"]], fmt="s", capsize=3, color="C1")
        ax.axvline(0, color="k", lw=0.8)
        ax.set_yticks(yy)
        ax.set_yticklabels(g["analysis"], fontsize=7)
        ax.set_title(f"Sensitivity:\n{OUTCOMES[key][2]}", fontsize=9)   # two lines: long titles were clipped
        ax.set_xlabel("Dose coefficient (+10 min/day, 95% CI)", fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{FIG_DIR}/sensitivity_dose.png", dpi=150)
    plt.close(fig)


# main
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(FIG_DIR, exist_ok=True)
    d = prepare(load_facility(), pac_fill=PAC)
    out("Version: " + ("v1.2 AS PLANNED (suppressed dual / rural % left missing)" if AS_PLANNED
                       else f"v1.3 (suppressed dual / rural % filled with {PAC} beneficiaries + indicators)"))
    out(f"Facilities in database: {len(d):,}. Impairment covariates (reference = all other conditions): {imp_terms(d)}")
    sd_min = float(d["therapy_min_per_day"].std())
    out(f"SD of therapy dose across facilities = {sd_min:.1f} min/day (so +10 min is {10 / sd_min:.2f} SD)")

    ladders, fulls, senss, losos = [], {}, [], []
    for key in OUTCOMES:
        res = run_outcome(d, key, sd_min)
        if res is None:
            continue
        lad, full, sens, loso, _ = res
        ladders.append(lad)
        senss.append(sens)
        losos.append(loso)
        full["label"] = full["term"].map(label)
        fulls[key] = full
    if not ladders:
        raise SystemExit("No model could be fitted.")
    ladder, sens, loso = pd.concat(ladders), pd.concat(senss), pd.concat(losos)

    prim = ladder[ladder["analysis"].str.startswith("P")].copy()
    prim["p_holm"] = holm(prim["p"].to_numpy())
    ladder.round(4).to_csv(f"{OUT_DIR}/dose_ladder.csv", index=False)
    prim.round(4).to_csv(f"{OUT_DIR}/dose_primary_holm.csv", index=False)
    sens.round(4).to_csv(f"{OUT_DIR}/sensitivity.csv", index=False)
    loso.round(4).to_csv(f"{OUT_DIR}/leave_one_state_out.csv", index=False)
    for key, full in fulls.items():
        full[~full["term"].str.startswith("st_")].round(4).to_csv(f"{OUT_DIR}/primary_model_{key.lower()}.csv", index=False)
    figures(ladder, sens)

    out("\n=== CONFIRMATORY: therapy-dose coefficient in the primary model (Holm-adjusted across the outcomes) ===")
    out(prim[["outcome", "n", "coef", "ci_low", "ci_high", "per_sd", "p", "p_holm"]].round(3).to_string(index=False))
    out("\n=== Dose coefficient across the adjustment ladder ===")
    out(ladder[["outcome", "analysis", "n", "coef", "ci_low", "ci_high", "p"]].round(3).to_string(index=False))
    for key, full in fulls.items():
        out(f"\n=== Primary model, all terms except states: {OUTCOMES[key][2]} (exploratory except the dose row) ===")
        show = full[~full["term"].str.startswith("st_") & (full["term"] != "Intercept")]
        out(show[["label", "coef", "ci_low", "ci_high", "p"]].round(3).to_string(index=False))
    out("\n=== Sensitivity analyses: dose coefficient ===")
    out(sens[sens["term"].isin(["therapy10", "therapy10_c"])][["outcome", "analysis", "n", "coef", "ci_low", "ci_high"]].round(3).to_string(index=False))
    out("\n=== Sensitivity analyses: non-linearity (S6), skew of group share (S9), dose x ownership interaction (S8) ===")
    other = sens[sens["term"].isin(["therapy10_sq", "tq2", "tq3", "tq4", "gt2", "gt3", "dose_x_for_profit", "dose_x_government"])]
    out(other[["outcome", "analysis", "term", "coef", "ci_low", "ci_high", "p"]].round(3).to_string(index=False))
    out("\n=== Leave-one-state-out: range of the dose coefficient ===")
    out(loso.groupby("outcome")["coef"].agg(["min", "median", "max"]).round(3).to_string())

    f, summ = funnel(d)
    f.to_csv(f"{OUT_DIR}/funnel_flags.csv", index=False)
    summ.to_csv(f"{OUT_DIR}/funnel_summary_by_ownership.csv", index=False)
    out("\n=== Readmission outliers by ownership (over-dispersion-adjusted 99.8% limit) ===")
    out(summ.to_string(index=False))
    out("\nCaveat: facility-level associations; measure windows differ; not causal. Only the dose rows above are confirmatory.")
    with open(f"{OUT_DIR}/analysis_summary.txt", "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    print(f"\nSaved tables to {OUT_DIR}/ and figures to {FIG_DIR}/")


if __name__ == "__main__":
    main()
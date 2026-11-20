"""Check the historical therapy-dose regressions for collinearity and influential facilities.

For each outcome, plot the residuals and compare the dose estimate across refits.
"""
import io
import os

import matplotlib
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from irf_common import design, fit, imp_terms, label, load_facility, prepare  # noqa: E402

OUT_DIR = "data/processed/diagnostics"
FIG_DIR = "reports/figures"
OUTCOMES = {"DTC": ("i_019_02_dtc_rs_rate", "DTC rate (%)"),
            "RSRR": ("i_017_01_ppr_pd_rsrr", "Readmission RSRR (%)"),
            "PAY": ("std_pay_k", "Standardized payment per stay ($1,000)")}
# same terms as the v1.3 primary model in 04_analysis.py
OWN = ["for_profit", "government", "physician", "unit"]
MODE = ["group10", "cotreat5", "pt5", "slp5"]
COV = ["risk", "age", "dual10", "rural10", "dual_suppressed", "rural_suppressed"]
buf = io.StringIO()


def out(*a):
    print(*a)
    print(*a, file=buf)


def vif_within(s, terms):
    """VIF after removing state means: 1 / (1 - R^2) of each term on the others."""
    X = s[terms] - s.groupby("state")[terms].transform("mean")
    rows = []
    for t in terms:
        others = X.drop(columns=t).to_numpy()
        beta = np.linalg.lstsq(others, X[t].to_numpy(), rcond=None)[0]
        resid = X[t].to_numpy() - others @ beta
        r2 = 1 - resid.var(ddof=0) / X[t].var(ddof=0)
        rows.append({"term": t, "label": label(t), "vif": 1 / (1 - r2) if r2 < 1 else np.inf})
    return pd.DataFrame(rows)


def dose(s, ycol, terms):
    t = fit(s, ycol, terms, True)[0].loc["therapy10"]
    return {"n": len(s), "coef": t.coef, "ci_low": t.ci_low, "ci_high": t.ci_high}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    d = prepare(load_facility())
    terms = ["therapy10"] + MODE + OWN + COV + imp_terms(d) + ["log_stays"]
    out("Diagnostics of the v1.3 primary model (validation only; the primary model is unchanged).")
    vifs, checks, resid_stats = [], [], []
    fig, axes = plt.subplots(2, len(OUTCOMES), figsize=(4.6 * len(OUTCOMES), 8))

    for j, (key, (ycol, ylab)) in enumerate(OUTCOMES.items()):
        s = d.dropna(subset=[ycol, "state"] + terms).copy()
        n = len(s)

        v = vif_within(s, terms).assign(outcome=key)
        vifs.append(v)

        checks.append({"outcome": key, "analysis": "primary", **dose(s, ycol, terms)})
        checks.append({"outcome": key, "analysis": "without HCC risk score",
                       **dose(s, ycol, [t for t in terms if t != "risk"])})
        checks.append({"outcome": key, "analysis": "without dual % and its indicator",
                       **dose(s, ycol, [t for t in terms if t not in ("dual10", "dual_suppressed")])})

        X, _ = design(s, terms, True)
        res = sm.OLS(s[ycol].to_numpy(float), X).fit()
        with np.errstate(invalid="ignore", divide="ignore"):
            infl = res.get_influence()
            cook = infl.cooks_distance[0]
        # A state with one facility gives it unit leverage. Leave Cook's D missing
        # and retain the facility in the sensitivity fit.
        lev1 = infl.hat_matrix_diag > 1 - 1e-8
        cook = np.where(lev1, np.nan, cook)
        n_lev1 = int(lev1.sum())
        keep = ~(cook > 4 / n)
        checks.append({"outcome": key, "analysis": f"without {int((~keep).sum())} facilities with Cook's D > 4/n",
                       **dose(s[keep], ycol, terms)})

        e = res.resid
        resid_stats.append({"outcome": key, "n": n, "skewness": stats.skew(e), "excess_kurtosis": stats.kurtosis(e),
                            "max_cooks_d": np.nanmax(cook), "n_cooks_gt_4_over_n": int((~keep).sum()),
                            "n_leverage_1_kept": n_lev1})
        ax = axes[0, j]
        ax.scatter(res.fittedvalues, e, s=6, alpha=0.5)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_title(f"{ylab}\nresiduals vs fitted", fontsize=9)
        ax.set_xlabel("Fitted value", fontsize=8)
        ax.set_ylabel("Residual", fontsize=8)
        sm.qqplot(e, line="s", ax=axes[1, j], markersize=2)
        axes[1, j].set_title("Normal Q-Q plot of residuals", fontsize=9)

    fig.tight_layout()
    fig.savefig(f"{FIG_DIR}/diagnostics_residuals.png", dpi=150)
    plt.close(fig)

    vifs = pd.concat(vifs, ignore_index=True)
    checks = pd.DataFrame(checks)
    resid_stats = pd.DataFrame(resid_stats)
    vifs.round(3).to_csv(f"{OUT_DIR}/vif.csv", index=False)
    checks.round(4).to_csv(f"{OUT_DIR}/dose_checks.csv", index=False)
    resid_stats.round(4).to_csv(f"{OUT_DIR}/residuals.csv", index=False)

    out("\n=== 1) VIF after removing state means (> 5 noted, > 10 a concern); largest 8 per outcome ===")
    for key, g in vifs.groupby("outcome", sort=False):
        out(f"--- {key} ---")
        out(g.sort_values("vif", ascending=False).head(8)[["label", "vif"]].round(2).to_string(index=False))
    out(f"Terms with VIF > 5: {int((vifs['vif'] > 5).sum())}; VIF > 10: {int((vifs['vif'] > 10).sum())}")
    v = vifs[vifs["term"].isin(["risk", "dual10", "therapy10"])].pivot(index="label", columns="outcome", values="vif")
    out("\nVIF of the dose, the HCC risk score and dual %:")
    out(v.round(2).to_string())
    out("\n=== 2) and 4) Dose coefficient (+10 min/day) under the diagnostic refits ===")
    out(checks.round(3).to_string(index=False))
    out("\n=== 3) Residuals ===")
    out(resid_stats.round(3).to_string(index=False))
    out(f"\nFigure: {FIG_DIR}/diagnostics_residuals.png")
    with open(f"{OUT_DIR}/diagnostics_summary.txt", "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    print(f"\nSaved to {OUT_DIR}/")


if __name__ == "__main__":
    main()

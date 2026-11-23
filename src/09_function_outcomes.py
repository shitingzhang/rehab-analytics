"""Run the historical exploratory analysis of discharge function, self-care and mobility."""

import io
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from irf_common import DB_PATH, fit, holm, imp_terms, label, load_facility, prepare, sample_flow

OUT_DIR = Path("data/processed/function_outcomes")
FIG_DIR = Path("reports/figures")
RATIO_LABEL = "Historical therapy ratio (+10 units)"
OUTCOMES = {  # key: (column in facility, QRP measure prefix for the denominator, label)
    "FUNC": ("i_026_01_obs_rate", "I_026_01", "Discharge function score (% meeting expected)"),
    "SELF": ("i_011_05_obs_rate", "I_011_05", "Discharge self-care (% meeting expected)"),
    "MOB": ("i_012_06_obs_rate", "I_012_06", "Discharge mobility (% meeting expected)"),
}
# same terms as the v1.3 primary model in 04_analysis.py
OWN = ["for_profit", "government", "physician", "unit"]
MODE = ["group10", "cotreat5", "pt5", "slp5"]
SUPP = ["dual_suppressed", "rural_suppressed"]
COV = ["risk", "age", "dual10", "rural10"] + SUPP
buf = io.StringIO()


def out(*a):
    print(*a)
    print(*a, file=buf)


def denominators():
    conn = sqlite3.connect(DB_PATH)
    try:
        q = pd.read_sql_query("SELECT ccn, measure_code, score_num FROM qrp_long WHERE measure_code LIKE '%_DENOMINATOR'",
                              conn)
    finally:
        conn.close()
    q = q[q["measure_code"].isin([f"{m}_DENOMINATOR" for _, m, _ in OUTCOMES.values()])]
    return q.pivot_table(index="ccn", columns="measure_code", values="score_num", aggfunc="first")


def row(outcome, analysis, n, tab, term="therapy10"):
    t = tab.loc[term]
    return {"outcome": outcome, "analysis": analysis, "term": term, "n": n, "coef": t.coef, "ci_low": t.ci_low,
            "ci_high": t.ci_high, "p": t.p}


def plot_estimates(primary):
    fig, axis = plt.subplots(figsize=(8.4, 2.8), layout="constrained")
    fig.get_layout_engine().set(w_pad=0.16, h_pad=0.10)
    for i, estimate in enumerate(primary.itertuples()):
        axis.plot([estimate.ci_low, estimate.ci_high], [i, i], color="#176373", lw=2)
        axis.plot(estimate.coef, i, "o", color="#176373", ms=6)
    axis.axvline(0, color="#657481", lw=1, ls="--")
    axis.set_yticks(range(3), ["Discharge function", "Discharge self-care", "Discharge mobility"])
    axis.set(ylim=(2.5, -0.5), xlabel="Adjusted difference (percentage points)")
    axis.grid(axis="x", alpha=0.16)
    axis.tick_params(axis="y", length=0)
    axis.spines[["top", "right", "left"]].set_visible(False)
    fig.suptitle("Historical therapy ratio and discharge function", x=0.025, ha="left", fontweight="bold")
    fig.supxlabel(
        "Estimates and 95% intervals per 10 units of the historical recorded-minutes/covered-day ratio.",
        x=0.025,
        ha="left",
        fontsize="small",
    )
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_DIR / "historical_function_outcomes.png", dpi=200, facecolor="white")
    plt.close(fig)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    d = prepare(load_facility())
    d = d.merge(denominators(), left_on="ccn", right_index=True, how="left")
    terms = ["therapy10"] + MODE + OWN + COV + imp_terms(d) + ["log_stays"]
    ratio_sd = float(d["therapy_min_per_day"].std())
    out("EXPLORATORY (added after the v1.2/v1.3 results were seen). Function measures: CY2025; therapy data: FY2024.")
    out(f"SD of historical therapy ratio = {ratio_sd:.1f} units")

    prim, sens, fulls = [], [], {}
    for key, (ycol, code, ylab) in OUTCOMES.items():
        s = d.dropna(subset=[ycol, "state"] + terms).copy()
        out(f"\n=== {ylab}: n = {len(s):,} of {d[ycol].notna().sum():,} facilities with the outcome, "
            f"{s['state'].nunique()} states; outcome mean {s[ycol].mean():.1f}, SD {s[ycol].std():.1f} ===")
        sample_flow(d, ycol, terms, out)
        tab = fit(s, ycol, terms, True)[0]
        r = row(key, "primary (v1.3 model)", len(s), tab)
        r["per_sd"] = r["coef"] * ratio_sd / 10
        prim.append(r)
        full = tab.reset_index()
        full["label"] = full["term"].map(label)
        full.loc[full.term == "therapy10", "label"] = RATIO_LABEL
        fulls[key] = full

        k = (s["dual_suppressed"] == 0) & (s["rural_suppressed"] == 0)
        terms_v12 = [t for t in terms if t not in SUPP]
        sens.append(row(key, "as planned (v1.2): complete cases, no fill", int(k.sum()), fit(s[k], ycol, terms_v12, True)[0]))
        w = s[f"{code}_DENOMINATOR"].where(s[f"{code}_DENOMINATOR"] > 0)
        ok = w.notna()
        sens.append(row(key, "weighted by measure denominator (WLS)", int(ok.sum()),
                        fit(s[ok], ycol, terms, True, w[ok].to_numpy())[0]))
        terms_nodr = [t for t in terms_v12 if t not in ("dual10", "rural10")]
        sens.append(row(key, "without rural % and dual %", len(s), fit(s, ycol, terms_nodr, True)[0]))

    prim = pd.DataFrame(prim)
    prim["p_holm"] = holm(prim["p"].to_numpy())
    sens = pd.DataFrame(sens)
    prim.round(4).to_csv(OUT_DIR / "dose_function_holm.csv", index=False, lineterminator="\n")
    sens.round(4).to_csv(OUT_DIR / "sensitivity.csv", index=False, lineterminator="\n")
    for key, full in fulls.items():
        full[~full["term"].str.startswith("st_")].round(4).to_csv(
            OUT_DIR / f"model_{key.lower()}.csv", index=False, lineterminator="\n"
        )
    plot_estimates(prim)

    out("\n=== Historical therapy ratio (+10 units) and function outcomes (percentage points, Holm across three) ===")
    out(prim[["outcome", "n", "coef", "ci_low", "ci_high", "per_sd", "p", "p_holm"]].round(3).to_string(index=False))
    out("\n=== Sensitivity estimates for the historical therapy ratio ===")
    out(sens[["outcome", "analysis", "n", "coef", "ci_low", "ci_high"]].round(3).to_string(index=False))
    for key, full in fulls.items():
        out(f"\n=== Full model: {OUTCOMES[key][2]} (all terms exploratory, not adjusted for multiple comparisons) ===")
        show = full[~full["term"].str.startswith("st_") & (full["term"] != "Intercept")]
        out(show[["label", "coef", "ci_low", "ci_high", "p"]].round(3).to_string(index=False))
    out("\nCaveat: exploratory, facility-level, observational; function (CY2025) and therapy (FY2024) windows do not overlap.")
    (OUT_DIR / "function_summary.txt").write_text(buf.getvalue(), encoding="utf-8", newline="\n")
    print(f"\nSaved to {OUT_DIR}/")


if __name__ == "__main__":
    main()

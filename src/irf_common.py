"""
irf_common.py - helpers shared by 04_analysis.py and 05_screening.py (keep it in src/ next to them).

  * regression engine: OLS / WLS with cluster-robust (CR1) standard errors, Holm adjustment
  * data loading from data/processed/irf_analytics.db and construction of the analysis variables
  * impairment-group shares with a configurable fill value for CMS-suppressed counts
"""
import sqlite3
import os

import numpy as np
import pandas as pd
from scipy import stats

DB_PATH = "data/processed/irf_analytics.db"
SUPPRESSED_FILL_DB = 5            # value 01_data_quality_cms.py used to fill counts of 1-10
PAC_FILL = 5                      # plan v1.3: suppressed dual / rural beneficiary counts (true 1-10); None = v1.2 (no fill)
REF_GROUP = "all_other_conditions"
MERGE_SPINAL = True               # merge the two (small) spinal-cord impairment groups; set False to keep all nine
SPINAL = ["spinal_cord_disease_or_condition_non_traumatic", "spinal_cord_injury_traumatic"]

NICE = {
    "therapy10": "Therapy dose (+10 min/day)", "group10": "Group/concurrent share (+10 pts)",
    "cotreat5": "Co-treatment share (+5 pts)", "pt5": "PT share of minutes (+5 pts)",
    "slp5": "SLP share of minutes (+5 pts)", "for_profit": "For-profit (vs non-profit)",
    "government": "Government (vs non-profit)", "physician": "Physician-owned (vs non-profit)",
    "unit": "Hospital-based unit (vs free-standing)", "risk": "HCC risk score (+1)", "age": "Mean age (+1 yr)",
    "dual10": "Dual-eligible % (+10 pts)", "rural10": "Rural % (+10 pts)", "log_stays": "log(Medicare stays)",
    "dual_suppressed": "Dual-eligible % suppressed (filled)", "rural_suppressed": "Rural % suppressed (filled)",
}


def label(term: str) -> str:
    if term in NICE:
        return NICE[term]
    if term.startswith("imp10_"):
        return f"Impairment: {term[6:].replace('_', ' ')} (+10 pts)"
    if term.startswith("st_"):
        return f"State {term[3:]}"
    return term


# ------------------------------------------------------------------------------ statistics
def ols_cluster(y, X, groups, w=None):
    """OLS / WLS with CR1 cluster-robust covariance. Returns beta, se, dof (G-1), r2."""
    y, X = np.asarray(y, float), np.asarray(X, float)
    w = np.ones(len(y)) if w is None else np.asarray(w, float)
    sw = np.sqrt(w)
    Xw, yw = X * sw[:, None], y * sw
    bread = np.linalg.pinv(Xw.T @ Xw)
    beta = bread @ Xw.T @ yw
    resid = y - X @ beta
    scores = X * (w * resid)[:, None]
    G = pd.DataFrame(scores).groupby(np.asarray(groups)).sum().to_numpy()
    n, k = X.shape
    g = G.shape[0]
    c = (g / (g - 1)) * ((n - 1) / (n - k))
    V = c * bread @ (G.T @ G) @ bread
    ybar = np.average(y, weights=w)
    r2 = 1 - np.sum(w * resid ** 2) / np.sum(w * (y - ybar) ** 2)
    return beta, np.sqrt(np.clip(np.diag(V), 0, None)), g - 1, r2


def tidy(names, beta, se, dof):
    t = stats.t.ppf(0.975, dof)
    with np.errstate(divide="ignore", invalid="ignore"):
        tv = beta / np.where(se > 0, se, np.nan)
    return pd.DataFrame({"term": names, "coef": beta, "se": se, "ci_low": beta - t * se,
                         "ci_high": beta + t * se, "p": 2 * stats.t.sf(np.abs(tv), dof)})


def holm(p):
    """Holm step-down adjusted p-values."""
    p = np.asarray(p, float)
    order = np.argsort(p)
    m, adj, running = len(p), np.empty(len(p)), 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * p[i])
        adj[i] = min(1.0, running)
    return adj


def design(s: pd.DataFrame, terms, state_fe: bool):
    mats, names = [np.ones((len(s), 1)), s[list(terms)].to_numpy(float)], ["Intercept"] + list(terms)
    if state_fe:
        D = pd.get_dummies(s["state"], prefix="st", drop_first=True, dtype=float)
        mats.append(D.to_numpy())
        names += list(D.columns)
    return np.hstack(mats), names


def fit(s: pd.DataFrame, ycol: str, terms, state_fe=True, w=None):
    """Returns (tidy table indexed by term, r2, number of clusters)."""
    X, names = design(s, terms, state_fe)
    beta, se, dof, r2 = ols_cluster(s[ycol].to_numpy(float), X, s["state"].to_numpy(), w=w)
    return tidy(names, beta, se, dof).set_index("term"), r2, dof + 1


# ------------------------------------------------------------------------------ data
def load_facility() -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"{DB_PATH} not found. Run 02_build_database.py first.")
    conn = sqlite3.connect(DB_PATH)
    try:
        fac = pd.read_sql_query("SELECT * FROM facility", conn)
        wide = pd.read_sql_query("SELECT * FROM facility_wide", conn)
    finally:
        conn.close()
    if fac.empty:
        raise SystemExit("The facility table is empty. Re-run 02_build_database.py and fix any error it reports.")
    extra = ["ccn"] + [c for c in wide.columns if c.startswith(("dx_n_", "dx_supp_")) and c not in fac.columns]
    return fac.merge(wide[extra], on="ccn", how="left")


def impairment_shares(d: pd.DataFrame, fill: float = SUPPRESSED_FILL_DB, merge: bool = MERGE_SPINAL):
    """Percent of patients in each impairment group. Suppressed cells (true 1-10) take value `fill`.
    Returns None if `fill` differs from the stored one and the per-group suppression flags are missing."""
    groups = [c[len("dx_pct_"):] for c in d.columns if c.startswith("dx_pct_")]
    cnt = pd.DataFrame({g: d[f"dx_n_{g}"] for g in groups})
    if fill != SUPPRESSED_FILL_DB:
        if not all(f"dx_supp_{g}" in d.columns for g in groups):
            return None
        for g in groups:
            cnt[g] = cnt[g] + (fill - SUPPRESSED_FILL_DB) * d[f"dx_supp_{g}"]
    if merge and all(g in cnt.columns for g in SPINAL):
        cnt["spinal_cord_any"] = cnt[SPINAL].sum(axis=1, min_count=1)
        cnt = cnt.drop(columns=SPINAL)
    return 100 * cnt.div(cnt.sum(axis=1, min_count=1), axis=0)


def prepare(d: pd.DataFrame, fill: float = SUPPRESSED_FILL_DB, pac_fill=PAC_FILL) -> pd.DataFrame:
    """Add analysis variables (scaled so a coefficient reads 'per +10 minutes', 'per +10 points', ...).
    `pac_fill`: beneficiaries assumed behind a suppressed dual / rural % ('*'); None keeps them missing (v1.2)."""
    d = d.copy()
    d["therapy10"] = d["therapy_min_per_day"] / 10
    d["group10"] = d["group_therapy_share"] * 10          # share is 0-1: x10 = per +10 percentage points
    d["cotreat5"] = d["cotreat_share"] * 20               # per +5 percentage points
    d["pt5"], d["slp5"] = d["pt_share"] * 20, d["slp_share"] * 20
    d["risk"], d["age"] = d["bene_avg_risk_scre"], d["bene_avg_age"]
    for short, col in [("dual", "bene_dual_pct"), ("rural", "bene_rrl_pct")]:
        flag = f"{short}_suppressed"
        if flag not in d.columns:
            raise SystemExit(f"{flag} missing: re-run 01_data_quality_cms.py and 02_build_database.py.")
        d[flag] = d[flag].astype(float)
        pct = d[col] if pac_fill is None else d[col].mask(d[flag] == 1, 100 * pac_fill / d["bene_dstnct_cnt"])
        d[f"{short}10"] = pct / 10
    d["log_stays"] = np.log(d["tot_epsd_stay_cnt"].where(d["tot_epsd_stay_cnt"] > 0))
    d["unit"] = d["is_unit_ccn"].astype(float)
    d["std_pay_k"] = d["std_pay_per_stay"] / 1000
    own = d["ownership_type"].astype("string").str.strip().str.lower()
    known = own.isin(["for profit", "non-profit", "government", "physician"])
    for col, lab in [("for_profit", "for profit"), ("government", "government"), ("physician", "physician")]:
        d[col] = (own == lab).astype(float).where(known)
    pct = impairment_shares(d, fill)
    if pct is None:
        raise SystemExit("Cannot rebuild impairment shares for this fill value (dx_supp_* columns missing).")
    for g in pct.columns:
        d[f"imp10_{g}"] = pct[g] / 10
    d = d.drop(columns=[c for c in d.columns if c.startswith("imp10_") and c == f"imp10_{REF_GROUP}"], errors="ignore")
    return d


def imp_terms(d: pd.DataFrame):
    return sorted(c for c in d.columns if c.startswith("imp10_"))

def sample_flow(d: pd.DataFrame, ycol: str, terms, out=print):
    """Print which variables remove facilities from a complete-case analysis (never drop rows silently)."""
    names = list(terms) + ["state"]
    base = d[d[ycol].notna()]
    miss = base[names].isna().sum().sort_values(ascending=False)
    out(f"    Sample flow ({ycol}): {len(base):,} facilities have the outcome")
    cur = base
    for t in miss.index:
        if miss[t] == 0:
            break
        before = len(cur)
        cur = cur[cur[t].notna()]
        out(f"      {label(t) if t != 'state' else 'state'}: missing in {int(miss[t]):,}; "
            f"{len(cur):,} remain after requiring it (-{before - len(cur):,})")
    out(f"      complete cases: {len(base.dropna(subset=names)):,}")
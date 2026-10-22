"""
01_data_quality_cms.py
Data-quality audit, cleaning and feature engineering for REAL public CMS inpatient
rehabilitation (IRF) data.

Grain changes from  encounter (patient stay)  ->  facility (one row per CCN).

Inputs (download manually, then rename into data/raw/):
    pac_irf_by_provider.csv      Medicare Post-Acute Care Utilization - IRF by Geography and Provider (FY2024)
    irf_general_information.csv  Provider Data Catalog: Inpatient Rehabilitation Facility - General Information
    irf_provider_data.csv        Provider Data Catalog: Inpatient Rehabilitation Facility - Provider Data (long format)
    irf_conditions.csv           Provider Data Catalog: Inpatient Rehabilitation Facility - Conditions

Outputs (data/processed/):
    audit_log.csv                every rule, count, and action  (nothing is dropped silently)
    clean_facility_cohort.csv    one row per IRF, PAC utilization + QRP outcomes + case-mix
"""

import os
import re

import numpy as np
import pandas as pd

RAW_DIR = "data/raw"
PROCESSED_DIR = "data/processed"

FILES = {
    "pac": "pac_irf_by_provider.csv",
    "general": "irf_general_information.csv",
    "qrp": "irf_provider_data.csv",
    "conditions": "irf_conditions.csv",
}

# CMS suppression markers (small cells / privacy). They are NOT zeros.
NO_FOOTNOTE_TOKENS = {"", "-"}   # "-" = CMS placeholder for "no footnote"
SUPPRESSION_TOKENS = {"*", "+", "", "NA", "N/A", "NOT AVAILABLE", "LESS THAN 11", "<11"}

PCT_COLS_HINT = re.compile(r"_pct$")
SUPPRESSED_FILL = 5          # CMS suppresses counts of 1-10; midpoint used for impairment-group shares
MEASURES_KEEP = [  # QRP measure codes used downstream (see IRF_Data_Dictionary.pdf, Table 9)
    "I_017_01_PPR_PD_OBS_RATE", "I_017_01_PPR_PD_RSRR",
    "I_017_01_PPR_PD_RSRR_2_5", "I_017_01_PPR_PD_RSRR_97_5", "I_017_01_PPR_PD_VOLUME",
    "I_019_02_DTC_OBS_RATE", "I_019_02_DTC_RS_RATE", "I_019_02_DTC_VOLUME",
    "I_011_05_OBS_RATE", "I_012_06_OBS_RATE", "I_026_01_OBS_RATE",
    "I_020_01_MSPB_SCORE",
]


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def snake(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name).strip().lower()).strip("_")


def find_col(df: pd.DataFrame, *patterns: str) -> str:
    """Return the first column whose snake_case name matches any regex (robust to header drift)."""
    for pat in patterns:
        for col in df.columns:
            if re.search(pat, col):
                return col
    raise KeyError(f"None of {patterns} found in columns: {list(df.columns)[:12]} ...")


def to_ccn(s: pd.Series) -> pd.Series:
    """CCNs are 6-character IDs. Keep LETTERS (hospital-based rehab units carry a letter in
    position 3, e.g. '01T011') and keep leading zeros - never read them as numbers."""
    t = s.astype("string").str.strip().str.upper().str.replace(r"[^0-9A-Z]", "", regex=True)
    t = t.mask(t.fillna("") == "")
    return t.str.zfill(6)


def is_suppressed(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip().str.upper().isin(SUPPRESSION_TOKENS)


def to_num(s: pd.Series) -> pd.Series:
    cleaned = s.astype("string").str.replace(r"[,$%]", "", regex=True).str.strip()
    cleaned = cleaned.mask(is_suppressed(cleaned))
    return pd.to_numeric(cleaned, errors="coerce").astype("float64")  # plain float64: keeps np.select/comparisons simple


def read_raw(key: str) -> pd.DataFrame:
    df = pd.read_csv(f"{RAW_DIR}/{FILES[key]}", dtype=str, keep_default_na=False)
    df.columns = [snake(c) for c in df.columns]
    return df


class Audit:
    def __init__(self) -> None:
        self.rows = []

    def add(self, rule_id, table, description, n, action) -> None:
        self.rows.append(
            {"rule_id": rule_id, "table": table, "check_description": description,
             "records_affected": int(n), "action_taken": action}
        )

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


# ------------------------------------------------------------------------------
# 1. PAC utilization file (one row per provider for FY2024; also contains NATION/STATE rows)
# ------------------------------------------------------------------------------
def build_ccn_crosswalk(source_ids: pd.Series, pac_ids: set, audit: Audit) -> dict:
    """Provider Data files identify hospital-based rehab units with a letter in position 3
    ('01T011'); the PAC utilization file uses a numeric ID for the same facility ('010011').

    Rules (the data decides, nothing is hard-coded):
      1. Try every digit 0-9 in position 3 and keep candidates that exist in the PAC file.
      2. A candidate that is ALREADY an exact ID of another facility in the source files
         (e.g. the free-standing hospital '013025' vs the unit '01T025') is not allowed.
      3. Exactly one candidate -> mapped. Several -> resolved with the letter->digit rule learned
         from the unambiguous cases (only if >=95% of them agree), otherwise left unmapped.
    """
    from collections import Counter
    src = set(source_ids.dropna())
    rows, pending = [], []
    for sid in sorted(src):
        if sid in pac_ids or not sid[2].isalpha():
            continue
        hits = [c for c in (sid[:2] + d + sid[3:] for d in "0123456789") if c in pac_ids and c not in src]
        if len(hits) == 1:
            rows.append((sid, hits[0], "unique"))
        elif len(hits) > 1:
            pending.append((sid, hits))
        else:
            rows.append((sid, None, "no_pac_counterpart"))

    pairs = Counter((sid[2], tgt[2]) for sid, tgt, m in rows if m == "unique")
    dominant = {}
    for letter in {l for l, _ in pairs}:
        tot = sum(v for (l, _), v in pairs.items() if l == letter)
        digit, v = max(((d, v) for (l, d), v in pairs.items() if l == letter), key=lambda x: x[1])
        if v / tot >= 0.95:
            dominant[letter] = digit
    print("   Crosswalk letter->digit counts (unambiguous cases):", {f"{l}->{d}": v for (l, d), v in sorted(pairs.items())})
    for sid, hits in pending:
        d = dominant.get(sid[2])
        cand = sid[:2] + d + sid[3:] if d else None
        rows.append((sid, cand, "ambiguous_resolved") if cand in hits else (sid, None, "ambiguous_unresolved"))

    used, final = set(), []
    n_collide = 0
    for sid, tgt, m in rows:
        if tgt is not None and tgt in used:
            n_collide += 1
            tgt, m = None, "collision"
        if tgt is not None:
            used.add(tgt)
        final.append((sid, tgt, m))
    xw = pd.DataFrame(final, columns=["source_id", "ccn", "method"])
    xw.to_csv(f"{PROCESSED_DIR}/ccn_crosswalk.csv", index=False)
    cnt = xw["method"].value_counts().to_dict()
    audit.add("DQ-09a", "crosswalk", "Unit-style CCNs (letter in position 3) in Provider Data files", len(xw), "Informational")
    audit.add("DQ-09b", "crosswalk", "...mapped to exactly one PAC provider ID", cnt.get("unique", 0), "Mapped")
    audit.add("DQ-09c", "crosswalk", "...ambiguous, resolved by the learned letter->digit rule", cnt.get("ambiguous_resolved", 0), "Mapped")
    audit.add("DQ-09d", "crosswalk", "...ambiguous, could not be resolved", cnt.get("ambiguous_unresolved", 0), "Left unmapped")
    audit.add("DQ-09e", "crosswalk", "...no PAC counterpart (e.g., closed/renumbered, too few Medicare FFS stays)",
              cnt.get("no_pac_counterpart", 0), "Left unmapped")
    audit.add("DQ-09f", "crosswalk", "...would collide with an already-mapped PAC ID", n_collide, "Left unmapped")
    return {r.source_id: r.ccn for r in xw.itertuples() if r.ccn is not None}


def clean_pac(audit: Audit) -> pd.DataFrame:
    pac = read_raw("pac")
    audit.add("DQ-00", "pac", "Rows loaded (all summary levels)", len(pac), "Informational")

    # Keep provider rows only; national/state rows are reference totals.
    level = pac["smry_ctgry"].str.upper()
    n_ref = level.isin(["NATION", "STATE"]).sum()
    audit.add("DQ-01", "pac", "National/State summary rows separated from provider rows",
              n_ref, "Moved out of facility table (kept for benchmarking)")
    pac_ref = pac[level.isin(["NATION", "STATE"])].copy()
    pac = pac[~level.isin(["NATION", "STATE"])].copy()

    pac["ccn"] = to_ccn(pac["prvdr_id"])
    bad_ccn = pac["ccn"].isna() | ~pac["ccn"].fillna("").str.fullmatch(r"[0-9A-Z]{6}")
    audit.add("DQ-02", "pac", "Provider ID is not a valid 6-character CCN", bad_ccn.sum(), "Excluded")
    pac = pac[~bad_ccn]

    dup = pac.duplicated(subset=["year", "ccn"], keep="first")
    audit.add("DQ-03", "pac", "Duplicate (year, CCN) rows", dup.sum(), "Deduplicated (kept first)")
    pac = pac[~dup].copy()

    # Plan v1.3: '*' in dual % / rural % = suppressed beneficiary count of 1-10 (filled in irf_common.prepare).
    # '+' (dual % of 8 Puerto Rico facilities) is a different marker and stays plain missing.
    for c, flag in [("bene_dual_pct", "dual_suppressed"), ("bene_rrl_pct", "rural_suppressed")]:
        pac[flag] = pac[c].astype("string").str.strip().eq("*")
        audit.add("DQ-04b", "pac", f"{c} suppressed ('*', true count 1-10)", pac[flag].sum(),
                  f"Flagged ({flag}); filled with 5 beneficiaries in the analysis (plan v1.3)")

    # Numeric conversion + suppression accounting (so '*' is never silently treated as 0)
    num_cols = [c for c in pac.columns if c.startswith(("bene_", "tot_", "indvdl_", "cncrnt_", "cotrt_", "prmry_dx_"))]
    n_suppressed = 0
    for c in num_cols:
        n_suppressed += int(is_suppressed(pac[c]).sum())
        pac[c] = to_num(pac[c])
    audit.add("DQ-04", "pac", "Suppressed cells ('*', '+') converted to missing, across all numeric columns",
              n_suppressed, "Set to NaN; never imputed as 0")

    pct_cols = [c for c in num_cols if PCT_COLS_HINT.search(c)]
    out_of_range = ((pac[pct_cols] < 0) | (pac[pct_cols] > 100)).any(axis=1)
    audit.add("DQ-05", "pac", "Any percentage column outside 0-100", out_of_range.sum(),
              "Flagged (dq_flag_pct_range); kept")
    pac["dq_flag_pct_range"] = out_of_range

    # Reconciliation identity from the data dictionary: total minutes = individual + concurrent/group + cotreatment
    bad_minutes = pd.Series(False, index=pac.index)
    for t in ("pt", "ot", "slp"):
        parts = pac[[f"indvdl_{t}_mnts", f"cncrnt_grp_{t}_mnts", f"cotrt_{t}_mnts"]]
        total = pac[f"tot_{t}_mnts"]
        mismatch = parts.notna().all(axis=1) & total.notna() & (parts.sum(axis=1) != total)
        bad_minutes |= mismatch
    audit.add("DQ-06", "pac", "Therapy-minute components do not sum to total (PT/OT/SLP)",
              bad_minutes.sum(), "Flagged (dq_flag_minutes); kept")
    pac["dq_flag_minutes"] = bad_minutes

    bad_stay = (pac["tot_epsd_stay_cnt"] <= 0) | (pac["tot_srvc_days"] < pac["tot_epsd_stay_cnt"])
    audit.add("DQ-07", "pac", "Non-positive stays, or covered days < stays (implied LOS < 1)",
              bad_stay.fillna(False).sum(), "Excluded from LOS/efficiency metrics (set to NaN)")

    bad_pay = pac["tot_alowd_amt"] < pac["tot_mdcr_pymt_amt"]
    audit.add("DQ-08", "pac", "Allowed amount < Medicare payment (should not occur)",
              bad_pay.fillna(False).sum(), "Flagged (dq_flag_payment); kept")
    pac["dq_flag_payment"] = bad_pay.fillna(False)

    # Derived metrics (provider-level, Medicare FFS only)
    ok = ~bad_stay.fillna(True)
    stays = pac["tot_epsd_stay_cnt"].where(ok)
    days = pac["tot_srvc_days"].where(ok)
    pac["alos_days"] = (days / stays).round(2)
    pac["medicare_pay_per_stay"] = (pac["tot_mdcr_pymt_amt"] / stays).round(0)
    pac["stays_per_beneficiary"] = (pac["tot_epsd_stay_cnt"] / pac["bene_dstnct_cnt"]).round(3)
    for t in ("pt", "ot", "slp"):
        pac[f"{t}_min_per_day"] = (pac[f"tot_{t}_mnts"] / days).round(1)
    pac["therapy_min_per_day"] = pac[["pt_min_per_day", "ot_min_per_day", "slp_min_per_day"]].sum(axis=1, min_count=3)

    # --- Therapy DOSE / MODE / MIX (research question: how much, in what format, and which disciplines) ---
    def _sum3(prefix):                      # sum a minute type across PT, OT, SLP (needs all three)
        return pac[[f"{prefix}_{t}_mnts" for t in ("pt", "ot", "slp")]].sum(axis=1, min_count=3)

    total_min = (pac["tot_pt_mnts"] + pac["tot_ot_mnts"] + pac["tot_slp_mnts"]).where(lambda x: x > 0)
    indiv_min, group_min, cotrt_min = _sum3("indvdl"), _sum3("cncrnt_grp"), _sum3("cotrt")
    pac["individual_min_per_day"] = (indiv_min / days).round(1)          # one-on-one minutes per covered day
    pac["group_conc_min_per_day"] = (group_min / days).round(1)          # concurrent + group minutes per covered day
    pac["cotreat_min_per_day"] = (cotrt_min / days).round(1)             # PT+OT / PT+SLP co-treatment minutes
    pac["individual_share"] = (indiv_min / total_min).round(3)
    pac["group_therapy_share"] = (group_min / total_min).round(3)        # concurrent + group share of all minutes
    pac["cotreat_share"] = (cotrt_min / total_min).round(3)
    for t in ("pt", "ot", "slp"):                                        # discipline mix (shares sum to 1)
        pac[f"{t}_share"] = (pac[f"tot_{t}_mnts"] / total_min).round(3)

    # Standardized payment (the PAC file's own 'standardized' amount; confirm in the data dictionary that it
    # removes geographic payment adjustments) is the cost outcome, so states are comparable.
    pac["std_pay_per_stay"] = (pac["tot_mdcr_stdzd_pymt_amt"] / stays).round(0)
    pac["outlier_payment_share"] = (pac["tot_outlier_pymt_amt"] / pac["tot_mdcr_pymt_amt"]).round(4)

    pac_ref.to_csv(f"{PROCESSED_DIR}/pac_national_state_reference.csv", index=False)
    return pac


# ------------------------------------------------------------------------------
# 2. Quality Reporting Program (long format: one row per provider x measure)
# ------------------------------------------------------------------------------
def clean_qrp(audit: Audit, xwalk: dict) -> pd.DataFrame:
    qrp = read_raw("qrp")
    ccn_col = find_col(qrp, r"ccn", r"certification")
    code_col, score_col = find_col(qrp, r"^measure_code$"), find_col(qrp, r"^score$")
    foot_col = find_col(qrp, r"^footnote$")
    qrp["ccn_source"] = to_ccn(qrp[ccn_col])
    qrp["ccn"] = qrp["ccn_source"].map(xwalk).fillna(qrp["ccn_source"])
    audit.add("DQ-10", "qrp", "Long-format rows loaded (provider x measure)", len(qrp), "Informational")

    dup = qrp.duplicated(subset=["ccn", code_col], keep="first")
    audit.add("DQ-11", "qrp", "Duplicate (CCN, measure code) rows", dup.sum(), "Deduplicated (kept first)")
    qrp = qrp[~dup].copy()

    qrp["score_num"] = to_num(qrp[score_col])
    fn = qrp[foot_col].astype("string").str.strip()
    # '-' is CMS's placeholder for "no footnote". Codes (IRF_Data_Dictionary.pdf, Table 10): 1 too few cases,
    # 2 not available, 3 shorter period than required (score IS reported), 4 suppressed, 5 not submitted,
    # 6/7 infection measures only, 8 waiver. Scores that are not numeric are already NaN via to_num.
    has_fn = (fn.notna() & ~fn.isin(list(NO_FOOTNOTE_TOKENS))).fillna(False)
    # A footnote EXPLAINS a score; it does not invalidate it. Non-numeric/suppressed scores are
    # already NaN through to_num, so nothing is nulled on the footnote alone.
    audit.add("DQ-12", "qrp", "Rows carrying a CMS footnote", has_fn.sum(),
              "Kept; footnote preserved in irf_qrp_long_clean.csv (score NOT nulled)")
    audit.add("DQ-14", "qrp", "Numeric scores that also carry a footnote", (has_fn & qrp["score_num"].notna()).sum(),
              "Kept; check footnote meaning in IRF_Data_Dictionary.pdf")
    audit.add("DQ-15", "qrp", "Scores that are non-numeric or suppressed", qrp["score_num"].isna().sum(),
              "Set to NaN (includes comparative-performance text rows)")
    print("   QRP footnote values excluding '-' (top 8):", fn[has_fn].value_counts().head(8).to_dict())
    try:  # reporting window of each key measure - needed to document the time mismatch with FY2024 PAC data
        sd, ed = find_col(qrp, r"^start_date$"), find_col(qrp, r"^end_date$")
        per = (qrp[qrp[code_col].isin(MEASURES_KEEP)].groupby(code_col)[[sd, ed]]
               .agg(lambda x: x.mode().iat[0] if len(x.mode()) else ""))
        per.to_csv(f"{PROCESSED_DIR}/irf_measure_periods.csv")
        print("   Reporting period per key measure (most common):")
        print(per.to_string())
    except KeyError:
        pass

    qrp[["ccn_source", "ccn", code_col, score_col, foot_col, "score_num"]].to_csv(
        f"{PROCESSED_DIR}/irf_qrp_long_clean.csv", index=False)

    wide = qrp[qrp[code_col].isin(MEASURES_KEEP)].pivot(index="ccn", columns=code_col, values="score_num")
    for m in MEASURES_KEEP:
        if m not in wide.columns:
            wide[m] = np.nan
    wide = wide[MEASURES_KEEP].reset_index()
    wide.columns = ["ccn"] + [snake(m) for m in MEASURES_KEEP]
    fn_codes = ["I_019_02_DTC_RS_RATE", "I_017_01_PPR_PD_RSRR"]
    fn_wide = (qrp[qrp[code_col].isin(fn_codes)].assign(fn_code=fn.where(has_fn, ""))
               .pivot(index="ccn", columns=code_col, values="fn_code"))
    fn_wide.columns = [snake(c) + "_fn" for c in fn_wide.columns]
    wide = wide.merge(fn_wide.reset_index(), on="ccn", how="left")

    # rates must lie in 0-100
    rate_cols = [c for c in wide.columns if c.endswith(("obs_rate", "rs_rate", "rsrr"))]
    bad_rate = ((wide[rate_cols] < 0) | (wide[rate_cols] > 100)).any(axis=1)
    audit.add("DQ-13", "qrp", "Rate/RSRR outside 0-100", bad_rate.sum(), "Set to NaN")
    wide.loc[bad_rate, rate_cols] = np.nan
    return wide


# ------------------------------------------------------------------------------
# 3. General information + conditions (case mix by diagnosis group)
# ------------------------------------------------------------------------------
def clean_general(audit: Audit, xwalk: dict) -> pd.DataFrame:
    gen = read_raw("general")
    gen["ccn_source"] = to_ccn(gen[find_col(gen, r"ccn", r"certification_number")])
    gen["ccn"] = gen["ccn_source"].map(xwalk).fillna(gen["ccn_source"])
    gen["is_unit_ccn"] = gen["ccn_source"].str[2].str.isalpha().fillna(False).astype(bool)  # hospital-based unit
    dup = gen.duplicated(subset=["ccn"], keep="first")
    audit.add("DQ-20", "general", "Duplicate CCN rows (after unit-ID mapping)", dup.sum(), "Deduplicated (kept first)")
    gen = gen[~dup]
    keep = {"ccn": "ccn", "ccn_source": "ccn_source", "is_unit_ccn": "is_unit_ccn"}
    for out, pat in {"general_name": r"provider_name", "general_state": r"^state$",
                     "cms_region": r"cms_region", "ownership_type": r"ownership",
                     "certification_date": r"certification_date"}.items():
        try:
            keep[find_col(gen, pat)] = out
        except KeyError:
            pass
    return gen[list(keep)].rename(columns=keep)


def clean_conditions(audit: Audit, xwalk: dict) -> pd.DataFrame:
    """Impairment-group case mix. Counts below 11 are suppressed by CMS (true value 1-10); dropping them
    would delete a large share of facilities from any model, so they are filled with the midpoint
    (SUPPRESSED_FILL) and the number of suppressed cells is kept so the choice can be tested."""
    cond = read_raw("conditions")
    cond["ccn_source"] = to_ccn(cond[find_col(cond, r"ccn", r"certification_number")])
    cond["ccn"] = cond["ccn_source"].map(xwalk).fillna(cond["ccn_source"])
    cond_col, cnt_col = find_col(cond, r"^condition$"), find_col(cond, r"^count$")
    cond["suppressed"] = is_suppressed(cond[cnt_col])
    cond["n_filled"] = to_num(cond[cnt_col]).where(~cond["suppressed"], SUPPRESSED_FILL)
    audit.add("DQ-21", "conditions", "Condition counts suppressed ('less than 11')", cond["suppressed"].sum(),
              f"Filled with {SUPPRESSED_FILL} (midpoint of 1-10) for shares; per-facility count kept as dx_n_suppressed_cells")
    n_supp = cond.groupby("ccn")["suppressed"].sum()
    audit.add("DQ-22", "conditions", "Facilities with 3 or more suppressed impairment groups",
              (n_supp >= 3).sum(), "Kept; used in the sensitivity analysis (restrict to <=1 suppressed)")
    dup = cond.duplicated(subset=["ccn", cond_col], keep="first")
    audit.add("DQ-23", "conditions", "Duplicate (CCN, condition) rows", dup.sum(), "Deduplicated (kept first)")
    cond = cond[~dup]

    wide = cond.pivot_table(index="ccn", columns=cond_col, values="n_filled", aggfunc="first")
    wide.columns = ["dx_n_" + snake(c) for c in wide.columns]
    wide["dx_n_total"] = wide.sum(axis=1, min_count=1)
    for c in [c for c in wide.columns if c.startswith("dx_n_") and c != "dx_n_total"]:
        wide[c.replace("dx_n_", "dx_pct_")] = (100 * wide[c] / wide["dx_n_total"]).round(1)
    wide["dx_n_suppressed_cells"] = n_supp.reindex(wide.index).astype(float)
    # which impairment groups were suppressed (1/0): lets the analysis re-compute shares with other fill values
    cond["supp_f"] = cond["suppressed"].astype(float)
    supp_wide = cond.pivot_table(index="ccn", columns=cond_col, values="supp_f", aggfunc="first")
    supp_wide.columns = ["dx_supp_" + snake(c) for c in supp_wide.columns]
    wide = wide.join(supp_wide)
    return wide.reset_index()


# ------------------------------------------------------------------------------
# 4. Build the facility-level analytical table
# ------------------------------------------------------------------------------
def run_quality_pipeline() -> None:
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    audit = Audit()

    print("1/4 Cleaning PAC utilization ...")
    pac = clean_pac(audit)
    print("2/4 Linking Provider Data IDs to PAC IDs, cleaning quality measures ...")
    gen_raw = read_raw("general")
    gen_ids = to_ccn(gen_raw[find_col(gen_raw, r"ccn", r"certification_number")])
    xwalk = build_ccn_crosswalk(gen_ids, set(pac["ccn"]), audit)
    qrp = clean_qrp(audit, xwalk)
    print("3/4 Cleaning general information & conditions ...")
    gen = clean_general(audit, xwalk)
    cond = clean_conditions(audit, xwalk)

    print("4/4 Joining on CCN and logging every join loss ...")
    # Referential integrity: previously missing in the synthetic project, now explicit
    audit.add("DQ-30", "join", "PAC providers with no match in General Information",
              (~pac["ccn"].isin(gen["ccn"])).sum(), "Kept (left join); ownership/region = NaN")
    audit.add("DQ-31", "join", "PAC providers with no QRP measures (e.g., not required / too small)",
              (~pac["ccn"].isin(qrp["ccn"])).sum(), "Kept; outcome columns = NaN")
    audit.add("DQ-32", "join", "QRP facilities absent from PAC file (non-FFS-Medicare volume, new, or suppressed)",
              (~qrp["ccn"].isin(pac["ccn"])).sum(), "Not in analytical cohort")

    cohort = (pac.merge(gen, on="ccn", how="left")
                 .merge(qrp, on="ccn", how="left")
                 .merge(cond, on="ccn", how="left"))

    # 3-tier operational review flag, now RISK-ADJUSTED by peer group instead of a fixed LOS cutoff
    peer = cohort.groupby("ownership_type")["alos_days"]
    cohort["alos_vs_peer_median"] = (cohort["alos_days"] / peer.transform("median")).round(3)
    low_dtc = cohort["i_019_02_dtc_rs_rate"] < cohort["i_019_02_dtc_rs_rate"].quantile(0.25)
    cohort["review_flag"] = np.select(
        [cohort["i_019_02_dtc_rs_rate"].isna() | cohort["alos_days"].isna(),
         (cohort["alos_vs_peer_median"] >= 1.2) & low_dtc],
        ["Incomplete Data", "Priority for Review"],
        default="Standard",
    )
    audit.add("DQ-40", "cohort", "Facilities flagged 'Incomplete Data' (missing DTC or ALOS)",
              (cohort["review_flag"] == "Incomplete Data").sum(), "Retained; excluded from outcome comparisons")

    audit_df = audit.frame()
    audit_df.to_csv(f"{PROCESSED_DIR}/audit_log.csv", index=False)
    cohort.to_csv(f"{PROCESSED_DIR}/clean_facility_cohort.csv", index=False)

    print(f"\nDone. Facilities: {len(cohort):,}  Columns: {cohort.shape[1]}")
    print(audit_df[["rule_id", "table", "records_affected", "action_taken"]].to_string(index=False))
    print("\n--- Review flag ---")
    print(cohort["review_flag"].value_counts().to_string())


if __name__ == "__main__":
    run_quality_pipeline()
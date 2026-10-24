"""
02_build_database.py
Builds the SQLite analytical database  data/processed/irf_analytics.db  from the outputs of
01_data_quality_cms.py  (run from the project root).

Tables
    facility            curated, typed analysis table: PRIMARY KEY (ccn) + CHECK constraints
    facility_wide       every column of clean_facility_cohort.csv (no constraints)
    qrp_long            cleaned quality measures, one row per facility x measure (with footnote)
    ccn_crosswalk       Provider Data ID -> PAC ID mapping and how each ID was resolved
    pac_reference       national / state summary rows from the PAC file (benchmarks)
    measure_periods     reporting window of each key quality measure
    audit_log           every data-quality rule, count and action

The DDL is also written to sql/schema.sql so it is visible on GitHub.
"""
import os
import sqlite3

import numpy as np
import pandas as pd

PROCESSED_DIR = "data/processed"
SQL_DIR = "sql"
DB_PATH = f"{PROCESSED_DIR}/irf_analytics.db"

PCT = "{c} IS NULL OR {c} BETWEEN 0 AND 100"
NONNEG = "{c} IS NULL OR {c} >= 0"
SHARE = "{c} IS NULL OR {c} BETWEEN 0 AND 1"

# (column, SQLite type, CHECK template or None). Columns missing from the CSV are skipped with a warning.
FACILITY_SPEC = [
    ("ccn_source", "TEXT", None),
    ("is_unit_ccn", "INTEGER", "{c} IS NULL OR {c} IN (0, 1)"),
    ("prvdr_name", "TEXT", None),
    ("prvdr_city", "TEXT", None),
    ("state", "TEXT", None),
    ("cms_region", "TEXT", None),
    ("ownership_type", "TEXT", None),
    # utilization (PAC, FY2024, Original Medicare)
    ("bene_dstnct_cnt", "INTEGER", NONNEG),
    ("tot_epsd_stay_cnt", "INTEGER", NONNEG),
    ("tot_srvc_days", "INTEGER", NONNEG),
    ("tot_mdcr_pymt_amt", "REAL", NONNEG),
    ("alos_days", "REAL", "{c} IS NULL OR {c} >= 1"),
    ("medicare_pay_per_stay", "REAL", None),
    ("stays_per_beneficiary", "REAL", None),
    ("pt_min_per_day", "REAL", NONNEG),
    ("ot_min_per_day", "REAL", NONNEG),
    ("slp_min_per_day", "REAL", NONNEG),
    ("therapy_min_per_day", "REAL", NONNEG),
    ("group_therapy_share", "REAL", SHARE),
    ("individual_min_per_day", "REAL", NONNEG),
    ("group_conc_min_per_day", "REAL", NONNEG),
    ("cotreat_min_per_day", "REAL", NONNEG),
    ("individual_share", "REAL", SHARE),
    ("cotreat_share", "REAL", SHARE),
    ("pt_share", "REAL", SHARE),
    ("ot_share", "REAL", SHARE),
    ("slp_share", "REAL", SHARE),
    ("std_pay_per_stay", "REAL", NONNEG),
    ("tot_mdcr_stdzd_pymt_amt", "REAL", NONNEG),
    ("outlier_payment_share", "REAL", None),
    # case mix
    ("bene_avg_age", "REAL", None),
    ("bene_avg_risk_scre", "REAL", NONNEG),
    ("bene_dual_pct", "REAL", PCT),
    ("bene_rrl_pct", "REAL", PCT),
    ("dual_suppressed", "INTEGER", "{c} IS NULL OR {c} IN (0, 1)"),
    ("rural_suppressed", "INTEGER", "{c} IS NULL OR {c} IN (0, 1)"),
    ("bene_male_pct", "REAL", PCT),
    ("bene_feml_pct", "REAL", PCT),
    ("bene_race_wht_pct", "REAL", PCT),
    ("bene_race_black_pct", "REAL", PCT),
    ("bene_race_hspnc_pct", "REAL", PCT),
    ("bene_race_api_pct", "REAL", PCT),
    ("dx_n_total", "REAL", NONNEG),
    ("dx_n_suppressed_cells", "REAL", NONNEG),
    # (dx_pct_<impairment group> columns are added dynamically in build_database)
    # quality outcomes (Provider Data Catalog; windows differ by measure - see measure_periods)
    ("i_019_02_dtc_rs_rate", "REAL", PCT),
    ("i_019_02_dtc_obs_rate", "REAL", PCT),
    ("i_019_02_dtc_volume", "REAL", NONNEG),
    ("i_017_01_ppr_pd_rsrr", "REAL", PCT),
    ("i_017_01_ppr_pd_rsrr_2_5", "REAL", PCT),
    ("i_017_01_ppr_pd_rsrr_97_5", "REAL", PCT),
    ("i_017_01_ppr_pd_obs_rate", "REAL", PCT),
    ("i_017_01_ppr_pd_volume", "REAL", NONNEG),
    ("i_011_05_obs_rate", "REAL", PCT),
    ("i_012_06_obs_rate", "REAL", PCT),
    ("i_026_01_obs_rate", "REAL", PCT),
    ("i_020_01_mspb_score", "REAL", NONNEG),
    ("i_019_02_dtc_rs_rate_fn", "TEXT", None),
    ("i_017_01_ppr_pd_rsrr_fn", "TEXT", None),
    # derived governance fields
    ("alos_vs_peer_median", "REAL", NONNEG),
    ("review_flag", "TEXT", "{c} IN ('Standard', 'Incomplete Data', 'Priority for Review')"),
    ("dq_flag_pct_range", "INTEGER", "{c} IS NULL OR {c} IN (0, 1)"),
    ("dq_flag_minutes", "INTEGER", "{c} IS NULL OR {c} IN (0, 1)"),
    ("dq_flag_payment", "INTEGER", "{c} IS NULL OR {c} IN (0, 1)"),
]
FLAG_COLS = ["is_unit_ccn", "dual_suppressed", "rural_suppressed", "dq_flag_pct_range", "dq_flag_minutes", "dq_flag_payment"]
BOOL_MAP = {True: 1, False: 0, "True": 1, "False": 0, "true": 1, "false": 0}

INDEXES = [
    ("idx_fac_state", "facility", "state"),
    ("idx_fac_owner", "facility", "ownership_type"),
    ("idx_fac_flag", "facility", "review_flag"),
]


def build_ddl(present: set, spec: list) -> str:
    lines = ["ccn TEXT PRIMARY KEY NOT NULL CHECK (length(ccn) = 6)"]
    for col, typ, chk in spec:
        if col not in present:
            print(f"   [warn] column '{col}' not found in clean_facility_cohort.csv - skipped in facility table")
            continue
        line = f"{col} {typ}"
        if chk:
            line += f" CHECK ({chk.format(c=col)})"
        lines.append(line)
    return "CREATE TABLE facility (\n  " + ",\n  ".join(lines) + "\n);"


def load_csv(name: str, **kw) -> pd.DataFrame:
    path = f"{PROCESSED_DIR}/{name}"
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found. Run 01_data_quality_cms.py first.")
    return pd.read_csv(path, **kw)


def build_database() -> None:
    os.makedirs(SQL_DIR, exist_ok=True)
    id_types = {"ccn": str, "ccn_source": str, "source_id": str}

    print("1/4 Reading cleaned outputs ...")
    wide = load_csv("clean_facility_cohort.csv", dtype=id_types)
    wide = wide.replace([np.inf, -np.inf], np.nan)          # a division by zero must never reach SQL
    for c in FLAG_COLS:
        if c in wide.columns:
            wide[c] = wide[c].map(BOOL_MAP)

    print("2/4 Creating typed facility table with constraints ...")
    dx_cols = sorted(c for c in wide.columns if c.startswith("dx_pct_"))        # one share per impairment group
    spec = FACILITY_SPEC + [(c, "REAL", PCT) for c in dx_cols]
    print(f"   impairment-group share columns found: {len(dx_cols)}")
    ddl = build_ddl(set(wide.columns), spec)
    cols = ["ccn"] + [c for c, _, _ in spec if c in wide.columns]
    index_sql = [f"CREATE INDEX IF NOT EXISTS {n} ON {t}({c});" for n, t, c in INDEXES if c in wide.columns]
    with open(f"{SQL_DIR}/schema.sql", "w", encoding="utf-8") as f:
        f.write("-- Generated by 02_build_database.py. Do not edit by hand.\n\n")
        f.write(ddl + "\n\n" + "\n".join(index_sql) + "\n")

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)                                   # rebuild from scratch, never append to stale data
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(ddl)
        try:
            wide[cols].to_sql("facility", conn, if_exists="append", index=False)
        except (sqlite3.IntegrityError, pd.errors.DatabaseError) as e:   # pandas wraps the sqlite error
            cause = e.__cause__ or e
            conn.close()
            os.remove(DB_PATH)                               # never leave an empty/half-built database behind
            raise SystemExit(f"\nA constraint in sql/schema.sql rejected the data: {cause}\n"
                             "That is the schema doing its job - paste this message to Claude to decide whether the "
                             "data or the rule is wrong.")

        print("3/4 Loading supporting tables ...")
        wide.to_sql("facility_wide", conn, if_exists="replace", index=False)
        load_csv("irf_qrp_long_clean.csv", dtype=id_types).to_sql("qrp_long", conn, if_exists="replace", index=False)
        load_csv("ccn_crosswalk.csv", dtype=id_types).to_sql("ccn_crosswalk", conn, if_exists="replace", index=False)
        load_csv("pac_national_state_reference.csv", dtype={"prvdr_id": str}).to_sql(
            "pac_reference", conn, if_exists="replace", index=False)
        load_csv("irf_measure_periods.csv").to_sql("measure_periods", conn, if_exists="replace", index=False)
        load_csv("audit_log.csv").to_sql("audit_log", conn, if_exists="replace", index=False)

        print("4/4 Creating indexes ...")
        for stmt in index_sql + ["CREATE INDEX IF NOT EXISTS idx_qrp_ccn_measure ON qrp_long(ccn, measure_code);"]:
            conn.execute(stmt)
        conn.commit()

        print("\nDatabase build complete:", DB_PATH)
        for t in ["facility", "facility_wide", "qrp_long", "ccn_crosswalk", "pac_reference", "measure_periods", "audit_log"]:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:16s} {n:>8,} rows")
    finally:
        conn.close()


if __name__ == "__main__":
    build_database()
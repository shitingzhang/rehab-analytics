"""
02_build_database.py
Builds the SQLite analytical database  data/processed/irf_analytics.db  from the outputs of
01_data_quality_cms.py  (run from the project root).

Tables
    facility            curated, typed analysis table: PRIMARY KEY (ccn) + CHECK constraints
    facility_wide       every column of clean_facility_cohort.csv (no constraints)
    qrp_long            cleaned quality measures, one row per facility x measure (with footnote)
    ccn_crosswalk       Provider Data ID -> PAC ID mapping and how each ID was resolved
    pac_reference       typed national / state benchmarks; numeric source tokens retained in *_raw
    measure_periods     reporting window of each key quality measure
    audit_log           every data-quality rule, count and action

The DDL is also written to sql/schema.sql so it is visible on GitHub.
A complete, checked temporary database replaces the previous database only on success.
Run without concurrent database writers; active journal/WAL files stop publication.
"""
import os
import sqlite3
import tempfile
from pathlib import Path

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
FOOTNOTE_COLS = [name for name, _, _ in FACILITY_SPEC if name.endswith("_fn")]


def parse_boolean_token(value):
    """Reject invalid flag text before pandas can silently treat it as missing."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return np.nan
    token = str(value).strip().lower()
    if token == "":
        return np.nan
    if token in {"true", "1", "1.0"}:
        return 1
    if token in {"false", "0", "0.0"}:
        return 0
    raise ValueError(f"Invalid boolean token: {value!r}")

INDEXES = [
    ("idx_fac_state", "facility", "state"),
    ("idx_fac_owner", "facility", "ownership_type"),
    ("idx_fac_flag", "facility", "review_flag"),
]


# Explicit PAC reference contract: never infer numeric types from observed markers.
# Raw companions preserve every numeric source token, including missing markers.
REFERENCE_TYPES = {
    'year': 'INTEGER',
    'year_type': 'TEXT',
    'smry_ctgry': 'TEXT',
    'srvc_ctgry': 'TEXT',
    'prvdr_id': 'TEXT',
    'prvdr_name': 'TEXT',
    'prvdr_city': 'TEXT',
    'state': 'TEXT',
    'prvdr_zip': 'TEXT',
    'bene_dstnct_cnt': 'REAL',
    'tot_epsd_stay_cnt': 'REAL',
    'tot_srvc_days': 'REAL',
    'tot_chrg_amt': 'REAL',
    'tot_alowd_amt': 'REAL',
    'tot_mdcr_pymt_amt': 'REAL',
    'tot_mdcr_stdzd_pymt_amt': 'REAL',
    'tot_outlier_pymt_amt': 'REAL',
    'bene_dual_pct': 'REAL',
    'bene_rrl_pct': 'REAL',
    'bene_avg_age': 'REAL',
    'bene_male_pct': 'REAL',
    'bene_feml_pct': 'REAL',
    'bene_race_wht_pct': 'REAL',
    'bene_race_black_pct': 'REAL',
    'bene_race_api_pct': 'REAL',
    'bene_race_hspnc_pct': 'REAL',
    'bene_race_natind_pct': 'REAL',
    'bene_race_unk_pct': 'REAL',
    'bene_race_othr_pct': 'REAL',
    'bene_avg_risk_scre': 'REAL',
    'bene_cc_bh_adhd_othcd_v1_pct': 'REAL',
    'bene_cc_bh_alcohol_drug_v1_pct': 'REAL',
    'bene_cc_bh_alz_nonalzdem_v2_pct': 'REAL',
    'bene_cc_bh_anxiety_v1_pct': 'REAL',
    'bene_cc_bh_bipolar_v1_pct': 'REAL',
    'bene_cc_bh_depress_v1_pct': 'REAL',
    'bene_cc_bh_mood_v2_pct': 'REAL',
    'bene_cc_bh_pd_v1_pct': 'REAL',
    'bene_cc_bh_ptsd_v1_pct': 'REAL',
    'bene_cc_bh_schizo_othpsy_v1_pct': 'REAL',
    'bene_cc_bh_tobacco_v1_pct': 'REAL',
    'bene_cc_ph_afib_v2_pct': 'REAL',
    'bene_cc_ph_arthritis_v2_pct': 'REAL',
    'bene_cc_ph_asthma_v2_pct': 'REAL',
    'bene_cc_ph_cancer6_v2_pct': 'REAL',
    'bene_cc_ph_ckd_v2_pct': 'REAL',
    'bene_cc_ph_copd_v2_pct': 'REAL',
    'bene_cc_ph_diabetes_v2_pct': 'REAL',
    'bene_cc_ph_hf_nonihd_v2_pct': 'REAL',
    'bene_cc_ph_hyperlipidemia_v2_pct': 'REAL',
    'bene_cc_ph_hypertension_v2_pct': 'REAL',
    'bene_cc_ph_ischemicheart_v2_pct': 'REAL',
    'bene_cc_ph_osteoporosis_v2_pct': 'REAL',
    'bene_cc_ph_parkinson_v2_pct': 'REAL',
    'bene_cc_ph_stroke_tia_v2_pct': 'REAL',
    'prmry_dx_infctn_pct': 'REAL',
    'prmry_dx_neobld_pct': 'REAL',
    'prmry_dx_endonutrmet_pct': 'REAL',
    'prmry_dx_mntbehneudis_pct': 'REAL',
    'prmry_dx_nervsystm_pct': 'REAL',
    'prmry_dx_entsys_pct': 'REAL',
    'prmry_dx_circsystm_pct': 'REAL',
    'prmry_dx_rspsystm_pct': 'REAL',
    'prmry_dx_digsystm_pct': 'REAL',
    'prmry_dx_sknmussystm_pct': 'REAL',
    'prmry_dx_gusystm_pct': 'REAL',
    'prmry_dx_prgpericong_pct': 'REAL',
    'prmry_dx_sxilldef_pct': 'REAL',
    'prmry_dx_injpois_pct': 'REAL',
    'prmry_dx_hlthsrv_pct': 'REAL',
    'tot_pt_mnts': 'REAL',
    'indvdl_pt_mnts': 'REAL',
    'cncrnt_grp_pt_mnts': 'REAL',
    'cotrt_pt_mnts': 'REAL',
    'tot_ot_mnts': 'REAL',
    'indvdl_ot_mnts': 'REAL',
    'cncrnt_grp_ot_mnts': 'REAL',
    'cotrt_ot_mnts': 'REAL',
    'tot_slp_mnts': 'REAL',
    'indvdl_slp_mnts': 'REAL',
    'cncrnt_grp_slp_mnts': 'REAL',
    'cotrt_slp_mnts': 'REAL',
}


def prepare_reference(frame):
    """Parse benchmark numbers without treating suppression as zero or imputing it."""
    unknown = set(frame.columns) - set(REFERENCE_TYPES)
    if unknown:
        raise ValueError(f"Unknown PAC reference columns: {sorted(unknown)}")
    out = frame.copy()
    sql_types = {c: REFERENCE_TYPES[c] for c in frame.columns}
    for col in frame.columns:
        if REFERENCE_TYPES[col] == "TEXT":
            continue
        raw = frame[col].astype("string").fillna("")
        token = raw.str.strip()
        missing = token.isin(["", "*", "+"])
        numeric = pd.to_numeric(token.mask(missing), errors="coerce").astype("float64")
        invalid = ~missing & (~np.isfinite(numeric))
        if invalid.any():
            raise ValueError(f"Invalid numeric token in PAC reference {col}: {raw[invalid].unique().tolist()}")
        if REFERENCE_TYPES[col] == "INTEGER":
            if (numeric.dropna() % 1 != 0).any():
                raise ValueError(f"Non-integer PAC reference {col}")
            numeric = numeric.astype("Int64")
        out[col] = numeric
        out[col + "_raw"] = raw
        sql_types[col + "_raw"] = "TEXT"
    return out, sql_types


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


def validate_database(conn, expected_rows, expected_indexes):
    """Reject an incomplete build before its file can become the active database."""
    integrity = conn.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != set(expected_rows):
        raise RuntimeError(f"Unexpected database tables: {sorted(tables)}")
    for table, expected in expected_rows.items():
        actual = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if actual != expected:
            raise RuntimeError(f"Row count mismatch in {table}: {actual} != {expected}")
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    if not set(expected_indexes) <= indexes:
        raise RuntimeError("Required database indexes are missing")


def require_no_journal(target):
    """Fail conservatively if a database may have another writer or need recovery."""
    if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise RuntimeError("Database journal/WAL files exist; close writers and inspect the database before rebuilding.")


def publish_database(staged_db, schema_text, target, schema_path):
    """Publish the database with one same-filesystem replace after connections close.

    Restore the old schema file if the database replace raises. This is exception
    recovery, not a cross-file transaction or a guarantee against power loss.
    """
    require_no_journal(target)
    with tempfile.TemporaryDirectory(dir=schema_path.parent, prefix=".schema-build-") as tmp:
        staged_schema = Path(tmp) / "schema.sql"
        staged_schema.write_text(schema_text, encoding="utf-8")
        old_schema = schema_path.read_bytes() if schema_path.exists() else None
        changed = old_schema != staged_schema.read_bytes()
        backup = Path(tmp) / "previous.sql"
        if changed and old_schema is not None:
            backup.write_bytes(old_schema)
        if changed:
            os.replace(staged_schema, schema_path)
        try:
            os.replace(staged_db, target)
        except BaseException:
            if changed:
                if old_schema is None:
                    schema_path.unlink()
                else:
                    os.replace(backup, schema_path)
            raise


def build_database(before_publish=None) -> None:
    target = Path(DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    schema_path = Path(SQL_DIR) / "schema.sql"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    require_no_journal(target)
    id_types = {"ccn": str, "ccn_source": str, "source_id": str}

    print("1/4 Reading cleaned outputs ...")
    wide = load_csv("clean_facility_cohort.csv",
                    dtype={**id_types, **{c: str for c in FOOTNOTE_COLS}},
                    converters={c: parse_boolean_token for c in FLAG_COLS})
    wide = wide.replace([np.inf, -np.inf], np.nan)

    print("2/4 Creating typed facility table with constraints ...")
    dx_cols = sorted(c for c in wide.columns if c.startswith("dx_pct_"))
    spec = FACILITY_SPEC + [(c, "REAL", PCT) for c in dx_cols]
    print(f"   impairment-group share columns found: {len(dx_cols)}")
    ddl = build_ddl(set(wide.columns), spec)
    cols = ["ccn"] + [c for c, _, _ in spec if c in wide.columns]
    indexes = [(n, t, c) for n, t, c in INDEXES if c in wide.columns]
    index_sql = [f"CREATE INDEX IF NOT EXISTS {n} ON {t}({c});" for n, t, c in indexes]
    schema_text = "-- Generated by 02_build_database.py. Do not edit by hand.\n\n" + ddl + "\n\n" + "\n".join(index_sql) + "\n"
    expected_rows = {"facility": len(wide), "facility_wide": len(wide)}

    # Same parent filesystem is required for a single-file atomic replacement.
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".db-build-") as tmp:
        staged_db = Path(tmp) / "database.sqlite"
        conn = sqlite3.connect(staged_db)
        try:
            conn.execute(ddl)
            try:
                wide[cols].to_sql("facility", conn, if_exists="append", index=False)
            except (sqlite3.IntegrityError, pd.errors.DatabaseError) as exc:
                raise RuntimeError(f"Facility constraints rejected the data: {exc}") from exc

            print("3/4 Loading supporting tables ...")
            wide.to_sql("facility_wide", conn, if_exists="replace", index=False)
            inputs = [
                ("qrp_long", "irf_qrp_long_clean.csv", id_types),
                ("ccn_crosswalk", "ccn_crosswalk.csv", id_types),
                ("pac_reference", "pac_national_state_reference.csv", {"prvdr_id": str}),
                ("measure_periods", "irf_measure_periods.csv", {}),
                ("audit_log", "audit_log.csv", {}),
            ]
            for table, filename, types in inputs:
                if table == "pac_reference":
                    frame, reference_types = prepare_reference(
                        load_csv(filename, dtype=str, keep_default_na=False))
                    frame.to_sql(table, conn, if_exists="replace", index=False, dtype=reference_types)
                else:
                    frame = load_csv(filename, dtype=types) if types else load_csv(filename)
                    frame.to_sql(table, conn, if_exists="replace", index=False)
                expected_rows[table] = len(frame)

            print("4/4 Creating indexes and validating the complete database ...")
            for stmt in index_sql + ["CREATE INDEX IF NOT EXISTS idx_qrp_ccn_measure ON qrp_long(ccn, measure_code);"]:
                conn.execute(stmt)
            conn.commit()
            validate_database(conn, expected_rows, [n for n, _, _ in indexes] + ["idx_qrp_ccn_measure"])
        finally:
            conn.close()
        if before_publish is not None:
            before_publish()
        publish_database(staged_db, schema_text, target, schema_path)

    print("\nDatabase build complete:", DB_PATH)
    for table, count in expected_rows.items():
        print(f"  {table:16s} {count:>8,} rows")


if __name__ == "__main__":
    from _infra.input_provenance import recorded_stage
    with recorded_stage("database") as check_sources:
        build_database(before_publish=check_sources)

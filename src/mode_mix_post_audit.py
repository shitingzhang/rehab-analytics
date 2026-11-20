"""Construct therapy shares from reported minutes and impairment shares from CMS counts."""

from pathlib import Path

import numpy as np
import pandas as pd

import irf_common as legacy
from strict_regression import fit as strict_fit

EXPOSURES = ["group10", "cotreat5", "pt5", "slp5"]
ADJUST = [
    "for_profit",
    "government",
    "physician",
    "unit",
    "risk",
    "age",
    "dual10",
    "rural10",
    "dual_suppressed",
    "rural_suppressed",
    "log_stays",
]
OUTCOMES = {
    "DTC": "i_019_02_dtc_rs_rate",
    "RSRR": "i_017_01_ppr_pd_rsrr",
    "PAY": "std_pay_k",
}


def snake(value):
    import re

    return re.sub(r"[^0-9a-z]+", "_", str(value).strip().lower()).strip("_")


def condition_tables(raw, expected_groups, fill=5):
    r = raw.copy()
    r["ccn"] = r["CMS Certification Number (CCN)"].astype("string").str.strip()
    if not r.ccn.str.fullmatch(r"[0-9A-Z]{6}").fillna(False).all():
        raise ValueError("Invalid condition CCN")
    r["group"] = r.Condition.map(snake)
    if set(r["group"]) - set(expected_groups):
        raise ValueError("Unexpected impairment group")
    if r.duplicated(["ccn", "group"]).any():
        raise ValueError("Duplicate condition key")
    token = r.Count.astype("string").str.strip().str.upper()
    number = pd.to_numeric(token, errors="coerce")
    footnote = r.Footnote.astype("string").str.strip()
    small = token.eq("LESS THAN 11") & footnote.eq("1")
    unknown = token.eq("NOT AVAILABLE") & footnote.eq("2")
    numeric = number.notna() & footnote.eq("-")
    if not (small | unknown | numeric).all():
        raise ValueError("Unknown count token or contradictory footnote")
    if (
        not np.isfinite(number.dropna()).all()
        or ((number.dropna() < 0) | (number.dropna() % 1 != 0)).any()
    ):
        raise ValueError("Counts must be finite nonnegative integers")
    r["filled_count"] = number.mask(small, fill)
    r["small"] = small.astype(int)
    r["unknown"] = unknown.astype(int)
    counts = r.pivot(index="ccn", columns="group", values="filled_count").reindex(
        columns=expected_groups
    )
    flags = r.pivot(index="ccn", columns="group", values="small").reindex(
        columns=expected_groups
    )
    strict_total = counts.sum(axis=1, min_count=len(expected_groups)).where(
        lambda s: s > 0
    )
    pct = 100 * counts.div(strict_total, axis=0)
    merged = counts.copy()
    if all(g in merged for g in legacy.SPINAL):
        merged["spinal_cord_any"] = merged[legacy.SPINAL].sum(axis=1, min_count=2)
        merged = merged.drop(columns=legacy.SPINAL)
    denominator = merged.sum(axis=1, min_count=merged.shape[1]).where(lambda s: s > 0)
    model_pct = 100 * merged.div(denominator, axis=0)
    return {
        "counts": counts,
        "flags": flags,
        "pct": pct,
        "model_pct": model_pct,
        "total": strict_total,
        "small_cells": r.groupby("ccn").small.sum(),
        "unavailable_cells": r.groupby("ccn").unknown.sum(),
        "audit": {
            "condition_rows": len(r),
            "true_suppressed_cells": int(small.sum()),
            "unavailable_cells": int(unknown.sum()),
        },
    }


def minute_exposures(raw):
    p = raw.loc[raw.SMRY_CTGRY.eq("PROVIDER")].copy()
    p["PRVDR_ID"] = p.PRVDR_ID.astype("string").str.strip()
    if not p.PRVDR_ID.is_unique:
        raise ValueError("Duplicate provider minute key")
    p = p.set_index("PRVDR_ID")
    cols = [
        f"{prefix}_{d}_MNTS"
        for prefix in ["TOT", "INDVDL", "CNCRNT_GRP", "COTRT"]
        for d in ["PT", "OT", "SLP"]
    ]
    tokens = p[cols].apply(lambda s: s.astype("string").str.strip())
    x = tokens.apply(pd.to_numeric, errors="coerce")
    if ((x.isna()) & tokens.ne("*")).any(axis=None):
        raise ValueError("Unexpected missing minute token")
    if x.lt(0).any(axis=None) or np.isinf(x.to_numpy(float)).any():
        raise ValueError("Invalid therapy minutes")
    for discipline in ["PT", "OT", "SLP"]:
        needed = [
            f"{prefix}_{discipline}_MNTS"
            for prefix in ["TOT", "INDVDL", "CNCRNT_GRP", "COTRT"]
        ]
        complete = x[needed].notna().all(axis=1)
        if (
            not x.loc[complete, needed[0]]
            .eq(x.loc[complete, needed[1:]].sum(axis=1))
            .all()
        ):
            raise ValueError("Minute components do not sum to total")
    total = (
        x[[f"TOT_{g}_MNTS" for g in ["PT", "OT", "SLP"]]]
        .sum(axis=1, min_count=3)
        .where(lambda s: s > 0)
    )
    output = pd.DataFrame(index=p.index)
    for name, prefix, scale in [
        ("group10", "CNCRNT_GRP", 10),
        ("cotreat5", "COTRT", 20),
    ]:
        output[name] = (
            x[[f"{prefix}_{g}_MNTS" for g in ["PT", "OT", "SLP"]]].sum(
                axis=1, min_count=3
            )
            / total
            * scale
        )
    output["pt5"] = x.TOT_PT_MNTS / total * 20
    output["slp5"] = x.TOT_SLP_MNTS / total * 20
    return output


def construct(root=Path(".")):
    d = legacy.prepare(legacy.load_facility())
    if not d.ccn.is_unique:
        raise ValueError("Duplicate facility key")
    groups = [c[len("dx_pct_") :] for c in d if c.startswith("dx_pct_")]
    raw = pd.read_csv(
        root / "data/raw/irf_conditions.csv", dtype=str, keep_default_na=False
    )
    xw = pd.read_csv(
        root / "data/processed/ccn_crosswalk.csv", dtype=str, keep_default_na=False
    )
    xw = xw[xw.ccn.ne("")]
    if not xw.source_id.is_unique or not xw.ccn.is_unique:
        raise ValueError("Ambiguous crosswalk")
    mapping = xw.set_index("source_id").ccn
    key = "CMS Certification Number (CCN)"
    raw[key] = raw[key].map(mapping).fillna(raw[key])
    tables = condition_tables(raw, groups)
    # Rebuild impairment fields from counts and footnotes.
    d = d.drop(
        columns=[
            c for c in d if c.startswith(("dx_n_", "dx_pct_", "dx_supp_", "imp10_"))
        ]
    )
    for g in groups:
        for prefix, source in [
            ("dx_n_", "counts"),
            ("dx_pct_", "pct"),
            ("dx_supp_", "flags"),
        ]:
            d[prefix + g] = tables[source][g].reindex(d.ccn).to_numpy()
    d["dx_n_total"] = tables["total"].reindex(d.ccn).to_numpy()
    d["dx_n_suppressed_cells"] = tables["small_cells"].reindex(d.ccn).to_numpy()
    d["dx_n_unavailable_cells"] = tables["unavailable_cells"].reindex(d.ccn).to_numpy()
    for g in tables["model_pct"]:
        if g != legacy.REF_GROUP:
            d["imp10_" + g] = tables["model_pct"][g].reindex(d.ccn).to_numpy() / 10
    d["condition_record_missing"] = ~d.ccn.isin(tables["counts"].index)
    d["composition_unknown"] = d.dx_n_total.isna()
    pac = pd.read_csv(
        root / "data/raw/pac_irf_by_provider.csv", dtype=str, keep_default_na=False
    )
    exposures = minute_exposures(pac)
    for term in EXPOSURES:
        d[term] = exposures[term].reindex(d.ccn).to_numpy()
    # Label the historical covered-day ratio separately from the therapy shares.
    d = d.rename(columns={"therapy10": "legacy_recorded_minutes_per_covered_day_div10"})
    return d, tables["audit"]


def checked_fit(s, y, terms):
    X, names = legacy.design(s, terms, True)
    if not np.isfinite(X).all() or not np.isfinite(s[y].to_numpy(float)).all():
        raise ValueError("Nonfinite design or outcome")
    if s.state.isna().any() or s.state.nunique() < 2 or len(s) <= X.shape[1]:
        raise ValueError("Insufficient observations or clusters")
    if np.linalg.matrix_rank(X) != X.shape[1]:
        raise ValueError("Rank deficient design")
    return strict_fit(s, y, terms, True), X, names

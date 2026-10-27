"""check_linkage.py - run from the project root after 01_data_quality_cms.py:  python src/check_linkage.py
Facility linkage audit (deviation log, plan v1.3). For every facility linked between the Provider Data General Information
file and the PAC utilization file, compares state, city and ZIP code; name similarity (token Jaccard) is descriptive only.
A link is LOW-CONFIDENCE if the state differs, or if both the city and the ZIP code differ.
Looks at facility identifiers only, never at outcomes.
Outputs: data/processed/linkage_audit/linkage_check.csv (one row per linked facility, no names) and linkage_summary.txt"""
import io
import os
import re

import pandas as pd

OUT_DIR = "data/processed/linkage_audit"
STOP = {"THE", "OF", "AND", "INC", "LLC", "CENTER", "HOSPITAL", "REHABILITATION", "REHAB", "INPATIENT", "UNIT", "MEDICAL",
        "HEALTH", "INSTITUTE", "FACILITY", "SYSTEM", "LP", "CORP", "CO"}
pd.set_option("display.width", 200)
buf = io.StringIO()


def out(*a):
    print(*a)
    print(*a, file=buf)


def ccn(s):
    t = s.astype("string").str.strip().str.upper().str.replace(r"[^0-9A-Z]", "", regex=True)
    return t.str.zfill(6)


def norm(s):
    return s.astype("string").str.upper().str.replace(r"[^A-Z0-9 ]", " ", regex=True).str.split().str.join(" ")


def zip5(s):
    return s.astype("string").str.extract(r"(\d+)")[0].str.zfill(5).str[:5]


def tokens(name):
    return set(t for t in re.split(r"\s+", name or "") if t and t not in STOP)


def jaccard(a, b):
    a, b = tokens(a), tokens(b)
    return len(a & b) / len(a | b) if (a | b) else float("nan")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    gen = pd.read_csv("data/raw/irf_general_information.csv", dtype=str, keep_default_na=False)
    gen = pd.DataFrame({"ccn_source": ccn(gen["CMS Certification Number (CCN)"]), "name_g": norm(gen["Provider Name"]),
                        "city_g": norm(gen["City/Town"]), "state_g": gen["State"].str.strip().str.upper(),
                        "zip_g": zip5(gen["ZIP Code"])})
    pac = pd.read_csv("data/raw/pac_irf_by_provider.csv", dtype=str, keep_default_na=False)
    pac = pac[pac["SMRY_CTGRY"].str.upper() == "PROVIDER"]
    pac = pd.DataFrame({"ccn": ccn(pac["PRVDR_ID"]), "name_p": norm(pac["PRVDR_NAME"]), "city_p": norm(pac["PRVDR_CITY"]),
                        "state_p": pac["STATE"].str.strip().str.upper(), "zip_p": zip5(pac["PRVDR_ZIP"])})
    xw = pd.read_csv("data/processed/ccn_crosswalk.csv", dtype=str)

    exact = gen.merge(pac, left_on="ccn_source", right_on="ccn")
    exact["link"] = "exact CCN"
    cross = gen.merge(xw.dropna(subset=["ccn"]), left_on="ccn_source", right_on="source_id").merge(pac, on="ccn")
    cross["link"] = "crosswalk: " + cross["method"]
    links = pd.concat([exact, cross], ignore_index=True)
    links["state_match"] = links["state_g"] == links["state_p"]
    links["city_match"] = links["city_g"] == links["city_p"]
    links["zip_match"] = links["zip_g"] == links["zip_p"]
    links["name_jaccard"] = [jaccard(a, b) for a, b in zip(links["name_g"], links["name_p"])]
    links["low_confidence"] = ~links["state_match"] | (~links["city_match"] & ~links["zip_match"])

    out(f"PAC provider rows: {len(pac):,}; General Information rows: {len(gen):,}; linked: {len(links):,} "
        f"(each PAC CCN linked at most once: {links['ccn'].is_unique}; each source CCN at most once: "
        f"{links['ccn_source'].is_unique})")
    out(f"PAC providers with no General Information record: {len(set(pac['ccn']) - set(links['ccn'])):,}")
    tab = links.groupby("link").agg(links=("ccn", "size"), state_match=("state_match", "mean"),
                                    city_match=("city_match", "mean"), zip_match=("zip_match", "mean"),
                                    median_name_jaccard=("name_jaccard", "median"),
                                    low_confidence=("low_confidence", "sum"))
    for c in ["state_match", "city_match", "zip_match"]:
        tab[c] = (100 * tab[c]).round(1)
    out("\n=== Agreement of identifiers by link type (match columns in %) ===")
    out(tab.round(2).to_string())
    low = links[links["low_confidence"]]
    out(f"\n=== Low-confidence links: {len(low)} (state differs, or both city and ZIP differ) - listed for manual review ===")
    if len(low):
        out(low[["link", "ccn_source", "ccn", "name_g", "name_p", "city_g", "city_p", "state_g", "state_p", "zip_g",
                 "zip_p"]].to_string(index=False))
    out("\nName similarity is descriptive only: unit names often differ between files (e.g. hospital name vs rehab unit name).")
    links[["ccn", "ccn_source", "link", "state_match", "city_match", "zip_match", "name_jaccard", "low_confidence"]].round(
        3).to_csv(f"{OUT_DIR}/linkage_check.csv", index=False)
    with open(f"{OUT_DIR}/linkage_summary.txt", "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    print(f"\n>>> saved to {OUT_DIR}/")


if __name__ == "__main__":
    main()

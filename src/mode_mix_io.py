"""Read facility-level inputs for the four Model A follow-up analyses."""

from pathlib import Path

import pandas as pd


RESULTS_DIR = Path("data/processed/mode_mix_post_audit")


def load_inputs(results_dir=RESULTS_DIR):
    identifiers = {"ccn": str}
    cohort = pd.read_csv(results_dir / "cohort_private.csv", dtype=identifiers).set_index("ccn")
    members = pd.read_csv(results_dir / "model_membership_private.csv", dtype=identifiers)
    primary = pd.read_csv(results_dir / "primary_12.csv", dtype=identifiers).set_index(["outcome", "term"])
    if cohort.index.hasnans or not cohort.index.is_unique:
        raise ValueError("Each cohort row needs a unique facility identifier")
    if not primary.index.is_unique:
        raise ValueError("Duplicate outcome and term in the primary estimates")
    return cohort, members, primary


def load_outcome_sample(cohort, members, model, outcome):
    ids = members.loc[(members.model == model) & (members.outcome == outcome), "ccn"]
    if ids.empty or not ids.is_unique or not ids.isin(cohort.index).all():
        raise ValueError(f"Invalid facility membership for {model}, {outcome}")
    return cohort.loc[ids]

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import irf_common as legacy
from mode_mix_post_audit import checked_fit, condition_tables, minute_exposures


def fixture(values, notes=None, groups=None):
    groups = groups or ["a", "b"]
    return pd.DataFrame(
        {
            "CMS Certification Number (CCN)": ["000001"] * len(values),
            "Condition": groups,
            "Count": values,
            "Footnote": notes or ["-"] * len(values),
        }
    )


class ConstructionTests(unittest.TestCase):
    def test_true_small_count_only(self):
        t = condition_tables(fixture(["100", "Less than 11"], ["-", "1"]), ["a", "b"])
        self.assertEqual(t["counts"].loc["000001", "b"], 5)
        self.assertAlmostEqual(t["total"].iloc[0], 105)

    def test_unavailable_not_renormalized(self):
        t = condition_tables(fixture(["100", "Not Available"], ["-", "2"]), ["a", "b"])
        self.assertTrue(t["model_pct"].isna().all(axis=None))
        self.assertEqual(t["counts"].iloc[0, 0], 100)
        self.assertEqual(t["audit"]["true_suppressed_cells"], 0)

    def test_all_unavailable_row_retained(self):
        t = condition_tables(fixture(["Not Available"] * 2, ["2"] * 2), ["a", "b"])
        self.assertEqual(len(t["counts"]), 1)
        self.assertTrue(t["pct"].isna().all(axis=None))

    def test_missing_group_not_silently_omitted(self):
        t = condition_tables(fixture(["100"], groups=["a"]), ["a", "b"])
        self.assertTrue(t["model_pct"].isna().all(axis=None))

    def test_unknown_spinal_group_remains_unknown_after_merge(self):
        groups = ["all_other_conditions"] + legacy.SPINAL
        t = condition_tables(
            fixture(["100", "10", "Not Available"], ["-", "-", "2"], groups), groups
        )
        self.assertIn("spinal_cord_any", t["model_pct"])
        self.assertTrue(t["model_pct"].isna().all(axis=None))

    def test_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            condition_tables(fixture(["1", "2"], groups=["a", "a"]), ["a", "b"])

    def test_unknown_token_and_contradictory_note_rejected(self):
        for values, notes in [
            (["100", "Less than 11"], ["-", "2"]),
            (["100", "?"], ["-", "-"]),
            (["100", "5"], ["-", "1"]),
        ]:
            with self.assertRaises(ValueError):
                condition_tables(fixture(values, notes), ["a", "b"])

    def test_negative_fractional_infinite_count_rejected(self):
        for value in ["-1", "1.5", "inf"]:
            with self.assertRaises(ValueError):
                condition_tables(fixture(["100", value]), ["a", "b"])

    def test_zero_denominator_stays_undefined(self):
        t = condition_tables(fixture(["0", "0"]), ["a", "b"])
        self.assertTrue(t["model_pct"].isna().all(axis=None))

    def test_full_precision_small_positive_minutes(self):
        row = {"SMRY_CTGRY": "PROVIDER", "PRVDR_ID": "000001"}
        for g in ["PT", "OT", "SLP"]:
            row.update(
                {
                    f"TOT_{g}_MNTS": "1000",
                    f"INDVDL_{g}_MNTS": "1000",
                    f"CNCRNT_GRP_{g}_MNTS": "0",
                    f"COTRT_{g}_MNTS": "0",
                }
            )
        row["INDVDL_PT_MNTS"] = "999"
        row["CNCRNT_GRP_PT_MNTS"] = "1"
        t = minute_exposures(pd.DataFrame([row]))
        self.assertAlmostEqual(t.group10.iloc[0], 10 / 3000)
        self.assertGreater(t.group10.iloc[0], 0)
        self.assertEqual(round((t.group10.iloc[0] / 10), 3), 0)

    def test_invalid_minutes_not_silently_coerced(self):
        row = {"SMRY_CTGRY": "PROVIDER", "PRVDR_ID": "000001"}
        for g in ["PT", "OT", "SLP"]:
            for prefix in ["TOT", "INDVDL", "CNCRNT_GRP", "COTRT"]:
                row[f"{prefix}_{g}_MNTS"] = "*"
        row["TOT_PT_MNTS"] = "?"
        with self.assertRaises(ValueError):
            minute_exposures(pd.DataFrame([row]))

    def test_model_recovers_synthetic_truth(self):
        rng = np.random.default_rng(2026)
        s = pd.DataFrame(
            {
                "state": np.repeat(np.arange(20).astype(str), 10),
                "a": rng.normal(size=200),
                "b": rng.normal(size=200),
            }
        )
        s["y"] = 1 + 0.4 * s.a - 0.3 * s.b
        (tab, _, _), _, _ = checked_fit(s, "y", ["a", "b"])
        np.testing.assert_allclose(tab.loc[["a", "b"], "coef"], [0.4, -0.3], atol=1e-10)

    def test_nonfinite_or_rank_deficient_design_rejected(self):
        s = pd.DataFrame(
            {
                "state": ["A"] * 10 + ["B"] * 10,
                "a": np.arange(20, dtype=float),
                "y": np.arange(20, dtype=float),
            }
        )
        s.loc[0, "a"] = np.inf
        with self.assertRaises(ValueError):
            checked_fit(s, "y", ["a"])
        s["a"] = 0.0
        with self.assertRaises(ValueError):
            checked_fit(s, "y", ["a"])


if __name__ == "__main__":
    unittest.main()

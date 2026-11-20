import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mode_mix_post_audit import EXPOSURES, OUTCOMES

P = Path("data/processed/mode_mix_post_audit")


class OutputTests(unittest.TestCase):
    def test_all_estimates_disclosed_once(self):
        t = pd.read_csv(P / "all_36_estimates.csv")
        self.assertEqual(len(t), 36)
        self.assertFalse(t.duplicated(["model", "outcome", "term"]).any())
        for _, g in t.groupby(["model", "outcome"]):
            self.assertEqual(set(g.term), set(EXPOSURES))
        self.assertTrue((t.ci_low <= t.coef).all() and (t.coef <= t.ci_high).all())

    def test_bh_family_is_only_primary_twelve(self):
        t = pd.read_csv(P / "all_36_estimates.csv")
        mask = t.model.eq("A_complete_composition")
        self.assertEqual(int(mask.sum()), 12)
        self.assertTrue(t.loc[~mask, "q_bh_primary_12"].isna().all())
        expected = multipletests(t.loc[mask, "p"], method="fdr_bh")[1]
        np.testing.assert_allclose(t.loc[mask, "q_bh_primary_12"], expected, rtol=1e-12)
        self.assertTrue((t.loc[mask, "q_bh_primary_12"] > 0).all())

    def test_same_sample_and_subset_rules(self):
        t = pd.read_csv(P / "model_membership_private.csv", dtype={"ccn": str})
        for y in OUTCOMES:
            ids = lambda m: set(t.loc[t.outcome.eq(y) & t.model.eq(m), "ccn"])
            self.assertEqual(
                ids("A_complete_composition"), ids("C_no_composition_same_sample")
            )
            self.assertTrue(
                ids("A_complete_composition") <= ids("B_no_composition_all")
            )

    def test_unknown_composition_never_becomes_known(self):
        d = pd.read_csv(P / "cohort_private.csv", dtype={"ccn": str})
        terms = [c for c in d if c.startswith("imp10_")]
        self.assertTrue(d.loc[d.composition_unknown, terms].isna().all(axis=None))
        self.assertTrue(d.ccn.is_unique)
        self.assertNotIn("therapy10", d.columns)

    def test_no_legacy_dose_in_new_model(self):
        t = pd.read_csv(P / "all_model_terms.csv")
        self.assertNotIn("therapy10", set(t.term))
        self.assertFalse(t.term.str.startswith("legacy_recorded_minutes").any())

    def test_reference_checks_all_nine_models(self):
        t = pd.read_csv(P / "statsmodels_checks.csv")
        self.assertEqual(len(t), 9)
        self.assertTrue(t[["coefficient_error", "se_error"]].lt(1e-6).all(axis=None))


if __name__ == "__main__":
    unittest.main()

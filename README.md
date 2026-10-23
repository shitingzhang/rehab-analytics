# Therapy Dose, Mode & Mix in U.S. Inpatient Rehabilitation Facilities

![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-database-003B57?logo=sqlite&logoColor=white)
![Data](https://img.shields.io/badge/data-CMS%20public%20files-2E7D32)
![Analysis](https://img.shields.io/badge/analysis-pre--specified%20plan-6A1B9A)

> **In U.S. inpatient rehabilitation facilities (IRFs), giving more therapy minutes is not linked to more patients
> going home or fewer readmissions, but it is linked to lower Medicare payment per stay; how therapy is delivered, not
> just how much, shows signals worth testing.**
>
> A study of **1,165 IRFs** using public CMS data, run under a pre-specified analysis plan with every later change
> logged. The questions are listed under [Research questions](#research-questions).

**New to the topic?** Start with the one-page [plain-language summary](docs/summary.md).

## Key findings

1. **More is not better for outcomes.** No association was found between daily therapy dose and **discharge to
   community** (−0.07 percentage points per +10 min/day, 95% CI −0.23 to 0.10) or **readmission** (0.02 points, 95% CI
   −0.004 to 0.05). The same held for **functional recovery** in a supplementary exploratory analysis added later
   (−0.05 points, 95% CI −0.52 to 0.42).
2. **More went with lower payment.** Each extra **10 minutes of therapy per day** went with about **$200–280 lower**
   standardized Medicare payment per stay: $280 in the primary model (95% CI $150–$400), and about $200 without the most
   influential facilities. The data cannot show why.
3. **How therapy is delivered shows signals (exploratory).** A higher share of group/concurrent therapy went with
   *higher* discharge-to-community **and** *higher* readmission rates. Both survive false-discovery-rate correction
   (q < 0.001), but they point in opposite directions for quality, were not pre-specified and are not independently
   replicated.

How far these hold:
- Findings 1 and 2 are **consistent in direction under the missing-data treatments tested**: as planned, corrected,
  alternative fill values, and without the affected variables.
- A **missing-data problem** removed 49.8% of facilities from the as-planned analysis. It was traced to CMS small-cell
  suppression, corrected, and **both versions are reported side by side**.
- A **clinical check of the data** passed: facilities treating more patients with stroke or brain injury use more
  speech-language therapy, as expected (supplementary H1).

![Therapy-dose coefficient across the sensitivity analyses for the three outcomes](reports/figures/sensitivity_dose.png)
*Therapy-dose coefficient (+10 min/day, 95% CI) across the sensitivity analyses: S1–S9 pre-specified, S10–S13 added
later (see the deviation log).*

> **Associations, not causal effects.** The data are facility-level, observational and limited to Original Medicare. See
> [Limitations](#limitations).

## Contents

- [Research questions](#research-questions)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [Project structure](#project-structure)
- [Data](#data)
- [Methods](#methods)
- [Results](#results)
- [Limitations](#limitations)
- [Transparency](#transparency)

## Research questions

| | Question | Type |
|---|---|---|
| **RQ-A** | Is a higher daily therapy dose (+10 min/day) associated with discharge to community (primary outcome), readmission and standardized payment per stay? | Confirmatory, Holm-adjusted |
| **RQ-B** | Holding dose fixed, are group/concurrent share, co-treatment share, discipline mix (PT/OT/SLP) and ownership associated with the outcomes? | Exploratory |
| **RQ-C** | Can a case-mix-adjusted screen flag facilities with a long length of stay *and* a low discharge-to-community rate? | Method demonstration |

## Tech stack

| Area | Tools |
|---|---|
| Data cleaning | Python, pandas: CCN crosswalk, suppression handling, rule-based audit log |
| Storage and queries | SQLite with typed tables and CHECK constraints; 7 descriptive SQL queries |
| Statistics | NumPy and SciPy: OLS/WLS with cluster-robust (CR1) standard errors written from scratch, Holm and Benjamini–Hochberg adjustment, bootstrap |
| Validation | statsmodels (cross-check of the estimator, VIF and influence diagnostics); automated data and result checks |
| Figures | matplotlib |

## Quick start

```bash
git clone https://github.com/shitingzhang/rehab-analytics.git
cd rehab-analytics
pip install -r requirements.txt
python run_all.py                       # every step below, in order, then the automated checks (~1 minute)
```

`run_all.py` stops at the first step that fails. Each step can also be run on its own:

```bash
python src/01_data_quality_cms.py       # clean, link files, write audit log
python src/check_linkage.py             # facility-linkage audit (used by sensitivity analysis 13)
python src/02_build_database.py         # build the SQLite database
python src/03_execute_sql.py            # descriptive SQL queries
python src/04_analysis.py --as-planned  # as-planned results (v1.2)
python src/04_analysis.py               # corrected results (v1.3)
python src/05_screening.py --as-planned
python src/05_screening.py
python src/06_compare_versions.py       # side-by-side comparison tables
python src/07_model_diagnostics.py      # model diagnostics (needs statsmodels)
python src/08_figures.py                # sample-flow and missingness figures
python src/09_function_outcomes.py      # supplementary exploratory analysis RQ-A2
python src/11_slp_neuro_check.py        # supplementary positive-control check H1 (SLP vs brain conditions)
python tests/test_pipeline.py           # automated checks of the data and of the key results
```

Run from the project root. The cleaned facility file, the database and the facility-level screen files are rebuilt by
these steps and are not committed. The only random step, the bootstrap in `05`, uses a fixed seed (2026).

**Automated checks** ([`tests/test_pipeline.py`](tests/test_pipeline.py), 11 checks):
- **Data:** raw IDs keep their leading zeros; the facility key is unique; percentages, shares and flags are in range;
  suppression counts match the raw markers; facility links are one-to-one and in the same state.
- **Results:** the published as-planned and corrected estimates, the exploratory classification, and the RQ-A2 and H1
  estimates are reproduced exactly.

The four CMS source files are included in `data/raw/`; see [Data](#data) for where they come from and how to refresh
them.

## Project structure

```
rehab-analytics/
├── run_all.py                   one command for the whole pipeline
├── inspect_new_vars.py          pre-modelling check of exposures and case mix (cited in the deviation log)
├── requirements.txt
├── tests/test_pipeline.py       automated data and result checks
├── data/
│   ├── raw/                     CMS source files (unchanged)
│   └── processed/
│       ├── analysis/            corrected results (v1.3)
│       ├── analysis_as_planned/ as-planned results (v1.2), kept unchanged
│       ├── comparison/          side-by-side tables of all versions
│       ├── diagnostics/         model diagnostics (VIF, residuals, influence)
│       ├── function_outcomes/   supplementary analysis RQ-A2
│       ├── linkage_audit/       facility-linkage checks
│       ├── screening*/          screen summaries (aggregate only)
│       ├── slp_check/           supplementary positive-control check H1
│       └── sql_outputs/         descriptive query results
├── docs/
│   ├── analysis_plan.md         pre-specified plan + deviation log
│   ├── data_dictionary.md       ERD and variable definitions
│   ├── model_diagnostics.md     estimator check, VIF, residuals, influence
│   ├── summary.md               one-page plain-language summary
│   └── work_log.md              step-by-step record of runs and decisions
├── reports/figures/             figures (as_planned/ for v1.2)
├── sql/                         schema and analytical queries
└── src/
    ├── 01 … 09_*.py             pipeline, in run order
    ├── 11_slp_neuro_check.py    supplementary positive-control check H1
    ├── irf_common.py            shared regression engine and variable construction
    └── check_*.py               diagnostics incl. the linkage audit (never look at outcomes)
```

An earlier version of this project, built on synthetic data, is kept only in the git history (commit `82551d5`).

## Data

| File in `data/raw/` | CMS dataset | Content | Version |
|---|---|---|---|
| `pac_irf_by_provider.csv` | Medicare Post-Acute Care Utilization – Inpatient Rehabilitation Facility by Geography and Provider ([data.cms.gov](https://data.cms.gov/provider-summary-by-type-of-service/medicare-post-acute-care-hospice/medicare-post-acute-care-utilization-inpatient-rehabilitation-facility-by-geography-and-provider)) | Stays, covered days, payments, beneficiary mix, therapy minutes by discipline and mode | FY2024, Original Medicare |
| `irf_provider_data.csv` | Inpatient Rehabilitation Facility – Provider Data ([Provider Data Catalog](https://data.cms.gov/provider-data/topics/inpatient-rehabilitation-facilities)) | Discharge to community, readmission and other quality measures | Sept 2026 refresh |
| `irf_general_information.csv` | Inpatient Rehabilitation Facility – General Information (same catalog) | Ownership, address | Sept 2026 refresh |
| `irf_conditions.csv` | Inpatient Rehabilitation Facility – Conditions (same catalog) | Patients by impairment group | Sept 2026 refresh |

These are public U.S. government data. Do not re-save the files in Excel, which drops the leading zeros of IDs. To
refresh them, download the CSV of each dataset and save it under the file name above. A newer refresh can change the
numbers; the automated result checks will then fail, which is intended.

The files are linked by CMS Certification Number (CCN). Hospital-based units have a letter in position 3 in the quality
files but a digit in the utilization file. Of the 810 unit-style CCNs, a data-driven crosswalk links 692: 686 map to
exactly one PAC provider, and 6 ambiguous ones are resolved by the learned letter-to-digit rule. 118 have no PAC
counterpart, and none collide with an already-mapped ID (audit rules DQ-09a–f).

A [linkage audit](data/processed/linkage_audit/linkage_summary.txt) ([`check_linkage.py`](src/check_linkage.py))
compares state, city and ZIP code between the two files for all 1,077 linked facilities:
- Every link is one-to-one, and the **state agrees for 100%** of them.
- 27 links (26 crosswalk, 1 exact) differ in both city and ZIP code. Most are units listed under the address of a parent
  hospital or health system.
- Excluding these 27 and the 6 rule-resolved links (sensitivity analysis 13) leaves the dose results unchanged in
  direction: discharge to community −0.059 [−0.238, 0.120]; readmission 0.019 [−0.009, 0.047]; payment −0.268
  [−0.397, −0.139].

## Methods

- **Model:** OLS for each outcome, adjusting for therapy mode and mix, ownership, setting, case mix (risk score, age,
  dual-eligible %, rural %, impairment groups), log volume and state fixed effects. Standard errors are cluster-robust by
  state.
- **Confirmatory family:** the dose coefficient for the three outcomes, Holm-adjusted. Everything else is exploratory.
- **Sensitivity analyses:**
  - **Pre-specified (S1–S9):** case weighting, state clustering without state fixed effects, trimming, impairment
    suppression fills, setting subgroups, non-linear dose, dose × ownership, group share as tertiles, and
    leave-one-state-out.
  - **Added later, logged in the deviation log:**
    - S10: the as-planned complete-case sample.
    - S11: suppressed dual and rural % filled with 1 or 10.
    - S12: without rural and dual %.
    - S13: excluding low-confidence facility links.
- **Validation:** the hand-written regression code was checked in two ways.
  - Before it was run on real data, it was tested on simulated data with a known truth.
  - On the real data, it was cross-checked against statsmodels (`python src/04_analysis.py --check`). For all three
    outcomes, coefficients and cluster-robust standard errors agree within 1e-8.

## Results

### Why there are two versions

In the as-planned analysis (v1.2), only **539 of 1,073** facilities with a discharge-to-community rate were usable:
**534 (49.8%) were dropped** because rural % or dual-eligible % was missing. The dropped facilities were smaller and more
often hospital-based units.

![Sample flow from the CMS file to the as-planned and corrected analysis samples](reports/figures/sample_flow.png)

![Share of facilities with a suppressed value, by size, setting and ownership](reports/figures/missingness_pattern.png)

The missing values are coded `*` in the raw file, the CMS marker for suppressed small counts.
[`check_suppression.py`](src/check_suppression.py) showed the following:
- **Dual %** met both checks for suppressed counts of 1–10 beneficiaries.
- **Rural %** met one check: shown values never imply fewer than about 11 beneficiaries, allowing for rounding. Its
  missing rate fell only slowly with facility size; the deviation log explains why that check was judged not to apply.

The corrected analysis (v1.3) fills each suppressed value with 5 beneficiaries and adds a "suppressed" indicator.
In the corrected samples, 49.3% of facilities have at least one filled value: dual % in 12.8%, rural % in 42.9%.

> **This change to the primary analysis was made after the as-planned results had been seen.** It is recorded in the
> [deviation log](docs/analysis_plan.md#12-deviation-log), and the as-planned results are reported alongside.

### RQ-A — Therapy dose (+10 min/day)

Coefficient [95% CI]. Discharge to community and readmission are in percentage points; payment is in $1,000 per stay.

| Outcome | As planned, v1.2<br>(n = 539) | **Corrected, v1.3**<br>**(primary)** | Fill = 1 | Fill = 10 | Without rural & dual % |
|---|---|---|---|---|---|
| Discharge to community | −0.084 [−0.260, 0.091]<br>Holm p = 0.68 | **−0.067 [−0.232, 0.097]**<br>Holm p = 0.42, n = 1,063 | −0.066 [−0.230, 0.097] | −0.068 [−0.234, 0.097] | −0.062 [−0.229, 0.104] |
| Readmission (RSRR) | 0.011 [−0.020, 0.042]<br>Holm p = 0.68 | **0.023 [−0.004, 0.050]**<br>Holm p = 0.19, n = 1,063 | 0.023 [−0.004, 0.050] | 0.023 [−0.004, 0.050] | 0.024 [−0.002, 0.051] |
| Payment per stay ($1,000) | −0.253 [−0.423, −0.083]<br>Holm p = 0.013 | **−0.278 [−0.401, −0.154]**<br>Holm p < 0.001, n = 1,065 | −0.277 [−0.401, −0.154] | −0.278 [−0.401, −0.154] | −0.274 [−0.396, −0.153] |

Across the other sensitivity analyses (figure above), including the exclusion of low-confidence facility links:
- The **payment** coefficient was negative, with a CI excluding 0, every time.
- The **discharge-to-community** coefficient changed sign in some analyses, and its CI always included 0.
- The **readmission** CI included 0 in all but one: state clustering without state fixed effects, 0.033 [0.009, 0.056].

### RQ-B — Exploratory associations

The 95% CIs are not adjusted for multiple comparisons; Benjamini–Hochberg q-values are given alongside. The reporting
rule below was fixed **after both versions had been seen** and
before it was applied: a coefficient is reported only if it has the same sign in both versions and its corrected 95% CI
excludes 0. It is a stability filter, not a validation. Both versions use largely the same facilities and the same
model, so agreement between them is **not an independent replication**.

Two further checks were added after an external review (deviation log). Neither changes the classification:
- **q (BH):** Benjamini–Hochberg false-discovery-rate q-values across all 60 exploratory terms. 8 of the 14 reported
  associations have q < 0.05.
- **Fill sensitivity:** all 14 keep their sign under fills of 1 and 10. All but one also keep a CI excluding 0; the
  exception is the nervous-system share for payment, whose lower CI bound is 0.002 in the primary model.

<details>
<summary><b>Reported associations (14)</b></summary>

| Outcome | Term | As planned | Corrected [95% CI] | q (BH) |
|---|---|---|---|---|
| Discharge to community | Group/concurrent share (+10 pts) | 0.97 | 0.83 [0.46, 1.20] | < 0.001 |
| Discharge to community | Dual-eligible % (+10 pts) | −1.47 | −1.13 [−1.66, −0.60] | 0.002 |
| Discharge to community | Stroke share (+10 pts) | 1.14 | 0.77 [0.27, 1.27] | 0.031 |
| Discharge to community | Nervous-system disorder share (+10 pts) † | 0.70 | 0.81 [0.30, 1.32] | 0.030 |
| Readmission | Group/concurrent share (+10 pts) | 0.16 | 0.14 [0.09, 0.20] | < 0.001 |
| Readmission | Co-treatment share (+5 pts) | 0.12 | 0.08 [0.03, 0.14] | 0.030 |
| Readmission | Nervous-system disorder share (+10 pts) | 0.16 | 0.15 [0.05, 0.25] | 0.033 |
| Readmission | For-profit vs non-profit † | 0.13 | 0.23 [0.05, 0.40] | 0.070 |
| Payment ($1,000) | Hospital-based unit vs free-standing † | 0.49 | 1.08 [0.08, 2.08] | 0.174 |
| Payment ($1,000) | Non-traumatic brain disease share (+10 pts) | 1.37 | 1.03 [0.03, 2.03] | 0.199 |
| Payment ($1,000) | Nervous-system disorder share (+10 pts) ‡ | 0.46 | 0.34 [0.00, 0.67] | 0.208 |
| Payment ($1,000) | Spinal-cord share (+10 pts) | 1.64 | 1.60 [0.52, 2.68] | 0.033 |
| Payment ($1,000) | Stroke share (+10 pts) | 0.82 | 0.79 [0.09, 1.49] | 0.148 |
| Payment ($1,000) | log(Medicare stays) | −0.70 | −0.49 [−0.86, −0.12] | 0.069 |

† The 95% CI in the as-planned version included 0.
‡ The CI includes 0 under the fill of 1 or 10.

</details>

- **Depends on the missing-data treatment; not interpreted:** PT share of minutes and discharge to community: −1.26
  [−2.20, −0.32] as planned, 0.11 [−0.61, 0.84] corrected.
- **No association found** for the other 45 terms. **Every** exploratory coefficient, with its CI in all three versions,
  is listed in [`comparison_summary.txt`](data/processed/comparison/comparison_summary.txt) (table 2) and
  [`2_primary_model_all_terms.csv`](data/processed/comparison/2_primary_model_all_terms.csv).

### RQ-C — Review screen (secondary; exploratory method demonstration)

An exploratory facility-level screening framework that shows how case-mix-adjusted operational indicators could be
combined. **It is not validated for operational or regulatory decisions.**

- **Rule:** observed/expected length of stay ≥ 1.20 **and** a discharge-to-community residual in the lowest quartile,
  with expected values from case mix only.
- **Unstable:** 4 facilities are flagged as planned and 11 corrected, but only 2 in both.
- **Biased by size:** in the corrected version, the flag rate is 2.2% in the lowest volume tertile and 0% in the highest.
- **No facility-level list is published**, and no facility should be described as inefficient on this basis.

### Supplementary — Therapy dose and functional recovery (RQ-A2, exploratory, added later)

**This analysis was added after the results above had been seen** ([deviation log](docs/analysis_plan.md#12-deviation-log)).
It is exploratory and cannot be read as confirmatory evidence. It was briefly removed from this report because its result
did not support the hypothesis, and then restored, because an analysis should not be dropped for that reason.

- **Why:** therapy aims at functional recovery, while going home also depends on home support and finances.
- **Outcomes (CY2025):** the % of patients who meet or exceed the discharge function score CMS expects for their
  condition, for overall function, self-care and mobility.
- **Model:** same as the primary model, with Holm adjustment across the three outcomes.
- **Hypothesis, stated before fitting:** a higher dose goes with a higher %.

| Outcome (n = 1,061) | Dose coefficient, +10 min/day [95% CI] | Holm p |
|---|---|---|
| Discharge function score | −0.05 [−0.52, 0.42] | 1.0 |
| Discharge self-care | −0.14 [−0.57, 0.28] | 1.0 |
| Discharge mobility | −0.08 [−0.55, 0.39] | 1.0 |

**No association was found, and the hypothesis was not supported.**
- The outcomes have an SD of about 19–21 points, so the intervals exclude effects larger than about half a point per
  10 minutes.
- The result is the same in direction under the as-planned sample, case weighting and the model without rural and
  dual %. Details: [`function_summary.txt`](data/processed/function_outcomes/function_summary.txt).

In the same models, two coefficients are large. Both are exploratory and not adjusted for multiple comparisons:
- **Group/concurrent share:** +4.4 points per +10 points of share.
- **For-profit ownership:** +7.7 points.

Function scores come from assessments recorded by the facilities themselves, so differences in assessment or
documentation practice cannot be ruled out. The function data (CY2025) and the therapy data (FY2024) do not overlap in
time.

### Supplementary — Do the therapy-mix data match clinical need? (H1, positive control, added later)

**This check was added after all the results above had been seen** ([deviation log](docs/analysis_plan.md#12-deviation-log)).
Its method and pass rule were written down before it was run.

- **Why:** RQ-B relies on therapy-mix variables computed from CMS minutes. If they reflect real clinical practice,
  facilities that treat more patients with brain conditions should use more **speech-language pathology (SLP)**, because
  aphasia, dysphagia and cognitive-communication problems are common after stroke and brain injury.
- **Exposure:** share of patients with stroke, traumatic brain injury or non-traumatic brain disease (+10 points).
  "Other nervous system disorders" was left out in advance as too mixed in SLP need.
- **Outcome:** SLP share of therapy minutes (percentage points). Covariates: hospital-based unit, ownership,
  log(Medicare stays), HCC risk score and mean age; state fixed effects; state-clustered SEs.
- **Pass rule, stated before fitting:** the coefficient is positive and its 95% CI excludes 0.

| Analysis (n = 1,071) | SLP share, points per +10 points of brain-condition share [95% CI] |
|---|---|
| Primary (suppressed counts filled with 5) | **1.71 [0.99, 2.43]** |
| Suppressed counts filled with 1 | 1.50 [0.84, 2.16] |
| Suppressed counts filled with 10 | 1.81 [1.07, 2.54] |
| Dual % and rural % added (n = 1,065) | 1.67 [0.92, 2.42] |

**The check passed.** The average SLP share is 10.6%, so a facility with 10 more points of brain-condition patients
uses about 1.7 points more SLP time: a modest but consistent difference. This supports, but does not prove, that the
therapy-mix variables carry real clinical information. Details:
[`slp_check_summary.txt`](data/processed/slp_check/slp_check_summary.txt).

A second idea, that co-treatment would be more common where more patients have spinal-cord or brain injuries, was
considered and not pursued: the primary models already adjust for impairment mix, and co-treatment use may depend
mostly on facility practice.

## Limitations

- Facility-level, observational, cross-sectional data. Associations only, and they do not apply to individual patients.
- Original Medicare only; Medicare Advantage patients are not included.
- Measurement windows differ. The therapy data (FY2024, Oct 2023 – Sep 2024) overlap only the first year of the
  two-year outcome window (Oct 2023 – Sep 2025), so a facility's FY2024 therapy pattern may not represent the whole
  outcome period.
- Cross-sectional: reverse relationships (e.g. facilities giving more therapy to patients who respond poorly) and common
  causes of both therapy patterns and outcomes cannot be excluded.
- Unmeasured confounding (patient tolerance, medical complexity, referral patterns).
- Discharge to community and readmission are already risk-standardized by CMS. The dose coefficient is therefore a
  facility-level association **conditional on the added covariates, on top of CMS risk standardization**, not an
  unadjusted total effect; some adjustment may be duplicated.
- Payment residuals have a strong right tail ([model diagnostics](docs/model_diagnostics.md)). Without influential
  facilities, the payment association is smaller (about $200 rather than $280 per +10 min/day) but keeps its direction.
  A log-payment model was not pre-specified and was not run.
- How CMS counts concurrent/group minutes was not verified, so the 3-hour therapy rule is not used to judge facilities.
- Suppressed values are filled with 5 beneficiaries, close to the midpoint of 1–10. The CMS methodology defines `*`
  as "ten or fewer beneficiaries" and `+` as dual % withheld for Puerto Rico because of data limitations (8
  facilities, left missing)
  ([PAC PUF methodology, RY26](https://data.cms.gov/sites/default/files/2026-08/338c40d4-05f7-4339-9ef8-0a7834fcf538/MUP_PAC_RY26_Methodology.pdf),
  which covers IRF FY2014–2024).
  The true values behind `*` are unknown; fills of 1 and 10 are reported as sensitivity analyses.
- The IRF therapy rules were checked against [MLN Matters SE17036](https://cms.gov/sites/default/files/repo-new/26/se17036_0.pdf):
  about 3 hours per day on 5 days per week (or 15 hours in 7 days), one-on-one therapy as the standard of care, and
  group/concurrent therapy on a "limited" basis with no numeric limit. CMS states that medical necessity is not judged
  by any threshold of therapy time.
- The Review Choice Demonstration began in Alabama (2023) and Pennsylvania (2024), within the outcome window. State
  fixed effects absorb state-level differences, but changes over time within a state cannot be separated.

## Transparency

| File | What it shows |
|---|---|
| [`docs/summary.md`](docs/summary.md) | One-page plain-language summary for non-specialist readers |
| [`docs/analysis_plan.md`](docs/analysis_plan.md) | Pre-specified questions, models and sensitivity analyses, plus the deviation log |
| [`docs/work_log.md`](docs/work_log.md) | Every run, with its result and the decision taken |
| [`docs/model_diagnostics.md`](docs/model_diagnostics.md) | Estimator cross-check, collinearity, residuals, influential facilities |
| [`docs/data_dictionary.md`](docs/data_dictionary.md) | Database diagram (ERD), every analysis variable and how suppressed values are handled |
| [`data/processed/analysis_as_planned/`](data/processed/analysis_as_planned/) | Full as-planned results, unchanged |
| [`data/processed/comparison/`](data/processed/comparison/) | Side-by-side tables of all versions |
| [`data/processed/linkage_audit/`](data/processed/linkage_audit/) | Facility-linkage audit: identifier agreement and the low-confidence links |
| [`data/processed/audit_log.csv`](data/processed/audit_log.csv) | Every data-quality rule and the number of records it affected |

### AI assistance

The topic, research questions, overall direction and clinical reasoning of this project are my own. An AI assistant
(Claude, Anthropic) played a supporting role only: it helped with coding, debugging, statistical checks, drafting
documentation and pushing the repository to GitHub. The final decisions on the topic, the research questions and the
analysis were mine; the assistant's suggestions were adopted only after my review and approval, and every change to the
pre-specified plan is recorded in the deviation log of [`docs/analysis_plan.md`](docs/analysis_plan.md). All numbers
reported here are produced by the code in this repository, and the key results are checked automatically on every run
([`tests/test_pipeline.py`](tests/test_pipeline.py)).

Examples of my own work and decisions:
- **Data cleaning and facility-ID mapping.** I did most of the data cleaning and wrote the code that maps facility
  IDs between the CMS files before I started working with the AI assistant, which later helped refine it.
- **Co-treatment as its own variable.** From clinical practice, two therapists treating one patient together differs
  from group or concurrent therapy, so I kept the two apart before any model was run.
- **No tuning for better-looking results.** I set the rule that assumptions, models and thresholds are not changed to
  improve the results, and that every change is logged before it is run.
- **Reporting a null result.** I restored the function-outcome analysis (RQ-A2) to the report after it had been removed,
  because an analysis should not be dropped when it does not support the hypothesis.
- **A clinical check of the data.** I chose to test whether facilities with more stroke and brain-injury patients use
  more speech-language therapy (H1), confirmed which patient groups to include, and decided not to pursue a second,
  weaker hypothesis on co-treatment.
- **The core message.** I chose how the findings are summarised, keeping the exploratory results labelled as signals
  rather than conclusions.

## Author

**Shiting Zhang** — rehabilitation-therapy background, working in health data science.
GitHub: [@shitingzhang](https://github.com/shitingzhang)

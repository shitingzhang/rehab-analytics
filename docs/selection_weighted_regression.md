# How much do treatment-share estimates move after reweighting the sample?

Facilities with complete impairment data differ in size and setting from the broader eligible population. This post-results check gives each retained facility the inverse of its stratum’s observed retention fraction and refits the three original Model A regressions. The target for each outcome is its Model B eligible population.

## Treatment-share estimates

DTC (community discharge) and RSRR (readmission) are measured in percentage points. PAY is standardized Medicare payment in dollars per stay. Each change is calculated before rounding. PT and SLP are relative to OT.

| Outcome | Higher share | Original A [95% CI] | Weighted A [95% CI] | Change |
|---|---|---:|---:|---:|
| DTC | Group/concurrent (+10 pp) | +0.904 [+0.513, +1.295] | +0.873 [+0.471, +1.276] | -0.031 |
| DTC | Co-treatment (+5 pp) | -0.289 [-0.877, +0.298] | -0.273 [-0.829, +0.283] | +0.016 |
| DTC | PT share (+5 pp) | -0.069 [-0.662, +0.524] | -0.012 [-0.627, +0.603] | +0.057 |
| DTC | SLP share (+5 pp) | -0.004 [-0.401, +0.394] | +0.003 [-0.407, +0.412] | +0.006 |
| RSRR | Group/concurrent (+10 pp) | +0.153 [+0.087, +0.218] | +0.151 [+0.089, +0.212] | -0.002 |
| RSRR | Co-treatment (+5 pp) | +0.092 [+0.020, +0.165] | +0.082 [+0.014, +0.149] | -0.011 |
| RSRR | PT share (+5 pp) | -0.090 [-0.227, +0.048] | -0.078 [-0.209, +0.052] | +0.011 |
| RSRR | SLP share (+5 pp) | -0.072 [-0.174, +0.030] | -0.070 [-0.167, +0.027] | +0.002 |
| PAY | Group/concurrent (+10 pp) | -23 [-353, +306] | -20 [-362, +322] | +4 |
| PAY | Co-treatment (+5 pp) | +41 [-444, +525] | +65 [-431, +561] | +24 |
| PAY | PT share (+5 pp) | +89 [-459, +637] | +103 [-457, +663] | +14 |
| PAY | SLP share (+5 pp) | +246 [-256, +748] | +278 [-261, +817] | +32 |

Both fits use the same retained facilities, adjustment variables and state fixed effects. Pointwise intervals use state-clustered CR1 standard errors and a t critical value with the number of groups minus one degrees of freedom. Weighted intervals condition on the observed weights. The last column describes movement of paired point estimates. The original BH adjustment applies only to the original primary family.

## Who is represented by the weights?

| Outcome | Retained A | Eligible B | State groups in A | Weight range | Kish effective sample size |
|---|---:|---:|---:|---:|---:|
| DTC | 938 | 1063 | 51 | 1.000–1.387 | 924.1 |
| RSRR | 938 | 1063 | 51 | 1.000–1.387 | 924.1 |
| PAY | 938 | 1065 | 51 | 1.000–1.393 | 923.8 |

Stay-volume quartiles are defined within the eligible population for each outcome. Pairing each quartile with facility setting gives eight strata. Each retained facility receives its stratum’s eligible count divided by its retained count. Weighted counts reproduce every target cell. The effective sample size summarizes weight dispersion.

| Outcome | Setting | Stay quartile | Retained | Eligible | Weight |
|---|---|---|---:|---:|---:|
| DTC | Freestanding | Q1 | 17 | 19 | 1.118 |
| DTC | Freestanding | Q2 | 24 | 26 | 1.083 |
| DTC | Freestanding | Q3 | 101 | 103 | 1.020 |
| DTC | Freestanding | Q4 | 233 | 233 | 1.000 |
| DTC | Hospital unit | Q1 | 181 | 251 | 1.387 |
| DTC | Hospital unit | Q2 | 204 | 238 | 1.167 |
| DTC | Hospital unit | Q3 | 145 | 160 | 1.103 |
| DTC | Hospital unit | Q4 | 33 | 33 | 1.000 |
| RSRR | Freestanding | Q1 | 17 | 19 | 1.118 |
| RSRR | Freestanding | Q2 | 24 | 26 | 1.083 |
| RSRR | Freestanding | Q3 | 101 | 103 | 1.020 |
| RSRR | Freestanding | Q4 | 233 | 233 | 1.000 |
| RSRR | Hospital unit | Q1 | 181 | 251 | 1.387 |
| RSRR | Hospital unit | Q2 | 204 | 238 | 1.167 |
| RSRR | Hospital unit | Q3 | 145 | 160 | 1.103 |
| RSRR | Hospital unit | Q4 | 33 | 33 | 1.000 |
| PAY | Freestanding | Q1 | 16 | 19 | 1.188 |
| PAY | Freestanding | Q2 | 25 | 27 | 1.080 |
| PAY | Freestanding | Q3 | 101 | 103 | 1.020 |
| PAY | Freestanding | Q4 | 233 | 233 | 1.000 |
| PAY | Hospital unit | Q1 | 178 | 248 | 1.393 |
| PAY | Hospital unit | Q2 | 204 | 239 | 1.172 |
| PAY | Hospital unit | Q3 | 148 | 163 | 1.101 |
| PAY | Hospital unit | Q4 | 33 | 33 | 1.000 |

Selection can still depend on patients, facilities or volume differences within these eight groups. The excluded facilities’ impairment profiles remain unknown. The sample supports facility-level associations.

## Reproduction

Run `python src/selection_weighted_regression.py` from the repository root. The script verifies membership and cell counts, reproduces the saved original estimates, and checks every weighted coefficient, clustered standard error and interval against statsmodels. [Study design and sample selection](study_design.md)

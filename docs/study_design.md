# Study design and changes to the analysis

## Source files

The facility is the unit of observation. Download the four original CMS CSVs and give them the names below in `data/raw/`. Keep facility identifiers as text so that leading zeros are preserved.

| Filename | Release |
|---|---|
| `pac_irf_by_provider.csv` | FY2024 [Medicare Post-Acute Care Utilization, Inpatient Rehabilitation Facility by Geography and Provider](https://data.cms.gov/provider-summary-by-type-of-service/medicare-post-acute-care-hospice/medicare-post-acute-care-utilization-inpatient-rehabilitation-facility-by-geography-and-provider) |
| `irf_general_information.csv` | September 2026 refresh, IRF General Information in the [CMS Provider Data Catalog](https://data.cms.gov/provider-data/topics/inpatient-rehabilitation-facilities) |
| `irf_provider_data.csv` | September 2026 refresh, IRF Provider Data in the same catalog |
| `irf_conditions.csv` | September 2026 refresh, IRF Conditions in the same catalog |

The utilization file covers October 2023 through September 2024. The two risk-standardized quality measures cover October 2023 through September 2025. The payment outcome is standardized Medicare payment per stay.

## Sample and model comparisons

Of 1,165 facilities in the utilization file, 1,077 were linked to the outcome dataset. Before requiring calculable impairment composition, 1,063 were eligible for the two quality models and 1,065 for payment. The main model included 938 facilities for each outcome.

| Model | Sample | Case-mix adjustment |
|---|---|---|
| A | Facilities with calculable impairment shares | Includes impairment shares |
| B | All otherwise eligible facilities | Omits impairment shares |
| C | The Model A facilities | Omits impairment shares |

Comparing B with C describes how estimates change when the sample is restricted. Comparing C with A shows the change when impairment shares enter the regression on the same facilities. All models include the four therapy measures, ownership, setting, age, HCC risk, dual eligibility and rural percentages and their suppression indicators, log stay volume and state fixed effects. The four exposure terms in A across the three outcomes form the 12-term multiple-testing family.

## Decisions that shaped the current study

| Decision and timing | Reason | Consequence |
|---|---|---|
| Separate co-treatment from group/concurrent therapy before fitting the models | Co-treatment involves clinicians working with the same patient. Group and concurrent treatment describe how a clinician delivers therapy across patients. | Each format has a separate share of recorded minutes. |
| Revisit the daily-dose question after the earlier results were available | First-two-week therapy minutes and full-stay covered days measure different windows. | The current exploratory question uses shares of recorded minutes. The historical ratio and estimates remain available. |
| Rebuild the impairment composition after auditing the CMS footnotes | CMS distinguishes a suppressed small count from unavailable information. | A suppressed count of 1–10 is assigned the working midpoint of 5. An unavailable or absent category leaves the full composition unknown. |
| Examine influence, selection, payment scale and curvature after seeing Model A | A subset of otherwise eligible facilities lacked calculable impairment shares, and high-payment facilities had larger residuals. | The four sensitivity analyses have separate reports. Primary estimates and their original adjustment for multiple testing are reported together. |

Of the 125 otherwise eligible facilities excluded from the community discharge and readmission models, 95.2% were hospital-based units, compared with 60.0% of the 938 retained facilities. The [sample comparison](../data/processed/mode_mix_post_audit/retained_excluded_characteristics.csv) also reports age, risk, size and ownership.

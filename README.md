# Therapy Dose, Mode & Mix in U.S. Inpatient Rehabilitation Facilities

![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-database-003B57?logo=sqlite&logoColor=white)
![Data](https://img.shields.io/badge/data-CMS%20public%20files-2E7D32)
![Analysis](https://img.shields.io/badge/analysis-pre--specified%20plan-6A1B9A)

> **Does more therapy in inpatient rehabilitation facilities (IRFs) go with better outcomes, or mostly with higher
> costs?** This project uses public CMS data on about 1,165 U.S. IRFs to study how the amount, mode and mix of therapy
> relate to discharge to the community, readmission and Medicare payment per stay.

**This repository is published in stages.** This README currently explains **Stage 1: data cleaning and linkage**.
Later stages will be documented here as they are written up.

## Why this project

I have a rehabilitation background. During a hospital rehab internship in China, I noticed that many patients left
after about two weeks, even when they were still improving. If time in rehab is limited, how much therapy a patient
gets, and how it is given, should matter. In the U.S., Medicare pays IRFs a fixed amount per stay and generally expects
patients to handle about three hours of therapy a day. CMS also publishes facility-level data on therapy minutes and
outcomes, which makes the question testable.

## Research questions

| | Question | Type |
|---|---|---|
| **RQ-A** | Is a higher daily therapy dose (+10 min/day) associated with discharge to community (primary outcome), readmission and standardized payment per stay? | Confirmatory |
| **RQ-B** | Holding dose fixed, are group/concurrent share, co-treatment share, discipline mix (PT/OT/SLP) and ownership associated with the outcomes? | Exploratory |
| **RQ-C** | Can a case-mix-adjusted screen flag facilities with a long length of stay *and* a low discharge-to-community rate? | Method demonstration |

The questions and methods were written down in an analysis plan before any outcomes were looked at.

## Roadmap

| Stage | Content | Status |
|---|---|---|
| **1** | **Data cleaning and facility linkage** (`src/01_data_quality_cms.py`) | **Documented below** |
| 2 | SQLite database and descriptive SQL queries (`src/02`, `src/03`, `sql/`) | Code uploaded; write-up coming |
| 3 | Main analysis and review screen (`src/04`, `src/05`) | Code uploaded; write-up coming |
| 4 | Missing-data correction, diagnostics and supplementary checks | Coming |
| 5 | Power BI dashboard | Coming |

## Stage 1 — Data cleaning and linkage

### Data

| File in `data/raw/` | CMS dataset | Content | Version |
|---|---|---|---|
| `pac_irf_by_provider.csv` | Medicare Post-Acute Care Utilization – Inpatient Rehabilitation Facility by Geography and Provider ([data.cms.gov](https://data.cms.gov/provider-summary-by-type-of-service/medicare-post-acute-care-hospice/medicare-post-acute-care-utilization-inpatient-rehabilitation-facility-by-geography-and-provider)) | Stays, covered days, payments, beneficiary mix, therapy minutes by discipline and mode | FY2024, Original Medicare |
| `irf_provider_data.csv` | Inpatient Rehabilitation Facility – Provider Data ([Provider Data Catalog](https://data.cms.gov/provider-data/topics/inpatient-rehabilitation-facilities)) | Discharge to community, readmission and other quality measures | Sept 2026 refresh |
| `irf_general_information.csv` | Inpatient Rehabilitation Facility – General Information (same catalog) | Ownership, address | Sept 2026 refresh |
| `irf_conditions.csv` | Inpatient Rehabilitation Facility – Conditions (same catalog) | Patients by impairment group | Sept 2026 refresh |

These are public U.S. government data. The raw files are not stored in this repository: download the CSV of each
dataset and save it under the file name above.

> **Do not open and re-save these files in Excel.** Excel drops the leading zeros of facility IDs, and the files will
> no longer link.

### Linking the files

The four files are linked by CMS Certification Number (CCN). Therapy minutes and payments are in the utilization (PAC)
file; outcomes are in the quality (Provider Data) files.

- Hospital-based rehab units have a **letter in position 3** of their CCN in the quality files, but a **digit** in the
  utilization file, so a direct join misses them.
- A data-driven crosswalk (`data/processed/ccn_crosswalk.csv`) handles this. Of the **810** unit-style CCNs:
  - **686** map to exactly one PAC provider;
  - **6** ambiguous ones are resolved by a letter-to-digit rule learned from the unambiguous matches;
  - **118** have no PAC counterpart (for example, closed, renumbered or too few Medicare stays);
  - **0** collide with an ID that is already mapped.
- After removing 53 national and state summary rows, the PAC file has **1,165 facilities**.

### Suppressed and missing values

CMS hides small numbers to protect privacy. These markers are never treated as 0.

| Marker | Meaning | Stage 1 handling |
|---|---|---|
| `*` in PAC numeric columns | Ten or fewer beneficiaries | Set to missing; for dual-eligible % and rural % a flag column records that the value was suppressed |
| `+` in dual-eligible % | Withheld for Puerto Rico because of data limitations | Left missing |
| Impairment-group count "less than 11" | Count of 1–10 | Flagged; filled with 5 only when computing shares |
| Non-numeric quality score | Not available, too few cases, etc. | Set to missing; the CMS footnote is kept |

How these values are handled in the models is part of a later stage.

### Audit log

Every cleaning rule, the number of records it affected and the action taken is written to
[`data/processed/audit_log.csv`](data/processed/audit_log.csv) (31 rows). Examples:

| Rule | Check | Records | Action |
|---|---|---|---|
| DQ-04 | Suppressed cells (`*`, `+`) across all PAC numeric columns | 15,294 | Set to missing, never 0 |
| DQ-06 | PT/OT/SLP therapy minutes do not add up to the total | 0 | Flag (none found) |
| DQ-09b–e | Unit-style CCN crosswalk | 810 | 692 mapped, 118 unmapped |
| DQ-15 | Quality scores that are non-numeric or suppressed | 14,175 | Set to missing |
| DQ-32 | Quality-file facilities absent from the PAC file | 145 | Not in the analytical cohort |

### Run Stage 1

```bash
git clone https://github.com/shitingzhang/rehab-analytics.git
cd rehab-analytics
pip install -r requirements.txt
# save the four CMS files in data/raw/ first (see Data above)
python src/01_data_quality_cms.py
```

Outputs in `data/processed/`: the audit log, the CCN crosswalk, the cleaned quality measures in long format, the
national and state reference rows and the measurement window of each quality measure.

## AI assistance

The topic, research questions and clinical reasoning are my own. I did most of the data cleaning and wrote the code
that maps facility IDs before I started working with an AI assistant (Claude, Anthropic), which later helped refine the
code, check the statistics and draft documentation. Every decision was reviewed and approved by me.

## Author

**Shiting Zhang** — rehabilitation background, working in health data science.
GitHub: [@shitingzhang](https://github.com/shitingzhang)

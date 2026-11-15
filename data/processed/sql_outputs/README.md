# SQL outputs: current batches and legacy snapshots

The `query_01.csv` through `query_07.csv` files in this directory are published snapshots from the historical descriptive workflow. Step 03 no longer overwrites them. Do not treat them as the latest run.

Run from the project root:

```powershell
python src/03_execute_sql.py
python src/03_execute_sql.py --show-current
python src/03_execute_sql.py --show-last-successful  # deliberate historical audit/recovery
```

The second command requires the latest recorded attempt to be successful, validates the batch,
and prints its directory. Failed/incomplete/unknown states are rejected.
Its seven CSVs and `manifest.json` live together under `runs/<run-id>/`.
`run_status.json` records the latest attempt and the retained `current_batch` reference.
The third command deliberately allows the retained historical success after a failure or interruption.
It still checks hashes/completeness and never treats failed output as successful. It is not proof
that the latest attempt succeeded. The two reader flags are mutually exclusive.

Programmatic readers should call `current_result_dir()` from the step-03 module
once per operation and read every CSV from that returned directory. The resolver
checks the manifest and file hashes and never falls back to root-level snapshots.
A later successful run does not overwrite the directory already selected by a reader.
For explicit historical access use `last_successful_result_dir()`; there is no automatic fallback
from a failed current read. Both readers reject altered or incomplete batches and root snapshots.
Status is checked when selecting a batch, not continuously. This is not a guarantee that source
inputs were rerun after every edit, nor a unified status across all pipeline stages.

Manifests record database, SQL and exporter hashes, versions, query IDs, row counts,
columns and output checksums. A pointer switch happens only after the full batch
exists and validates. A failure before the switch does not publish a partial batch.
These exports use the historical database and are not the new mode/mix model outputs.

Batches and status are generated locally and ignored by Git. Reproduction creates them.
Do not automatically choose an unreferenced batch left by an interrupted publication.
A `.writer.lock` left by forced termination requires checking that no writer remains
before removing the lock; the exporter does not automatically reclaim it.
This protects ordinary failures and process interruptions, not every power-loss or
filesystem-corruption scenario. It is not whole-project transactional publication.


## Metric definitions

These are historical descriptive queries, not the current 938-facility model results.
The existing CSV aliases are retained for compatibility and should be interpreted as follows:

| Query / column | Meaning |
|---|---|
| 01/04 `mean_pay_per_stay`; 03 `avg_pay_per_stay` | Unweighted mean of available **actual Medicare payment per stay** across facilities, in dollars. Each facility's value is rounded to whole dollars before aggregation. This is not standardized payment, pooled national payment per stay, provider cost or charges. |
| 06 `facilities_with_rsrr` | Facilities in the ownership group with a non-NULL RSRR point estimate. The benchmark is the unweighted mean of all available facility RSRRs in the joined database. |
| 06 `clearly_worse` | Count whose lower RSRR interval bound is above that sample mean. A legacy alias, not a CMS or clinical quality classification. |
| 06 `clearly_better` | Count whose upper RSRR interval bound is below that sample mean. A legacy alias, not a CMS or clinical quality classification. |
| 06 `pct_clearly_worse` | 100 times the above-mean interval count divided by `facilities_with_rsrr`, rounded to one decimal. |

Query 06 does not use the official national-comparison labels supplied under raw QRP
measure `I_017_01_PPR_PD_COMP_PERF`. It treats the estimated sample mean as fixed and
does not adjust for its uncertainty or multiple comparisons. A missing bound does
not satisfy its comparison; the SQL does not create a separate missing-interval category.
Do not call the remaining facilities equivalent to the benchmark or officially average.

The current regression instead uses **standardized** Medicare payment per stay,
scaled to $1,000 (`std_pay_k`). The columns have distinct source totals and are not
interchangeable. See [historical results and measurement](../../../README.md#historical-results).

Other historical limitations remain: the recorded-minutes/covered-days ratio is not
accurate daily intensity; the state rank uses rounded means and a minimum total
facility count, not per-outcome effective sample sizes; the earlier review flag is
distinct from the later screening demonstration; neither is an operationally validated tool.
Clarifying these labels does not change those algorithms or resolve their limitations.


## State-summary denominators (Query 04)

`facilities` counts all facilities in the state/region; eligibility still means at
least five **total** facilities. `facilities_with_dtc` and `facilities_with_rsrr`
count non-NULL outcome values actually contributing to the corresponding means.
These are facility counts, not patient counts. They add transparency, not a new filter.

The historical `dtc_rank` is retained: it ranks the one-decimal displayed mean.
Rounding can create ties between distinct means. Those ranks have no uncertainty
adjustment and should not be read as evidence of state quality differences. Showing
more decimals or breaking ties would not establish such evidence. A low valid count
also limits the interpretation even when total-facility eligibility is met.

No state/region, threshold, original mean or original rank is changed by adding
the two count columns. Earlier batches retain their original CSV schema.

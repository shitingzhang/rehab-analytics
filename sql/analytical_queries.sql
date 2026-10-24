-- =============================================================================
-- Inpatient Rehabilitation Facility (IRF) Quality, Utilization & Case-Mix Queries
-- Database: data/processed/irf_analytics.db   Grain: one row per facility (CCN)
-- Sources : CMS PAC Utilization PUF (FY2024, Original Medicare) joined to
--           CMS IRF Quality Reporting Program data (Sept 2026 refresh).
-- Caution : measure windows differ (see table measure_periods). Results are
--           facility-level associations, not patient-level causal effects.
-- =============================================================================

-- Query 01: Facility benchmark by ownership type and setting (hospital-based unit vs free-standing)
SELECT
    COALESCE(ownership_type, 'Unknown') AS ownership_type,
    CASE is_unit_ccn WHEN 1 THEN 'Hospital-based unit' WHEN 0 THEN 'Free-standing / other' ELSE 'Unknown' END AS setting,
    COUNT(*) AS facilities,
    ROUND(AVG(alos_days), 1) AS mean_alos_days,
    ROUND(AVG(therapy_min_per_day), 1) AS mean_therapy_min_per_day,
    ROUND(AVG(medicare_pay_per_stay), 0) AS mean_pay_per_stay,
    ROUND(AVG(bene_avg_risk_scre), 2) AS mean_risk_score,
    ROUND(AVG(i_019_02_dtc_rs_rate), 1) AS mean_dtc_rs_rate,
    ROUND(
        SUM(i_019_02_dtc_rs_rate * i_019_02_dtc_volume)
        / SUM(CASE WHEN i_019_02_dtc_rs_rate IS NOT NULL AND i_019_02_dtc_volume IS NOT NULL
                   THEN i_019_02_dtc_volume END),
        1
    ) AS volume_weighted_dtc_rate,
    ROUND(AVG(i_017_01_ppr_pd_rsrr), 2) AS mean_readmit_rsrr
FROM facility
GROUP BY 1, 2
ORDER BY 1, 2;

-- Query 02: Case-mix stratification: facilities grouped into tertiles of stroke share
WITH tiers AS (
    SELECT *, NTILE(3) OVER (ORDER BY dx_pct_stroke) AS stroke_tertile
    FROM facility
    WHERE dx_pct_stroke IS NOT NULL
)
SELECT
    stroke_tertile,
    COUNT(*) AS facilities,
    ROUND(AVG(dx_pct_stroke), 1) AS mean_stroke_pct,
    ROUND(AVG(bene_avg_age), 1) AS mean_age,
    ROUND(AVG(bene_avg_risk_scre), 2) AS mean_risk_score,
    ROUND(AVG(alos_days), 1) AS mean_alos_days,
    ROUND(AVG(i_019_02_dtc_rs_rate), 1) AS mean_dtc_rs_rate,
    ROUND(AVG(i_017_01_ppr_pd_rsrr), 2) AS mean_readmit_rsrr
FROM tiers
GROUP BY stroke_tertile
ORDER BY stroke_tertile;

-- Query 03: Therapy intensity quartile (PT+OT+SLP minutes per covered day) versus outcomes and cost
WITH ranked AS (
    SELECT *, NTILE(4) OVER (ORDER BY therapy_min_per_day) AS therapy_quartile
    FROM facility
    WHERE therapy_min_per_day IS NOT NULL
)
SELECT
    therapy_quartile,
    COUNT(*) AS facilities,
    ROUND(AVG(therapy_min_per_day), 1) AS avg_therapy_min_per_day,
    ROUND(AVG(group_therapy_share), 3) AS avg_group_therapy_share,
    ROUND(AVG(bene_avg_risk_scre), 2) AS avg_risk_score,
    ROUND(AVG(i_019_02_dtc_rs_rate), 1) AS avg_dtc_rs_rate,
    ROUND(AVG(i_017_01_ppr_pd_rsrr), 2) AS avg_readmit_rsrr,
    ROUND(AVG(medicare_pay_per_stay), 0) AS avg_pay_per_stay
FROM ranked
GROUP BY therapy_quartile
ORDER BY therapy_quartile;

-- Query 04: State-level performance and ranking (states with at least 5 facilities)
WITH state_stats AS (
    SELECT
        state,
        COUNT(*) AS facilities,
        SUM(tot_epsd_stay_cnt) AS medicare_ffs_stays,
        ROUND(AVG(alos_days), 1) AS mean_alos_days,
        ROUND(AVG(i_019_02_dtc_rs_rate), 1) AS mean_dtc_rs_rate,
        ROUND(AVG(i_017_01_ppr_pd_rsrr), 2) AS mean_readmit_rsrr,
        ROUND(AVG(medicare_pay_per_stay), 0) AS mean_pay_per_stay
    FROM facility
    WHERE state IS NOT NULL
    GROUP BY state
    HAVING COUNT(*) >= 5
)
SELECT
    RANK() OVER (ORDER BY mean_dtc_rs_rate DESC) AS dtc_rank,
    state, facilities, medicare_ffs_stays, mean_alos_days,
    mean_dtc_rs_rate, mean_readmit_rsrr, mean_pay_per_stay
FROM state_stats
ORDER BY dtc_rank;

-- Query 05: Operational review summary (peer-adjusted flag, see 01_data_quality_cms.py)
SELECT
    review_flag,
    COUNT(*) AS facilities,
    ROUND(AVG(alos_days), 1) AS mean_alos_days,
    ROUND(AVG(alos_vs_peer_median), 2) AS mean_alos_vs_peer_median,
    ROUND(AVG(i_019_02_dtc_rs_rate), 1) AS mean_dtc_rs_rate,
    ROUND(AVG(therapy_min_per_day), 1) AS mean_therapy_min_per_day,
    ROUND(AVG(bene_avg_risk_scre), 2) AS mean_risk_score
FROM facility
GROUP BY review_flag
ORDER BY CASE review_flag WHEN 'Priority for Review' THEN 1 WHEN 'Standard' THEN 2 ELSE 3 END;

-- Query 06: Readmission versus the all-facility mean, using the 95% confidence interval of each facility
WITH bench AS (
    SELECT AVG(i_017_01_ppr_pd_rsrr) AS mean_rsrr FROM facility
)
SELECT
    COALESCE(f.ownership_type, 'Unknown') AS ownership_type,
    COUNT(*) AS facilities_with_rsrr,
    SUM(CASE WHEN f.i_017_01_ppr_pd_rsrr_2_5 > b.mean_rsrr THEN 1 ELSE 0 END) AS clearly_worse,
    SUM(CASE WHEN f.i_017_01_ppr_pd_rsrr_97_5 < b.mean_rsrr THEN 1 ELSE 0 END) AS clearly_better,
    ROUND(100.0 * SUM(CASE WHEN f.i_017_01_ppr_pd_rsrr_2_5 > b.mean_rsrr THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_clearly_worse
FROM facility f
CROSS JOIN bench b
WHERE f.i_017_01_ppr_pd_rsrr IS NOT NULL
GROUP BY 1
ORDER BY pct_clearly_worse DESC;

-- Query 07: Data-quality audit trail (rules that affected records)
SELECT rule_id, "table" AS source_table, records_affected, action_taken
FROM audit_log
WHERE records_affected > 0
ORDER BY rule_id;
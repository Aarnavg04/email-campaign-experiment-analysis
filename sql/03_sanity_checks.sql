-- Data quality and pooled distribution checks.
--
-- OUTCOME-BLIND BY CONSTRUCTION. Every query below is either pre-treatment or
-- pooled across all three arms. Nothing here groups visit / conversion /
-- spend by `segment`, so running this file cannot reveal a treatment contrast
-- and cannot influence any decision made in PRE_REGISTRATION.md.
--
-- This file is committed BEFORE the pre-registration on purpose: the
-- pre-registration's power calculations need a baseline conversion rate and
-- an SD for spend, and those come from the pooled moments in section 6. The
-- ordering is the evidence that those inputs were not chosen to flatter a
-- result.
--
-- Run:  psql -h localhost -p 5433 -U hillstrom -d hillstrom -f sql/03_sanity_checks.sql


\echo '=== 1. Row count (expect exactly 64,000) ==='
SELECT COUNT(*) AS n_rows FROM customers;


\echo '=== 2. Nulls by column (expect all zero) ==='
SELECT
    COUNT(*) FILTER (WHERE recency         IS NULL) AS null_recency,
    COUNT(*) FILTER (WHERE history         IS NULL) AS null_history,
    COUNT(*) FILTER (WHERE history_segment IS NULL) AS null_history_segment,
    COUNT(*) FILTER (WHERE mens            IS NULL) AS null_mens,
    COUNT(*) FILTER (WHERE womens          IS NULL) AS null_womens,
    COUNT(*) FILTER (WHERE zip_code        IS NULL) AS null_zip_code,
    COUNT(*) FILTER (WHERE newbie          IS NULL) AS null_newbie,
    COUNT(*) FILTER (WHERE channel         IS NULL) AS null_channel,
    COUNT(*) FILTER (WHERE segment         IS NULL) AS null_segment
FROM customers;


\echo '=== 3. Exact duplicate rows on all substantive columns ==='
-- customer_id is synthetic (SERIAL), so it is excluded from the key.
SELECT COUNT(*) AS n_duplicate_groups
FROM (
    SELECT recency, history_segment, history, mens, womens, zip_code,
           newbie, channel, segment, visit, conversion, spend
    FROM customers
    GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12
    HAVING COUNT(*) > 1
) dupes;


\echo '=== 4. Logical consistency of the outcome funnel (pooled) ==='
-- Expect: spend > 0 implies conversion = 1, and conversion = 1 implies
-- visit = 1. Any violation is investigated, not silently dropped.
SELECT
    COUNT(*) FILTER (WHERE spend > 0 AND conversion = 0) AS spend_without_conversion,
    COUNT(*) FILTER (WHERE conversion = 1 AND spend = 0) AS conversion_without_spend,
    COUNT(*) FILTER (WHERE conversion = 1 AND visit = 0) AS conversion_without_visit,
    COUNT(*) FILTER (WHERE spend > 0 AND visit = 0)      AS spend_without_visit
FROM customers;


\echo '=== 5. Covariate coherence ==='
-- 5a. Does the banded history_segment agree with continuous history?
SELECT
    history_segment,
    COUNT(*)      AS n,
    MIN(history)  AS min_history,
    MAX(history)  AS max_history
FROM customers
GROUP BY history_segment
ORDER BY history_segment;

\echo '--- 5b. Can mens = 0 AND womens = 0 at the same time? ---'
SELECT mens, womens, COUNT(*) AS n
FROM customers
GROUP BY mens, womens
ORDER BY mens, womens;

\echo '--- 5c. Categorical levels (also confirms the "Surburban" spelling) ---'
SELECT 'zip_code' AS col, zip_code AS value, COUNT(*) AS n FROM customers GROUP BY zip_code
UNION ALL
SELECT 'channel',  channel,  COUNT(*) FROM customers GROUP BY channel
UNION ALL
SELECT 'segment',  segment,  COUNT(*) FROM customers GROUP BY segment
ORDER BY col, value;


\echo '=== 6. POOLED outcome moments -- the power-analysis inputs ==='
-- These are the numbers PRE_REGISTRATION.md uses to size MDEs. Pooled across
-- all three arms, so no treatment contrast is exposed.
SELECT
    COUNT(*)                                   AS n,
    AVG(visit)::numeric(10,6)                  AS pooled_visit_rate,
    AVG(conversion)::numeric(10,6)             AS pooled_conversion_rate,
    AVG(spend)::numeric(10,4)                  AS pooled_mean_spend,
    STDDEV_SAMP(spend)::numeric(10,4)          AS pooled_sd_spend,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY spend) AS median_spend,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY spend) AS p99_spend,
    MAX(spend)                                 AS max_spend
FROM customers;


\echo '=== 7. Spend concentration -- how fragile is the mean? ==='
-- Share of all spend attributable to the top 50 spenders. Published work on
-- this dataset suggests a handful of customers dominate; quantify it here so
-- the pre-registration can commit to a winsorisation rule with eyes open.
WITH ranked AS (
    SELECT spend, ROW_NUMBER() OVER (ORDER BY spend DESC) AS rn
    FROM customers
)
SELECT
    (SELECT COUNT(*) FROM customers WHERE spend > 0)          AS n_spenders,
    (SELECT SUM(spend) FROM customers)::numeric(12,2)         AS total_spend,
    SUM(spend) FILTER (WHERE rn <= 50)::numeric(12,2)         AS top50_spend,
    ROUND(100.0 * SUM(spend) FILTER (WHERE rn <= 50)
          / NULLIF((SELECT SUM(spend) FROM customers), 0), 2) AS top50_pct_of_total,
    ROUND(100.0 * SUM(spend) FILTER (WHERE rn <= 640)
          / NULLIF((SELECT SUM(spend) FROM customers), 0), 2) AS top1pct_pct_of_total
FROM ranked;


\echo '=== 8. Arm sizes -- assignment counts only, no outcomes ==='
-- Needed for the SRM test in Phase 3. Counting assignments reveals nothing
-- about treatment effects.
SELECT segment, COUNT(*) AS n,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 4) AS pct
FROM customers
GROUP BY segment
ORDER BY segment;

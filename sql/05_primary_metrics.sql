-- Primary and secondary metrics, by treatment arm.
--
-- ============================================================================
-- THIS IS WHERE THE OUTCOME BLIND ENDS.
--
-- Every file committed before this one was either pooled across arms or
-- restricted to pre-treatment covariates, so nothing computed so far could
-- reveal which campaign won. This file computes outcomes BY ARM. That is
-- deliberate and it is the intended next step: PRE_REGISTRATION.md was
-- committed, tagged `pre-registration-v1` and verified on the remote before
-- this file existed, so every analytical choice was already fixed and public.
-- Git history is what makes that verifiable rather than merely asserted.
-- ============================================================================
--
-- The winsorisation constant below is READ FROM the pre-registration, not
-- recomputed here. Recomputing a percentile at analysis time is exactly how a
-- "pre-specified" threshold quietly drifts to fit the data. PRE_REGISTRATION.md
-- section 6 fixes it at $243.66, capping 64 customers; if a recomputation
-- disagreed, the pre-registered number would still govern.
--
-- Aggregation lives here. Inference lives in src/inference.py -- note that the
-- pre-registered PRIMARY estimator (Lin-adjusted OLS) needs row-level data and
-- therefore cannot be produced by this file at all. What this file provides is
-- the descriptive table and the inputs for the unadjusted sensitivity check.
--
-- Run: psql -h localhost -p 5433 -U hillstrom -d hillstrom -f sql/05_primary_metrics.sql

\set winsor_cap 243.66

SELECT
    segment,
    COUNT(*)                                              AS n,

    -- Primary metric
    SUM(conversion)                                       AS conversions,
    AVG(conversion)                                       AS conversion_rate,
    VAR_SAMP(conversion)                                  AS conversion_var,

    -- Secondary: directional coherence check
    SUM(visit)                                            AS visits,
    AVG(visit)                                            AS visit_rate,
    VAR_SAMP(visit)                                       AS visit_var,

    -- Secondary: spend, raw
    AVG(spend)                                            AS spend_mean,
    VAR_SAMP(spend)                                       AS spend_var,
    SUM(spend)                                            AS spend_total,

    -- Secondary: spend, winsorised at the pre-registered cap
    AVG(LEAST(spend, :winsor_cap))                        AS spend_mean_wins,
    VAR_SAMP(LEAST(spend, :winsor_cap))                   AS spend_var_wins,
    COUNT(*) FILTER (WHERE spend > :winsor_cap)           AS n_capped,

    -- Secondary: the P(conversion) x E[spend | conversion] decomposition.
    -- Separates "more people bought" from "buyers spent more" -- different
    -- product stories with different follow-ups.
    AVG(spend) FILTER (WHERE conversion = 1)              AS spend_given_conversion,
    VAR_SAMP(spend) FILTER (WHERE conversion = 1)         AS spend_given_conversion_var
FROM customers
GROUP BY segment
ORDER BY segment;

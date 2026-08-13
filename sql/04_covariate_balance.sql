-- Covariate balance ("Table 1") -- per-arm moments for every pre-treatment
-- covariate.
--
-- PRE-TREATMENT ONLY. Every covariate below was measured in the twelve months
-- BEFORE the campaign, so splitting it by arm reveals nothing about how the
-- treatments performed. This file contains no reference to visit, conversion
-- or spend, which is what allows randomisation to be verified while the
-- outcome blind is still fully intact.
--
-- The eight covariates are those fixed in PRE_REGISTRATION.md section 5.
-- Multi-level categoricals (zip_code, channel, history_segment) are one-hot
-- expanded so each level gets its own row, as is standard for a Table 1.
--
-- Output is long-format: one row per (covariate, level, arm). Aggregation
-- happens here; the standardised mean differences are computed in
-- src/balance.py. Reporting VAR_SAMP for every covariate -- including the 0/1
-- indicators, where it equals p(1-p) up to n/(n-1) -- lets a single SMD
-- formula serve both continuous and binary cases.
--
-- Run: psql -h localhost -p 5433 -U hillstrom -d hillstrom -f sql/04_covariate_balance.sql

WITH indicators AS (
    SELECT
        segment,
        recency::numeric                                        AS recency,
        history                                                 AS history,
        mens::numeric                                           AS mens,
        womens::numeric                                         AS womens,
        newbie::numeric                                         AS newbie,
        (zip_code = 'Rural')::int::numeric                       AS zip_rural,
        (zip_code = 'Surburban')::int::numeric                   AS zip_surburban,
        (zip_code = 'Urban')::int::numeric                       AS zip_urban,
        (channel = 'Phone')::int::numeric                        AS chan_phone,
        (channel = 'Web')::int::numeric                          AS chan_web,
        (channel = 'Multichannel')::int::numeric                 AS chan_multi,
        (history_segment = '1) $0 - $100')::int::numeric         AS hs_1,
        (history_segment = '2) $100 - $200')::int::numeric       AS hs_2,
        (history_segment = '3) $200 - $350')::int::numeric       AS hs_3,
        (history_segment = '4) $350 - $500')::int::numeric       AS hs_4,
        (history_segment = '5) $500 - $750')::int::numeric       AS hs_5,
        (history_segment = '6) $750 - $1,000')::int::numeric     AS hs_6,
        (history_segment = '7) $1,000 +')::int::numeric          AS hs_7
    FROM customers
),
long AS (
    SELECT segment, 'recency'         AS covariate, 'months'            AS level, recency       AS value, 'continuous' AS kind FROM indicators
    UNION ALL SELECT segment, 'history',         'dollars',             history,       'continuous' FROM indicators
    UNION ALL SELECT segment, 'mens',            'purchased',           mens,          'binary'     FROM indicators
    UNION ALL SELECT segment, 'womens',          'purchased',           womens,        'binary'     FROM indicators
    UNION ALL SELECT segment, 'newbie',          'new customer',        newbie,        'binary'     FROM indicators
    UNION ALL SELECT segment, 'zip_code',        'Rural',               zip_rural,     'binary'     FROM indicators
    UNION ALL SELECT segment, 'zip_code',        'Surburban',           zip_surburban, 'binary'     FROM indicators
    UNION ALL SELECT segment, 'zip_code',        'Urban',               zip_urban,     'binary'     FROM indicators
    UNION ALL SELECT segment, 'channel',         'Phone',               chan_phone,    'binary'     FROM indicators
    UNION ALL SELECT segment, 'channel',         'Web',                 chan_web,      'binary'     FROM indicators
    UNION ALL SELECT segment, 'channel',         'Multichannel',        chan_multi,    'binary'     FROM indicators
    UNION ALL SELECT segment, 'history_segment', '1) $0 - $100',        hs_1,          'binary'     FROM indicators
    UNION ALL SELECT segment, 'history_segment', '2) $100 - $200',      hs_2,          'binary'     FROM indicators
    UNION ALL SELECT segment, 'history_segment', '3) $200 - $350',      hs_3,          'binary'     FROM indicators
    UNION ALL SELECT segment, 'history_segment', '4) $350 - $500',      hs_4,          'binary'     FROM indicators
    UNION ALL SELECT segment, 'history_segment', '5) $500 - $750',      hs_5,          'binary'     FROM indicators
    UNION ALL SELECT segment, 'history_segment', '6) $750 - $1,000',    hs_6,          'binary'     FROM indicators
    UNION ALL SELECT segment, 'history_segment', '7) $1,000 +',         hs_7,          'binary'     FROM indicators
)
SELECT
    covariate,
    level,
    kind,
    segment,
    COUNT(*)                          AS n,
    AVG(value)                        AS mean,
    VAR_SAMP(value)                   AS var
FROM long
GROUP BY covariate, level, kind, segment
ORDER BY covariate, level, segment;

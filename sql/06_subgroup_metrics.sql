-- Subgroup metrics: outcomes by (subgroup, arm).
--
-- The eleven subgroups are those fixed in PRE_REGISTRATION.md section 7, and
-- only those. Four families -- prior purchase history, newbie, channel and
-- recency band -- defined entirely from PRE-TREATMENT covariates, so slicing
-- on them is causally legitimate. `history_segment` is deliberately absent as
-- a subgroup; section 7 records why.
--
-- Adding a subgroup here after seeing section 5's results would be the single
-- most damaging thing that could be done to this repository. The list is
-- closed.
--
-- The prior-purchase family has THREE cells, not four: `mens = 0 AND
-- womens = 0` never occurs in this dataset, as the Phase 2 checks established.
--
-- Aggregation only. The interaction tests that section 9.1 requires for any
-- differential claim cannot be computed from grouped means -- they need
-- row-level data, and live in src/targeting.py.
--
-- Run: psql -h localhost -p 5433 -U hillstrom -d hillstrom -f sql/06_subgroup_metrics.sql

WITH labelled AS (
    SELECT
        segment,
        conversion,
        visit,
        spend,
        'prior purchase' AS family_1,
        CASE WHEN mens = 1 AND womens = 1 THEN 'both'
             WHEN mens = 1                THEN 'mens only'
             ELSE                              'womens only' END AS level_1,
        'newbie' AS family_2,
        CASE WHEN newbie = 1 THEN 'newbie' ELSE 'established' END AS level_2,
        'channel' AS family_3,
        channel AS level_3,
        'recency band' AS family_4,
        CASE WHEN recency <= 3 THEN '1-3 months'
             WHEN recency <= 6 THEN '4-6 months'
             ELSE                   '7-12 months' END AS level_4
    FROM customers
),
stacked AS (
    SELECT family_1 AS family, level_1 AS level, segment, conversion, visit, spend FROM labelled
    UNION ALL SELECT family_2, level_2, segment, conversion, visit, spend FROM labelled
    UNION ALL SELECT family_3, level_3, segment, conversion, visit, spend FROM labelled
    UNION ALL SELECT family_4, level_4, segment, conversion, visit, spend FROM labelled
)
SELECT
    family,
    level,
    segment,
    COUNT(*)                AS n,
    SUM(conversion)         AS conversions,
    AVG(conversion)         AS conversion_rate,
    VAR_SAMP(conversion)    AS conversion_var,
    AVG(visit)              AS visit_rate,
    AVG(spend)              AS spend_mean
FROM stacked
GROUP BY family, level, segment
ORDER BY family, level, segment;

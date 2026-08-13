-- Schema for the Hillstrom MineThatData E-Mail Analytics Challenge (2008).
--
-- One row per customer. 64,000 rows exactly. Every customer purchased within
-- the twelve months before the campaign and was randomly assigned to one of
-- three arms.
--
-- Column groups are kept explicit below because the pre-treatment /
-- post-treatment boundary is the single most important fact about this table:
-- conditioning on a post-treatment column breaks the randomisation, so the
-- schema documents which is which rather than leaving it to memory.

DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id     SERIAL PRIMARY KEY,

    -- ---- Treatment assignment -------------------------------------------
    segment         TEXT    NOT NULL
        CONSTRAINT customers_segment_valid
        CHECK (segment IN ('Mens E-Mail', 'Womens E-Mail', 'No E-Mail')),

    -- ---- Pre-treatment covariates ---------------------------------------
    -- All measured over the twelve months BEFORE the campaign. Safe to
    -- condition on, safe to adjust for, safe to define subgroups with.
    recency         INTEGER NOT NULL CHECK (recency >= 0),
    history         NUMERIC(10, 2) NOT NULL CHECK (history >= 0),
    history_segment TEXT    NOT NULL,
    mens            SMALLINT NOT NULL CHECK (mens IN (0, 1)),
    womens          SMALLINT NOT NULL CHECK (womens IN (0, 1)),
    zip_code        TEXT    NOT NULL
        CHECK (zip_code IN ('Rural', 'Surburban', 'Urban')),
    newbie          SMALLINT NOT NULL CHECK (newbie IN (0, 1)),
    channel         TEXT    NOT NULL
        CHECK (channel IN ('Phone', 'Web', 'Multichannel')),

    -- ---- Outcomes (two weeks post-campaign) ------------------------------
    -- NEVER use these to define a subgroup or a filter. They are measured
    -- after treatment, so segmenting on them produces a non-causal
    -- comparison. Phase 6 demonstrates that trap deliberately and labels it.
    visit           SMALLINT NOT NULL CHECK (visit IN (0, 1)),
    conversion      SMALLINT NOT NULL CHECK (conversion IN (0, 1)),
    spend           NUMERIC(10, 2) NOT NULL CHECK (spend >= 0)
);

-- Aggregation happens by arm, and subgroup metrics slice by these covariates.
CREATE INDEX idx_customers_segment         ON customers (segment);
CREATE INDEX idx_customers_history_segment ON customers (history_segment);
CREATE INDEX idx_customers_channel         ON customers (channel);

COMMENT ON TABLE customers IS
    'Hillstrom 2008 e-mail experiment: 64,000 customers, three randomised arms.';
COMMENT ON COLUMN customers.history IS
    'Prior-year spend. Pre-period value of `spend` -- the CUPED covariate.';
COMMENT ON COLUMN customers.spend IS
    'Post-campaign spend over two weeks. Median is zero; severely right-skewed.';

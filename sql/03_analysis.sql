-- =============================================================================
-- 03_analysis.sql
-- Business questions answered on clean_sales. Each result is stored as an
-- agg_* table; src/build_database.py exports every agg_* table to
-- data/processed/ as CSV for Power BI. All outputs are aggregates: no
-- sale-level rows leave the database.
--
-- Medians, not means, throughout: a few luxury sales pull averages up.
-- =============================================================================


-- Q1. What is the median price per m2 in each arrondissement, each year? ------
CREATE OR REPLACE TABLE agg_median_by_arr_year AS
SELECT
    arrondissement,
    year,
    COUNT(*)                                   AS sales,
    ROUND(MEDIAN(price_per_m2))                AS median_price_m2,
    ROUND(QUANTILE_CONT(price_per_m2, 0.25))   AS p25_price_m2,
    ROUND(QUANTILE_CONT(price_per_m2, 0.75))   AS p75_price_m2
FROM clean_sales
GROUP BY arrondissement, year
ORDER BY arrondissement, year;


-- Q2. How did each arrondissement change year over year? (LAG) ----------------
CREATE OR REPLACE TABLE agg_yoy_change AS
SELECT
    arrondissement,
    year,
    median_price_m2,
    LAG(median_price_m2) OVER w                                         AS prev_year_median,
    ROUND(100.0 * (median_price_m2 / LAG(median_price_m2) OVER w - 1), 1) AS yoy_change_pct
FROM agg_median_by_arr_year
WINDOW w AS (PARTITION BY arrondissement ORDER BY year)
ORDER BY arrondissement, year;

-- Paris-wide, for the headline trend.
CREATE OR REPLACE TABLE agg_paris_by_year AS
WITH yearly AS (
    SELECT year, COUNT(*) AS sales, ROUND(MEDIAN(price_per_m2)) AS median_price_m2
    FROM clean_sales
    GROUP BY year
)
SELECT
    year,
    sales,
    median_price_m2,
    ROUND(100.0 * (median_price_m2 / LAG(median_price_m2) OVER (ORDER BY year) - 1), 1) AS yoy_change_pct
FROM yearly
ORDER BY year;


-- Q3. Biggest gainers and losers over the whole period ---------------------------
-- (FIRST_VALUE / LAST_VALUE need the full frame, otherwise LAST_VALUE only sees
-- rows up to the current one.)
CREATE OR REPLACE TABLE agg_period_change AS
WITH first_last AS (
    SELECT DISTINCT
        arrondissement,
        FIRST_VALUE(year)            OVER w AS first_year,
        LAST_VALUE(year)             OVER w AS last_year,
        FIRST_VALUE(median_price_m2) OVER w AS first_median,
        LAST_VALUE(median_price_m2)  OVER w AS last_median
    FROM agg_median_by_arr_year
    WINDOW w AS (PARTITION BY arrondissement ORDER BY year
                 ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
)
SELECT
    arrondissement,
    first_year,
    last_year,
    first_median,
    last_median,
    ROUND(100.0 * (last_median / first_median - 1), 1)                   AS change_pct,
    RANK() OVER (ORDER BY last_median / first_median DESC)               AS rank_gainer,
    RANK() OVER (ORDER BY last_median / first_median ASC)                AS rank_loser
FROM first_last
ORDER BY change_pct DESC;


-- Q4. Rolling 3-month median price per m2, Paris-wide (window frame) -------------
-- The median is computed over all SALES in the current and two previous months,
-- not as an average of monthly medians, which would weight small months too much.
CREATE OR REPLACE TABLE agg_rolling_3m AS
WITH per_sale AS (
    SELECT
        month,
        MEDIAN(price_per_m2) OVER (
            ORDER BY month
            RANGE BETWEEN INTERVAL 2 MONTH PRECEDING AND CURRENT ROW
        ) AS rolling_3m_median
    FROM clean_sales
)
SELECT
    m.month,
    m.sales,
    m.median_price_m2,
    ROUND(ANY_VALUE(p.rolling_3m_median)) AS rolling_3m_median_m2
FROM (
    SELECT month, COUNT(*) AS sales, ROUND(MEDIAN(price_per_m2)) AS median_price_m2
    FROM clean_sales
    GROUP BY month
) AS m
JOIN per_sale AS p USING (month)
GROUP BY m.month, m.sales, m.median_price_m2
ORDER BY m.month;


-- Q5. Is there seasonality in sales volume? (DATE_TRUNC quarter) -----------------
CREATE OR REPLACE TABLE agg_sales_by_quarter AS
SELECT
    quarter,
    YEAR(quarter)                         AS year,
    QUARTER(quarter)                      AS quarter_of_year,
    COUNT(*)                              AS sales,
    ROUND(MEDIAN(price_per_m2))           AS median_price_m2,
    -- Share of the year's sales made in this quarter (25% = no seasonality)
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY YEAR(quarter)), 1) AS share_of_year_pct
FROM clean_sales
GROUP BY quarter
ORDER BY quarter;


-- Q6. Does price per m2 depend on apartment size? (CASE WHEN + PIVOT) ------------
CREATE OR REPLACE TABLE agg_size_bucket AS
WITH bucketed AS (
    SELECT
        *,
        CASE
            WHEN surface_reelle_bati < 20  THEN '1. < 20 m2'
            WHEN surface_reelle_bati < 35  THEN '2. 20-35 m2'
            WHEN surface_reelle_bati < 50  THEN '3. 35-50 m2'
            WHEN surface_reelle_bati < 80  THEN '4. 50-80 m2'
            WHEN surface_reelle_bati < 120 THEN '5. 80-120 m2'
            ELSE                                '6. 120+ m2'
        END AS size_bucket
    FROM clean_sales
)
SELECT size_bucket, year, COUNT(*) AS sales, ROUND(MEDIAN(price_per_m2)) AS median_price_m2
FROM bucketed
GROUP BY size_bucket, year
ORDER BY size_bucket, year;

-- Same result in wide format: one row per size bucket, one column per year.
CREATE OR REPLACE TABLE agg_size_bucket_pivot AS
PIVOT agg_size_bucket
ON year
USING ANY_VALUE(median_price_m2)
GROUP BY size_bucket
ORDER BY size_bucket;


-- Q7. Which arrondissements are the most volatile? (stddev, IQR) -----------------
CREATE OR REPLACE TABLE agg_volatility AS
SELECT
    arrondissement,
    COUNT(*)                                                         AS sales,
    ROUND(MEDIAN(price_per_m2))                                      AS median_price_m2,
    ROUND(STDDEV_SAMP(price_per_m2))                                 AS stddev_price_m2,
    ROUND(QUANTILE_CONT(price_per_m2, 0.75) - QUANTILE_CONT(price_per_m2, 0.25)) AS iqr_price_m2,
    -- IQR relative to the median, so cheap and expensive areas are comparable
    ROUND(100.0 * (QUANTILE_CONT(price_per_m2, 0.75) - QUANTILE_CONT(price_per_m2, 0.25))
          / MEDIAN(price_per_m2), 1)                                 AS iqr_pct_of_median
FROM clean_sales
GROUP BY arrondissement
ORDER BY iqr_pct_of_median DESC;


-- Q8 (areas priced below model predictions) is added after Phase 6.

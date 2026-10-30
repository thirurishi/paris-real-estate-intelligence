-- =============================================================================
-- 02_clean_sales.sql
-- From stg_dvf (one row per lot) to clean_sales (one row per apartment sale).
--
-- Each rule creates its own step table, so the cleaning funnel at the end can
-- count exactly how many sales every rule removes.
--
-- Rules, in order:
--   1. one row per sale (id_mutation)  - the price repeats on every lot row
--   2. nature_mutation = 'Vente'       - drops off-plan, auctions, exchanges
--   3. exactly one apartment           - cellars/parking allowed, flagged;
--                                        houses/commercial premises not allowed
--   4. valid price                     - valeur_fonciere present and > 0
--   5. surface_reelle_bati >= 9 m2     - legal minimum for a home
--   6. valid arrondissement 1-20       - from INSEE code 75101-75120
--   7. outliers removed                - 1st-99th percentile of price per m2,
--                                        computed per arrondissement
-- =============================================================================


-- Step 1: aggregate lots into one row per sale ------------------------------
-- valeur_fonciere is the TOTAL price of the sale, repeated on every lot row
-- (checked in notebook 01: identical on 100% of sales). We take it once with
-- ANY_VALUE and never SUM it. Apartment attributes come from the apartment row.
CREATE OR REPLACE TABLE step1_sales AS
SELECT
    id_mutation,
    ANY_VALUE(date_mutation)                                                 AS date_mutation,
    ANY_VALUE(nature_mutation)                                               AS nature_mutation,
    ANY_VALUE(valeur_fonciere)                                               AS valeur_fonciere,
    ANY_VALUE(code_commune)                                                  AS code_commune,
    COUNT(*)                                                                 AS n_rows,
    COUNT(*) FILTER (WHERE type_local = 'Appartement')                       AS n_apartments,
    COUNT(*) FILTER (WHERE type_local = 'Dépendance')                        AS n_dependencies,
    COUNT(*) FILTER (WHERE type_local IN ('Maison', 'Local industriel. commercial ou assimilé')) AS n_other_buildings,
    MAX(surface_reelle_bati)       FILTER (WHERE type_local = 'Appartement') AS surface_reelle_bati,
    MAX(nombre_pieces_principales) FILTER (WHERE type_local = 'Appartement') AS nombre_pieces_principales,
    MAX(longitude)                 FILTER (WHERE type_local = 'Appartement') AS longitude,
    MAX(latitude)                  FILTER (WHERE type_local = 'Appartement') AS latitude
FROM stg_dvf
GROUP BY id_mutation;


-- Step 2: standard sales only -------------------------------------------------
CREATE OR REPLACE TABLE step2_vente AS
SELECT * FROM step1_sales
WHERE nature_mutation = 'Vente';


-- Step 3: exactly one apartment ------------------------------------------------
-- With 2+ apartments, the single price can't be split between them, so the
-- price per m2 would be meaningless. Cellars and parking are allowed (they come
-- with 45% of apartment sales) but flagged, because they slightly inflate price.
CREATE OR REPLACE TABLE step3_one_apartment AS
SELECT * FROM step2_vente
WHERE n_apartments = 1
  AND n_other_buildings = 0;


-- Step 4: valid price ----------------------------------------------------------
CREATE OR REPLACE TABLE step4_valid_price AS
SELECT * FROM step3_one_apartment
WHERE valeur_fonciere IS NOT NULL
  AND valeur_fonciere > 0;


-- Step 5: realistic surface ----------------------------------------------------
CREATE OR REPLACE TABLE step5_surface AS
SELECT * FROM step4_valid_price
WHERE surface_reelle_bati >= 9;


-- Step 6: arrondissement 1-20, price per m2 computed once per sale -------------
-- INSEE codes 75101..75120 -> 1..20. (Postal codes 75001..75020 and 75116 are
-- a different system; we never use them.)
CREATE OR REPLACE TABLE step6_arrondissement AS
SELECT
    *,
    TRY_CAST(code_commune AS INTEGER) - 75100       AS arrondissement,
    valeur_fonciere / surface_reelle_bati           AS price_per_m2
FROM step5_surface
WHERE TRY_CAST(code_commune AS INTEGER) BETWEEN 75101 AND 75120;


-- Step 7: remove outliers per arrondissement -----------------------------------
-- A single Paris-wide cut-off would be wrong: 12,000 EUR/m2 is normal in the 6th
-- but high in the 19th. So bounds are computed inside each arrondissement.
CREATE OR REPLACE TABLE outlier_bounds AS
SELECT
    arrondissement,
    QUANTILE_CONT(price_per_m2, 0.01) AS p01,
    QUANTILE_CONT(price_per_m2, 0.99) AS p99
FROM step6_arrondissement
GROUP BY arrondissement;

CREATE OR REPLACE TABLE clean_sales AS
SELECT
    s.id_mutation,
    s.date_mutation,
    YEAR(s.date_mutation)                            AS year,
    DATE_TRUNC('month', s.date_mutation)             AS month,
    DATE_TRUNC('quarter', s.date_mutation)           AS quarter,
    s.arrondissement,
    s.code_commune,
    s.valeur_fonciere,
    s.surface_reelle_bati,
    s.nombre_pieces_principales,
    s.price_per_m2,
    s.n_dependencies > 0                             AS has_dependency,
    s.n_dependencies,
    s.longitude,
    s.latitude
FROM step6_arrondissement AS s
JOIN outlier_bounds AS b USING (arrondissement)
WHERE s.price_per_m2 BETWEEN b.p01 AND b.p99;


-- Cleaning funnel ----------------------------------------------------------------
-- Sales remaining after each rule. This table goes in the README.
CREATE OR REPLACE TABLE cleaning_funnel AS
WITH steps AS (
    SELECT 0 AS step_order, 'All sales (distinct id_mutation)'        AS step, (SELECT COUNT(*) FROM step1_sales)          AS sales_remaining
    UNION ALL SELECT 1, 'Standard sales only (Vente)',                          (SELECT COUNT(*) FROM step2_vente)
    UNION ALL SELECT 2, 'Exactly one apartment (cellar/parking allowed)',       (SELECT COUNT(*) FROM step3_one_apartment)
    UNION ALL SELECT 3, 'Price present and > 0',                                (SELECT COUNT(*) FROM step4_valid_price)
    UNION ALL SELECT 4, 'Surface >= 9 m2',                                      (SELECT COUNT(*) FROM step5_surface)
    UNION ALL SELECT 5, 'Valid arrondissement (1-20)',                          (SELECT COUNT(*) FROM step6_arrondissement)
    UNION ALL SELECT 6, 'Price per m2 within 1st-99th pct of its arrondissement', (SELECT COUNT(*) FROM clean_sales)
)
SELECT
    step_order,
    step,
    sales_remaining,
    LAG(sales_remaining) OVER (ORDER BY step_order) - sales_remaining              AS removed,
    ROUND(100.0 * sales_remaining / FIRST_VALUE(sales_remaining) OVER (ORDER BY step_order), 1) AS pct_of_start
FROM steps
ORDER BY step_order;


-- Intermediate step tables are only needed for the funnel.
DROP TABLE step1_sales;
DROP TABLE step2_vente;
DROP TABLE step3_one_apartment;
DROP TABLE step4_valid_price;
DROP TABLE step5_surface;
DROP TABLE step6_arrondissement;

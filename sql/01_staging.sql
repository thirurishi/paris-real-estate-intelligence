-- =============================================================================
-- 01_staging.sql
-- Load the raw DVF CSV files into one typed table: stg_dvf (one row per lot).
--
-- Everything is first read as text (all_varchar) so that a single badly
-- formatted value never stops the load, then cast explicitly with TRY_CAST:
-- a value that cannot be converted becomes NULL instead of crashing.
--
-- Address columns are deliberately NOT loaded: the analysis never needs them,
-- and DVF reuse rules forbid re-identifying people.
--
-- {raw_glob} is replaced by src/build_database.py with the path to data/raw.
-- =============================================================================

CREATE OR REPLACE TABLE stg_dvf AS
SELECT
    id_mutation,
    TRY_CAST(date_mutation AS DATE)                    AS date_mutation,
    TRY_CAST(numero_disposition AS INTEGER)            AS numero_disposition,
    nature_mutation,
    TRY_CAST(valeur_fonciere AS DOUBLE)                AS valeur_fonciere,
    code_commune,
    code_postal,
    type_local,
    TRY_CAST(surface_reelle_bati AS DOUBLE)            AS surface_reelle_bati,
    TRY_CAST(nombre_pieces_principales AS INTEGER)     AS nombre_pieces_principales,
    TRY_CAST(longitude AS DOUBLE)                      AS longitude,
    TRY_CAST(latitude AS DOUBLE)                       AS latitude,
    -- Year of the source file, e.g. .../dvf_75_2023.csv.gz -> 2023
    TRY_CAST(regexp_extract(filename, 'dvf_75_(\d{4})', 1) AS INTEGER) AS source_year
FROM read_csv(
    '{raw_glob}',
    header = true,
    all_varchar = true,
    union_by_name = true,
    filename = true
);

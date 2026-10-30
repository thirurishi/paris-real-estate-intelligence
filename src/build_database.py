"""
Build the DuckDB database from the raw DVF files.

Runs every file in sql/ in alphabetical order (01_staging.sql, 02_clean_sales.sql, ...)
against data/paris_dvf.duckdb, then prints and exports the cleaning funnel.

Usage:
    python src/build_database.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
SQL_DIR = PROJECT_ROOT / "sql"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DB_PATH = PROJECT_ROOT / "data" / "paris_dvf.duckdb"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("build_database")


def run_sql_file(con: duckdb.DuckDBPyConnection, path: Path, params: dict[str, str]) -> None:
    """Run one SQL file, after replacing {placeholders} such as {raw_glob}."""
    sql = path.read_text(encoding="utf-8")
    for key, value in params.items():
        sql = sql.replace("{" + key + "}", value)
    logger.info("RUN      %s", path.name)
    con.execute(sql)


def main() -> int:
    raw_files = sorted(RAW_DIR.glob("dvf_75_*.csv.gz"))
    if not raw_files:
        logger.error("No raw files in %s. Run python src/download_data.py first.", RAW_DIR)
        return 1
    logger.info("Raw files: %s", [f.name for f in raw_files])

    # DuckDB accepts forward slashes on Windows too, and they avoid escaping issues.
    params = {"raw_glob": (RAW_DIR / "dvf_75_*.csv.gz").as_posix()}

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB_PATH)) as con:
        for sql_file in sorted(SQL_DIR.glob("*.sql")):
            run_sql_file(con, sql_file, params)

        for table in ["stg_dvf", "clean_sales"]:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            logger.info("TABLE    %-12s %10s rows", table, f"{n:,}")

        funnel = con.execute("SELECT * FROM cleaning_funnel ORDER BY step_order").df()
        funnel.to_csv(PROCESSED_DIR / "cleaning_funnel.csv", index=False)
        print("\nCleaning funnel\n" + funnel.to_string(index=False))

        # Quick sanity check on the result, to decide whether the outlier rule is strict enough.
        check = con.execute("""
            SELECT
                ROUND(MIN(price_per_m2))                      AS min_m2,
                ROUND(QUANTILE_CONT(price_per_m2, 0.01))      AS p01_m2,
                ROUND(MEDIAN(price_per_m2))                   AS median_m2,
                ROUND(QUANTILE_CONT(price_per_m2, 0.99))      AS p99_m2,
                ROUND(MAX(price_per_m2))                      AS max_m2,
                COUNT(*) FILTER (WHERE price_per_m2 < 3000)   AS sales_below_3000_m2,
                ROUND(100 * AVG(has_dependency::INT), 1)      AS pct_with_dependency
            FROM clean_sales
        """).df()
        print("\nclean_sales price per m2 (EUR)\n" + check.to_string(index=False))

    logger.info("Database written to %s", DB_PATH)
    logger.info("Funnel exported to %s", PROCESSED_DIR / "cleaning_funnel.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Geographic features for each clean sale, stored in DuckDB as sales_features.

For every sale in clean_sales:
  - dist_metro_m    : distance in metres to the nearest metro or RER station
  - nearest_station : name of that station
  - dist_centre_m   : distance in metres to the centre of Paris (point zero,
                      in front of Notre-Dame)

Distances are computed in Lambert-93 (EPSG:2154), the official French projected
coordinate system, where units are metres. Computing distances directly on
latitude/longitude would mix degrees and give wrong results.

Usage:
    python src/features.py      (after python src/build_database.py)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "paris_dvf.duckdb"
STATIONS_PATH = PROJECT_ROOT / "data" / "external" / "stations_idf.geojson"

WGS84 = "EPSG:4326"       # latitude / longitude
LAMBERT93 = "EPSG:2154"   # metres, France
PARIS_CENTRE = (2.3488, 48.8534)  # point zero des routes de France (lon, lat)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
logger = logging.getLogger("features")


def find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first column of df whose lowercase name is in candidates."""
    lower = {c.lower(): c for c in df.columns}
    return next((lower[c] for c in candidates if c in lower), None)


def load_metro_rer_stations(path: Path = STATIONS_PATH) -> gpd.GeoDataFrame:
    """Load Ile-de-France stations and keep metro and RER only, in Lambert-93."""
    stations = gpd.read_file(path)
    mode_col = find_column(stations, ["mode", "mode_"])
    if mode_col is not None:
        mode = stations[mode_col].astype(str).str.upper()
        stations = stations[mode.str.contains("METRO|RER")]
    else:
        logger.warning("No 'mode' column in stations file: keeping all stations")

    name_col = find_column(stations, ["nom_long", "nom_gares", "nom_zdc", "nom", "name"])
    stations = stations.assign(station_name=stations[name_col] if name_col else "unknown")
    stations = stations[["station_name", "geometry"]].to_crs(LAMBERT93)
    logger.info("Metro/RER station entries: %s", f"{len(stations):,}")
    return stations


def add_geo_features(sales: pd.DataFrame, stations: gpd.GeoDataFrame) -> pd.DataFrame:
    """Add dist_metro_m, nearest_station and dist_centre_m to a DataFrame with longitude/latitude."""
    points = gpd.GeoDataFrame(
        sales[["id_mutation"]],
        geometry=gpd.points_from_xy(sales["longitude"], sales["latitude"]),
        crs=WGS84,
    ).to_crs(LAMBERT93)

    # Nearest station for every sale (spatial index, fast even for 140k points).
    nearest = gpd.sjoin_nearest(points, stations, how="left", distance_col="dist_metro_m")
    # A sale exactly equidistant to two stations appears twice: keep one row.
    nearest = nearest[~nearest.index.duplicated(keep="first")]

    centre = gpd.GeoSeries(gpd.points_from_xy([PARIS_CENTRE[0]], [PARIS_CENTRE[1]]), crs=WGS84).to_crs(LAMBERT93).iloc[0]

    return pd.DataFrame({
        "id_mutation": nearest["id_mutation"],
        "dist_metro_m": nearest["dist_metro_m"].round(0),
        "nearest_station": nearest["station_name"],
        "dist_centre_m": points.distance(centre).round(0),
    })


def main() -> int:
    if not STATIONS_PATH.exists():
        logger.error("%s not found. Run python src/download_data.py first.", STATIONS_PATH)
        return 1

    with duckdb.connect(str(DB_PATH)) as con:
        sales = con.execute("""
            SELECT id_mutation, longitude, latitude
            FROM clean_sales
            WHERE longitude IS NOT NULL AND latitude IS NOT NULL
        """).df()
        logger.info("Sales with coordinates: %s", f"{len(sales):,}")

        features = add_geo_features(sales, load_metro_rer_stations())

        con.register("features_df", features)
        con.execute("CREATE OR REPLACE TABLE sales_features AS SELECT * FROM features_df")
        summary = con.execute("""
            SELECT
                COUNT(*)                                   AS sales,
                ROUND(MEDIAN(dist_metro_m))                AS median_dist_metro_m,
                ROUND(QUANTILE_CONT(dist_metro_m, 0.9))    AS p90_dist_metro_m,
                ROUND(MAX(dist_metro_m))                   AS max_dist_metro_m,
                ROUND(MEDIAN(dist_centre_m))               AS median_dist_centre_m
            FROM sales_features
        """).df()
    print("\nsales_features\n" + summary.to_string(index=False))
    logger.info("Table sales_features written to %s", DB_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())

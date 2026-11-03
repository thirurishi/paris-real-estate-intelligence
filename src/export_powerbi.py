"""
Export the tables for the Power BI dashboard into data/powerbi/ (git-ignored).

Star schema:
  - Sales.csv          : fact table, one row per clean sale. No id, no address and
                         no coordinates, so rows can't be linked back to a property.
  - Arrondissement.csv : dimension, one row per arrondissement (names match the
                         TopoJSON map in dashboard/arrondissements_map.json)
  - The Date table is created in Power BI with DAX (see dashboard/POWERBI_GUIDE.md).

Model results (metrics, SHAP importance, gap by arrondissement) are read directly
from reports/ and data/processed/.

Usage:
    python src/export_powerbi.py      (after train_model.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import geopandas as gpd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "paris_dvf.duckdb"
OUT_DIR = PROJECT_ROOT / "data" / "powerbi"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        sales = con.execute("""
            SELECT
                s.date_mutation,
                s.arrondissement,
                s.valeur_fonciere                           AS price_eur,
                s.surface_reelle_bati                       AS surface_m2,
                s.nombre_pieces_principales                 AS rooms,
                ROUND(s.price_per_m2)                       AS price_per_m2,
                s.has_dependency,
                CASE
                    WHEN s.surface_reelle_bati < 20  THEN '1. < 20 m2'
                    WHEN s.surface_reelle_bati < 35  THEN '2. 20-35 m2'
                    WHEN s.surface_reelle_bati < 50  THEN '3. 35-50 m2'
                    WHEN s.surface_reelle_bati < 80  THEN '4. 50-80 m2'
                    WHEN s.surface_reelle_bati < 120 THEN '5. 80-120 m2'
                    ELSE                                  '6. 120+ m2'
                END                                         AS size_bucket,
                ROUND(f.dist_metro_m)                       AS dist_metro_m,
                ROUND(f.dist_centre_m)                      AS dist_centre_m,
                ROUND(p.predicted_price_m2)                 AS predicted_price_m2
            FROM clean_sales AS s
            LEFT JOIN sales_features    AS f USING (id_mutation)
            LEFT JOIN model_predictions AS p USING (id_mutation)
            ORDER BY s.date_mutation
        """).df()
    sales.to_csv(OUT_DIR / "Sales.csv", index=False)

    arr = gpd.read_file(PROJECT_ROOT / "data" / "external" / "arrondissements.geojson")
    dim = (arr.rename(columns={"c_ar": "arrondissement", "l_ar": "map_name", "l_aroff": "quartier"})
              [["arrondissement", "map_name", "quartier", "surface"]]
              .assign(label=lambda d: d["map_name"].str.replace(" Ardt", "") + " - " + d["quartier"],
                      surface_km2=lambda d: (d["surface"] / 1e6).round(2),
                      bank=lambda d: d["arrondissement"].map(lambda a: "Left bank" if a in (5, 6, 7, 13, 14, 15) else "Right bank"))
              .drop(columns="surface")
              .sort_values("arrondissement"))
    dim.to_csv(OUT_DIR / "Arrondissement.csv", index=False)

    print(f"Sales.csv          {len(sales):,} rows")
    print(f"Arrondissement.csv {len(dim)} rows")
    print(f"Written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

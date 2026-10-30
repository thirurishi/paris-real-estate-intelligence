"""
Train and compare price-per-m2 models on clean_sales + sales_features.

Models, from simplest to most complex:
  1. Baseline        : median price per m2 of the arrondissement (training data)
  2. Linear regression
  3. Random Forest
  4. XGBoost          (light tuning with time-series cross-validation)

Target: log(price per m2). Prices are right-skewed; the log makes errors
relative (10% off is equally bad at 8,000 and 15,000 EUR/m2). Predictions are
converted back to EUR/m2 before computing metrics.

Split: TIME-BASED. The last 12 months are the test set, everything before is
training. A random split would leak the future into training: the model would
learn 2025 prices from 2025 neighbours and look better than it will be when
pricing a new sale.

Outputs:
  - reports/model_metrics.csv       : MAE, MAPE, R2 for every model
  - models/xgb_price_m2.json        : the XGBoost model (used by the SHAP notebook)
  - DuckDB table model_predictions  : test-set predictions (stays local)
  - data/processed/agg_model_gap_by_arr.csv : actual vs predicted by arrondissement

Usage:
    python src/train_model.py      (after build_database.py and features.py)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "paris_dvf.duckdb"
MODEL_DIR = PROJECT_ROOT / "models"
REPORTS_DIR = PROJECT_ROOT / "reports"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

RANDOM_STATE = 42
TEST_MONTHS = 12

NUMERIC_FEATURES = [
    "surface_reelle_bati", "nombre_pieces_principales", "latitude", "longitude",
    "dist_metro_m", "dist_centre_m", "has_dependency", "months_since_start",
]
CATEGORICAL_FEATURES = ["arrondissement"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
TARGET = "price_per_m2"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
logger = logging.getLogger("train_model")


def load_dataset(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per clean sale with all model features."""
    df = con.execute("""
        SELECT
            s.id_mutation, s.date_mutation, s.arrondissement,
            s.surface_reelle_bati, s.nombre_pieces_principales,
            s.latitude, s.longitude, f.dist_metro_m, f.dist_centre_m,
            CAST(s.has_dependency AS INTEGER) AS has_dependency,
            s.price_per_m2
        FROM clean_sales AS s
        JOIN sales_features AS f USING (id_mutation)
        WHERE s.nombre_pieces_principales IS NOT NULL
    """).df()
    df["date_mutation"] = pd.to_datetime(df["date_mutation"])
    start = df["date_mutation"].min()
    df["months_since_start"] = (
        (df["date_mutation"].dt.year - start.year) * 12 + (df["date_mutation"].dt.month - start.month)
    )
    return df.sort_values("date_mutation").reset_index(drop=True)


def time_split(df: pd.DataFrame, test_months: int = TEST_MONTHS) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    """Train on everything before the last `test_months` months, test on those months."""
    cutoff = (df["date_mutation"].max() - pd.DateOffset(months=test_months)).normalize()
    train = df[df["date_mutation"] <= cutoff]
    test = df[df["date_mutation"] > cutoff]
    return train, test, cutoff


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "MAE_eur_m2": round(mean_absolute_error(y_true, y_pred), 0),
        "MAPE_pct": round(100 * mean_absolute_percentage_error(y_true, y_pred), 2),
        "R2": round(r2_score(y_true, y_pred), 3),
    }


def make_linear() -> Pipeline:
    pre = ColumnTransformer([
        ("num", StandardScaler(), NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
    ])
    return Pipeline([("pre", pre), ("model", LinearRegression())])


def make_random_forest() -> RandomForestRegressor:
    return RandomForestRegressor(n_estimators=300, min_samples_leaf=5, max_features=0.5,
                                 n_jobs=-1, random_state=RANDOM_STATE)


def tune_xgboost(X: pd.DataFrame, y: pd.Series) -> XGBRegressor:
    """Light random search, validated with TimeSeriesSplit so each fold also respects time."""
    search = RandomizedSearchCV(
        XGBRegressor(objective="reg:squarederror", tree_method="hist", n_jobs=-1, random_state=RANDOM_STATE),
        param_distributions={
            "n_estimators": [300, 500, 800],
            "max_depth": [4, 6, 8],
            "learning_rate": [0.03, 0.05, 0.1],
            "subsample": [0.7, 0.85, 1.0],
            "colsample_bytree": [0.7, 0.85, 1.0],
            "min_child_weight": [1, 5, 10],
        },
        n_iter=12,
        cv=TimeSeriesSplit(n_splits=3),
        scoring="neg_mean_absolute_error",
        random_state=RANDOM_STATE,
        n_jobs=1,
    )
    search.fit(X, y)
    logger.info("XGBoost best params: %s", search.best_params_)
    return search.best_estimator_


def main() -> int:
    with duckdb.connect(str(DB_PATH)) as con:
        df = load_dataset(con)
        train, test, cutoff = time_split(df)
        logger.info("Rows: %s | train: %s (until %s) | test: %s (after)",
                    f"{len(df):,}", f"{len(train):,}", cutoff.date(), f"{len(test):,}")

        X_train, X_test = train[FEATURES], test[FEATURES]
        y_train_log = np.log(train[TARGET])
        y_test = test[TARGET].to_numpy()

        results, predictions = [], {}

        # 1. Baseline: arrondissement median from the training period.
        arr_median = train.groupby("arrondissement")[TARGET].median()
        predictions["Baseline (arrondissement median)"] = test["arrondissement"].map(arr_median).to_numpy()

        # 2-4. Models trained on log(price per m2).
        models = {
            "Linear regression": make_linear(),
            "Random Forest": make_random_forest(),
        }
        for name, model in models.items():
            logger.info("TRAIN    %s", name)
            model.fit(X_train, y_train_log)
            predictions[name] = np.exp(model.predict(X_test))

        logger.info("TUNE     XGBoost (time-series CV, 12 candidates x 3 folds)")
        xgb = tune_xgboost(X_train, y_train_log)
        predictions["XGBoost"] = np.exp(xgb.predict(X_test))

        for name, y_pred in predictions.items():
            results.append({"model": name, **metrics(y_test, y_pred)})
        metrics_df = pd.DataFrame(results)
        REPORTS_DIR.mkdir(exist_ok=True)
        metrics_df.to_csv(REPORTS_DIR / "model_metrics.csv", index=False)
        print("\nTest set: last 12 months\n" + metrics_df.to_string(index=False))

        MODEL_DIR.mkdir(exist_ok=True)
        xgb.save_model(MODEL_DIR / "xgb_price_m2.json")

        # Test-set predictions stay in the local database (sale-level data is never exported).
        preds = pd.DataFrame({
            "id_mutation": test["id_mutation"].to_numpy(),
            "arrondissement": test["arrondissement"].to_numpy(),
            "actual_price_m2": y_test,
            "predicted_price_m2": predictions["XGBoost"],
        })
        con.register("preds_df", preds)
        con.execute("CREATE OR REPLACE TABLE model_predictions AS SELECT * FROM preds_df")

        # Q8: where do actual prices sit below what the model expects?
        gap = con.execute("""
            SELECT
                arrondissement,
                COUNT(*)                                                         AS test_sales,
                ROUND(MEDIAN(actual_price_m2))                                   AS median_actual_m2,
                ROUND(MEDIAN(predicted_price_m2))                                AS median_predicted_m2,
                ROUND(100 * MEDIAN(actual_price_m2 / predicted_price_m2 - 1), 1) AS median_gap_pct,
                ROUND(100 * AVG(CAST(actual_price_m2 < predicted_price_m2 AS INTEGER)), 1) AS pct_sales_below_prediction
            FROM model_predictions
            GROUP BY arrondissement
            ORDER BY median_gap_pct
        """).df()
        con.execute("CREATE OR REPLACE TABLE agg_model_gap_by_arr AS SELECT * FROM gap")
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    gap.to_csv(PROCESSED_DIR / "agg_model_gap_by_arr.csv", index=False)
    print("\nActual vs predicted by arrondissement (negative gap = cheaper than the model expects)\n"
          + gap.to_string(index=False))
    logger.info("Saved reports/model_metrics.csv, models/xgb_price_m2.json, data/processed/agg_model_gap_by_arr.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())

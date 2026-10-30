# Power BI dashboard: step-by-step guide

Four pages built on a star schema: fact `Sales`, dimensions `Date` and `Arrondissement`, plus three small model tables.
Time needed: about 2 to 3 hours the first time.

---

## 0. Prepare the data (terminal, in the project folder, with `.venv` active)

```
git pull
python src/export_powerbi.py
```

This writes `data/powerbi/Sales.csv` and `data/powerbi/Arrondissement.csv`. That folder is git-ignored: the dashboard data stays on your computer.

---

## 1. Load the tables

Open **Power BI Desktop** → **Get data** → **Text/CSV**, and load each file below. For each one, click **Transform Data** if a type needs fixing, then **Close & Apply**.

| Table name in Power BI | File | Check these types |
|---|---|---|
| `Sales` | `data/powerbi/Sales.csv` | `date_mutation` = Date, `has_dependency` = True/False, `price_per_m2` and `predicted_price_m2` = Decimal number |
| `Arrondissement` | `data/powerbi/Arrondissement.csv` | `arrondissement` = Whole number |
| `Model Metrics` | `reports/model_metrics.csv` | |
| `SHAP Importance` | `data/processed/agg_shap_importance.csv` | |
| `Model Gap` | `data/processed/agg_model_gap_by_arr.csv` | |

> If Power BI reads decimals wrongly (for example `10345.0` becoming `103450`), it's the French locale. In **Transform Data**, right-click the column → **Change Type** → **Using Locale…** → Decimal number, **English (United States)**.

---

## 2. Create the Date table

**Modeling** tab → **New table**, paste:

```DAX
Date =
ADDCOLUMNS (
    CALENDAR (
        DATE ( YEAR ( MIN ( Sales[date_mutation] ) ), 1, 1 ),
        DATE ( YEAR ( MAX ( Sales[date_mutation] ) ), 12, 31 )
    ),
    "Year", YEAR ( [Date] ),
    "Quarter", "Q" & QUARTER ( [Date] ),
    "Year Quarter", YEAR ( [Date] ) & " Q" & QUARTER ( [Date] ),
    "Month Number", MONTH ( [Date] ),
    "Month", FORMAT ( [Date], "mmm yyyy" ),
    "Month Start", DATE ( YEAR ( [Date] ), MONTH ( [Date] ), 1 )
)
```

Then select the `Date` table → **Table tools** → **Mark as date table** → column `Date`.
Select the `Month` column → **Sort by column** → `Month Start`, so months sort in calendar order.

---

## 3. Relationships (Model view, the third icon on the left)

Drag to create:

| From | To | Cardinality |
|---|---|---|
| `Sales[date_mutation]` | `Date[Date]` | Many to one, single direction |
| `Sales[arrondissement]` | `Arrondissement[arrondissement]` | Many to one, single direction |
| `Model Gap[arrondissement]` | `Arrondissement[arrondissement]` | Many to one, single direction |

`Model Metrics` and `SHAP Importance` stay unconnected: they are model-level tables.

*Why a star schema:* each filter (year, arrondissement) lives in one small dimension table and flows to the fact table. It's faster, avoids ambiguous filters, and makes time-intelligence DAX like `SAMEPERIODLASTYEAR` work.

---

## 4. Measures

Create a table to hold them: **Home** → **Enter data** → name it `_Measures` → **Load**. Then select it and use **New measure** for each:

```DAX
Median Price m2 = MEDIAN ( Sales[price_per_m2] )

Sales Count = COUNTROWS ( Sales )

Median Price m2 LY = CALCULATE ( [Median Price m2], SAMEPERIODLASTYEAR ( 'Date'[Date] ) )

YoY % = DIVIDE ( [Median Price m2] - [Median Price m2 LY], [Median Price m2 LY] )

Sales Count LY = CALCULATE ( [Sales Count], SAMEPERIODLASTYEAR ( 'Date'[Date] ) )

Sales YoY % = DIVIDE ( [Sales Count] - [Sales Count LY], [Sales Count LY] )

Median Surface m2 = MEDIAN ( Sales[surface_m2] )

Median Price = MEDIAN ( Sales[price_eur] )

Rolling 3M Median m2 =
CALCULATE (
    [Median Price m2],
    DATESINPERIOD ( 'Date'[Date], MAX ( 'Date'[Date] ), -3, MONTH )
)

Median Predicted m2 = MEDIAN ( Sales[predicted_price_m2] )

Actual vs Predicted % = DIVIDE ( [Median Price m2] - [Median Predicted m2], [Median Predicted m2] )

Share With Cellar or Parking = DIVIDE ( CALCULATE ( [Sales Count], Sales[has_dependency] = TRUE () ), [Sales Count] )
```

Formatting: select each measure, then in **Measure tools** set `€` currency with 0 decimals for prices, and **Percentage** with 1 decimal for the `%` measures.

---

## 5. Page 1 — Overview

| Visual | Fields |
|---|---|
| 4 **Card** visuals in a row | `Median Price m2`, `YoY %`, `Sales Count`, `Median Surface m2` |
| **Line chart** (full width) | X axis `Date[Month Start]`, Y axis `Median Price m2` and `Rolling 3M Median m2` |
| **Clustered column chart** | X axis `Date[Year]`, Y axis `Sales Count` |
| **Slicer** (top right) | `Date[Year]`, style **Tile** |

Title text box: *"Paris apartment prices fell 11% from 2021 to 2024 and stabilised in 2025"*. Use your real headline from notebook 02.

---

## 6. Page 2 — Map

Power BI's **Shape map** is a preview feature. Turn it on once: **File** → **Options and settings** → **Options** → **Preview features** → tick **Shape map visual** → restart Power BI.

1. Add a **Shape map** visual.
2. **Location:** `Arrondissement[map_name]`. **Color saturation:** `Median Price m2`.
3. Format pane → **Map settings** → **Map type: Custom map** → **Add a map type** → choose `dashboard/arrondissements.topojson` from the project.
4. Click **View map keys** and check that the 20 names match. The map file already uses the same names as `map_name`.
5. **Default color** → set a gradient: minimum light blue, maximum dark blue.

Next to the map:
- **Table**: `Arrondissement[label]`, `Median Price m2`, `YoY %`, `Sales Count`. Sort by `Median Price m2` descending, and add **Conditional formatting** → Data bars on `Median Price m2`.
- **Slicer**: `Date[Year]`.

---

## 7. Page 3 — Deep dive

| Visual | Fields |
|---|---|
| **Clustered column chart** | X `Sales[size_bucket]`, Y `Median Price m2` |
| **Clustered column chart** | X `Sales[rooms]`, Y `Median Price m2` (filter rooms ≤ 6 in the Filters pane) |
| **Clustered column chart** | X `Date[Quarter]`, Y `Sales Count` |
| **Matrix** | Rows `Arrondissement[label]`, Columns `Date[Year]`, Values `Median Price m2`. Format → Cell elements → Background color, gradient |
| **Slicers** | `Arrondissement[label]` (dropdown), `Arrondissement[bank]`, `Date[Year]`, `Sales[has_dependency]` |

---

## 8. Page 4 — Model insights

| Visual | Fields |
|---|---|
| **Table** | `Model Metrics`: `model`, `MAE_eur_m2`, `MAPE_pct`, `R2` |
| **Clustered bar chart** | Y `SHAP Importance[feature]`, X `SHAP Importance[approx_avg_effect_pct]`, sorted descending. Title: *"What drives the price per m² (average SHAP effect, %)"* |
| **Scatter chart** | X `Median Predicted m2`, Y `Median Price m2`, Values `Arrondissement[label]`. Filter pane: `Sales[predicted_price_m2]` **is not blank** (test period only) |
| **Clustered bar chart** | Y `Arrondissement[label]`, X `Model Gap[median_gap_pct]`. Title: *"Actual vs predicted, last 12 months (%)"* |
| **Image** | `reports/figures/shap_summary.png` (Insert → Image) |

Add a text box with the limitation: *"DVF has no floor, condition, balcony or energy rating; R² 0.44 on a strict time-based test."*

---

## 9. Finishing touches

- **Theme:** View → Themes → pick a simple one (for example *Executive*), and use the same blue across pages.
- **Page names:** Overview, Map, Deep dive, Model insights.
- **Sync slicers** for `Date[Year]`: View → Sync slicers, tick all pages.

## 10. Save and publish to GitHub

1. Save as `dashboard/paris_real_estate.pbix`. It contains the sale-level data, so it is git-ignored and stays on your computer.
2. **File → Export → Power BI template** → save as `dashboard/paris_real_estate.pbit`. A template contains the model, DAX and layout but **no data**, so it's safe to publish.
3. Take one screenshot per page (Windows: `Win + Shift + S`) and save them as:
   `dashboard/screenshots/1_overview.png`, `2_map.png`, `3_deep_dive.png`, `4_model.png`
4. Push:
   ```
   git add dashboard
   git commit -m "Phase 7: Power BI dashboard template and screenshots"
   git push
   ```

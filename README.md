# Paris Real Estate Intelligence

What drives apartment prices in Paris, how have they moved by arrondissement, and which areas look under- or over-priced?

Analysis of 5 years of official French property transactions (DVF géolocalisées, département 75) with SQL (DuckDB), geospatial analysis, explainable machine learning (SHAP) and a Power BI dashboard.

> 🚧 Work in progress. Findings, maps and model results will be added as each phase is completed.

## Project status

- [x] Phase 1: project setup and reproducible data download
- [ ] Phase 2: raw data exploration
- [ ] Phase 3: SQL cleaning in DuckDB
- [ ] Phase 4: SQL analysis
- [ ] Phase 5: geospatial analysis and maps
- [ ] Phase 6: price model and SHAP explanations
- [ ] Phase 7: Power BI dashboard and final write-up

## How to run

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python src/download_data.py
```

Raw data is not stored in this repository. `src/download_data.py` downloads it from
[data.gouv.fr](https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres-geolocalisees/) into `data/raw/`.

## Data

Source: *Demandes de valeurs foncières géolocalisées* (DGFiP, Etalab), published under the Licence Ouverte.
Only aggregated results are published here, in line with DVF reuse rules.

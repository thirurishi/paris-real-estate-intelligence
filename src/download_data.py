"""
Download DVF géolocalisées files for Paris (département 75) into data/raw/.

DVF (Demandes de Valeurs Foncières) lists every property sale in France.
The "géolocalisées" version published by Etalab adds latitude/longitude to each row.
Files are organised as:  <BASE_URL>/<year>/departements/<dept>.csv.gz

By default the script detects which years are online and downloads the last
5 COMPLETE years (the most recent year is usually partial, so it is skipped).

It also downloads two small reference files into data/external/ for the maps:
  - arrondissements.geojson : arrondissement boundaries (opendata.paris.fr, ODbL)
  - stations_idf.geojson    : rail stations of Ile-de-France incl. metro and RER
                              (Ile-de-France Mobilites open data, ODbL)

Usage:
    python src/download_data.py                     # last 5 complete years
    python src/download_data.py --years 2021 2022   # specific years
    python src/download_data.py --force             # re-download existing files
"""

from __future__ import annotations

import argparse
import gzip
import logging
import re
import sys
from datetime import date
from pathlib import Path

import requests
from tqdm import tqdm

# --- Configuration -----------------------------------------------------------

BASE_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv"
DEPARTEMENT = "75"  # Paris
N_YEARS = 5

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
EXTERNAL_DIR = PROJECT_ROOT / "data" / "external"
LOG_DIR = PROJECT_ROOT / "logs"

EXTERNAL_FILES = {
    "arrondissements.geojson":
        "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/arrondissements/exports/geojson",
    "stations_idf.geojson":
        "https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/emplacement-des-gares-idf/exports/geojson",
}

TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1024 * 1024  # 1 MB

logger = logging.getLogger("download_data")


# --- Helpers -----------------------------------------------------------------

def setup_logging() -> None:
    """Log to the console and to logs/download_data.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file_handler = logging.FileHandler(LOG_DIR / "download_data.log", encoding="utf-8")
    file_handler.setFormatter(fmt)

    logger.setLevel(logging.INFO)
    logger.addHandler(console)
    logger.addHandler(file_handler)


def file_url(year: int) -> str:
    return f"{BASE_URL}/{year}/departements/{DEPARTEMENT}.csv.gz"


def year_is_online(session: requests.Session, year: int) -> bool:
    """Check that the Paris file exists for a given year without downloading it."""
    try:
        with session.get(file_url(year), stream=True, timeout=TIMEOUT_SECONDS) as response:
            return response.status_code == 200
    except requests.RequestException:
        return False


def available_years(session: requests.Session) -> list[int]:
    """Return the years published on data.gouv.fr.

    First try to read the folder listing. Its HTML format has changed before,
    so if no year is found there, fall back to checking the file URL directly
    for each of the last 10 years.
    """
    try:
        response = session.get(f"{BASE_URL}/", timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        # Accept relative or absolute links: href="2023/", href="./2023/", href="/geo-dvf/.../2023/"
        years = sorted({int(y) for y in re.findall(r'href="[^"]*?\b(20\d{2})/?"', response.text)})
        if years:
            return years
        logger.warning("Folder listing returned no years, checking file URLs one by one instead")
    except requests.RequestException as exc:
        logger.warning("Could not read folder listing (%s), checking file URLs one by one instead", exc)

    this_year = date.today().year
    years = [y for y in range(this_year - 9, this_year + 1) if year_is_online(session, y)]
    if not years:
        raise RuntimeError(
            f"No DVF file found under {BASE_URL}/. Check the site in a browser, "
            "or pass years explicitly: python src/download_data.py --years 2021 2022 2023 2024 2025"
        )
    return years


def pick_last_complete_years(years: list[int], n: int) -> list[int]:
    """Keep the last n years, excluding the current calendar year (always partial)."""
    complete = [y for y in years if y < date.today().year]
    return complete[-n:]


def is_valid_gzip(path: Path) -> bool:
    """Check that a .gz file can be fully read (catches interrupted downloads)."""
    try:
        with gzip.open(path, "rb") as f:
            while f.read(CHUNK_SIZE):
                pass
        return True
    except (OSError, EOFError):
        return False


def download_file(session: requests.Session, url: str, destination: Path) -> None:
    """Stream a file to disk via a temporary .part file, then rename it.

    Writing to .part first means a crash never leaves a half-written file
    under the final name, so skip-if-exists stays safe.
    """
    tmp_path = destination.with_suffix(destination.suffix + ".part")
    with session.get(url, stream=True, timeout=TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        with open(tmp_path, "wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc=destination.name, leave=False
        ) as bar:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)
                bar.update(len(chunk))
    tmp_path.replace(destination)


# --- Main --------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Download DVF géolocalisées for Paris.")
    parser.add_argument("--years", type=int, nargs="+", help="Years to download (default: last 5 complete years).")
    parser.add_argument("--force", action="store_true", help="Re-download files that already exist.")
    args = parser.parse_args()

    setup_logging()
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    with requests.Session() as session:
        if args.years:
            years = sorted(args.years)
        else:
            online = available_years(session)
            logger.info("Years available online: %s", online)
            years = pick_last_complete_years(online, N_YEARS)
        logger.info("Years to download: %s", years)

        failures: list[int | str] = []
        for year in years:
            url = file_url(year)
            destination = RAW_DIR / f"dvf_{DEPARTEMENT}_{year}.csv.gz"

            if destination.exists() and not args.force:
                if is_valid_gzip(destination):
                    logger.info("SKIP     %s already exists (%.1f MB)", destination.name, destination.stat().st_size / 1e6)
                    continue
                logger.warning("Corrupted file %s, downloading again", destination.name)

            try:
                logger.info("DOWNLOAD %s", url)
                download_file(session, url, destination)
                logger.info("OK       %s (%.1f MB)", destination.name, destination.stat().st_size / 1e6)
            except requests.RequestException as exc:
                logger.error("FAILED   %s: %s", url, exc)
                failures.append(year)

        EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
        for name, url in EXTERNAL_FILES.items():
            destination = EXTERNAL_DIR / name
            if destination.exists() and destination.stat().st_size > 0 and not args.force:
                logger.info("SKIP     %s already exists (%.1f MB)", name, destination.stat().st_size / 1e6)
                continue
            try:
                logger.info("DOWNLOAD %s", url)
                download_file(session, url, destination)
                logger.info("OK       %s (%.1f MB)", name, destination.stat().st_size / 1e6)
            except requests.RequestException as exc:
                logger.error("FAILED   %s: %s", url, exc)
                failures.append(name)

    if failures:
        logger.error("Finished with errors for: %s", failures)
        return 1
    logger.info("All files are in %s", RAW_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())

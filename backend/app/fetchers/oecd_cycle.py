"""Industrial production, OECD leading indicators and comparable core inflation.

The OECD endpoints are queried directly instead of relying on legacy FRED series
that were discontinued during OECD's SDMX migration.  FRED remains useful for
current national industrial-production series and Eurostat HICP distribution.
"""

import io
import time

import pandas as pd
import requests

from app.fetchers.fred_source import _fred_abs, _fred_yoy

OECD_CLI_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.SDD.STES,DSD_STES@DF_CLI,/CHN+USA+JPN+KOR+G4E.M.LI...AA...H"
)
OECD_CORE_1999_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_N_TXCP01_NRG,1.0/USA+KOR.M......"
)
OECD_CORE_2018_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.SDD.TPS,DSD_PRICES_COICOP2018@DF_PRICES_C2018_N_TXCP01_NRG,1.0/JPN.M......"
)
EUROSTAT_IP_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/sts_inpr_m"

_HEADERS = {"User-Agent": "economic-dashboard/1.0"}
_CACHE_TTL = 60 * 60
_cache: dict[str, dict] = {}


def _cached_csv(key: str, url: str) -> pd.DataFrame:
    now = time.time()
    entry = _cache.get(key)
    if entry is None or now - entry["ts"] > _CACHE_TTL:
        response = requests.get(
            url,
            params={"dimensionAtObservation": "AllDimensions", "format": "csvfilewithlabels"},
            headers=_HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        entry = {"df": pd.read_csv(io.StringIO(response.text)), "ts": now}
        _cache[key] = entry
    return entry["df"]


def _oecd_series(dataset: str, url: str, area: str) -> pd.DataFrame:
    raw = _cached_csv(dataset, url)
    out = raw.loc[raw["REF_AREA"] == area, ["TIME_PERIOD", "OBS_VALUE"]].copy()
    out.columns = ["date", "value"]
    out["date"] = pd.to_datetime(out["date"], format="%Y-%m", errors="coerce").dt.date
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    return out.dropna().drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def _make_oecd_fetcher(dataset: str, url: str, area: str):
    def _fetch() -> pd.DataFrame:
        return _oecd_series(dataset, url, area)

    return _fetch


def fetch_eu_industrial_production() -> pd.DataFrame:
    """Euro area industrial production index, SA/WDA, converted to year-on-year."""
    response = requests.get(
        EUROSTAT_IP_URL,
        params={
            "geo": "EA20",
            "s_adj": "SCA",
            "unit": "I21",
            "indic_bt": "PRD",
            "nace_r2": "B-D",
        },
        headers=_HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    time_index = payload["dimension"]["time"]["category"]["index"]
    values = payload.get("value", {})
    rows = [(period, values.get(str(position))) for period, position in time_index.items()]
    out = pd.DataFrame(rows, columns=["date", "value"])
    out["date"] = pd.to_datetime(out["date"], format="%Y-%m", errors="coerce").dt.date
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna().sort_values("date").reset_index(drop=True)
    out["value"] = out["value"].pct_change(12) * 100
    return out.dropna().reset_index(drop=True)


CYCLE_FETCHERS = {
    # Industrial production: all values are monthly year-on-year growth rates.
    "US_IP": lambda: _fred_yoy("INDPRO"),
    "JP_IP": lambda: _fred_abs("JPNPRINTO01GYSAM"),
    "EU_IP": fetch_eu_industrial_production,
    "KR_IP": lambda: _fred_abs("KORPRINTO01GYSAM"),
    # OECD amplitude-adjusted CLI; long-term average = 100.
    "CN_CLI": _make_oecd_fetcher("cli", OECD_CLI_URL, "CHN"),
    "US_CLI": _make_oecd_fetcher("cli", OECD_CLI_URL, "USA"),
    "JP_CLI": _make_oecd_fetcher("cli", OECD_CLI_URL, "JPN"),
    # G4E is an explicit proxy because OECD does not publish a current euro-area CLI.
    "EU_CLI": _make_oecd_fetcher("cli", OECD_CLI_URL, "G4E"),
    "KR_CLI": _make_oecd_fetcher("cli", OECD_CLI_URL, "KOR"),
    # National CPI excluding food and energy, year-on-year.
    "US_CORE_CPI": _make_oecd_fetcher("core_1999", OECD_CORE_1999_URL, "USA"),
    "JP_CORE_CPI": _make_oecd_fetcher("core_2018", OECD_CORE_2018_URL, "JPN"),
    "KR_CORE_CPI": _make_oecd_fetcher("core_1999", OECD_CORE_1999_URL, "KOR"),
    "EU_CORE_CPI": lambda: _fred_yoy("00XEFDEZ19M086NEST"),
}

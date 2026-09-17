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
    "OECD.SDD.STES,DSD_STES@DF_CLI,/CHN+USA+JPN+KOR.M.LI...AA...H"
)
OECD_CORE_1999_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_N_TXCP01_NRG,1.0/USA+KOR.M......"
)
OECD_CORE_2018_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.SDD.TPS,DSD_PRICES_COICOP2018@DF_PRICES_C2018_N_TXCP01_NRG,1.0/JPN.M......"
)
EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"

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


def _eurostat_series(dataset: str, params: dict[str, str], date_format: str) -> pd.DataFrame:
    response = requests.get(
        f"{EUROSTAT_API}/{dataset}",
        params=params,
        headers=_HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    time_index = payload["dimension"]["time"]["category"]["index"]
    values = payload.get("value", {})
    rows = [(period, values.get(str(position))) for period, position in time_index.items()]
    out = pd.DataFrame(rows, columns=["date", "value"])
    if date_format == "quarter":
        parts = out["date"].str.extract(r"^(\d{4})-Q([1-4])$")
        quarter_month = {"1": "01", "2": "04", "3": "07", "4": "10"}
        out["date"] = pd.to_datetime(
            parts[0] + "-" + parts[1].map(quarter_month),
            format="%Y-%m",
            errors="coerce",
        ).dt.date
    else:
        out["date"] = pd.to_datetime(out["date"], format=date_format, errors="coerce").dt.date
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    return out.dropna().sort_values("date").reset_index(drop=True)


def _calendar_month_yoy(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate YoY growth only when the same calendar month exists.

    A positional ``pct_change(12)`` silently compares against a 13-month-old
    observation when one month is missing from the source history.  Indexing by
    calendar month makes that gap explicit instead.
    """
    out = frame[["date", "value"]].copy()
    out["_period"] = pd.to_datetime(out["date"], errors="coerce").dt.to_period("M")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["_period", "value"]).sort_values("date")
    out = out.drop_duplicates("_period", keep="last")
    values_by_period = out.set_index("_period")["value"]
    out["_year_ago"] = (out["_period"] - 12).map(values_by_period)
    out["value"] = (out["value"] / out["_year_ago"] - 1) * 100
    return out.dropna(subset=["value"])[["date", "value"]].reset_index(drop=True)


def fetch_eu_industrial_production() -> pd.DataFrame:
    """EA21 industrial production index, SA/WDA, converted to year-on-year."""
    out = _eurostat_series(
        "sts_inpr_m",
        {
            "geo": "EA21",
            "s_adj": "SCA",
            "unit": "I21",
            "indic_bt": "PRD",
            "nace_r2": "B-D",
        },
        "%Y-%m",
    )
    return _calendar_month_yoy(out)


def fetch_eu_economic_sentiment() -> pd.DataFrame:
    """European Commission ESI for the 21-country euro area, long-run mean=100."""
    return _eurostat_series(
        "ei_bssi_m_r2",
        {
            "geo": "EA21",
            "indic": "BS-ESI-I",
            "s_adj": "SA",
            "sinceTimePeriod": "2000-01",
        },
        "%Y-%m",
    )


def fetch_eu_gdp() -> pd.DataFrame:
    """EA21 real GDP change from the same quarter a year earlier."""
    return _eurostat_series(
        "namq_10_gdp",
        {
            "geo": "EA21",
            "s_adj": "SCA",
            "unit": "CLV_PCH_SM",
            "na_item": "B1GQ",
            "sinceTimePeriod": "2000-Q1",
        },
        "quarter",
    )


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
    # 欧元区使用欧盟委员会ESI，不再用包含英国的“欧洲四国”代理。
    "EU_CLI": fetch_eu_economic_sentiment,
    "EU_GDP": fetch_eu_gdp,
    "KR_CLI": _make_oecd_fetcher("cli", OECD_CLI_URL, "KOR"),
    # National CPI excluding food and energy, year-on-year.
    "US_CORE_CPI": _make_oecd_fetcher("core_1999", OECD_CORE_1999_URL, "USA"),
    "JP_CORE_CPI": _make_oecd_fetcher("core_2018", OECD_CORE_2018_URL, "JPN"),
    "KR_CORE_CPI": _make_oecd_fetcher("core_1999", OECD_CORE_1999_URL, "KOR"),
    "EU_CORE_CPI": lambda: _fred_yoy("00XEFDEZCCM086NEST"),
}

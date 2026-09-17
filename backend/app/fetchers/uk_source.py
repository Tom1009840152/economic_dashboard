"""United Kingdom macro indicators from official UK sources.

ONS time-series pages expose JSON at ``/data`` without an API key.  We use
explicit CDIDs so headline CPI, core CPI, industrial production and GDP keep
their UK definitions and cannot be confused with EU or euro-area aggregates.
"""

from __future__ import annotations

import io
import datetime as dt

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_HEADERS = {"User-Agent": "economic-dashboard/1.0"}
DATA_FLOOR_DATE = dt.date(2000, 1, 1)

_SESSION = requests.Session()
_SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
    ),
)

ONS_SERIES = {
    "cpi": (
        "https://www.ons.gov.uk/economy/inflationandpriceindices/"
        "timeseries/d7g7/mm23/data",
        "months",
    ),
    "core_cpi": (
        "https://www.ons.gov.uk/economy/inflationandpriceindices/"
        "timeseries/dko8/mm23/data",
        "months",
    ),
    "industrial_production": (
        "https://www.ons.gov.uk/economy/economicoutputandproductivity/output/"
        "timeseries/k222/diop/data",
        "months",
    ),
    "gdp": (
        "https://www.ons.gov.uk/economy/grossdomesticproductgdp/"
        "timeseries/ihyr/pn2/data",
        "quarters",
    ),
}


def _ons_series(key: str) -> pd.DataFrame:
    url, frequency = ONS_SERIES[key]
    response = _SESSION.get(url, headers=_HEADERS, timeout=45)
    response.raise_for_status()
    rows = response.json().get(frequency, [])
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError(f"ONS returned no observations for {key}")

    if frequency == "months":
        out["date"] = pd.to_datetime(
            out["year"].astype(str) + " " + out["month"].astype(str),
            format="%Y %B",
            errors="coerce",
        )
    else:
        quarter_month = {"Q1": "01", "Q2": "04", "Q3": "07", "Q4": "10"}
        out["date"] = pd.to_datetime(
            out["year"].astype(str) + "-" + out["quarter"].map(quarter_month),
            format="%Y-%m",
            errors="coerce",
        )

    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["date", "value"])
    out["date"] = out["date"].dt.date
    return (
        out.loc[out["date"] >= DATA_FLOOR_DATE, ["date", "value"]]
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _calendar_month_yoy(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate YoY growth only when the same calendar month exists."""
    out = frame[["date", "value"]].copy()
    out["_period"] = pd.to_datetime(out["date"], errors="coerce").dt.to_period("M")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["_period", "value"]).sort_values("date")
    out = out.drop_duplicates("_period", keep="last")
    values_by_period = out.set_index("_period")["value"]
    out["_year_ago"] = (out["_period"] - 12).map(values_by_period)
    out["value"] = (out["value"] / out["_year_ago"] - 1) * 100
    return out.dropna(subset=["value"])[["date", "value"]].reset_index(drop=True)


def fetch_uk_cpi() -> pd.DataFrame:
    """ONS CPI 12-month rate, not the EU HICP aggregate."""
    return _ons_series("cpi")


def fetch_uk_core_cpi() -> pd.DataFrame:
    """ONS CPI excluding energy, food, alcohol and tobacco, 12-month rate."""
    return _ons_series("core_cpi")


def fetch_uk_industrial_production() -> pd.DataFrame:
    """ONS total production index (B-E), converted to year-on-year growth."""
    return _calendar_month_yoy(_ons_series("industrial_production"))


def fetch_uk_gdp() -> pd.DataFrame:
    """ONS real GDP year-on-year growth, seasonally adjusted."""
    return _ons_series("gdp")


def fetch_uk_bank_rate() -> pd.DataFrame:
    """Official Bank Rate change history published by the Bank of England."""
    url = "https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp"
    response = _SESSION.get(url, headers=_HEADERS, timeout=45)
    response.raise_for_status()
    tables = pd.read_html(io.StringIO(response.text))
    if not tables:
        raise ValueError("Bank of England returned no Bank Rate table")
    out = tables[0].rename(columns={"Date Changed": "date", "Rate": "value"})
    out["date"] = pd.to_datetime(out["date"], format="%d %b %y", errors="coerce")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["date", "value"])
    out["date"] = out["date"].dt.date
    return (
        out.loc[out["date"] >= DATA_FLOOR_DATE, ["date", "value"]]
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


UK_MACRO_FETCHERS = {
    "GB_CPI": fetch_uk_cpi,
    "GB_CORE_CPI": fetch_uk_core_cpi,
    "GB_IP": fetch_uk_industrial_production,
    "GB_GDP": fetch_uk_gdp,
    "GB_BOE": fetch_uk_bank_rate,
}

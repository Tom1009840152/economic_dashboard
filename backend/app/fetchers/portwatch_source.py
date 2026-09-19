"""IMF PortWatch daily trade-panel fetchers.

The current ``Daily_Trade_Data_REG`` table contains both country rows and a
current world aggregate.  The older standalone WLD service stopped updating in
2025 and must not be spliced into this panel.  PortWatch publishes its 30-day
moving-average fields as strings, so coercion and unit conversion are explicit.

No observation-level publication timestamps are available.  Fetchers therefore
leave ``available_at`` unset; the ingestion timestamp remains the honest first
time at which this project observed a vintage.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Iterable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


PORTWATCH_TRADE_LAYER_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/"
    "Daily_Trade_Data_REG/FeatureServer/0"
)
PORTWATCH_TRADE_QUERY_URL = f"{PORTWATCH_TRADE_LAYER_URL}/query"
PORTWATCH_SOURCE_URL = "https://portwatch.imf.org/pages/trade-monitor"
PORTWATCH_FORMULA_VERSION = "pw-reg-30ma-yoy-v1"

PORTWATCH_TRADE_FIELDS = (
    "ISO3",
    "country",
    "date",
    "portcalls_container",
    "portcalls_dry_bulk",
    "portcalls_general_cargo",
    "portcalls_roro",
    "portcalls_tanker",
    "portcalls_cargo",
    "portcalls",
    "import_container",
    "import_dry_bulk",
    "import_general_cargo",
    "import_roro",
    "import_tanker",
    "import_cargo",
    "import",
    "export_container",
    "export_dry_bulk",
    "export_general_cargo",
    "export_roro",
    "export_tanker",
    "export_cargo",
    "export",
    "shipment",
    "portcalls_container_30MA",
    "portcalls_container_30MA_yoy_doy",
    "shipment_30MA",
    "shipment_30MA_yoy_doy",
    "import_container_30MA",
    "import_container_30MA_yoy_doy",
    "export_container_30MA",
    "export_container_30MA_yoy_doy",
    "ObjectId",
)

PORTWATCH_PULSE_FIELDS: dict[str, tuple[str, str]] = {
    "PW_WLD_CNTR_SHIP_30D_YOY": ("WLD", "shipment_30MA_yoy_doy"),
    "PW_WLD_CNTR_CALLS_30D_YOY": ("WLD", "portcalls_container_30MA_yoy_doy"),
    "PW_WLD_CNTR_IMPORT_30D_YOY": ("WLD", "import_container_30MA_yoy_doy"),
    "PW_WLD_CNTR_EXPORT_30D_YOY": ("WLD", "export_container_30MA_yoy_doy"),
    "PW_CHN_CNTR_SHIP_30D_YOY": ("CHN", "shipment_30MA_yoy_doy"),
    "PW_CHN_CNTR_CALLS_30D_YOY": ("CHN", "portcalls_container_30MA_yoy_doy"),
    "PW_CHN_CNTR_IMPORT_30D_YOY": ("CHN", "import_container_30MA_yoy_doy"),
    "PW_CHN_CNTR_EXPORT_30D_YOY": ("CHN", "export_container_30MA_yoy_doy"),
}

_NUMERIC_FIELDS = tuple(
    field
    for field in PORTWATCH_TRADE_FIELDS
    if field not in {"ISO3", "country", "date", "ObjectId"}
)
_CACHE_TTL_SECONDS = 15 * 60
_FAILURE_CACHE_TTL_SECONDS = 60
_cache_lock = threading.Lock()
_cache_panels: dict[tuple[str, ...], tuple[float, pd.DataFrame]] = {}
_cache_failures: dict[tuple[str, ...], tuple[float, str]] = {}
_NULL_TEXT_TOKENS = frozenset({"", "na", "n/a", "nan", "none", "null", "-", ".."})

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": "economic-dashboard/1.0 (+PortWatch research observatory)",
        "Accept": "application/json",
    }
)
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


def portwatch_get(url: str, **kwargs):
    """Shared retrying HTTP client for the trade and chokepoint layers."""

    return _SESSION.get(url, **kwargs)


def _arcgis_json(response: Any) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        details = payload["error"].get("details") or []
        message = payload["error"].get("message") or "ArcGIS query failed"
        raise RuntimeError(f"{message}: {'; '.join(map(str, details))}".rstrip(": "))
    return payload


def _download_trade_panel(
    *,
    request_get: Callable[..., Any] | None = None,
    page_size: int = 1000,
    country_code: str = "CHN",
    geography_codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Download selected official geographies plus WLD with deterministic pagination."""

    requested = [country_code] if geography_codes is None else list(geography_codes)
    codes = tuple(dict.fromkeys(str(code).strip().upper() for code in requested))
    if not codes:
        raise ValueError("at least one PortWatch geography is required")
    invalid_codes = [
        code
        for code in codes
        if len(code) != 3
        or not code.isascii()
        or not code.isalnum()
        or code == "WLD"
    ]
    if invalid_codes:
        raise ValueError(f"invalid PortWatch geography codes: {invalid_codes}")
    query_codes = (*codes, "WLD")
    quoted_codes = ",".join(f"'{code}'" for code in query_codes)
    get = request_get or portwatch_get
    rows: list[dict[str, Any]] = []
    offset = 0

    for _ in range(20):
        request_params = {
            "f": "json",
            "where": f"ISO3 IN ({quoted_codes})",
            "outFields": ",".join(PORTWATCH_TRADE_FIELDS),
            "returnGeometry": "false",
            "orderByFields": "ISO3,date,ObjectId",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }
        for attempt in range(2):
            try:
                response = get(
                    PORTWATCH_TRADE_QUERY_URL,
                    params=request_params,
                    timeout=30,
                )
                payload = _arcgis_json(response)
                break
            except requests.RequestException:
                if attempt == 1:
                    raise
                time.sleep(0.25)
        features = payload.get("features") or []
        rows.extend(feature.get("attributes") or {} for feature in features)
        offset += len(features)
        if not payload.get("exceededTransferLimit") or not features:
            break
    else:
        raise RuntimeError("PortWatch pagination exceeded the safety limit")

    if not rows:
        raise RuntimeError(f"PortWatch returned no {','.join(codes)}/WLD trade rows")

    panel = pd.DataFrame(rows)
    missing = set(PORTWATCH_TRADE_FIELDS) - set(panel.columns)
    if missing:
        raise RuntimeError(f"PortWatch trade schema missing fields: {sorted(missing)}")

    raw_dates = panel["date"]
    parsed_dates = pd.to_datetime(raw_dates, format="%Y-%m-%d", errors="coerce")
    if parsed_dates.isna().any():
        bad_dates = raw_dates.loc[parsed_dates.isna()].head(5).tolist()
        raise RuntimeError(f"PortWatch trade schema contains invalid dates: {bad_dates}")
    panel["date"] = parsed_dates.dt.date
    panel["ISO3"] = panel["ISO3"].astype("string").str.upper()
    for field in _NUMERIC_FIELDS:
        raw_values = panel[field]
        parsed_values = pd.to_numeric(raw_values, errors="coerce")
        normalized = raw_values.astype("string").str.strip().str.lower()
        legitimate_missing = raw_values.isna() | normalized.isin(_NULL_TEXT_TOKENS)
        invalid = ~legitimate_missing & parsed_values.isna()
        if invalid.any():
            samples = raw_values.loc[invalid].head(5).tolist()
            raise RuntimeError(
                f"PortWatch trade schema contains non-numeric {field} values: {samples}"
            )
        non_finite = parsed_values.isin([float("inf"), float("-inf")])
        if non_finite.any():
            samples = raw_values.loc[non_finite].head(5).tolist()
            raise RuntimeError(
                f"PortWatch trade schema contains non-finite {field} values: {samples}"
            )
        panel[field] = parsed_values

    panel = panel.dropna(subset=["ISO3", "date"])
    semantic_rows = panel.dropna(
        subset=["shipment", "import_container", "export_container"]
    )
    semantic_gap = (
        semantic_rows["shipment"]
        - semantic_rows["import_container"]
        - semantic_rows["export_container"]
    ).abs()
    if (semantic_gap > 1).any():
        raise RuntimeError(
            "PortWatch shipment semantics changed; expected container import + export"
        )
    moving_average_rows = panel.dropna(
        subset=["shipment_30MA", "import_container_30MA", "export_container_30MA"]
    )
    moving_average_gap = (
        moving_average_rows["shipment_30MA"]
        - moving_average_rows["import_container_30MA"]
        - moving_average_rows["export_container_30MA"]
    ).abs()
    if (moving_average_gap > 1).any():
        raise RuntimeError(
            "PortWatch 30MA shipment semantics changed; expected container import + export"
        )
    duplicates = panel.duplicated(["ISO3", "date"], keep=False)
    if duplicates.any():
        duplicate_keys = panel.loc[duplicates, ["ISO3", "date"]].head(5).to_dict("records")
        raise RuntimeError(f"PortWatch returned duplicate region/date rows: {duplicate_keys}")

    regions = set(panel["ISO3"].dropna().astype(str))
    expected_regions = {*codes, "WLD"}
    if regions != expected_regions:
        raise RuntimeError(f"PortWatch region coverage changed: {sorted(regions)}")
    world_dates = set(panel.loc[panel["ISO3"] == "WLD", "date"])
    for code in codes:
        geography_dates = set(panel.loc[panel["ISO3"] == code, "date"])
        if geography_dates != world_dates:
            geography_only = sorted(geography_dates - world_dates)[:5]
            world_only = sorted(world_dates - geography_dates)[:5]
            raise RuntimeError(
                f"PortWatch {code}/WLD date grids diverged: "
                f"{code}-only={geography_only}, WLD-only={world_only}"
            )

    return panel.sort_values(["ISO3", "date"]).reset_index(drop=True)


def fetch_portwatch_trade_panel(
    *,
    country_code: str = "CHN",
    force: bool = False,
) -> pd.DataFrame:
    """Return a country-keyed short-lived copy used by PortWatch consumers."""

    country_code = country_code.strip().upper()
    cache_key = (country_code,)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache_panels.get(cache_key)
        if not force and cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1].copy(deep=True)
        failed = _cache_failures.get(cache_key)
        if not force and failed is not None and now - failed[0] < _FAILURE_CACHE_TTL_SECONDS:
            raise RuntimeError(
                f"PortWatch recent fetch failed; retry is temporarily paused: {failed[1]}"
            )

        try:
            panel = _download_trade_panel(country_code=country_code)
        except Exception as exc:
            _cache_failures[cache_key] = (
                time.monotonic(),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        _cache_panels[cache_key] = (time.monotonic(), panel)
        _cache_failures.pop(cache_key, None)
        return panel.copy(deep=True)


def fetch_portwatch_comparison_panel(
    geography_codes: Iterable[str],
    *,
    force: bool = False,
) -> pd.DataFrame:
    """Return a shared panel for up to five selected official geographies."""

    cache_key = tuple(
        sorted(dict.fromkeys(str(code).strip().upper() for code in geography_codes))
    )
    if not cache_key:
        raise ValueError("at least one PortWatch geography is required")
    if len(cache_key) > 5:
        raise ValueError("PortWatch comparison supports at most five geographies")

    now = time.monotonic()
    with _cache_lock:
        cached = _cache_panels.get(cache_key)
        if not force and cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1].copy(deep=True)
        failed = _cache_failures.get(cache_key)
        if not force and failed is not None and now - failed[0] < _FAILURE_CACHE_TTL_SECONDS:
            raise RuntimeError(
                f"PortWatch recent fetch failed; retry is temporarily paused: {failed[1]}"
            )

        try:
            panel = _download_trade_panel(geography_codes=cache_key)
        except Exception as exc:
            _cache_failures[cache_key] = (
                time.monotonic(),
                f"{type(exc).__name__}: {exc}",
            )
            raise
        _cache_panels[cache_key] = (time.monotonic(), panel)
        _cache_failures.pop(cache_key, None)
        return panel.copy(deep=True)


def clear_portwatch_cache() -> None:
    """Test/operations hook; normal refreshes rely on the short TTL."""

    with _cache_lock:
        _cache_panels.clear()
        _cache_failures.clear()


def _pulse_frame(code: str) -> pd.DataFrame:
    region, field = PORTWATCH_PULSE_FIELDS[code]
    panel = fetch_portwatch_trade_panel()
    subset = panel.loc[panel["ISO3"] == region, ["date", field]].copy()
    subset = subset.rename(columns={field: "value"}).dropna(subset=["value"])
    # ArcGIS stores yoy as a decimal ratio; dashboard indicator units are percent.
    subset["value"] = subset["value"].astype(float) * 100.0
    subset["source_url"] = PORTWATCH_TRADE_LAYER_URL
    subset["status"] = "published"
    subset["formula_version"] = PORTWATCH_FORMULA_VERSION
    if not subset.empty:
        subset.attrs["verified_through"] = max(subset["date"])
    return subset.reset_index(drop=True)


def _make_pulse_fetcher(code: str) -> Callable[[], pd.DataFrame]:
    def fetch() -> pd.DataFrame:
        return _pulse_frame(code)

    return fetch


PORTWATCH_FETCHERS: dict[str, Callable[[], pd.DataFrame]] = {
    code: _make_pulse_fetcher(code) for code in PORTWATCH_PULSE_FIELDS
}

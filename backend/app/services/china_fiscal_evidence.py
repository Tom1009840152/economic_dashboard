"""Build auditable first-release evidence for China's fiscal impulse proxy.

The current-value tables deliberately keep the latest GDP and fiscal values.
This module instead works only with official release rows and carries the six
leaf inputs of every fiscal-impulse observation into a canonical provenance
record.  It never writes or mutates a current ``DataPoint``.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from urllib.parse import urlparse

import pandas as pd

from app.services.derived_metrics import (
    DERIVED_METRIC_SPECS,
    calculate_fiscal_broad_expenditure,
    calculate_fiscal_metrics,
)


DIRECT_EVIDENCE_CODES = (
    "CN_GDP_NOMINAL_YTD",
    "CN_FISCAL_GENERAL_SPEND_YTD",
    "CN_FISCAL_FUND_EXPENDITURE_YTD",
)
DERIVED_EVIDENCE_CODES = (
    "CN_FISCAL_BROAD_EXPENDITURE_YTD",
    "CN_FISCAL_SPEND_INTENSITY",
    "CN_FISCAL_IMPULSE_PROXY",
)
FISCAL_EVIDENCE_CODES = (*DIRECT_EVIDENCE_CODES, *DERIVED_EVIDENCE_CODES)
_EVIDENCE_COLUMNS = (
    "date",
    "value",
    "release_date",
    "available_at",
    "source_url",
    "status",
    "formula_version",
    "provenance_json",
)


def _official_https(value: object, domain: str) -> bool:
    try:
        parsed = urlparse(str(value))
    except (TypeError, ValueError):
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (
        hostname == domain or hostname.endswith(f".{domain}")
    )


def _precise_datetime(value: object) -> dt.datetime | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is not None:
        # D8 will introduce an explicit UTC contract.  Until then, accepting
        # an aware timestamp here and merely stripping its offset could turn a
        # UTC release into an earlier Shanghai wall-clock instant and bypass
        # the stricter generic storage boundary.
        raise ValueError(
            "timezone-aware fiscal release timestamps are unsupported until D8"
        )
    return value


def _clean_direct_release(
    frame: pd.DataFrame,
    *,
    code: str,
    domain: str,
) -> pd.DataFrame:
    required = {
        "date",
        "value",
        "release_date",
        "available_at",
        "source_url",
        "status",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{code} release evidence is missing columns: {sorted(missing)}")

    rows: list[dict] = []
    for raw in frame.to_dict("records"):
        observed = pd.to_datetime(raw.get("date"), errors="coerce")
        value = pd.to_numeric(raw.get("value"), errors="coerce")
        available_at = _precise_datetime(raw.get("available_at"))
        release_date = pd.to_datetime(raw.get("release_date"), errors="coerce")
        source_url = raw.get("source_url")
        if (
            pd.isna(observed)
            or pd.isna(value)
            or pd.isna(release_date)
            or available_at is None
            or not _official_https(source_url, domain)
            or raw.get("status") != "published"
        ):
            continue
        rows.append(
            {
                "date": observed.date(),
                "value": round(float(value), 6),
                "release_date": release_date.date(),
                "available_at": available_at,
                "source_url": str(source_url),
                "status": "published",
                "formula_version": None,
            }
        )

    if not rows:
        return pd.DataFrame(columns=_EVIDENCE_COLUMNS)
    result = pd.DataFrame(rows).sort_values(["date", "available_at", "source_url"])
    for observed, group in result.groupby("date", sort=False):
        if group["value"].nunique(dropna=False) != 1:
            raise ValueError(f"{code} has conflicting first-release values for {observed}")
    result = result.drop_duplicates("date", keep="first").reset_index(drop=True)
    result["provenance_json"] = [
        _provenance_json([_leaf(code, row)]) for row in result.to_dict("records")
    ]
    return result.loc[:, _EVIDENCE_COLUMNS]


def _leaf(code: str, row: Mapping[str, object]) -> dict:
    available_at = _precise_datetime(row.get("available_at"))
    if available_at is None:
        raise ValueError(f"{code} provenance is missing a precise available_at")
    observed = pd.to_datetime(row.get("date"), errors="raise").date()
    return {
        "indicator_code": code,
        "date": observed.isoformat(),
        "value": f"{float(row['value']):.6f}",
        "available_at": available_at.isoformat(timespec="seconds"),
        "source_url": str(row.get("source_url") or ""),
    }


def _provenance_json(leaves: list[dict]) -> str:
    ordered = sorted(
        leaves,
        key=lambda item: (
            item["date"],
            item["indicator_code"],
            item["available_at"],
            item["source_url"],
        ),
    )
    return json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _leaf_rows(frame: pd.DataFrame, code: str) -> dict[pd.Period, dict]:
    return {
        pd.Period(row["date"], freq="M"): {
            **row,
            "leaf": _leaf(code, row),
        }
        for row in frame.to_dict("records")
    }


def _derived_row(
    *,
    code: str,
    period: pd.Period,
    value: float,
    leaves: list[dict],
) -> dict:
    latest = max(
        leaves,
        key=lambda item: (
            item["available_at"],
            item["indicator_code"],
            item["source_url"],
        ),
    )
    available_at = dt.datetime.fromisoformat(latest["available_at"])
    return {
        "date": dt.date(period.year, period.month, 1),
        "value": round(float(value), 6),
        "release_date": available_at.date(),
        "available_at": available_at,
        "source_url": latest["source_url"],
        "status": "derived",
        "formula_version": DERIVED_METRIC_SPECS[code].version,
        "provenance_json": _provenance_json(leaves),
    }


def _series(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="float64")
    return pd.Series(
        frame["value"].astype(float).to_numpy(),
        index=pd.PeriodIndex(pd.to_datetime(frame["date"]), freq="M"),
        dtype="float64",
    ).sort_index()


def build_fiscal_release_evidence(
    fiscal: Mapping[str, pd.DataFrame],
    nominal_gdp: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Return direct and derived official evidence without touching current values."""

    general = _clean_direct_release(
        fiscal["CN_FISCAL_GENERAL_SPEND_YTD"],
        code="CN_FISCAL_GENERAL_SPEND_YTD",
        domain="mof.gov.cn",
    )
    fund = _clean_direct_release(
        fiscal["CN_FISCAL_FUND_EXPENDITURE_YTD"],
        code="CN_FISCAL_FUND_EXPENDITURE_YTD",
        domain="mof.gov.cn",
    )
    gdp = _clean_direct_release(
        nominal_gdp,
        code="CN_GDP_NOMINAL_YTD",
        domain="stats.gov.cn",
    )

    general_rows = _leaf_rows(general, "CN_FISCAL_GENERAL_SPEND_YTD")
    fund_rows = _leaf_rows(fund, "CN_FISCAL_FUND_EXPENDITURE_YTD")
    gdp_rows = _leaf_rows(gdp, "CN_GDP_NOMINAL_YTD")
    broad_values = calculate_fiscal_broad_expenditure(_series(general), _series(fund))

    broad_records: list[dict] = []
    broad_leaves: dict[pd.Period, list[dict]] = {}
    for period, value in broad_values.items():
        if period not in general_rows or period not in fund_rows:
            continue
        leaves = [general_rows[period]["leaf"], fund_rows[period]["leaf"]]
        broad_leaves[period] = leaves
        broad_records.append(
            _derived_row(
                code="CN_FISCAL_BROAD_EXPENDITURE_YTD",
                period=period,
                value=float(value),
                leaves=leaves,
            )
        )
    broad = pd.DataFrame(broad_records, columns=_EVIDENCE_COLUMNS)

    intensity_values, impulse_values = calculate_fiscal_metrics(
        _series(broad), _series(gdp)
    )
    intensity_records: list[dict] = []
    intensity_leaves: dict[pd.Period, list[dict]] = {}
    for period, value in intensity_values.items():
        if period not in broad_leaves or period not in gdp_rows:
            continue
        leaves = [*broad_leaves[period], gdp_rows[period]["leaf"]]
        intensity_leaves[period] = leaves
        intensity_records.append(
            _derived_row(
                code="CN_FISCAL_SPEND_INTENSITY",
                period=period,
                value=float(value),
                leaves=leaves,
            )
        )

    impulse_records: list[dict] = []
    for period, value in impulse_values.items():
        previous = period - 12
        if period not in intensity_leaves or previous not in intensity_leaves:
            continue
        leaves = [*intensity_leaves[period], *intensity_leaves[previous]]
        impulse_records.append(
            _derived_row(
                code="CN_FISCAL_IMPULSE_PROXY",
                period=period,
                value=float(value),
                leaves=leaves,
            )
        )

    return {
        "CN_GDP_NOMINAL_YTD": gdp,
        "CN_FISCAL_GENERAL_SPEND_YTD": general,
        "CN_FISCAL_FUND_EXPENDITURE_YTD": fund,
        "CN_FISCAL_BROAD_EXPENDITURE_YTD": broad,
        "CN_FISCAL_SPEND_INTENSITY": pd.DataFrame(
            intensity_records, columns=_EVIDENCE_COLUMNS
        ),
        "CN_FISCAL_IMPULSE_PROXY": pd.DataFrame(
            impulse_records, columns=_EVIDENCE_COLUMNS
        ),
    }

"""Build an auditable PBOC release chain for China's credit-cycle block.

The current-value tables and the historical information set serve different
purposes.  This module keeps the rounded values printed in each monthly PBOC
release, derives monthly residuals only when no direct monthly value exists,
and propagates every source leaf into credit intensity and credit impulse.
Nothing here writes or revises ``DataPoint``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from collections.abc import Mapping
from urllib.parse import urlparse

import pandas as pd

from app.fetchers.china_cycle_data import (
    PBOC_YTD_DIFF_FORMULA_VERSION,
    _credit_monthly_values,
    _credit_ytd_values,
    _get_pboc_archive_text,
    _is_pboc_https_url,
    _pboc_release_catalog,
    _period_date,
    _publication_metadata,
    _text_from_html,
)
from app.services.china_money_definition import (
    CN_M1_2024_COMPARABLE,
    CN_M1_2024_COMPARABLE_AVAILABLE_AT,
    CN_M1_2024_COMPARABLE_SOURCE_URL,
    CN_M1_2025_DEFINITION_VERSION,
)
from app.services.derived_metrics import (
    DERIVED_METRIC_SPECS,
    calculate_credit_metrics,
    calculate_spread,
    trailing_nominal_gdp_from_ytd,
)


logger = logging.getLogger(__name__)

_PBOC_SUPPLEMENTAL_RELEASES = (
    (
        CN_M1_2024_COMPARABLE_SOURCE_URL,
        "2025年1月金融统计数据报告",
    ),
    (
        "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/"
        "2025092212554511660/index.html",
        "2025年1月社会融资规模存量统计数据报告",
    ),
    (
        "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/"
        "2025092212554594361/index.html",
        "2025年1月社会融资规模增量统计数据报告",
    ),
)
_YTD_TARGET_CODES = {
    "CN_TSF": "CN_TSF_YTD",
    "CN_TSF_RMB_LOANS_FLOW": "CN_TSF_RMB_LOANS_FLOW_YTD",
    "CN_CORP_BOND_FINANCING": "CN_CORP_BOND_FINANCING_YTD",
    "CN_GOV_BOND_FINANCING": "CN_GOV_BOND_FINANCING_YTD",
}
_MONTHLY_FLOW_CODES = tuple(_YTD_TARGET_CODES)
_STOCK_CODES = ("CN_TSF_STOCK_YOY", "CN_TSF_RMB_LOAN_STOCK_YOY")
_MONEY_CODES = ("CN_M1_YOY", "CN_M2_YOY")
_MONTHLY_INPUT_SUFFIX = "__direct_month"
_EVIDENCE_COLUMNS = [
    "date",
    "value",
    "release_date",
    "available_at",
    "source_url",
    "status",
    "formula_version",
    "provenance_json",
    "evidence_kind",
    "chain_verified",
    "availability_precision",
]

CREDIT_EVIDENCE_CODES = (
    "CN_TSF_YTD",
    "CN_TSF_RMB_LOANS_FLOW_YTD",
    "CN_CORP_BOND_FINANCING_YTD",
    "CN_GOV_BOND_FINANCING_YTD",
    "CN_TSF",
    "CN_TSF_RMB_LOANS_FLOW",
    "CN_CORP_BOND_FINANCING",
    "CN_GOV_BOND_FINANCING",
    "CN_TSF_STOCK_YOY",
    "CN_TSF_RMB_LOAN_STOCK_YOY",
    "CN_M1_YOY",
    "CN_M2_YOY",
    "CN_M1M2",
    "CN_CREDIT_INTENSITY",
    "CN_CREDIT_IMPULSE",
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
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is not None:
        raise ValueError("timezone-aware credit release timestamps are unsupported")
    return parsed.to_pydatetime().replace(microsecond=0)


def _definition_version(observed: dt.date) -> str:
    if observed >= dt.date(2023, 1, 1):
        return "pboc_afre_scope_2023_v1"
    if observed >= dt.date(2019, 12, 1):
        return "pboc_afre_government_bond_2019_v1"
    if observed >= dt.date(2018, 9, 1):
        return "pboc_afre_special_bond_2018_v1"
    if observed >= dt.date(2018, 7, 1):
        return "pboc_afre_abs_writeoff_2018_v1"
    return "pboc_afre_legacy_v1"


def _leaf(
    *,
    code: str,
    observed: dt.date,
    value: float,
    available_at: dt.datetime,
    source_url: str,
    title: str,
    source_sha256: str | None,
    reported_scope: str,
    reported_unit: str,
    preliminary: bool,
    definition_version: str,
) -> dict:
    return {
        "indicator_code": code,
        "date": observed.isoformat(),
        "value": f"{float(value):.6f}",
        "available_at": available_at.isoformat(timespec="seconds"),
        "source_url": source_url,
        "source_title": title,
        "source_sha256": source_sha256,
        "reported_scope": reported_scope,
        "reported_unit": reported_unit,
        "preliminary": preliminary,
        "definition_version": definition_version,
    }


def _leaf_key(leaf: Mapping[str, object]) -> tuple[str, ...]:
    return (
        str(leaf.get("indicator_code") or ""),
        str(leaf.get("date") or ""),
        str(leaf.get("value") or ""),
        str(leaf.get("available_at") or ""),
        str(leaf.get("source_url") or ""),
    )


def _dedupe_leaves(leaves: list[dict]) -> list[dict]:
    unique = {_leaf_key(leaf): leaf for leaf in leaves}
    return [unique[key] for key in sorted(unique)]


def _provenance_json(leaves: list[dict]) -> str:
    return json.dumps(
        _dedupe_leaves(leaves),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _row_from_leaves(
    *,
    code: str,
    observed: dt.date,
    value: float,
    leaves: list[dict],
    status: str,
    formula_version: str | None,
) -> dict:
    ordered = _dedupe_leaves(leaves)
    if not ordered:
        raise ValueError(f"{code} evidence has no source leaves")
    latest = max(
        ordered,
        key=lambda item: (
            item["available_at"],
            item["indicator_code"],
            item["source_url"],
        ),
    )
    available_at = dt.datetime.fromisoformat(str(latest["available_at"]))
    return {
        "date": observed,
        "value": round(float(value), 6),
        "release_date": available_at.date(),
        "available_at": available_at,
        "source_url": str(latest["source_url"]),
        "status": status,
        "formula_version": formula_version,
        "provenance_json": _provenance_json(ordered),
        "evidence_kind": "official_release",
        "chain_verified": True,
        "availability_precision": "exact_minute",
    }


def _direct_row(
    *,
    code: str,
    observed: dt.date,
    value: float,
    available_at: dt.datetime,
    source_url: str,
    title: str,
    source_sha256: str | None,
    reported_scope: str,
    preliminary: bool,
    definition_version: str,
) -> dict:
    leaf = _leaf(
        code=code,
        observed=observed,
        value=value,
        available_at=available_at,
        source_url=source_url,
        title=title,
        source_sha256=source_sha256,
        reported_scope=reported_scope,
        reported_unit="亿元" if "YOY" not in code else "%",
        preliminary=preliminary,
        definition_version=definition_version,
    )
    return _row_from_leaves(
        code=code,
        observed=observed,
        value=value,
        leaves=[leaf],
        status="published",
        formula_version=None,
    )


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_EVIDENCE_COLUMNS)


def _first_release_frame(rows: list[dict], *, code: str) -> pd.DataFrame:
    """Keep the earliest verified release for each observation period."""

    if not rows:
        return _empty_frame()
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame["available_at"] = frame["available_at"].map(_precise_datetime)
    frame = frame.dropna(subset=["date", "value", "available_at", "source_url"])
    frame = frame.loc[
        frame["source_url"].map(lambda value: _official_https(value, "pbc.gov.cn"))
    ].copy()
    selected: list[dict] = []
    for observed, group in frame.groupby("date", sort=True):
        earliest = group["available_at"].min()
        candidates = group.loc[group["available_at"].eq(earliest)].copy()
        if candidates["value"].round(6).nunique(dropna=False) != 1:
            raise ValueError(
                f"{code} has conflicting first-release values for {observed}"
            )
        candidates = candidates.sort_values(
            ["source_url", "provenance_json"], kind="stable"
        )
        selected.append(candidates.iloc[0].to_dict())
    return (
        pd.DataFrame(selected, columns=_EVIDENCE_COLUMNS)
        .sort_values("date")
        .reset_index(drop=True)
    )


def _money_yoy_values(text: str) -> dict[str, float]:
    patterns = {
        "CN_M2_YOY": r"广义货币\s*[（(]M2[）)]",
        "CN_M1_YOY": r"狭义货币\s*[（(]M1[）)]",
    }
    output: dict[str, float] = {}
    for code, label in patterns.items():
        match = re.search(
            rf"{label}\s*余额(?:为)?\s*[\d.]+\s*万亿元"
            r"[，,；;。\s]*同比\s*(增长|下降|减少)\s*([\d.]+)%",
            text,
        )
        if match:
            value = float(match.group(2))
            output[code] = -value if match.group(1) in {"下降", "减少"} else value
    return output


def _stock_yoy_values(text: str) -> dict[str, float]:
    patterns = {
        "CN_TSF_STOCK_YOY": r"社会融资规模存量(?:为)?\s*[\d.]+\s*万亿元",
        "CN_TSF_RMB_LOAN_STOCK_YOY": (
            r"对实体经济发放的人民币贷款余额(?:为)?\s*[\d.]+\s*万亿元"
        ),
    }
    output: dict[str, float] = {}
    for code, label in patterns.items():
        match = re.search(
            rf"{label}[，,；;。\s]*同比\s*(增长|下降|减少)\s*([\d.]+)%",
            text,
        )
        if match:
            value = float(match.group(2))
            output[code] = -value if match.group(1) in {"下降", "减少"} else value
    return output


def collect_pboc_credit_release_inputs(
    *, page_count: int = 30
) -> dict[str, pd.DataFrame]:
    """Crawl official PBOC release pages and retain their first-release values."""

    if page_count < 1:
        raise ValueError("page_count must be at least 1")
    keys = [
        *_YTD_TARGET_CODES.values(),
        *(f"{code}{_MONTHLY_INPUT_SUFFIX}" for code in _MONTHLY_FLOW_CODES),
        *_STOCK_CODES,
        *_MONEY_CODES,
    ]
    rows: dict[str, list[dict]] = {key: [] for key in keys}
    catalog = dict(_pboc_release_catalog(page_count, archive=True))
    for source_url, title in _PBOC_SUPPLEMENTAL_RELEASES:
        catalog.setdefault(source_url, title)

    for source_url, title in catalog.items():
        if not _is_pboc_https_url(source_url):
            continue
        try:
            source = _get_pboc_archive_text(source_url)
        except Exception:
            logger.warning("PBOC credit evidence page failed: %s", source_url, exc_info=True)
            continue
        text = _text_from_html(source)
        observed = _period_date(title, text)
        metadata = _publication_metadata(source, source_url)
        available_at = _precise_datetime(metadata.get("available_at"))
        if observed is None or available_at is None:
            continue
        source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
        preliminary = "初步统计" in text or "当期数据为初步数" in text
        definition_version = _definition_version(observed)

        for raw_code, value in _credit_ytd_values(text).items():
            target = _YTD_TARGET_CODES[raw_code]
            rows[target].append(
                _direct_row(
                    code=target,
                    observed=observed,
                    value=value,
                    available_at=available_at,
                    source_url=source_url,
                    title=title,
                    source_sha256=source_sha256,
                    reported_scope="year_to_date",
                    preliminary=preliminary,
                    definition_version=definition_version,
                )
            )
        for code, value in _credit_monthly_values(text, observed).items():
            rows[f"{code}{_MONTHLY_INPUT_SUFFIX}"].append(
                _direct_row(
                    code=code,
                    observed=observed,
                    value=value,
                    available_at=available_at,
                    source_url=source_url,
                    title=title,
                    source_sha256=source_sha256,
                    reported_scope="month",
                    preliminary=preliminary,
                    definition_version=definition_version,
                )
            )
        for code, value in _stock_yoy_values(text).items():
            rows[code].append(
                _direct_row(
                    code=code,
                    observed=observed,
                    value=value,
                    available_at=available_at,
                    source_url=source_url,
                    title=title,
                    source_sha256=source_sha256,
                    reported_scope="stock_end_month_yoy",
                    preliminary=preliminary,
                    definition_version=definition_version,
                )
            )
        for code, value in _money_yoy_values(text).items():
            if observed < dt.date(2024, 1, 1):
                continue
            if code == "CN_M1_YOY" and observed < dt.date(2025, 1, 1):
                continue
            rows[code].append(
                _direct_row(
                    code=code,
                    observed=observed,
                    value=value,
                    available_at=available_at,
                    source_url=source_url,
                    title=title,
                    source_sha256=source_sha256,
                    reported_scope="stock_end_month_yoy",
                    preliminary=preliminary,
                    definition_version=(
                        CN_M1_2025_DEFINITION_VERSION
                        if code == "CN_M1_YOY"
                        else "pboc_m2_current_definition_v1"
                    ),
                )
            )

        if (
            source_url == CN_M1_2024_COMPARABLE_SOURCE_URL
            and "2024年各月末M1可比余额和增速" in text
        ):
            for backcast_date, _balance, yoy in CN_M1_2024_COMPARABLE:
                rows["CN_M1_YOY"].append(
                    _direct_row(
                        code="CN_M1_YOY",
                        observed=backcast_date,
                        value=yoy,
                        available_at=CN_M1_2024_COMPARABLE_AVAILABLE_AT,
                        source_url=source_url,
                        title=title,
                        source_sha256=source_sha256,
                        reported_scope="retrospective_comparable_backcast",
                        preliminary=False,
                        definition_version=CN_M1_2025_DEFINITION_VERSION,
                    )
                )

    return {key: _first_release_frame(value, code=key) for key, value in rows.items()}


def _leaves_from_row(row: Mapping[str, object]) -> list[dict]:
    payload = row.get("provenance_json")
    if not isinstance(payload, str):
        raise ValueError("credit evidence row is missing provenance_json")
    parsed = json.loads(payload)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("credit evidence provenance must be a non-empty list")
    return [dict(leaf) for leaf in parsed]


def _nodes(frame: pd.DataFrame) -> dict[pd.Period, dict]:
    return {
        pd.Period(row["date"], freq="M"): {
            "value": float(row["value"]),
            "leaves": _leaves_from_row(row),
            "row": row,
        }
        for row in frame.to_dict("records")
    }


def _build_monthly_flow(
    *,
    code: str,
    direct: pd.DataFrame,
    ytd: pd.DataFrame,
) -> pd.DataFrame:
    direct_nodes = _nodes(direct)
    official_ytd_nodes = _nodes(ytd)
    periods = sorted(set(direct_nodes) | set(official_ytd_nodes))
    if not periods:
        return _empty_frame()
    known_ytd: dict[pd.Period, dict] = {}
    records: list[dict] = []
    calendar = pd.period_range(periods[0], periods[-1], freq="M")
    for period in calendar:
        direct_node = direct_nodes.get(period)
        official_ytd = official_ytd_nodes.get(period)
        previous = known_ytd.get(period - 1) if period.month != 1 else None

        if official_ytd is not None:
            known_ytd[period] = official_ytd
        elif period.month == 1 and direct_node is not None:
            known_ytd[period] = direct_node
        elif previous is not None and direct_node is not None:
            known_ytd[period] = {
                "value": previous["value"] + direct_node["value"],
                "leaves": _dedupe_leaves(
                    [*previous["leaves"], *direct_node["leaves"]]
                ),
            }

        if direct_node is not None:
            records.append(direct_node["row"])
        elif official_ytd is not None and (period.month == 1 or previous is not None):
            base = 0.0 if period.month == 1 else float(previous["value"])
            leaves = list(official_ytd["leaves"])
            if previous is not None:
                leaves.extend(previous["leaves"])
            records.append(
                _row_from_leaves(
                    code=code,
                    observed=dt.date(period.year, period.month, 1),
                    value=float(official_ytd["value"]) - base,
                    leaves=leaves,
                    status="derived",
                    formula_version=PBOC_YTD_DIFF_FORMULA_VERSION,
                )
            )
    return _first_release_frame(records, code=code)


def _clean_nominal_gdp(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for raw in frame.to_dict("records"):
        observed = pd.to_datetime(raw.get("date"), errors="coerce")
        value = pd.to_numeric(raw.get("value"), errors="coerce")
        available_at = _precise_datetime(raw.get("available_at"))
        source_url = raw.get("source_url")
        if (
            pd.isna(observed)
            or pd.isna(value)
            or available_at is None
            or raw.get("status") != "published"
            or not _official_https(source_url, "stats.gov.cn")
        ):
            continue
        observed_date = observed.date()
        leaf = _leaf(
            code="CN_GDP_NOMINAL_YTD",
            observed=observed_date,
            value=float(value),
            available_at=available_at,
            source_url=str(source_url),
            title="国家统计局国内生产总值初步核算结果",
            source_sha256=None,
            reported_scope="year_to_date",
            reported_unit="亿元",
            preliminary=True,
            definition_version="nbs_nominal_gdp_current_v1",
        )
        rows.append(
            _row_from_leaves(
                code="CN_GDP_NOMINAL_YTD",
                observed=observed_date,
                value=float(value),
                leaves=[leaf],
                status="published",
                formula_version=None,
            )
        )
    if not rows:
        return _empty_frame()
    # NBS dependencies use the same first-release rule but a different domain.
    result = pd.DataFrame(rows)
    selected: list[dict] = []
    for observed, group in result.groupby("date", sort=True):
        earliest = group["available_at"].min()
        candidates = group.loc[group["available_at"].eq(earliest)]
        if candidates["value"].round(6).nunique(dropna=False) != 1:
            raise ValueError(
                f"CN_GDP_NOMINAL_YTD has conflicting first-release values for {observed}"
            )
        selected.append(candidates.sort_values("source_url").iloc[0].to_dict())
    return pd.DataFrame(selected, columns=_EVIDENCE_COLUMNS).reset_index(drop=True)


def _series(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="float64")
    return pd.Series(
        frame["value"].astype(float).to_numpy(),
        index=pd.PeriodIndex(pd.to_datetime(frame["date"]), freq="M"),
        dtype="float64",
    ).sort_index()


def _build_credit_metrics(
    monthly_tsf: pd.DataFrame,
    nominal_gdp: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    intensity_values, impulse_values = calculate_credit_metrics(
        _series(monthly_tsf), _series(nominal_gdp)
    )
    flow_nodes = _nodes(monthly_tsf)
    gdp_nodes = _nodes(nominal_gdp)
    trailing_gdp = trailing_nominal_gdp_from_ytd(_series(nominal_gdp))
    gdp_leaves: dict[pd.Period, list[dict]] = {}
    for period in trailing_gdp.index:
        previous_annual = pd.Period(year=period.year - 1, month=12, freq="M")
        previous_same = period - 12
        required = (period, previous_annual, previous_same)
        if any(item not in gdp_nodes for item in required):
            continue
        gdp_leaves[period] = _dedupe_leaves(
            [
                leaf
                for item in required
                for leaf in gdp_nodes[item]["leaves"]
            ]
        )

    intensity_rows: list[dict] = []
    intensity_leaves: dict[pd.Period, list[dict]] = {}
    for period, value in intensity_values.items():
        credit_periods = pd.period_range(period - 11, period, freq="M")
        completed_gdp = [item for item in gdp_leaves if item <= period]
        if (
            any(item not in flow_nodes for item in credit_periods)
            or not completed_gdp
        ):
            continue
        gdp_period = max(completed_gdp)
        leaves = _dedupe_leaves(
            [
                *[
                    leaf
                    for item in credit_periods
                    for leaf in flow_nodes[item]["leaves"]
                ],
                *gdp_leaves[gdp_period],
            ]
        )
        intensity_leaves[period] = leaves
        intensity_rows.append(
            _row_from_leaves(
                code="CN_CREDIT_INTENSITY",
                observed=dt.date(period.year, period.month, 1),
                value=float(value),
                leaves=leaves,
                status="derived",
                formula_version=DERIVED_METRIC_SPECS["CN_CREDIT_INTENSITY"].version,
            )
        )

    impulse_rows: list[dict] = []
    for period, value in impulse_values.items():
        previous = period - 12
        if period not in intensity_leaves or previous not in intensity_leaves:
            continue
        impulse_rows.append(
            _row_from_leaves(
                code="CN_CREDIT_IMPULSE",
                observed=dt.date(period.year, period.month, 1),
                value=float(value),
                leaves=[*intensity_leaves[period], *intensity_leaves[previous]],
                status="derived",
                formula_version=DERIVED_METRIC_SPECS["CN_CREDIT_IMPULSE"].version,
            )
        )
    return (
        pd.DataFrame(intensity_rows, columns=_EVIDENCE_COLUMNS),
        pd.DataFrame(impulse_rows, columns=_EVIDENCE_COLUMNS),
    )


def _build_m1m2(m1: pd.DataFrame, m2: pd.DataFrame) -> pd.DataFrame:
    values = calculate_spread(_series(m1), _series(m2))
    m1_nodes = _nodes(m1)
    m2_nodes = _nodes(m2)
    rows: list[dict] = []
    for period, value in values.items():
        if period not in m1_nodes or period not in m2_nodes:
            continue
        rows.append(
            _row_from_leaves(
                code="CN_M1M2",
                observed=dt.date(period.year, period.month, 1),
                value=float(value),
                leaves=[*m1_nodes[period]["leaves"], *m2_nodes[period]["leaves"]],
                status="derived",
                formula_version=DERIVED_METRIC_SPECS["CN_M1M2"].version,
            )
        )
    return pd.DataFrame(rows, columns=_EVIDENCE_COLUMNS)


def build_credit_release_evidence(
    inputs: Mapping[str, pd.DataFrame],
    nominal_gdp: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Return direct and derived credit evidence without mutating current data."""

    output: dict[str, pd.DataFrame] = {
        code: inputs.get(code, _empty_frame()).copy()
        for code in (
            *_YTD_TARGET_CODES.values(),
            *_STOCK_CODES,
            *_MONEY_CODES,
        )
    }
    for code, ytd_code in _YTD_TARGET_CODES.items():
        output[code] = _build_monthly_flow(
            code=code,
            direct=inputs.get(f"{code}{_MONTHLY_INPUT_SUFFIX}", _empty_frame()),
            ytd=output[ytd_code],
        )

    gdp = _clean_nominal_gdp(nominal_gdp)
    intensity, impulse = _build_credit_metrics(output["CN_TSF"], gdp)
    output["CN_CREDIT_INTENSITY"] = intensity
    output["CN_CREDIT_IMPULSE"] = impulse
    output["CN_M1M2"] = _build_m1m2(
        output["CN_M1_YOY"], output["CN_M2_YOY"]
    )
    return {code: output.get(code, _empty_frame()) for code in CREDIT_EVIDENCE_CODES}


__all__ = [
    "CREDIT_EVIDENCE_CODES",
    "build_credit_release_evidence",
    "collect_pboc_credit_release_inputs",
]

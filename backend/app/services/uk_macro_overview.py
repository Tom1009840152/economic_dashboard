"""Transparent current-snapshot overview for the United Kingdom.

Growth, labour, inflation, Bank of England policy and the limited available
money/market evidence remain separate.  Every observation keeps its own
period; missing or stale evidence is never converted to zero, and the service
does not publish an unvalidated composite score or recession probability.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import date
from statistics import mean
from threading import Lock
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fetchers.uk_employment import fetch_uk_employment_dashboard
from app.models import DataPoint, RefreshResult
from app.services.indicator_series import is_current_series_point


METHODOLOGY_VERSION = "1.0.0"
EMPLOYMENT_ENRICHMENT_TIMEOUT_SECONDS = 5.0
EMPLOYMENT_ENRICHMENT_TTL_SECONDS = 6 * 60 * 60

_employment_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uk-overview-employment")
_employment_future: Future[dict[str, Any]] | None = None
_employment_future_started_at: float | None = None
_employment_lock = Lock()

UK_OVERVIEW_CODES = (
    "GB_GDP",
    "GB_IP",
    "GB_CLI",
    "GB_CPI",
    "GB_CORE_CPI",
    "GB_BOE",
    "GB_M3",
    "FTSE100",
    "GBPCNY",
)


def _load_employment_enrichment() -> tuple[Mapping[str, Any] | None, str | None]:
    """Bound ONS latency while a shared background request warms."""

    global _employment_future, _employment_future_started_at
    with _employment_lock:
        now = time.monotonic()
        expired = (
            _employment_future is not None
            and _employment_future.done()
            and _employment_future_started_at is not None
            and now - _employment_future_started_at >= EMPLOYMENT_ENRICHMENT_TTL_SECONDS
        )
        if _employment_future is None or expired:
            _employment_future = _employment_executor.submit(fetch_uk_employment_dashboard)
            _employment_future_started_at = now
        future = _employment_future
    try:
        return future.result(timeout=EMPLOYMENT_ENRICHMENT_TIMEOUT_SECONDS), None
    except FutureTimeoutError:
        return None, "TimeoutError"
    except Exception as exc:
        with _employment_lock:
            if _employment_future is future:
                _employment_future = None
                _employment_future_started_at = None
        return None, type(exc).__name__


def _field(row: Any, name: str, default: Any = None) -> Any:
    return row.get(name, default) if isinstance(row, Mapping) else getattr(row, name, default)


def _period(code: str, observed: date | str | None) -> str | None:
    if observed is None:
        return None
    raw = observed if isinstance(observed, str) else observed.isoformat()
    if code == "GB_GDP":
        parsed = date.fromisoformat(raw[:10])
        return f"{parsed.year}-Q{((parsed.month - 1) // 3) + 1}"
    if code in {"GB_BOE", "FTSE100", "GBPCNY"}:
        return raw[:10]
    return raw[:7]


def _series(rows: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {code: [] for code in UK_OVERVIEW_CODES}
    for row in rows:
        code = str(_field(row, "indicator_code", ""))
        if code not in grouped:
            continue
        if not isinstance(row, Mapping) and not is_current_series_point(code, row):
            continue
        observed = _field(row, "date")
        value = _field(row, "value")
        if observed is None or value is None:
            continue
        retrieved_at = _field(row, "retrieved_at")
        if retrieved_at is not None:
            retrieved_at = (
                retrieved_at.isoformat()
                if hasattr(retrieved_at, "isoformat")
                else str(retrieved_at)
            )
        grouped[code].append(
            {
                "date": observed,
                "period": _period(code, observed),
                "value": float(value),
                "retrieved_at": retrieved_at,
            }
        )
    for values in grouped.values():
        values.sort(key=lambda item: str(item["date"]))
    return grouped


def _latest(series: dict[str, list[dict[str, Any]]], code: str) -> dict[str, Any] | None:
    values = series.get(code, [])
    return values[-1] if values else None


def _reference(
    series: dict[str, list[dict[str, Any]]], code: str, observations_back: int
) -> dict[str, Any] | None:
    values = series.get(code, [])
    index = len(values) - 1 - observations_back
    return values[index] if index >= 0 else None


def _calendar_period_index(period: str, frequency: str) -> int:
    if frequency == "quarterly":
        year_text, quarter_text = period.split("-Q")
        return int(year_text) * 4 + int(quarter_text) - 1
    if frequency == "monthly":
        return _month_index(period)
    raise ValueError(f"Unsupported calendar frequency: {frequency}")


def _calendar_reference(
    points: list[dict[str, Any]], periods_back: int, frequency: str
) -> dict[str, Any] | None:
    """Return the exact calendar-period reference, never merely the nth row."""

    if not points:
        return None
    try:
        target = _calendar_period_index(str(points[-1]["period"]), frequency) - periods_back
        return next(
            (
                point
                for point in reversed(points)
                if _calendar_period_index(str(point["period"]), frequency) == target
            ),
            None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _trend(value: float | None, reference: float | None, tolerance: float = 0.05) -> str:
    if value is None or reference is None:
        return "unavailable"
    difference = value - reference
    if difference > tolerance:
        return "up"
    if difference < -tolerance:
        return "down"
    return "flat"


def _freshness(period: str | None, frequency: str) -> str:
    if period is None:
        return "missing"
    try:
        if frequency == "quarterly":
            year_text, quarter_text = period.split("-Q")
            observed = date(int(year_text), (int(quarter_text) - 1) * 3 + 1, 1)
            limit = 220
        elif frequency == "daily":
            observed = date.fromisoformat(period[:10])
            limit = 14
        elif frequency == "mixed":
            observed = date.fromisoformat(period[:10])
            # Policy rates are event driven, but an unverified event series
            # should not be treated as current indefinitely.
            limit = 220
        else:
            year_text, month_text = period[:7].split("-")
            observed = date(int(year_text), int(month_text), 1)
            limit = 100
    except (TypeError, ValueError):
        return "missing"
    return "current" if max((date.today() - observed).days, 0) <= limit else "stale"


def _labour_freshness(period: str | None) -> str:
    """ONS LFS rolling-three-month estimates have a longer publication lag."""

    if period is None:
        return "missing"
    try:
        year_text, month_text = period[:7].split("-")
        observed = date(int(year_text), int(month_text), 1)
    except (TypeError, ValueError):
        return "missing"
    return "current" if max((date.today() - observed).days, 0) <= 150 else "stale"


def _combined_freshness(*states: str) -> str:
    """A derived metric is only as fresh as its least-current input."""

    if not states or "missing" in states:
        return "missing"
    if "stale" in states:
        return "stale"
    return "current"


def _combined_period(*periods: str | None) -> str | None:
    """Use the older input period as a derived metric's watermark."""

    available = [period for period in periods if period]
    return min(available) if len(available) == len(periods) and available else None


def _month_index(period: str) -> int:
    year_text, month_text = period[:7].split("-")
    return int(year_text) * 12 + int(month_text) - 1


def _trailing_mean(points: list[dict[str, Any]], count: int) -> float | None:
    trailing = points[-count:]
    if len(trailing) != count:
        return None
    try:
        indices = [_month_index(point["period"]) for point in trailing]
    except (KeyError, TypeError, ValueError):
        return None
    if any(right - left != 1 for left, right in zip(indices, indices[1:])):
        return None
    return mean(point["value"] for point in trailing)


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _fmt(value: float | None, digits: int = 2, suffix: str = "") -> str:
    return "暂无" if value is None else f"{value:.{digits}f}{suffix}"


def _confidence(available: int, expected: int) -> str:
    if available <= 0:
        return "unavailable"
    return "medium" if available / expected >= 0.5 else "low"


def _metric(
    *,
    key: str,
    label: str,
    value: float | None,
    unit: str,
    period: str | None,
    reference_value: float | None,
    reference_period: str | None,
    interpretation: str,
    formula: str,
    source_codes: list[str],
    frequency: str = "monthly",
    tolerance: float = 0.05,
    freshness_override: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": _round(value),
        "unit": unit,
        "period": period,
        "frequency": frequency,
        "freshness": (
            "missing"
            if value is None
            else freshness_override or _freshness(period, frequency)
        ),
        "trend": _trend(value, reference_value, tolerance),
        "reference_value": _round(reference_value),
        "reference_period": reference_period,
        "interpretation": interpretation,
        "formula": formula,
        "source_codes": source_codes,
    }


def _employment_points(employment: Mapping[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not employment:
        return []
    for item in employment.get("series", []):
        if item.get("key") == key:
            points = [
                {"period": str(point["period"]), "value": float(point["value"])}
                for point in item.get("points", [])
                if point.get("period") is not None and point.get("value") is not None
            ]
            return sorted(points, key=lambda point: point["period"])
    return []


def _build_growth(
    series: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, float | None]]:
    gdp = _latest(series, "GB_GDP")
    gdp_previous = _calendar_reference(series.get("GB_GDP", []), 1, "quarterly")
    industrial = _latest(series, "GB_IP")
    industrial_previous = _calendar_reference(series.get("GB_IP", []), 3, "monthly")
    cli = _latest(series, "GB_CLI")
    cli_previous = _calendar_reference(series.get("GB_CLI", []), 3, "monthly")
    gdp_value = gdp["value"] if gdp else None
    industrial_value = industrial["value"] if industrial else None
    industrial_average = _trailing_mean(series.get("GB_IP", []), 3)
    cli_value = cli["value"] if cli else None
    cli_delta = cli_value - cli_previous["value"] if cli_value is not None and cli_previous else None

    gdp_current = (
        gdp_value is not None
        and _freshness(gdp["period"] if gdp else None, "quarterly") == "current"
    )
    industrial_current = (
        industrial_average is not None
        and _freshness(industrial["period"] if industrial else None, "monthly")
        == "current"
    )
    cli_current = (
        cli_value is not None
        and _freshness(cli["period"] if cli else None, "monthly") == "current"
    )
    current_gdp = gdp_value if gdp_current else None
    current_industrial_average = industrial_average if industrial_current else None
    current_cli = cli_value if cli_current else None
    current_cli_delta = cli_delta if cli_current else None

    positive = [
        current_gdp > 0 if current_gdp is not None else None,
        current_industrial_average > 0
        if current_industrial_average is not None
        else None,
        current_cli >= 100 and current_cli_delta > 0
        if current_cli is not None and current_cli_delta is not None
        else None,
    ]
    negative = [
        current_gdp < 0 if current_gdp is not None else None,
        current_industrial_average < 0
        if current_industrial_average is not None
        else None,
        current_cli < 100 and current_cli_delta < 0
        if current_cli is not None and current_cli_delta is not None
        else None,
    ]
    available = sum(item is not None for item in positive)
    if sum(item is True for item in negative) >= 2:
        state_key, state_label, tone = "contraction", "活动收缩", "negative"
    elif sum(item is True for item in positive) >= 2:
        if current_gdp is not None and current_gdp < 1.5:
            state_key, state_label, tone = "slow_expansion_improving", "低速扩张，短期动能改善", "positive"
        else:
            state_key, state_label, tone = "moderate_expansion", "温和扩张，领先信号改善", "positive"
    elif available:
        state_key, state_label, tone = "mixed", "增长信号分化", "caution"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    metrics = [
        _metric(
            key="real_gdp_yoy", label="实际GDP同比", value=gdp_value, unit="%",
            period=gdp["period"] if gdp else None,
            reference_value=gdp_previous["value"] if gdp_previous else None,
            reference_period=gdp_previous["period"] if gdp_previous else None,
            interpretation="观察ONS季度实际GDP同比；不能把同比误写为环比或折年率。",
            formula="ONS实际GDP同比", source_codes=["GB_GDP"], frequency="quarterly",
        ),
        _metric(
            key="industrial_production_yoy", label="工业生产同比", value=industrial_value, unit="%",
            period=industrial["period"] if industrial else None,
            reference_value=industrial_previous["value"] if industrial_previous else None,
            reference_period=industrial_previous["period"] if industrial_previous else None,
            interpretation="状态判断使用连续三个月同比均值，以降低单月生产波动。",
            formula="最新工业生产同比；状态规则使用近3个月均值", source_codes=["GB_IP"],
        ),
        _metric(
            key="composite_leading_indicator", label="综合领先指标", value=cli_value, unit="点",
            period=cli["period"] if cli else None,
            reference_value=cli_previous["value"] if cli_previous else None,
            reference_period=cli_previous["period"] if cli_previous else None,
            interpretation="100附近是趋势基准；同时观察水平及近3个月方向。",
            formula="OECD CLI；参考值为3个月前", source_codes=["GB_CLI"], tolerance=0.02,
        ),
    ]
    return {
        "key": "growth", "title": "增长与景气", "state_key": state_key,
        "state_label": state_label, "tone": tone,
        "summary": (
            f"实际GDP同比 {_fmt(gdp_value, suffix='%')}，工业生产同比 {_fmt(industrial_value, suffix='%')}，"
            f"近3个月工业均值 {_fmt(industrial_average, suffix='%')}；CLI {_fmt(cli_value)}，"
            f"近3个月变化 {_fmt(cli_delta, suffix='点')}。"
        ),
        "confidence": _confidence(available, 3), "metrics": metrics,
    }, {
        "gdp": current_gdp,
        "industrial_average": current_industrial_average,
        "cli": current_cli,
        "cli_delta": current_cli_delta,
    }


def _build_labour(
    employment: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, float | None]]:
    unemployment = _employment_points(employment, "unemployment")
    youth = _employment_points(employment, "youth_unemployment")
    participation = _employment_points(employment, "labor_participation")
    employment_ratio = _employment_points(employment, "employment_ratio")

    def latest(points: list[dict[str, Any]]) -> float | None:
        return points[-1]["value"] if points else None

    def reference_point(points: list[dict[str, Any]], back: int) -> dict[str, Any] | None:
        return _calendar_reference(points, back, "monthly")

    unemployment_reference_point = reference_point(unemployment, 3)
    youth_reference_point = reference_point(youth, 12)
    participation_reference_point = reference_point(participation, 12)
    employment_reference_point = reference_point(employment_ratio, 12)
    unemployment_value = latest(unemployment)
    unemployment_reference = (
        unemployment_reference_point["value"] if unemployment_reference_point else None
    )
    youth_value = latest(youth)
    youth_reference = youth_reference_point["value"] if youth_reference_point else None
    participation_value = latest(participation)
    participation_reference = (
        participation_reference_point["value"] if participation_reference_point else None
    )
    employment_value = latest(employment_ratio)
    employment_reference = (
        employment_reference_point["value"] if employment_reference_point else None
    )
    unemployment_delta = (
        unemployment_value - unemployment_reference
        if unemployment_value is not None and unemployment_reference is not None else None
    )
    youth_change = youth_value - youth_reference if youth_value is not None and youth_reference is not None else None
    participation_change = (
        participation_value - participation_reference
        if participation_value is not None and participation_reference is not None else None
    )
    employment_change = (
        employment_value - employment_reference
        if employment_value is not None and employment_reference is not None else None
    )

    unemployment_current = (
        unemployment_value is not None
        and _labour_freshness(unemployment[-1]["period"] if unemployment else None)
        == "current"
    )
    youth_current = (
        youth_value is not None
        and _labour_freshness(youth[-1]["period"] if youth else None) == "current"
    )
    participation_current = (
        participation_value is not None
        and _labour_freshness(participation[-1]["period"] if participation else None)
        == "current"
    )
    employment_current = (
        employment_value is not None
        and _labour_freshness(
            employment_ratio[-1]["period"] if employment_ratio else None
        )
        == "current"
    )
    current_unemployment = unemployment_value if unemployment_current else None
    current_employment = employment_value if employment_current else None
    current_participation = participation_value if participation_current else None
    current_youth = youth_value if youth_current else None
    current_unemployment_delta = unemployment_delta if unemployment_current else None
    current_employment_change = employment_change if employment_current else None
    current_participation_change = (
        participation_change if participation_current else None
    )
    current_youth_change = youth_change if youth_current else None

    if (
        (
            current_unemployment_delta is not None
            and current_unemployment_delta >= 0.3 - 1e-9
        )
        or (
            current_employment_change is not None
            and current_employment_change <= -0.5 + 1e-9
        )
    ):
        state_key, state_label, tone = "deteriorating", "劳动力市场转弱", "negative"
    elif (
        current_unemployment_delta is not None
        and current_unemployment_delta <= 0.1 + 1e-9
        and current_employment_change is not None
        and current_employment_change >= -0.2 - 1e-9
        and current_participation_change is not None
        and current_participation_change >= 0
    ):
        if (
            current_youth is not None
            and current_youth >= 15
            and (current_youth_change or 0) > 0
        ):
            state_key, state_label, tone = "stable_youth_weakness", "总体稳定，青年就业偏弱", "neutral"
        else:
            state_key, state_label, tone = "resilient", "就业保持稳定", "positive"
    elif any(
        value is not None
        for value in (current_unemployment, current_employment, current_participation)
    ):
        state_key, state_label, tone = "mixed", "就业信号分化", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    def labour_metric(
        key: str, label: str, points: list[dict[str, Any]],
        reference_point: dict[str, Any] | None,
        value: float | None, ref: float | None, interpretation: str,
    ) -> dict[str, Any]:
        period = points[-1]["period"] if points else None
        labour_freshness = (
            _labour_freshness(period) if value is not None else "missing"
        )
        return _metric(
            key=key, label=label, value=value, unit="%",
            period=period,
            reference_value=ref,
            reference_period=reference_point["period"] if reference_point else None,
            interpretation=interpretation,
            formula="ONS英国劳动力调查季调滚动三个月序列",
            source_codes=["ONS:LMS"],
            freshness_override=labour_freshness,
        )

    metrics = [
        labour_metric("unemployment_rate", "失业率", unemployment, unemployment_reference_point, unemployment_value, unemployment_reference,
                      "16岁以上劳动力、季调、滚动三个月；与3个月前比较。"),
        labour_metric("youth_unemployment_rate", "青年失业率", youth, youth_reference_point, youth_value, youth_reference,
                      "16—24岁劳动力、季调、滚动三个月；与12个月前比较。"),
        labour_metric("participation_rate", "劳动参与率", participation, participation_reference_point, participation_value, participation_reference,
                      "16—64岁人口、季调、滚动三个月；与12个月前比较。"),
        labour_metric("employment_rate", "就业率", employment_ratio, employment_reference_point, employment_value, employment_reference,
                      "16—64岁人口、季调、滚动三个月；与12个月前比较。"),
    ]
    available = sum(
        (
            unemployment_current,
            youth_current,
            participation_current,
            employment_current,
        )
    )
    return {
        "key": "labour", "title": "就业与劳动力", "state_key": state_key,
        "state_label": state_label, "tone": tone,
        "summary": (
            f"失业率 {_fmt(unemployment_value, suffix='%')}，较3个月前变化 "
            f"{_fmt(unemployment_delta, suffix='个百分点')}；就业率 {_fmt(employment_value, suffix='%')}，"
            f"同比变化 {_fmt(employment_change, suffix='个百分点')}；青年失业率 {_fmt(youth_value, suffix='%')}。"
        ),
        "confidence": _confidence(available, 4), "metrics": metrics,
    }, {
        "unemployment_delta": current_unemployment_delta,
        "employment_change": current_employment_change,
        "youth": current_youth,
    }


def _build_inflation(series: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    headline = _latest(series, "GB_CPI")
    headline_previous = _calendar_reference(series.get("GB_CPI", []), 3, "monthly")
    core = _latest(series, "GB_CORE_CPI")
    core_previous = _calendar_reference(series.get("GB_CORE_CPI", []), 3, "monthly")
    headline_value = headline["value"] if headline else None
    core_value = core["value"] if core else None
    headline_delta = headline_value - headline_previous["value"] if headline_value is not None and headline_previous else None
    core_delta = core_value - core_previous["value"] if core_value is not None and core_previous else None
    headline_current = (
        headline_value is not None
        and _freshness(headline["period"] if headline else None, "monthly")
        == "current"
    )
    core_current = (
        core_value is not None
        and _freshness(core["period"] if core else None, "monthly") == "current"
    )
    current_headline = headline_value if headline_current else None
    current_core = core_value if core_current else None
    current_headline_delta = headline_delta if headline_current else None
    current_core_delta = core_delta if core_current else None
    if (
        current_headline is not None
        and current_core is not None
        and current_headline > 2.5
        and current_core > 2.5
    ):
        if (current_headline_delta or 0) >= 0.2 and (current_core_delta or 0) > 0.1:
            state_key, state_label, tone = "reaccelerating", "高于目标并重新加速", "negative"
        elif (current_headline_delta or 0) >= 0.2:
            state_key, state_label, tone = "headline_reaccelerating", "高于目标，表层通胀再升", "caution"
        else:
            state_key, state_label, tone = "above_target_sticky", "高于目标，核心仍有黏性", "caution"
    elif (
        current_headline is not None
        and current_core is not None
        and current_headline <= 2.3
        and current_core <= 2.3
    ):
        state_key, state_label, tone = "near_target", "接近2%目标", "positive"
    elif current_headline is not None or current_core is not None:
        state_key, state_label, tone = "mixed", "通胀信号分化", "caution"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    metrics = [
        _metric(
            key="headline_cpi_yoy", label="CPI同比", value=headline_value, unit="%",
            period=headline["period"] if headline else None,
            reference_value=headline_previous["value"] if headline_previous else None,
            reference_period=headline_previous["period"] if headline_previous else None,
            interpretation="英国央行通胀目标以CPI同比2%为基准；参考值为3个月前。",
            formula="ONS CPI十二个月变动率", source_codes=["GB_CPI"],
        ),
        _metric(
            key="core_cpi_yoy", label="核心CPI同比", value=core_value, unit="%",
            period=core["period"] if core else None,
            reference_value=core_previous["value"] if core_previous else None,
            reference_period=core_previous["period"] if core_previous else None,
            interpretation="剔除能源、食品、酒精和烟草后观察较持续的价格压力。",
            formula="ONS核心CPI十二个月变动率；参考值为3个月前", source_codes=["GB_CORE_CPI"],
        ),
    ]
    return {
        "key": "inflation", "title": "通胀环境", "state_key": state_key,
        "state_label": state_label, "tone": tone,
        "summary": (
            f"CPI同比 {_fmt(headline_value, suffix='%')}，核心CPI同比 {_fmt(core_value, suffix='%')}；"
            f"较3个月前分别变化 {_fmt(headline_delta)} / {_fmt(core_delta, suffix='个百分点')}。"
        ),
        "confidence": _confidence(sum((headline_current, core_current)), 2),
        "metrics": metrics,
    }


def _shift_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, min(value.day, 28))


def _event_value_at(points: list[dict[str, Any]], cutoff: date) -> float | None:
    eligible = [point for point in points if date.fromisoformat(str(point["period"])[:10]) <= cutoff]
    return eligible[-1]["value"] if eligible else None


def _build_policy(
    series: dict[str, list[dict[str, Any]]],
    policy_verified_at: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    points = series.get("GB_BOE", [])
    rate = points[-1] if points else None
    core = _latest(series, "GB_CORE_CPI")
    rate_value = rate["value"] if rate else None
    core_value = core["value"] if core else None
    verification_value = policy_verified_at or (rate.get("retrieved_at") if rate else None)
    verified_period = (
        verification_value.isoformat()[:10]
        if hasattr(verification_value, "isoformat")
        else str(verification_value or "")[:10]
    )
    try:
        verified_date = date.fromisoformat(verified_period)
        policy_freshness = "current" if max((date.today() - verified_date).days, 0) <= 30 else "stale"
    except ValueError:
        verified_date = date.today()
        verified_period = ""
        policy_freshness = "stale" if rate_value is not None else "missing"

    six_month_cutoff = _shift_months(verified_date, -6)
    twelve_month_cutoff = _shift_months(verified_date, -12)
    six_month_rate = _event_value_at(points, six_month_cutoff) if points else None
    twelve_month_rate = _event_value_at(points, twelve_month_cutoff) if points else None
    six_month_change = (
        rate_value - six_month_rate
        if rate_value is not None and six_month_rate is not None else None
    )
    twelve_month_change = (
        rate_value - twelve_month_rate
        if rate_value is not None and twelve_month_rate is not None else None
    )
    real_proxy = rate_value - core_value if rate_value is not None and core_value is not None else None
    core_freshness = (
        _freshness(core["period"] if core else None, "monthly")
        if core_value is not None
        else "missing"
    )
    real_proxy_freshness = _combined_freshness(policy_freshness, core_freshness)
    current_real_proxy = real_proxy if real_proxy_freshness == "current" else None

    if policy_freshness == "stale":
        state_key, state_label, tone = "stale_policy_input", "政策利率核验日期偏旧", "caution"
    elif six_month_change is not None and six_month_change <= -0.25:
        state_key, state_label, tone = "easing", "近6个月降息", "neutral"
    elif six_month_change is not None and six_month_change >= 0.25:
        state_key, state_label, tone = "tightening", "近6个月加息", "caution"
    elif twelve_month_change is not None and twelve_month_change < 0:
        state_key, state_label, tone = "easing_then_hold", "降息后持稳", "neutral"
    elif current_real_proxy is not None and current_real_proxy > 0.5:
        state_key, state_label, tone = "stable_restrictive_proxy", "利率持稳，限制性代理仍为正", "caution"
    elif rate_value is not None:
        state_key, state_label, tone = "stable", "政策利率大体稳定", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    common_freshness = policy_freshness if rate_value is not None else "missing"
    metrics = [
        _metric(
            key="bank_rate", label="当前Bank Rate", value=rate_value, unit="%",
            period=rate["period"] if rate else None,
            reference_value=six_month_rate,
            reference_period=six_month_cutoff.isoformat() if six_month_rate is not None else None,
            interpretation=(
                "该日期是最近一次利率变动生效日，不是数据过期日；事件表在持平会议后不会新增记录。"
            ),
            formula="BoE Bank Rate变更历史；当前值已按最近抓取日期复核",
            source_codes=["GB_BOE"], frequency="mixed", freshness_override=common_freshness,
        ),
        _metric(
            key="six_month_rate_change", label="Bank Rate近6个月变化", value=six_month_change, unit="百分点",
            period=verified_period or (rate["period"] if rate else None),
            reference_value=0 if six_month_change is not None else None,
            reference_period=six_month_cutoff.isoformat() if six_month_rate is not None else None,
            interpretation="按日历时点的有效利率比较；持平会议即使没有新事件，也会正确得到0。",
            formula="核验日有效Bank Rate − 6个月前有效Bank Rate",
            source_codes=["GB_BOE"], frequency="mixed", freshness_override=common_freshness,
        ),
        _metric(
            key="twelve_month_rate_change", label="Bank Rate近12个月变化", value=twelve_month_change, unit="百分点",
            period=verified_period or (rate["period"] if rate else None),
            reference_value=0 if twelve_month_change is not None else None,
            reference_period=twelve_month_cutoff.isoformat() if twelve_month_rate is not None else None,
            interpretation="按12个月日历窗口观察更完整的政策方向，不以事件条数代替时间跨度。",
            formula="核验日有效Bank Rate − 12个月前有效Bank Rate",
            source_codes=["GB_BOE"], frequency="mixed", freshness_override=common_freshness,
        ),
        _metric(
            key="ex_post_real_rate_proxy", label="事后实际利率代理", value=real_proxy, unit="百分点",
            period=_combined_period(
                verified_period or (rate["period"] if rate else None),
                core["period"] if core else None,
            ),
            reference_value=None, reference_period=core["period"] if core else None,
            interpretation="Bank Rate与核心CPI观察期不同；只作方向性代理，不是中性利率缺口。",
            formula="当前Bank Rate − 最新核心CPI同比", source_codes=["GB_BOE", "GB_CORE_CPI"],
            frequency="mixed", freshness_override=real_proxy_freshness,
        ),
    ]
    available = sum(
        metric["value"] is not None and metric["freshness"] == "current"
        for metric in metrics
    )
    confidence = _confidence(available, 4)
    return {
        "key": "monetary_policy", "title": "英格兰银行货币政策",
        "state_key": state_key, "state_label": state_label, "tone": tone,
        "summary": (
            f"Bank Rate {_fmt(rate_value, suffix='%')}，最近变动日 {rate['period'] if rate else '暂无'}，"
            f"数据核验至 {verified_period or '未知'}；近6个月变化 {_fmt(six_month_change, suffix='个百分点')}，"
            f"近12个月变化 {_fmt(twelve_month_change, suffix='个百分点')}，事后实际利率代理 "
            f"{_fmt(real_proxy, suffix='个百分点')}。"
        ),
        "confidence": confidence, "metrics": metrics,
    }, {
        "six_month_change": six_month_change,
        "twelve_month_change": twelve_month_change,
        "real_proxy": real_proxy,
        "policy_stale": 1.0 if policy_freshness == "stale" else 0.0,
        "verified_period": verified_period,
    }


def _percentage_change(latest: dict[str, Any] | None, reference: dict[str, Any] | None) -> float | None:
    if latest is None or reference is None or not reference["value"]:
        return None
    return (latest["value"] / reference["value"] - 1) * 100


def _bounded_daily_return(
    latest: dict[str, Any] | None,
    reference: dict[str, Any] | None,
    *,
    minimum_days: int = 45,
    maximum_days: int = 140,
) -> float | None:
    """Return a roughly three-month market move only for a plausible date span."""

    if latest is None or reference is None or not reference.get("value"):
        return None
    try:
        latest_date = date.fromisoformat(str(latest["date"])[:10])
        reference_date = date.fromisoformat(str(reference["date"])[:10])
    except (KeyError, TypeError, ValueError):
        return None
    elapsed = (latest_date - reference_date).days
    if elapsed < minimum_days or elapsed > maximum_days:
        return None
    return (latest["value"] / reference["value"] - 1) * 100


def _build_financial_conditions(series: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    money = _latest(series, "GB_M3")
    money_previous = _calendar_reference(series.get("GB_M3", []), 3, "monthly")
    equity, equity_previous = _latest(series, "FTSE100"), _reference(series, "FTSE100", 60)
    fx, fx_previous = _latest(series, "GBPCNY"), _reference(series, "GBPCNY", 60)
    money_value = money["value"] if money else None
    money_delta = money_value - money_previous["value"] if money_value is not None and money_previous else None
    equity_return = _bounded_daily_return(equity, equity_previous)
    fx_change = _bounded_daily_return(fx, fx_previous)
    money_current = (
        money_value is not None
        and _freshness(money["period"] if money else None, "monthly") == "current"
    )
    equity_current = (
        equity_return is not None
        and _freshness(equity["period"] if equity else None, "daily") == "current"
    )
    current_money = money_value if money_current else None
    current_equity_return = equity_return if equity_current else None
    restrictive = sum(
        (
            current_money is not None and current_money < 0,
            current_equity_return is not None and current_equity_return <= -10,
        )
    )
    supportive = sum(
        (
            current_money is not None and current_money >= 3,
            current_equity_return is not None and current_equity_return >= 5,
        )
    )
    if restrictive >= 2:
        state_key, state_label, tone = "tight", "可见条件偏紧", "negative"
    elif supportive >= 2:
        state_key, state_label, tone = "supportive", "可见条件有支撑", "positive"
    elif current_money is not None or current_equity_return is not None:
        state_key, state_label, tone = "mixed_limited", "货币增长有支撑，市场信号有限", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    metrics = [
        _metric(
            key="m3_yoy", label="广义货币M3同比", value=money_value, unit="%",
            period=money["period"] if money else None,
            reference_value=money_previous["value"] if money_previous else None,
            reference_period=money_previous["period"] if money_previous else None,
            interpretation="货币增长是流动性背景，不等同银行对实体经济的新增信贷。",
            formula="英国M3同比；参考值为3个月前", source_codes=["GB_M3"],
        ),
        _metric(
            key="m3_three_month_change", label="M3同比增速3个月变化", value=money_delta, unit="百分点",
            period=money["period"] if money else None,
            reference_value=0 if money_delta is not None else None,
            reference_period=money_previous["period"] if money_previous else None,
            interpretation="观察货币同比增速的方向，不把增速上升直接等同需求走强。",
            formula="最新M3同比 − 3个月前M3同比", source_codes=["GB_M3"],
        ),
        _metric(
            key="equity_return_60d", label="富时100约3个月回报", value=equity_return, unit="%",
            period=equity["period"] if equity else None,
            reference_value=0 if equity_return is not None else None,
            reference_period=equity_previous["period"] if equity_previous else None,
            interpretation="富时100具有较高海外收入敞口，只代表风险偏好与盈利预期的一部分。",
            formula="最新FTSE 100 ÷ 60个交易日前 − 1", source_codes=["FTSE100"], frequency="daily",
        ),
        _metric(
            key="gbp_cny_change_60d", label="英镑兑人民币约3个月变化", value=fx_change, unit="%",
            period=fx["period"] if fx else None,
            reference_value=0 if fx_change is not None else None,
            reference_period=fx_previous["period"] if fx_previous else None,
            interpretation="这是双边人民币交叉汇率，只作外部背景，不进入金融条件状态判断。",
            formula="最新GBP/CNY ÷ 60个观测日前 − 1", source_codes=["GBPCNY"], frequency="daily",
        ),
    ]
    return {
        "key": "financial_conditions", "title": "货币与市场背景",
        "state_key": state_key, "state_label": state_label, "tone": tone,
        "summary": (
            f"M3同比 {_fmt(money_value, suffix='%')}，较3个月前变化 {_fmt(money_delta, suffix='个百分点')}；"
            f"富时100约3个月回报 {_fmt(equity_return, suffix='%')}。缺少英国国债收益率、信用利差、"
            "银行贷款和央行资产负债表，因此不称为完整金融条件指数。"
        ),
        "confidence": _confidence(sum((money_current, equity_current)), 6),
        "metrics": metrics,
    }


def _downturn_breadth(
    growth: Mapping[str, float | None], labour: Mapping[str, float | None],
    series: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    checks: list[tuple[bool, str, str]] = []

    def add(value: float | None, active: bool, trigger: str, offset: str) -> None:
        if value is not None:
            checks.append((active, trigger, offset))

    add(growth.get("gdp"), bool(growth.get("gdp") is not None and growth["gdp"] < 0),
        f"实际GDP同比为 {_fmt(growth.get('gdp'), suffix='%')}", f"实际GDP同比仍为 {_fmt(growth.get('gdp'), suffix='%')}")
    add(growth.get("industrial_average"), bool(growth.get("industrial_average") is not None and growth["industrial_average"] < 0),
        f"工业生产近3个月均值为 {_fmt(growth.get('industrial_average'), suffix='%')}", f"工业生产近3个月均值为 {_fmt(growth.get('industrial_average'), suffix='%')}")
    cli, cli_delta = growth.get("cli"), growth.get("cli_delta")
    if cli is not None and cli_delta is not None:
        checks.append((cli < 100 and cli_delta < 0,
                       f"CLI低于100且近3个月下降 {_fmt(abs(cli_delta), suffix='点')}",
                       f"CLI为 {_fmt(cli)}，近3个月变化 {_fmt(cli_delta, suffix='点')}"))
    add(labour.get("unemployment_delta"), bool(labour.get("unemployment_delta") is not None and labour["unemployment_delta"] >= 0.3 - 1e-9),
        f"失业率较3个月前上升 {_fmt(labour.get('unemployment_delta'), suffix='个百分点')}", f"失业率较3个月前变化 {_fmt(labour.get('unemployment_delta'), suffix='个百分点')}")
    add(labour.get("employment_change"), bool(labour.get("employment_change") is not None and labour["employment_change"] <= -0.3 + 1e-9),
        f"就业率较一年前下降 {_fmt(abs(labour.get('employment_change') or 0), suffix='个百分点')}", f"就业率较一年前变化 {_fmt(labour.get('employment_change'), suffix='个百分点')}")
    equity, equity_reference = _latest(series, "FTSE100"), _reference(series, "FTSE100", 60)
    equity_return = _bounded_daily_return(equity, equity_reference)
    if (
        equity_return is not None
        and _freshness(equity["period"] if equity else None, "daily") == "current"
    ):
        add(
            equity_return,
            equity_return <= -10,
            f"富时100约3个月下跌 {_fmt(abs(equity_return), suffix='%')}",
            f"富时100约3个月回报 {_fmt(equity_return, suffix='%')}",
        )

    total = len(checks)
    triggers = [trigger for active, trigger, _ in checks if active]
    offsets = [offset for active, _, offset in checks if not active]
    active = len(triggers)
    if total < 3:
        state_key, state_label, tone = "unavailable", "下行证据不足", "unavailable"
    elif active >= 3:
        state_key, state_label, tone = "broad", "下行证据广泛", "negative"
    elif active >= 2:
        state_key, state_label, tone = "elevated", "下行证据增加", "caution"
    else:
        state_key, state_label, tone = "limited", "下行证据有限", "positive"
    return {
        "state_key": state_key, "state_label": state_label, "tone": tone,
        "active_signals": active, "total_signals": total,
        "triggers": triggers, "offsets": offsets,
        "summary": f"当前可用的{total}项增长、就业与市场检查中有{active}项触发。这是证据广度，不是衰退概率。",
        "methodology": (
            "检查GDP同比<0、工业生产连续3个月均值<0、CLI低于100且继续下降、"
            "失业率3个月上升至少0.3个百分点、就业率较一年前下降至少0.3个百分点、"
            "富时100约3个月跌幅超过10%；仅统计可用信号。"
        ),
    }


def _latest_period(series: dict[str, list[dict[str, Any]]], codes: tuple[str, ...]) -> str | None:
    periods = [
        point["period"] for code in codes
        if (point := _latest(series, code)) is not None and point["period"] is not None
    ]
    return max(periods) if periods else None


def _build_uk_macro_overview(
    rows: Iterable[Any], employment: Mapping[str, Any] | None = None,
    *, employment_error: str | None = None, policy_verified_at: Any = None,
) -> dict[str, Any]:
    series = _series(rows)
    growth, growth_diagnostics = _build_growth(series)
    labour, labour_diagnostics = _build_labour(employment)
    inflation = _build_inflation(series)
    financial = _build_financial_conditions(series)
    policy, policy_diagnostics = _build_policy(series, policy_verified_at)
    pillars = [growth, labour, inflation, financial, policy]
    downturn = _downturn_breadth(growth_diagnostics, labour_diagnostics, series)
    metrics = [metric for pillar in pillars for metric in pillar["metrics"]]
    missing_codes = [code for code in UK_OVERVIEW_CODES if not series.get(code)]
    stale_metrics = [metric["label"] for metric in metrics if metric["freshness"] == "stale"]
    warnings = [
        "本页使用当前修订后的最终快照；核心序列尚缺完整发布vintage，不能解读为伪实时回测或衰退概率。",
        "ONS劳动力调查为滚动三个月估计，近期存在抽样波动；应结合连续数期和行政就业数据判断。",
        "金融条件缺少英国国债收益率、信用利差、银行贷款及英格兰银行资产负债表；可见信号不代表完整融资环境。",
        "英镑兑人民币仅作外部背景；富时100海外收入占比较高，两者均不能单独代表英国国内需求。",
    ]
    if employment:
        warnings.extend(
            str(item)
            for item in employment.get("warnings", [])
            if item not in warnings and "抽样波动" not in str(item)
        )
    if employment_error:
        warnings.append("ONS就业专题本次未在时限内完成；就业模块保留为缺失，失败不解释为中性或零。")
    if missing_codes:
        warnings.append(f"当前缺少指标：{'、'.join(missing_codes)}。缺失值未按0处理。")
    if policy_diagnostics.get("policy_stale"):
        rate = _latest(series, "GB_BOE")
        warnings.append(
            f"Bank Rate最近一次变动发生于{rate['period'] if rate else '未知日期'}，但最近抓取核验日期偏旧；"
            "已从覆盖率中排除，不能作为当前政策利率。"
        )
    else:
        rate = _latest(series, "GB_BOE")
        if rate:
            warnings.append(
                f"Bank Rate是事件型变更历史：{rate['period']}是最近一次变动日，"
                f"不是过期日期；当前值已核验至{policy_diagnostics.get('verified_period') or '未知日期'}。"
            )
    policy_labels = {
        "当前Bank Rate",
        "Bank Rate近6个月变化",
        "Bank Rate近12个月变化",
        "事后实际利率代理",
    }
    specially_reported_stale = (
        policy_labels if policy_diagnostics.get("policy_stale") else set()
    )
    other_stale = [
        label for label in stale_metrics if label not in specially_reported_stale
    ]
    if other_stale:
        warnings.append(f"当前数据偏旧：{'、'.join(dict.fromkeys(other_stale))}；覆盖率已排除这些读数。")

    available_pillars = [pillar for pillar in pillars if pillar["confidence"] != "unavailable"]
    non_financial_metrics = [
        metric for pillar in pillars if pillar["key"] != "financial_conditions" for metric in pillar["metrics"]
    ]
    financial_core = [metric for metric in financial["metrics"] if metric["key"] in {"m3_yoy", "equity_return_60d"}]
    covered_evidence = sum(
        metric["value"] is not None and metric["freshness"] == "current"
        for metric in non_financial_metrics + financial_core
    )
    # Six desired finance/money channels: broad money, equities, gilts,
    # credit spreads, bank lending and the BoE balance sheet.
    expected_evidence = len(non_financial_metrics) + 6
    coverage = covered_evidence / expected_evidence if expected_evidence else 0.0
    if not available_pillars:
        status = "unavailable"
    elif (
        len(available_pillars) < len(pillars)
        or any(pillar["confidence"] == "low" for pillar in pillars)
        or employment_error or missing_codes or stale_metrics
    ):
        status = "partial"
    else:
        status = "ok"
    inflation_current = inflation["confidence"] != "unavailable"
    policy_current = policy["confidence"] != "unavailable"
    growth_current = growth["confidence"] != "unavailable"
    if downturn["state_key"] == "broad" or (
        inflation_current and inflation["tone"] == "negative"
    ):
        tone = "negative"
    elif (
        (inflation_current and inflation["tone"] == "caution")
        or (policy_current and policy["tone"] == "caution")
        or (growth_current and growth["tone"] == "caution")
    ):
        tone = "caution"
    else:
        tone = "neutral"

    headline = (
        f"{growth['state_label']}；{labour['state_label']}；{inflation['state_label']}。"
        f"{downturn['state_label']}，{policy['state_label']}。"
    )
    transmission = [
        {
            "key": "policy", "title": "英格兰银行政策起点", "state_label": policy["state_label"],
            "tone": policy["tone"],
            "detail": (
                f"近6个月变化 {_fmt(policy_diagnostics.get('six_month_change'), suffix='个百分点')}，"
                f"近12个月变化 {_fmt(policy_diagnostics.get('twelve_month_change'), suffix='个百分点')}；"
                "事件表的最近变动日与当前核验日分开展示。"
            ),
            "periods": [metric["period"] for metric in policy["metrics"] if metric["period"]],
        },
        {
            "key": "financial", "title": "货币与市场传导", "state_label": financial["state_label"],
            "tone": financial["tone"], "detail": financial["summary"],
            "periods": [metric["period"] for metric in financial["metrics"] if metric["period"]],
        },
        {
            "key": "activity", "title": "实体活动", "state_label": growth["state_label"],
            "tone": growth["tone"], "detail": growth["summary"],
            "periods": [metric["period"] for metric in growth["metrics"] if metric["period"]],
        },
        {
            "key": "labour", "title": "就业反馈", "state_label": labour["state_label"],
            "tone": labour["tone"], "detail": labour["summary"],
            "periods": [metric["period"] for metric in labour["metrics"] if metric["period"]],
        },
        {
            "key": "prices", "title": "价格反馈", "state_label": inflation["state_label"],
            "tone": inflation["tone"], "detail": inflation["summary"],
            "periods": [metric["period"] for metric in inflation["metrics"] if metric["period"]],
        },
    ]
    for step in transmission:
        step["periods"] = list(dict.fromkeys(step["periods"]))

    watch_items: list[str] = []
    if (labour_diagnostics.get("youth") or 0) >= 15:
        watch_items.append("青年失业率能否回落，避免劳动力市场分化继续扩大。")
    core = _latest(series, "GB_CORE_CPI")
    if (
        core
        and _freshness(core["period"], "monthly") == "current"
        and core["value"] > 2.3
    ):
        watch_items.append("核心CPI能否重新向2%目标回落，而不是在高位保持黏性。")
    if policy_diagnostics.get("policy_stale"):
        watch_items.append("优先核验下一次Bank Rate更新；核验前不把旧利率外推为当前政策立场。")
    watch_items.append("补齐英国国债收益率、信用利差、银行贷款和工资后，再扩展金融条件与通胀传导判断。")

    market_period = _latest_period(series, ("FTSE100", "GBPCNY"))
    monthly_period = _latest_period(series, ("GB_IP", "GB_CLI", "GB_CPI", "GB_CORE_CPI", "GB_M3"))
    quarterly_period = _latest_period(series, ("GB_GDP",))
    employment_period = str(employment.get("latest_month")) if employment and employment.get("latest_month") else None
    return {
        "region": "GB", "country": "英国", "title": "英国宏观综合分析",
        "status": status, "confidence": _confidence(len(available_pillars), len(pillars)),
        "coverage": round(coverage, 4), "realtime_ready": False, "tone": tone,
        "headline": headline, "as_of": market_period or monthly_period or quarterly_period,
        "freshness": {
            "market_observation_date": market_period,
            "monthly_observation_period": monthly_period,
            "quarterly_observation_period": quarterly_period,
            "employment_observation_period": employment_period,
        },
        "methodology_version": METHODOLOGY_VERSION,
        "methodology_note": (
            "五个分项分别按公开、可复核规则判断，不合成总分；不同频率保留各自观察期，"
            "缺失与过期值不填0。下行证据广度只统计当前可用规则，不输出未经回测的衰退概率。"
        ),
        "pillars": pillars, "downturn_breadth": downturn,
        "transmission": transmission, "watch_items": watch_items,
        "data_source_path": "/data-sources/uk", "warnings": warnings,
    }


def build_uk_macro_overview(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(DataPoint)
        .where(DataPoint.indicator_code.in_(UK_OVERVIEW_CODES))
        .order_by(DataPoint.indicator_code, DataPoint.date)
    ).scalars().all()
    policy_refresh = db.scalar(
        select(RefreshResult)
        .where(
            RefreshResult.indicator_code == "GB_BOE",
            RefreshResult.status.in_(("success", "no_change")),
        )
        .order_by(RefreshResult.finished_at.desc(), RefreshResult.id.desc())
        .limit(1)
    )
    employment, employment_error = _load_employment_enrichment()
    return _build_uk_macro_overview(
        rows,
        employment,
        employment_error=employment_error,
        policy_verified_at=policy_refresh.last_success_at if policy_refresh else None,
    )

"""Transparent current-snapshot overview for the euro area (EA21).

The overview keeps growth, labour, inflation, monetary policy and the
available liquidity/market evidence separate.  It deliberately avoids a
synthetic score and preserves each observation's own period.  Stored series
do not yet carry complete release vintages, so this is a current/final-data
snapshot rather than a pseudo-real-time recession model.
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

from app.fetchers.eu_employment import fetch_eu_employment_dashboard
from app.models import DataPoint
from app.services.indicator_series import is_current_series_point


METHODOLOGY_VERSION = "1.0.0"
EMPLOYMENT_ENRICHMENT_TIMEOUT_SECONDS = 5.0
EMPLOYMENT_ENRICHMENT_TTL_SECONDS = 6 * 60 * 60

_employment_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eu-overview-employment")
_employment_future: Future[dict[str, Any]] | None = None
_employment_future_started_at: float | None = None
_employment_lock = Lock()

EU_OVERVIEW_CODES = (
    "EU_GDP",
    "EU_IP",
    "EU_CLI",
    "EU_CPI",
    "EU_CORE_CPI",
    "EU_ECB",
    "EU_ECB_ASSETS",
    "STOXX50",
    "EURCNY",
)


def _load_employment_enrichment() -> tuple[Mapping[str, Any] | None, str | None]:
    """Bound Eurostat latency while one shared background request warms."""

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
            _employment_future = _employment_executor.submit(fetch_eu_employment_dashboard)
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
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _period(code: str, observed: date | str | None) -> str | None:
    if observed is None:
        return None
    raw = observed if isinstance(observed, str) else observed.isoformat()
    if len(raw) == 7:
        return raw
    if code == "EU_GDP":
        parsed = date.fromisoformat(raw[:10])
        return f"{parsed.year}-Q{((parsed.month - 1) // 3) + 1}"
    if code in {"EU_ECB_ASSETS", "STOXX50", "EURCNY"}:
        return raw[:10]
    return raw[:7]


def _series(rows: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {code: [] for code in EU_OVERVIEW_CODES}
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
        grouped[code].append(
            {
                "date": observed,
                "period": _period(code, observed),
                "value": float(value),
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


def _year_ago_reference(
    points: list[dict[str, Any]], *, tolerance_days: int = 10
) -> dict[str, Any] | None:
    """Match a daily/weekly series near the same date one year earlier."""

    if not points:
        return None
    try:
        latest_date = date.fromisoformat(str(points[-1]["period"])[:10])
        try:
            target = latest_date.replace(year=latest_date.year - 1)
        except ValueError:  # February 29
            target = latest_date.replace(year=latest_date.year - 1, day=28)
        candidates = []
        for point in points[:-1]:
            observed = date.fromisoformat(str(point["period"])[:10])
            distance = abs((observed - target).days)
            if distance <= tolerance_days:
                candidates.append((distance, -observed.toordinal(), point))
        return min(candidates, key=lambda item: item[:2])[2] if candidates else None
    except (KeyError, TypeError, ValueError):
        return None


def _trend(value: float | None, reference: float | None, *, tolerance: float = 0.05) -> str:
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
        else:
            year_text, month_text = period[:7].split("-")
            observed = date(int(year_text), int(month_text), 1)
            limit = 100
    except (TypeError, ValueError):
        return "missing"
    return "current" if max((date.today() - observed).days, 0) <= limit else "stale"


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


def _is_consecutive_months(points: list[dict[str, Any]]) -> bool:
    if len(points) < 2:
        return True
    try:
        indices = [_month_index(point["period"]) for point in points]
    except (KeyError, TypeError, ValueError):
        return False
    return all(right - left == 1 for left, right in zip(indices, indices[1:]))


def _trailing_mean(points: list[dict[str, Any]], count: int) -> float | None:
    trailing = points[-count:]
    if len(trailing) != count or not _is_consecutive_months(trailing):
        return None
    return mean(point["value"] for point in trailing)


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _fmt(value: float | None, digits: int = 2, suffix: str = "") -> str:
    return "暂无" if value is None else f"{value:.{digits}f}{suffix}"


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
        "trend": _trend(value, reference_value, tolerance=tolerance),
        "reference_value": _round(reference_value),
        "reference_period": reference_period,
        "interpretation": interpretation,
        "formula": formula,
        "source_codes": source_codes,
    }


def _confidence(available: int, expected: int) -> str:
    if available <= 0:
        return "unavailable"
    coverage = available / expected
    # Complete current data is still capped at medium until release vintages
    # are stored and the rules can be replayed in pseudo real time.
    if coverage >= 0.5:
        return "medium"
    return "low"


def _employment_points(employment: Mapping[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not employment:
        return []
    for item in employment.get("series", []):
        if item.get("key") != key:
            continue
        points = [
            {"period": str(point["period"]), "value": float(point["value"])}
            for point in item.get("points", [])
            if point.get("period") is not None and point.get("value") is not None
        ]
        points.sort(key=lambda point: point["period"])
        return points
    return []


def _build_growth(
    series: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, float | None]]:
    gdp = _latest(series, "EU_GDP")
    gdp_previous = _calendar_reference(series.get("EU_GDP", []), 1, "quarterly")
    industrial = _latest(series, "EU_IP")
    industrial_previous = _calendar_reference(series.get("EU_IP", []), 3, "monthly")
    sentiment = _latest(series, "EU_CLI")
    sentiment_previous = _calendar_reference(series.get("EU_CLI", []), 3, "monthly")

    gdp_value = gdp["value"] if gdp else None
    industrial_value = industrial["value"] if industrial else None
    industrial_average = _trailing_mean(series.get("EU_IP", []), 3)
    sentiment_value = sentiment["value"] if sentiment else None
    sentiment_delta = (
        sentiment_value - sentiment_previous["value"]
        if sentiment_value is not None and sentiment_previous is not None
        else None
    )

    gdp_current = (
        gdp_value is not None
        and _freshness(gdp["period"] if gdp else None, "quarterly") == "current"
    )
    industrial_current = (
        industrial_average is not None
        and _freshness(industrial["period"] if industrial else None, "monthly")
        == "current"
    )
    sentiment_current = (
        sentiment_value is not None
        and _freshness(sentiment["period"] if sentiment else None, "monthly")
        == "current"
    )
    current_gdp = gdp_value if gdp_current else None
    current_industrial_average = industrial_average if industrial_current else None
    current_sentiment = sentiment_value if sentiment_current else None
    current_sentiment_delta = sentiment_delta if sentiment_current else None

    positive_checks = [
        current_gdp > 0 if current_gdp is not None else None,
        current_industrial_average > 0
        if current_industrial_average is not None
        else None,
        current_sentiment >= 100 and current_sentiment_delta > 0
        if current_sentiment is not None and current_sentiment_delta is not None
        else None,
    ]
    negative_checks = [
        current_gdp < 0 if current_gdp is not None else None,
        current_industrial_average < 0
        if current_industrial_average is not None
        else None,
        current_sentiment < 100 and current_sentiment_delta < 0
        if current_sentiment is not None and current_sentiment_delta is not None
        else None,
    ]
    positive_count = sum(value is True for value in positive_checks)
    negative_count = sum(value is True for value in negative_checks)
    available_checks = sum(value is not None for value in positive_checks)

    if negative_count >= 2:
        state_key, state_label, tone = "contraction", "活动收缩", "negative"
    elif positive_count >= 2:
        state_key, state_label, tone = "moderate_expansion", "温和扩张", "positive"
    elif (
        current_gdp is not None
        and current_gdp > 0
        and current_industrial_average is not None
        and current_industrial_average < 0
        and current_sentiment_delta is not None
        and current_sentiment_delta > 0
    ):
        state_key, state_label, tone = (
            "slow_expansion_repairing",
            "低速扩张，景气修复未完成",
            "caution",
        )
    elif available_checks > 0:
        state_key, state_label, tone = "mixed", "增长信号分化", "caution"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    metrics = [
        _metric(
            key="real_gdp_yoy",
            label="实际GDP同比",
            value=gdp_value,
            unit="%",
            period=gdp["period"] if gdp else None,
            reference_value=gdp_previous["value"] if gdp_previous else None,
            reference_period=gdp_previous["period"] if gdp_previous else None,
            interpretation="观察欧元区季度总量同比；不能把同比误写为环比或折年率。",
            formula="Eurostat实际GDP同比",
            source_codes=["EU_GDP"],
            frequency="quarterly",
        ),
        _metric(
            key="industrial_production_yoy",
            label="工业生产同比",
            value=industrial_value,
            unit="%",
            period=industrial["period"] if industrial else None,
            reference_value=industrial_previous["value"] if industrial_previous else None,
            reference_period=industrial_previous["period"] if industrial_previous else None,
            interpretation="生产同比是实体活动同步证据；状态判断使用连续三个月均值降低噪声。",
            formula="最新工业生产同比；状态规则使用近3个月均值",
            source_codes=["EU_IP"],
        ),
        _metric(
            key="economic_sentiment",
            label="经济景气指数（ESI）",
            value=sentiment_value,
            unit="点",
            period=sentiment["period"] if sentiment else None,
            reference_value=sentiment_previous["value"] if sentiment_previous else None,
            reference_period=sentiment_previous["period"] if sentiment_previous else None,
            interpretation="100附近为长期均值；同时观察水平与近3个月方向。",
            formula="欧盟委员会ESI；参考值为3个月前",
            source_codes=["EU_CLI"],
            tolerance=0.02,
        ),
    ]
    summary = (
        f"实际GDP同比 {_fmt(gdp_value, suffix='%')}，工业生产同比 {_fmt(industrial_value, suffix='%')}，"
        f"近3个月工业均值 {_fmt(industrial_average, suffix='%')}；ESI {_fmt(sentiment_value)}，"
        f"近3个月变化 {_fmt(sentiment_delta, suffix='点')}。"
    )
    pillar = {
        "key": "growth",
        "title": "增长与景气",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": summary,
        "confidence": _confidence(available_checks, 3),
        "metrics": metrics,
    }
    return pillar, {
        "gdp": current_gdp,
        "industrial_average": current_industrial_average,
        "sentiment": current_sentiment,
        "sentiment_delta": current_sentiment_delta,
    }


def _build_labour(
    employment: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, float | None]]:
    unemployment = _employment_points(employment, "unemployment")
    youth = _employment_points(employment, "youth_unemployment")
    participation = _employment_points(employment, "labor_participation")
    employment_ratio = _employment_points(employment, "employment_ratio")
    slack = _employment_points(employment, "labour_slack")

    unemployment_value = unemployment[-1]["value"] if unemployment else None
    unemployment_reference_point = _calendar_reference(unemployment, 3, "monthly")
    unemployment_reference = (
        unemployment_reference_point["value"] if unemployment_reference_point else None
    )
    unemployment_delta = (
        unemployment_value - unemployment_reference
        if unemployment_value is not None and unemployment_reference is not None
        else None
    )
    youth_value = youth[-1]["value"] if youth else None
    youth_reference_point = _calendar_reference(youth, 3, "monthly")
    youth_reference = youth_reference_point["value"] if youth_reference_point else None
    participation_value = participation[-1]["value"] if participation else None
    participation_reference_point = _calendar_reference(participation, 4, "quarterly")
    participation_reference = (
        participation_reference_point["value"] if participation_reference_point else None
    )
    employment_value = employment_ratio[-1]["value"] if employment_ratio else None
    employment_reference_point = _calendar_reference(employment_ratio, 4, "quarterly")
    employment_reference = (
        employment_reference_point["value"] if employment_reference_point else None
    )
    slack_value = slack[-1]["value"] if slack else None
    slack_reference_point = _calendar_reference(slack, 4, "quarterly")
    slack_reference = slack_reference_point["value"] if slack_reference_point else None

    employment_change = (
        employment_value - employment_reference
        if employment_value is not None and employment_reference is not None
        else None
    )
    participation_change = (
        participation_value - participation_reference
        if participation_value is not None and participation_reference is not None
        else None
    )
    slack_change = (
        slack_value - slack_reference
        if slack_value is not None and slack_reference is not None
        else None
    )

    unemployment_current = (
        unemployment_value is not None
        and _freshness(unemployment[-1]["period"] if unemployment else None, "monthly")
        == "current"
    )
    youth_current = (
        youth_value is not None
        and _freshness(youth[-1]["period"] if youth else None, "monthly")
        == "current"
    )
    participation_current = (
        participation_value is not None
        and _freshness(
            participation[-1]["period"] if participation else None,
            "quarterly",
        )
        == "current"
    )
    employment_current = (
        employment_value is not None
        and _freshness(
            employment_ratio[-1]["period"] if employment_ratio else None,
            "quarterly",
        )
        == "current"
    )
    slack_current = (
        slack_value is not None
        and _freshness(slack[-1]["period"] if slack else None, "quarterly")
        == "current"
    )
    current_unemployment = unemployment_value if unemployment_current else None
    current_employment = employment_value if employment_current else None
    current_participation = participation_value if participation_current else None
    current_slack = slack_value if slack_current else None
    current_unemployment_delta = unemployment_delta if unemployment_current else None
    current_employment_change = employment_change if employment_current else None
    current_participation_change = (
        participation_change if participation_current else None
    )
    current_slack_change = slack_change if slack_current else None

    if (
        (
            current_unemployment_delta is not None
            and current_unemployment_delta >= 0.3 - 1e-9
        )
        or (
            current_employment_change is not None
            and current_employment_change <= -0.5 + 1e-9
        )
        or (current_slack_change is not None and current_slack_change >= 0.5 - 1e-9)
    ):
        state_key, state_label, tone = "deteriorating", "劳动力市场转弱", "negative"
    elif (
        current_unemployment_delta is not None
        and current_unemployment_delta <= 0.1 + 1e-9
        and current_employment_change is not None
        and current_employment_change >= 0
        and current_participation_change is not None
        and current_participation_change >= 0
    ):
        state_key, state_label, tone = "resilient", "就业保持稳定", "positive"
    elif any(
        value is not None
        for value in (
            current_unemployment,
            current_employment,
            current_participation,
            current_slack,
        )
    ):
        state_key, state_label, tone = "mixed", "就业信号分化", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    metrics = [
        _metric(
            key="unemployment_rate",
            label="失业率",
            value=unemployment_value,
            unit="%",
            period=unemployment[-1]["period"] if unemployment else None,
            reference_value=unemployment_reference,
            reference_period=(
                unemployment_reference_point["period"] if unemployment_reference_point else None
            ),
            interpretation="EA21、15—74岁、季调；与3个月前比较，不把单月变化直接等同衰退。",
            formula="Eurostat une_rt_m失业率",
            source_codes=["Eurostat:une_rt_m"],
        ),
        _metric(
            key="employment_rate",
            label="就业率",
            value=employment_value,
            unit="%",
            period=employment_ratio[-1]["period"] if employment_ratio else None,
            reference_value=employment_reference,
            reference_period=(
                employment_reference_point["period"] if employment_reference_point else None
            ),
            interpretation="EA21、20—64岁、季调；与一年前同季比较。",
            formula="Eurostat lfsi_emp_q就业人口占比",
            source_codes=["Eurostat:lfsi_emp_q"],
            frequency="quarterly",
        ),
        _metric(
            key="participation_rate",
            label="劳动参与率",
            value=participation_value,
            unit="%",
            period=participation[-1]["period"] if participation else None,
            reference_value=participation_reference,
            reference_period=(
                participation_reference_point["period"] if participation_reference_point else None
            ),
            interpretation="EA21、20—64岁、季调；参与改善可扩大劳动力供给。",
            formula="Eurostat lfsi_emp_q经济活动人口占比",
            source_codes=["Eurostat:lfsi_emp_q"],
            frequency="quarterly",
        ),
        _metric(
            key="labour_slack",
            label="劳动力市场闲置率",
            value=slack_value,
            unit="%",
            period=slack[-1]["period"] if slack else None,
            reference_value=slack_reference,
            reference_period=slack_reference_point["period"] if slack_reference_point else None,
            interpretation="扩展劳动力口径包含失业者及其他未满足就业需求人群。",
            formula="Eurostat lfsi_sla_q，EA21、20—64岁、季调",
            source_codes=["Eurostat:lfsi_sla_q"],
            frequency="quarterly",
        ),
        _metric(
            key="youth_unemployment",
            label="青年失业率",
            value=youth_value,
            unit="%",
            period=youth[-1]["period"] if youth else None,
            reference_value=youth_reference,
            reference_period=youth_reference_point["period"] if youth_reference_point else None,
            interpretation="EA21、15—24岁、季调；作为劳动力分化背景，不直接决定总量状态。",
            formula="Eurostat une_rt_m青年失业率",
            source_codes=["Eurostat:une_rt_m"],
        ),
    ]
    summary = (
        f"失业率 {_fmt(unemployment_value, suffix='%')}，较3个月前变化 "
        f"{_fmt(unemployment_delta, suffix='个百分点')}；就业率 {_fmt(employment_value, suffix='%')}，"
        f"参与率 {_fmt(participation_value, suffix='%')}，闲置率 {_fmt(slack_value, suffix='%')}。"
    )
    available = sum(
        (
            unemployment_current,
            employment_current,
            participation_current,
            slack_current,
            youth_current,
        )
    )
    pillar = {
        "key": "labour",
        "title": "就业与劳动力",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": summary,
        "confidence": _confidence(available, 5),
        "metrics": metrics,
    }
    return pillar, {
        "unemployment": current_unemployment,
        "unemployment_delta": current_unemployment_delta,
        "employment_change": current_employment_change,
        "participation_change": current_participation_change,
        "slack_change": current_slack_change,
    }


def _build_inflation(series: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    headline = _latest(series, "EU_CPI")
    headline_previous = _calendar_reference(series.get("EU_CPI", []), 3, "monthly")
    core = _latest(series, "EU_CORE_CPI")
    core_previous = _calendar_reference(series.get("EU_CORE_CPI", []), 3, "monthly")
    headline_value = headline["value"] if headline else None
    core_value = core["value"] if core else None
    headline_delta = (
        headline_value - headline_previous["value"]
        if headline_value is not None and headline_previous is not None
        else None
    )
    core_delta = (
        core_value - core_previous["value"]
        if core_value is not None and core_previous is not None
        else None
    )

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
    current_core_delta = core_delta if core_current else None

    if (
        current_core is not None
        and current_core > 2.3
        and current_core_delta is not None
        and current_core_delta > 0.1
    ):
        state_key, state_label, tone = "core_reaccelerating", "高于目标，核心回升", "negative"
    elif (
        current_core is not None
        and current_core > 2.3
        and current_core_delta is not None
        and current_core_delta < -0.1
    ):
        state_key, state_label, tone = "cooling_above_target", "仍高于目标但在回落", "caution"
    elif (
        current_headline is not None
        and current_core is not None
        and current_headline <= 2.3
        and current_core <= 2.3
    ):
        state_key, state_label, tone = "near_target", "接近价格稳定目标", "positive"
    elif any(value is not None for value in (current_headline, current_core)):
        state_key, state_label, tone = "sticky", "通胀仍有黏性", "caution"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    metrics = [
        _metric(
            key="headline_hicp",
            label="HICP同比",
            value=headline_value,
            unit="%",
            period=headline["period"] if headline else None,
            reference_value=headline_previous["value"] if headline_previous else None,
            reference_period=headline_previous["period"] if headline_previous else None,
            interpretation="ECB目标针对欧元区HICP中期同比2%；近3个月变化用于观察方向。",
            formula="欧元区HICP同比；参考值为3个月前",
            source_codes=["EU_CPI"],
        ),
        _metric(
            key="core_hicp",
            label="核心HICP同比",
            value=core_value,
            unit="%",
            period=core["period"] if core else None,
            reference_value=core_previous["value"] if core_previous else None,
            reference_period=core_previous["period"] if core_previous else None,
            interpretation="剔除能源和未加工食品后观察潜在价格压力；仍会受服务价格等影响。",
            formula="欧元区核心HICP同比；参考值为3个月前",
            source_codes=["EU_CORE_CPI"],
        ),
    ]
    return {
        "key": "inflation",
        "title": "通胀环境",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"HICP同比 {_fmt(headline_value, suffix='%')}，核心HICP同比 {_fmt(core_value, suffix='%')}；"
            f"较3个月前分别变化 {_fmt(headline_delta, suffix='')} / "
            f"{_fmt(core_delta, suffix='个百分点')}。"
        ),
        "confidence": _confidence(sum((headline_current, core_current)), 2),
        "metrics": metrics,
    }


def _build_policy(
    series: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, float | None]]:
    rate = _latest(series, "EU_ECB")
    rate_previous = _calendar_reference(series.get("EU_ECB", []), 6, "monthly")
    core = _latest(series, "EU_CORE_CPI")
    rate_value = rate["value"] if rate else None
    core_value = core["value"] if core else None
    rate_change = (
        rate_value - rate_previous["value"]
        if rate_value is not None and rate_previous is not None
        else None
    )
    real_proxy = (
        rate_value - core_value if rate_value is not None and core_value is not None else None
    )

    rate_freshness = (
        _freshness(rate["period"] if rate else None, "monthly")
        if rate_value is not None
        else "missing"
    )
    core_freshness = (
        _freshness(core["period"] if core else None, "monthly")
        if core_value is not None
        else "missing"
    )
    real_proxy_freshness = _combined_freshness(rate_freshness, core_freshness)
    current_rate = rate_value if rate_freshness == "current" else None
    current_rate_change = rate_change if rate_freshness == "current" else None
    current_real_proxy = real_proxy if real_proxy_freshness == "current" else None

    if current_rate_change is not None and current_rate_change >= 0.25:
        state_key, state_label, tone = "tightening", "近期加息，名义条件收紧", "caution"
    elif current_rate_change is not None and current_rate_change <= -0.25:
        state_key, state_label, tone = "easing", "近期降息，名义条件放松", "neutral"
    elif current_real_proxy is not None and current_real_proxy > 0.5:
        state_key, state_label, tone = "stable_restrictive_proxy", "利率稳定，限制性代理偏高", "caution"
    elif current_real_proxy is not None and current_real_proxy < 0:
        state_key, state_label, tone = "accommodative_proxy", "事后实际利率代理偏低", "neutral"
    elif current_rate is not None:
        state_key, state_label, tone = "nominal_rate_only", "仅有名义利率证据", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    metrics = [
        _metric(
            key="main_refinancing_rate",
            label="ECB主要再融资利率",
            value=rate_value,
            unit="%",
            period=rate["period"] if rate else None,
            reference_value=rate_previous["value"] if rate_previous else None,
            reference_period=rate_previous["period"] if rate_previous else None,
            interpretation="MRO利率是现有政策利率输入；它不是存款便利利率，不能代表操作框架的全部边际定价。",
            formula="ECB主要再融资操作利率；参考值为6个月前",
            source_codes=["EU_ECB"],
        ),
        _metric(
            key="six_month_rate_change",
            label="政策利率6个月变化",
            value=rate_change,
            unit="百分点",
            period=rate["period"] if rate else None,
            reference_value=0 if rate_change is not None else None,
            reference_period="不变基准" if rate_change is not None else None,
            interpretation="用于区分近期加息、降息或大体稳定，不推断下一次会议概率。",
            formula="最新MRO利率 − 6个月前MRO利率",
            source_codes=["EU_ECB"],
        ),
        _metric(
            key="ex_post_real_rate_proxy",
            label="事后实际利率代理",
            value=real_proxy,
            unit="百分点",
            period=_combined_period(
                rate["period"] if rate else None,
                core["period"] if core else None,
            ),
            reference_value=None,
            reference_period=None,
            interpretation="只作方向性限制程度代理；未使用通胀预期，也不是精确中性利率缺口。",
            formula="MRO利率 − 核心HICP同比",
            source_codes=["EU_ECB", "EU_CORE_CPI"],
            freshness_override=real_proxy_freshness,
        ),
    ]
    current_available = sum(
        metric["value"] is not None and metric["freshness"] == "current"
        for metric in metrics
    )
    pillar = {
        "key": "monetary_policy",
        "title": "ECB货币政策",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"主要再融资利率 {_fmt(rate_value, suffix='%')}，近6个月变化 "
            f"{_fmt(rate_change, suffix='个百分点')}；减核心HICP的事后实际利率代理 "
            f"{_fmt(real_proxy, suffix='个百分点')}。"
        ),
        "confidence": _confidence(current_available, 3),
        "metrics": metrics,
    }
    return pillar, {"rate": rate_value, "rate_change": rate_change, "real_proxy": real_proxy}


def _percentage_change(
    latest: dict[str, Any] | None, reference: dict[str, Any] | None
) -> float | None:
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
    assets = _latest(series, "EU_ECB_ASSETS")
    assets_year_ago = _year_ago_reference(series.get("EU_ECB_ASSETS", []))
    equity = _latest(series, "STOXX50")
    equity_previous = _reference(series, "STOXX50", 60)
    fx = _latest(series, "EURCNY")
    fx_previous = _reference(series, "EURCNY", 60)

    assets_level = assets["value"] / 1_000_000 if assets else None
    assets_reference_level = assets_year_ago["value"] / 1_000_000 if assets_year_ago else None
    assets_change = _percentage_change(assets, assets_year_ago)
    equity_return = _bounded_daily_return(equity, equity_previous)
    fx_change = _bounded_daily_return(fx, fx_previous)

    assets_current = (
        assets_change is not None
        and _freshness(assets["period"] if assets else None, "daily") == "current"
    )
    equity_current = (
        equity_return is not None
        and _freshness(equity["period"] if equity else None, "daily") == "current"
    )
    current_assets_change = assets_change if assets_current else None
    current_equity_return = equity_return if equity_current else None

    restrictive = sum(
        condition
        for condition in (
            current_assets_change is not None and current_assets_change <= -1,
            current_equity_return is not None and current_equity_return <= -10,
        )
    )
    supportive = sum(
        condition
        for condition in (
            current_assets_change is not None and current_assets_change >= 1,
            current_equity_return is not None and current_equity_return >= 5,
        )
    )
    if restrictive >= 2:
        state_key, state_label, tone = "tight", "可见条件偏紧", "negative"
    elif supportive >= 2:
        state_key, state_label, tone = "supportive", "可见条件有支撑", "positive"
    elif any(
        value is not None
        for value in (current_assets_change, current_equity_return)
    ):
        state_key, state_label, tone = "mixed_limited", "资产负债表收缩，市场信号有限", "caution"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    metrics = [
        _metric(
            key="ecb_total_assets",
            label="ECB总资产",
            value=assets_level,
            unit="万亿欧元",
            period=assets["period"] if assets else None,
            reference_value=assets_reference_level,
            reference_period=assets_year_ago["period"] if assets_year_ago else None,
            interpretation="资产负债表规模反映央行流动性背景，但变化也受到期与估值影响。",
            formula="ECB总资产（百万欧元）÷ 1,000,000",
            source_codes=["EU_ECB_ASSETS"],
            frequency="daily",
            tolerance=0.03,
        ),
        _metric(
            key="ecb_assets_yoy",
            label="ECB总资产约一年变化",
            value=assets_change,
            unit="%",
            period=assets["period"] if assets else None,
            reference_value=0 if assets_change is not None else None,
            reference_period=assets_year_ago["period"] if assets_year_ago else None,
            interpretation="用约52周变化观察资产负债表扩张或收缩，不等同银行信贷增速。",
            formula="最新ECB总资产 ÷ 约52周前 − 1",
            source_codes=["EU_ECB_ASSETS"],
            frequency="daily",
        ),
        _metric(
            key="equity_return_60d",
            label="EURO STOXX 50约3个月回报",
            value=equity_return,
            unit="%",
            period=equity["period"] if equity else None,
            reference_value=0 if equity_return is not None else None,
            reference_period=equity_previous["period"] if equity_previous else None,
            interpretation="股价仅代表市场风险偏好和盈利预期的一部分，不能替代融资成本。",
            formula="最新指数 ÷ 60个交易日前指数 − 1",
            source_codes=["STOXX50"],
            frequency="daily",
        ),
        _metric(
            key="eur_cny_change_60d",
            label="欧元兑人民币约3个月变化",
            value=fx_change,
            unit="%",
            period=fx["period"] if fx else None,
            reference_value=0 if fx_change is not None else None,
            reference_period=fx_previous["period"] if fx_previous else None,
            interpretation="这是双边人民币交叉汇率，只作外部背景，不进入金融条件状态判断。",
            formula="最新EUR/CNY ÷ 60个观测日前 − 1",
            source_codes=["EURCNY"],
            frequency="daily",
        ),
    ]
    return {
        "key": "financial_conditions",
        "title": "流动性与市场背景",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"ECB总资产约 {_fmt(assets_level, suffix='万亿欧元')}，约一年变化 "
            f"{_fmt(assets_change, suffix='%')}；EURO STOXX 50约3个月回报 "
            f"{_fmt(equity_return, suffix='%')}。缺少主权收益率、信用利差、银行贷款与M3，"
            "因此不把本模块称为完整金融条件指数。"
        ),
        # Only two of the core desired financial-condition channels are
        # currently available.  FX is context and does not raise confidence.
        "confidence": _confidence(
            sum((assets_current, equity_current)),
            5,
        ),
        "metrics": metrics,
    }


def _downturn_breadth(
    growth: Mapping[str, float | None],
    labour: Mapping[str, float | None],
    series: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    checks: list[tuple[bool, bool, str, str]] = []

    def add(value: float | None, triggered: bool, trigger: str, offset: str) -> None:
        if value is not None:
            checks.append((True, triggered, trigger, offset))

    add(
        growth.get("gdp"),
        growth.get("gdp") is not None and growth["gdp"] < 0,
        f"实际GDP同比为 {_fmt(growth.get('gdp'), suffix='%')}",
        f"实际GDP同比仍为 {_fmt(growth.get('gdp'), suffix='%')}",
    )
    add(
        growth.get("industrial_average"),
        growth.get("industrial_average") is not None and growth["industrial_average"] < 0,
        f"工业生产近3个月均值为 {_fmt(growth.get('industrial_average'), suffix='%')}",
        f"工业生产近3个月均值为 {_fmt(growth.get('industrial_average'), suffix='%')}",
    )
    sentiment = growth.get("sentiment")
    sentiment_delta = growth.get("sentiment_delta")
    if sentiment is not None and sentiment_delta is not None:
        checks.append(
            (
                True,
                sentiment < 100 and sentiment_delta < 0,
                f"ESI低于100且近3个月下降 {_fmt(abs(sentiment_delta), suffix='点')}",
                f"ESI为 {_fmt(sentiment)}，近3个月变化 {_fmt(sentiment_delta, suffix='点')}",
            )
        )
    add(
        labour.get("unemployment_delta"),
        labour.get("unemployment_delta") is not None
        and labour["unemployment_delta"] >= 0.3 - 1e-9,
        f"失业率较3个月前上升 {_fmt(labour.get('unemployment_delta'), suffix='个百分点')}",
        f"失业率较3个月前变化 {_fmt(labour.get('unemployment_delta'), suffix='个百分点')}",
    )
    add(
        labour.get("employment_change"),
        labour.get("employment_change") is not None and labour["employment_change"] < 0,
        f"就业率较一年前下降 {_fmt(abs(labour.get('employment_change') or 0), suffix='个百分点')}",
        f"就业率较一年前变化 {_fmt(labour.get('employment_change'), suffix='个百分点')}",
    )
    equity = _latest(series, "STOXX50")
    equity_reference = _reference(series, "STOXX50", 60)
    equity_return = _bounded_daily_return(equity, equity_reference)
    if (
        equity_return is not None
        and _freshness(equity["period"] if equity else None, "daily") == "current"
    ):
        add(
            equity_return,
            equity_return <= -10,
            f"EURO STOXX 50约3个月下跌 {_fmt(abs(equity_return), suffix='%')}",
            f"EURO STOXX 50约3个月回报 {_fmt(equity_return, suffix='%')}",
        )

    total = len(checks)
    triggers = [trigger for _, active, trigger, _ in checks if active]
    offsets = [offset for _, active, _, offset in checks if not active]
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
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "active_signals": active,
        "total_signals": total,
        "triggers": triggers,
        "offsets": offsets,
        "summary": (
            f"当前可用的{total}项增长与就业检查中有{active}项触发。"
            "这是证据广度，不是衰退概率，也不替代CEPR等事后周期认定。"
        ),
        "methodology": (
            "检查GDP同比<0、工业生产连续3个月均值<0、ESI低于100且继续下降、"
            "失业率3个月上升至少0.3个百分点、就业率较一年前下降、"
            "EURO STOXX 50约3个月跌幅超过10%；仅统计可用信号。"
        ),
    }


def _latest_period(
    series: dict[str, list[dict[str, Any]]], codes: tuple[str, ...]
) -> str | None:
    periods = [
        point["period"]
        for code in codes
        if (point := _latest(series, code)) is not None and point["period"] is not None
    ]
    return max(periods) if periods else None


def _build_eu_macro_overview(
    rows: Iterable[Any],
    employment: Mapping[str, Any] | None = None,
    *,
    employment_error: str | None = None,
) -> dict[str, Any]:
    series = _series(rows)
    growth, growth_diagnostics = _build_growth(series)
    labour, labour_diagnostics = _build_labour(employment)
    inflation = _build_inflation(series)
    financial = _build_financial_conditions(series)
    policy, policy_diagnostics = _build_policy(series)
    pillars = [growth, labour, inflation, financial, policy]
    downturn = _downturn_breadth(growth_diagnostics, labour_diagnostics, series)
    metrics = [metric for pillar in pillars for metric in pillar["metrics"]]

    missing_codes = [code for code in EU_OVERVIEW_CODES if not series.get(code)]
    stale_metrics = [metric["label"] for metric in metrics if metric["freshness"] == "stale"]
    warnings = [
        (
            "本页口径为欧元区EA21，不是欧盟EU27；聚合值可能遮蔽成员国在增长、就业、"
            "财政与融资条件上的显著分化。"
        ),
        (
            "本页使用当前修订后的最终快照；核心序列尚缺完整发布日期、可用时间与历史版本，"
            "因此不能解读为伪实时回测或衰退概率。"
        ),
        (
            "金融条件目前缺少欧元区主权收益率、信用利差、银行贷款和M3；"
            "可见市场与央行资产负债表信号不能代表完整融资环境。"
        ),
        "政策利率输入是ECB主要再融资利率，并非存款便利利率；政策立场判断仅作方向性代理。",
    ]
    if employment_error:
        warnings.append(
            "Eurostat就业专题本次未在时限内完成；就业模块保留为缺失，失败不解释为中性或零。"
        )
    if missing_codes:
        warnings.append(f"当前缺少指标：{'、'.join(missing_codes)}。缺失值未按0处理。")
    if stale_metrics:
        warnings.append(f"当前数据偏旧：{'、'.join(dict.fromkeys(stale_metrics))}；覆盖率已排除这些读数。")

    available_pillars = [pillar for pillar in pillars if pillar["confidence"] != "unavailable"]
    # Coverage includes four known financial-condition gaps instead of only
    # measuring whether the metrics we happened to implement have values.
    # The two currently scoreable channels are ECB assets and equities;
    # sovereign yields, credit spreads, bank lending and M3 remain absent.
    non_financial_metrics = [
        metric
        for pillar in pillars
        if pillar["key"] != "financial_conditions"
        for metric in pillar["metrics"]
    ]
    financial_core_metrics = [
        metric
        for metric in financial["metrics"]
        if metric["key"] in {"ecb_assets_yoy", "equity_return_60d"}
    ]
    covered_evidence = sum(
        metric["value"] is not None and metric["freshness"] == "current"
        for metric in non_financial_metrics + financial_core_metrics
    )
    expected_evidence = len(non_financial_metrics) + 6
    coverage = covered_evidence / expected_evidence if expected_evidence else 0.0
    status = "ok"
    if not available_pillars:
        status = "unavailable"
    elif (
        len(available_pillars) < len(pillars)
        or any(pillar["confidence"] == "low" for pillar in pillars)
        or employment_error
        or missing_codes
        or stale_metrics
    ):
        status = "partial"

    if downturn["state_key"] == "broad" or inflation["tone"] == "negative":
        tone = "negative"
    elif growth["tone"] == "caution" or policy["tone"] == "caution":
        tone = "caution"
    else:
        tone = "neutral"

    headline = (
        f"{growth['state_label']}；{labour['state_label']}；{inflation['state_label']}。"
        f"{downturn['state_label']}，{policy['state_label']}。"
    )

    transmission = [
        {
            "key": "policy",
            "title": "ECB政策起点",
            "state_label": policy["state_label"],
            "tone": policy["tone"],
            "detail": (
                f"MRO利率近6个月变化 {_fmt(policy_diagnostics.get('rate_change'), suffix='个百分点')}，"
                f"事后实际利率代理 {_fmt(policy_diagnostics.get('real_proxy'), suffix='个百分点')}。"
            ),
            "periods": [metric["period"] for metric in policy["metrics"] if metric["period"]],
        },
        {
            "key": "financial",
            "title": "融资与市场传导",
            "state_label": financial["state_label"],
            "tone": financial["tone"],
            "detail": financial["summary"],
            "periods": [metric["period"] for metric in financial["metrics"] if metric["period"]],
        },
        {
            "key": "activity",
            "title": "内需与实体活动",
            "state_label": growth["state_label"],
            "tone": growth["tone"],
            "detail": growth["summary"],
            "periods": [metric["period"] for metric in growth["metrics"] if metric["period"]],
        },
        {
            "key": "labour",
            "title": "就业反馈",
            "state_label": labour["state_label"],
            "tone": labour["tone"],
            "detail": labour["summary"],
            "periods": [metric["period"] for metric in labour["metrics"] if metric["period"]],
        },
        {
            "key": "prices",
            "title": "价格反馈",
            "state_label": inflation["state_label"],
            "tone": inflation["tone"],
            "detail": inflation["summary"],
            "periods": [metric["period"] for metric in inflation["metrics"] if metric["period"]],
        },
    ]
    for step in transmission:
        step["periods"] = list(dict.fromkeys(step["periods"]))

    watch_items: list[str] = []
    if growth_diagnostics.get("industrial_average") is not None and growth_diagnostics["industrial_average"] < 0:
        watch_items.append("工业生产近3个月均值能否转正，避免制造业偏弱继续拖累总量增长。")
    if growth_diagnostics.get("sentiment") is not None and growth_diagnostics["sentiment"] < 100:
        watch_items.append("ESI回升能否越过100附近长期均值，并传导到生产和内需。")
    core = _latest(series, "EU_CORE_CPI")
    if (
        core
        and _freshness(core["period"], "monthly") == "current"
        and core["value"] > 2.3
    ):
        watch_items.append("核心HICP能否重新向2%回落，而不是在高于目标的位置继续加速。")
    assets = _latest(series, "EU_ECB_ASSETS")
    assets_year_ago = _year_ago_reference(series.get("EU_ECB_ASSETS", []))
    if (
        assets
        and _freshness(assets["period"], "daily") == "current"
        and (_percentage_change(assets, assets_year_ago) or 0) < -1
    ):
        watch_items.append("央行资产负债表收缩时，银行贷款、主权融资成本和信用利差是否同步趋紧。")
    if not watch_items:
        watch_items.append("等待下一批增长、就业和通胀数据，按各自发布节奏更新判断。")

    market_period = _latest_period(series, ("EU_ECB_ASSETS", "STOXX50", "EURCNY"))
    monthly_period = _latest_period(
        series,
        ("EU_IP", "EU_CLI", "EU_CPI", "EU_CORE_CPI", "EU_ECB"),
    )
    quarterly_period = _latest_period(series, ("EU_GDP",))
    employment_period = (
        str(employment.get("latest_month"))
        if employment and employment.get("latest_month")
        else None
    )

    return {
        "region": "EU",
        "country": "欧元区（EA21）",
        "title": "欧元区宏观综合分析",
        "status": status,
        "confidence": _confidence(len(available_pillars), len(pillars)),
        "coverage": round(coverage, 4),
        "realtime_ready": False,
        "tone": tone,
        "headline": headline,
        "as_of": market_period or monthly_period or quarterly_period,
        "freshness": {
            "market_observation_date": market_period,
            "monthly_observation_period": monthly_period,
            "quarterly_observation_period": quarterly_period,
            "employment_observation_period": employment_period,
        },
        "methodology_version": METHODOLOGY_VERSION,
        "methodology_note": (
            "五个分项分别按公开、可复核规则判断，不合成总分；不同频率保留各自观察期，"
            "缺失值不填0。下行证据广度只统计当前可用的增长和就业规则，不输出未经回测的衰退概率。"
        ),
        "pillars": pillars,
        "downturn_breadth": downturn,
        "transmission": transmission,
        "watch_items": watch_items,
        "data_source_path": "/data-sources/eu",
        "warnings": warnings,
    }


def build_eu_macro_overview(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(DataPoint)
        .where(DataPoint.indicator_code.in_(EU_OVERVIEW_CODES))
        .order_by(DataPoint.indicator_code, DataPoint.date)
    ).scalars().all()

    employment, employment_error = _load_employment_enrichment()
    return _build_eu_macro_overview(
        rows,
        employment,
        employment_error=employment_error,
    )

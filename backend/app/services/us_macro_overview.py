"""Transparent current-snapshot overview for the United States economy.

The dashboard deliberately avoids a synthetic score.  Each pillar keeps its
own observation period and exposes the simple rule used for its state label.
The stored US series do not yet have complete release/vintage metadata, so the
result is a current/final snapshot rather than a pseudo-real-time backtest.
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

from app.fetchers.us_employment import fetch_us_employment_dashboard
from app.models import DataPoint
from app.services.indicator_series import is_current_series_point


METHODOLOGY_VERSION = "1.0.0"
EMPLOYMENT_ENRICHMENT_TIMEOUT_SECONDS = 5.0
EMPLOYMENT_ENRICHMENT_TTL_SECONDS = 6 * 60 * 60

_employment_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="us-overview-employment")
_employment_future: Future[dict[str, Any]] | None = None
_employment_future_started_at: float | None = None
_employment_lock = Lock()

US_OVERVIEW_CODES = (
    "DJI",
    "US_CPI",
    "US_CORE_CPI",
    "US_IP",
    "US_CLI",
    "US_NFP",
    "US_FFR",
    "US_GDP",
    "US_M2_YOY",
    "US_2Y",
    "US_10Y",
    "US_10Y2Y",
)

_DAILY_OVERVIEW_CODES = {"DJI", "US_2Y", "US_10Y", "US_10Y2Y"}
_QUARTERLY_OVERVIEW_CODES = {"US_GDP"}


def _load_employment_enrichment() -> tuple[Mapping[str, Any] | None, str | None]:
    """Bound live employment latency while allowing the shared fetch to warm.

    A cold FRED/BLS request can take tens of seconds.  The first overview
    request waits only briefly, then falls back to stored NFP while the single
    background future continues and populates the fetcher's six-hour cache.
    """

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
            _employment_future = _employment_executor.submit(fetch_us_employment_dashboard)
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
    if isinstance(observed, str):
        raw = observed
    else:
        raw = observed.isoformat()
    if len(raw) == 7:
        return raw
    if code == "US_GDP":
        parsed = date.fromisoformat(raw[:10])
        return f"{parsed.year}-Q{((parsed.month - 1) // 3) + 1}"
    if code in {"DJI", "US_2Y", "US_10Y", "US_10Y2Y"}:
        return raw[:10]
    return raw[:7]


def _series(rows: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {code: [] for code in US_OVERVIEW_CODES}
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
    if code in _DAILY_OVERVIEW_CODES:
        index = len(values) - 1 - observations_back
        return values[index] if index >= 0 else None
    if not values:
        return None

    latest_period = values[-1].get("period")
    try:
        if code in _QUARTERLY_OVERVIEW_CODES:
            year_text, quarter_text = str(latest_period).split("-Q")
            target_index = int(year_text) * 4 + int(quarter_text) - 1 - observations_back
            target_period = f"{target_index // 4}-Q{target_index % 4 + 1}"
        else:
            target_index = _month_index(str(latest_period)) - observations_back
            target_period = f"{target_index // 12:04d}-{target_index % 12 + 1:02d}"
    except (TypeError, ValueError):
        return None

    return next(
        (point for point in reversed(values[:-1]) if point.get("period") == target_period),
        None,
    )


def _trend(
    value: float | None,
    reference: float | None,
    *,
    tolerance: float = 0.05,
) -> str:
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
    if not states or "missing" in states:
        return "missing"
    if "stale" in states:
        return "stale"
    return "current"


def _combined_period(*periods: str | None) -> str | None:
    available = [period for period in periods if period]
    return min(available) if len(available) == len(periods) and available else None


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


def _monthly_window(
    points: list[dict[str, Any]],
    count: int,
    *,
    months_back: int = 0,
) -> list[dict[str, Any]]:
    """Return an exact calendar-month window ending relative to the latest point."""

    if not points or count <= 0 or months_back < 0:
        return []
    try:
        latest_index = _month_index(points[-1]["period"])
        by_index = {_month_index(point["period"]): point for point in points}
    except (KeyError, TypeError, ValueError):
        return []
    end_index = latest_index - months_back
    target_indices = range(end_index - count + 1, end_index + 1)
    if any(index not in by_index for index in target_indices):
        return []
    return [by_index[index] for index in target_indices]


def _monthly_reference(
    points: list[dict[str, Any]], months_back: int
) -> dict[str, Any] | None:
    window = _monthly_window(points, 1, months_back=months_back)
    return window[0] if window else None


def _trailing_mean(points: list[dict[str, Any]], count: int) -> float | None:
    trailing = _monthly_window(points, count)
    if len(trailing) != count:
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
    # Confidence is intentionally capped at medium until US release/vintage
    # evidence is persisted; complete current data is not the same as a
    # real-time-reproducible model.
    if coverage >= 0.8:
        return "medium"
    if coverage >= 0.5:
        return "medium"
    return "low"


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
            points.sort(key=lambda point: point["period"])
            return points
    return []


def _moving_average_gap(points: list[dict[str, Any]]) -> tuple[float | None, float | None, str | None]:
    """Return a current-data Sahm-style gap and its trailing minimum.

    This is intentionally labelled as a simplified current-snapshot check. It
    uses revised unemployment data and therefore is not the official real-time
    Sahm Rule series.
    """

    if len(points) < 5:
        return None, None, None
    averages = []
    for index in range(2, len(points)):
        window = points[index - 2 : index + 1]
        if not _is_consecutive_months(window):
            continue
        averages.append(
            {
                "period": points[index]["period"],
                "value": mean(point["value"] for point in window),
            }
        )
    if not averages or averages[-1]["period"] != points[-1]["period"]:
        return None, None, None
    current_index = _month_index(averages[-1]["period"])
    trailing = [
        point
        for point in averages
        if current_index - 11 <= _month_index(point["period"]) <= current_index
    ]
    # A historical missing month invalidates only the rolling windows that
    # cross it; it must never turn non-adjacent observations into a fake
    # three-month average. Nine valid windows retain useful trailing coverage.
    if len(trailing) < 9:
        return None, None, None
    low = min(trailing, key=lambda point: point["value"])
    return averages[-1]["value"] - low["value"], low["value"], low["period"]


def _build_growth(series: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    gdp = _latest(series, "US_GDP")
    gdp_previous = _reference(series, "US_GDP", 1)
    industrial = _latest(series, "US_IP")
    industrial_previous = _reference(series, "US_IP", 3)
    cli = _latest(series, "US_CLI")
    cli_previous = _reference(series, "US_CLI", 3)

    gdp_value = gdp["value"] if gdp else None
    industrial_value = industrial["value"] if industrial else None
    cli_value = cli["value"] if cli else None
    industrial_average = _trailing_mean(series.get("US_IP", []), 3)
    cli_delta = (
        cli_value - cli_previous["value"]
        if cli_value is not None and cli_previous is not None
        else None
    )
    gdp_current = (
        gdp_value
        if _freshness(gdp["period"] if gdp else None, "quarterly") == "current"
        else None
    )
    industrial_current = (
        industrial_average
        if _freshness(industrial["period"] if industrial else None, "monthly")
        == "current"
        else None
    )
    cli_is_current = (
        _freshness(cli["period"] if cli else None, "monthly") == "current"
    )
    current_cli_value = cli_value if cli_is_current else None
    current_cli_delta = cli_delta if cli_is_current else None

    positive_checks = [
        gdp_current > 0 if gdp_current is not None else None,
        industrial_current > 0 if industrial_current is not None else None,
        current_cli_value >= 100 and current_cli_delta > 0
        if current_cli_value is not None and current_cli_delta is not None
        else None,
    ]
    negative_checks = [
        gdp_current < 0 if gdp_current is not None else None,
        industrial_current < 0 if industrial_current is not None else None,
        current_cli_value < 100 and current_cli_delta < 0
        if current_cli_value is not None and current_cli_delta is not None
        else None,
    ]
    positive_count = sum(value is True for value in positive_checks)
    negative_count = sum(value is True for value in negative_checks)
    available_checks = sum(value is not None for value in positive_checks)

    if negative_count >= 2:
        state_key, state_label, tone = "contraction", "活动收缩", "negative"
    elif positive_count >= 2:
        state_key, state_label, tone = "moderate_expansion", "温和扩张", "positive"
    elif available_checks > 0:
        state_key, state_label, tone = "mixed", "增长信号分化", "caution"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    summary = (
        f"实际GDP环比折年率 {_fmt(gdp_value, suffix='%')}，工业生产同比 {_fmt(industrial_value, suffix='%')}；"
        f"CLI {_fmt(cli_value)}，近3个月变化 {_fmt(cli_delta, suffix='点')}。"
    )
    metrics = [
        _metric(
            key="real_gdp_qoq_annualized",
            label="实际GDP环比折年率",
            value=gdp_value,
            unit="%",
            period=gdp["period"] if gdp else None,
            reference_value=gdp_previous["value"] if gdp_previous else None,
            reference_period=gdp_previous["period"] if gdp_previous else None,
            interpretation="观察已公布季度的总量增长，不把折年率误写为同比。",
            formula="BEA实际GDP季调环比折年率",
            source_codes=["US_GDP"],
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
            interpretation="生产同比为实体活动同步证据；与3个月前比较方向。",
            formula="最新工业生产同比；参考值为3个月前",
            source_codes=["US_IP"],
        ),
        _metric(
            key="leading_indicator",
            label="综合领先指标",
            value=cli_value,
            unit="点",
            period=cli["period"] if cli else None,
            reference_value=cli_previous["value"] if cli_previous else None,
            reference_period=cli_previous["period"] if cli_previous else None,
            interpretation="100为长期趋势附近；同时观察水平与近3个月方向。",
            formula="OECD CLI水平；参考值为3个月前",
            source_codes=["US_CLI"],
            tolerance=0.02,
        ),
    ]
    return {
        "key": "growth",
        "title": "增长与景气",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": summary,
        "confidence": _confidence(available_checks, 3),
        "metrics": metrics,
    }


def _build_labour(
    series: dict[str, list[dict[str, Any]]],
    employment: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    unemployment = _employment_points(employment, "unemployment")
    payrolls = _employment_points(employment, "payroll_change")
    wages = _employment_points(employment, "hourly_earnings_yoy")
    vacancies = _employment_points(employment, "vacancy_unemployed_ratio")

    if not payrolls:
        payrolls = [
            {"period": point["period"], "value": point["value"] * 10}
            for point in series.get("US_NFP", [])
        ]

    unemployment_value = unemployment[-1]["value"] if unemployment else None
    unemployment_reference_point = _monthly_reference(unemployment, 3)
    unemployment_reference = (
        unemployment_reference_point["value"] if unemployment_reference_point else None
    )
    payroll_average = _trailing_mean(payrolls, 3)
    payroll_prior_window = _monthly_window(payrolls, 3, months_back=3)
    payroll_prior = (
        mean(point["value"] for point in payroll_prior_window)
        if len(payroll_prior_window) == 3
        else None
    )
    wage_value = wages[-1]["value"] if wages else None
    wage_reference_point = _monthly_reference(wages, 3)
    wage_reference = wage_reference_point["value"] if wage_reference_point else None
    vacancy_value = vacancies[-1]["value"] if vacancies else None
    vacancy_reference_point = _monthly_reference(vacancies, 3)
    vacancy_reference = (
        vacancy_reference_point["value"] if vacancy_reference_point else None
    )
    sahm_gap, sahm_low, sahm_low_period = _moving_average_gap(unemployment)
    unemployment_current = (
        unemployment_value is not None
        and _freshness(unemployment[-1]["period"], "monthly") == "current"
    )
    payroll_current = (
        payroll_average is not None
        and _freshness(payrolls[-1]["period"], "monthly") == "current"
    )
    wage_current = (
        wage_value is not None
        and _freshness(wages[-1]["period"], "monthly") == "current"
    )
    vacancy_current = (
        vacancy_value is not None
        and _freshness(vacancies[-1]["period"], "monthly") == "current"
    )
    current_unemployment = unemployment_value if unemployment_current else None
    current_payroll_average = payroll_average if payroll_current else None
    current_payroll_prior = payroll_prior if payroll_current else None
    current_sahm_gap = sahm_gap if unemployment_current else None

    if current_sahm_gap is not None and current_sahm_gap >= 0.5:
        state_key, state_label, tone = "unemployment_warning", "失业恶化警报", "negative"
    elif current_payroll_average is not None and current_payroll_average < 0:
        state_key, state_label, tone = "payroll_contraction", "就业岗位收缩", "negative"
    elif (
        current_payroll_average is not None
        and current_payroll_prior is not None
        and current_payroll_average < current_payroll_prior - 25
    ):
        state_key, state_label, tone = "cooling_resilient", "仍有韧性，招聘降温", "caution"
    elif (
        current_unemployment is not None
        and current_unemployment <= 4.5
        and current_payroll_average is not None
        and current_payroll_average > 0
    ):
        state_key, state_label, tone = "resilient", "就业仍有韧性", "positive"
    elif any(
        value is not None
        for value in (
            current_unemployment,
            current_payroll_average,
            wage_value if wage_current else None,
        )
    ):
        state_key, state_label, tone = "mixed", "就业信号分化", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    summary = (
        f"U-3失业率 {_fmt(unemployment_value, suffix='%')}，近3个月非农平均 {_fmt(payroll_average, 0, '千人')}；"
        f"工资同比 {_fmt(wage_value, suffix='%')}，职位空缺/失业人数 {_fmt(vacancy_value, suffix='倍')}。"
    )
    payroll_reference_period = None
    if len(payroll_prior_window) == 3:
        payroll_reference_period = (
            f"{payroll_prior_window[0]['period']}—{payroll_prior_window[-1]['period']}"
        )
    metrics = [
        _metric(
            key="unemployment_rate",
            label="U-3失业率",
            value=unemployment_value,
            unit="%",
            period=unemployment[-1]["period"] if unemployment else None,
            reference_value=unemployment_reference,
            reference_period=(
                unemployment_reference_point["period"]
                if unemployment_reference_point
                else None
            ),
            interpretation="与3个月前比较劳动力闲置程度；单月不直接等同衰退。",
            formula="BLS U-3失业率，季调",
            source_codes=["BLS:UNRATE"],
        ),
        _metric(
            key="payroll_change_3m_average",
            label="非农就业近3月均值",
            value=payroll_average,
            unit="千人",
            period=payrolls[-1]["period"] if payrolls else None,
            reference_value=payroll_prior,
            reference_period=payroll_reference_period,
            interpretation="三个月均值降低单月修订和波动的干扰，并与此前三个月比较。",
            formula="最近3个月非农就业变化均值",
            source_codes=["US_NFP", "BLS:PAYEMS"],
            tolerance=10,
        ),
        _metric(
            key="wage_growth",
            label="平均时薪同比",
            value=wage_value,
            unit="%",
            period=wages[-1]["period"] if wages else None,
            reference_value=wage_reference,
            reference_period=wage_reference_point["period"] if wage_reference_point else None,
            interpretation="工资同比用于观察劳动力成本压力，存在就业构成效应。",
            formula="私营非农平均时薪同比",
            source_codes=["BLS:CES0500000003"],
        ),
        _metric(
            key="vacancy_unemployed_ratio",
            label="职位空缺/失业人数",
            value=vacancy_value,
            unit="倍",
            period=vacancies[-1]["period"] if vacancies else None,
            reference_value=vacancy_reference,
            reference_period=(
                vacancy_reference_point["period"] if vacancy_reference_point else None
            ),
            interpretation="高于1通常表示空缺仍多于失业人数；JOLTS发布时间更晚。",
            formula="JOLTS职位空缺 ÷ 家庭调查失业人数",
            source_codes=["BLS:JTSJOL", "BLS:UNEMPLOY"],
            tolerance=0.03,
        ),
        _metric(
            key="sahm_style_gap",
            label="失业率三月均值缺口",
            value=sahm_gap,
            unit="百分点",
            period=unemployment[-1]["period"] if unemployment else None,
            reference_value=0.5 if sahm_gap is not None else None,
            reference_period="预警阈值" if sahm_gap is not None else None,
            interpretation=(
                "达到0.50个百分点才触发本页警报；"
                f"近12个月三月均值低点为 {_fmt(sahm_low, suffix='%')}（{sahm_low_period or '--'}）。"
                "当前修订值不能冒充实时Sahm规则。"
            ),
            formula="当前U-3三月均值 − 近12个月三月均值低点",
            source_codes=["BLS:UNRATE"],
            tolerance=0.05,
        ),
    ]
    available = sum(
        metric["value"] is not None and metric["freshness"] == "current"
        for metric in metrics
    )
    pillar = {
        "key": "labour",
        "title": "就业与工资",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": summary,
        "confidence": _confidence(available, 5),
        "metrics": metrics,
    }
    diagnostics = {
        "unemployment": unemployment_value,
        "payroll_average": payroll_average,
        "payroll_prior": payroll_prior,
        "payroll_current": payroll_current,
        "wage_growth": wage_value,
        "wage_period": wages[-1]["period"] if wages else None,
        "wage_current": wage_current,
        "vacancy_ratio": vacancy_value,
        "vacancy_current": vacancy_current,
        "sahm_gap": sahm_gap,
        "unemployment_current": unemployment_current,
    }
    return pillar, diagnostics


def _build_inflation(
    series: dict[str, list[dict[str, Any]]],
    labour: dict[str, Any],
) -> dict[str, Any]:
    headline = _latest(series, "US_CPI")
    headline_previous = _reference(series, "US_CPI", 3)
    core = _latest(series, "US_CORE_CPI")
    core_previous = _reference(series, "US_CORE_CPI", 3)
    headline_value = headline["value"] if headline else None
    core_value = core["value"] if core else None
    core_delta = core_value - core_previous["value"] if core_value is not None and core_previous else None
    wage_value = labour.get("wage_growth")
    headline_current = (
        headline_value
        if _freshness(headline["period"] if headline else None, "monthly")
        == "current"
        else None
    )
    core_current = (
        core_value
        if _freshness(core["period"] if core else None, "monthly") == "current"
        else None
    )
    current_core_delta = core_delta if core_current is not None else None
    current_wage = wage_value if labour.get("wage_current") else None

    if (
        core_current is not None
        and current_core_delta is not None
        and core_current > 2.3
        and current_core_delta > 0.1
    ):
        state_key, state_label, tone = "reaccelerating", "核心通胀再加速", "negative"
    elif (
        core_current is not None
        and core_current > 2.3
        and current_core_delta is not None
        and current_core_delta < -0.1
    ):
        state_key, state_label, tone = "cooling_above_benchmark", "仍偏高但在回落", "caution"
    elif (
        headline_current is not None
        and core_current is not None
        and headline_current <= 2.5
        and core_current <= 2.3
    ):
        state_key, state_label, tone = "near_stability", "接近价格稳定", "positive"
    elif any(value is not None for value in (headline_current, core_current)):
        state_key, state_label, tone = "sticky", "通胀仍有黏性", "caution"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    summary = (
        f"CPI同比 {_fmt(headline_value, suffix='%')}，核心CPI同比 {_fmt(core_value, suffix='%')}；"
        f"核心指标近3个月变化 {_fmt(core_delta, suffix='个百分点')}，工资同比 {_fmt(wage_value, suffix='%')}。"
    )
    metrics = [
        _metric(
            key="headline_cpi",
            label="CPI同比",
            value=headline_value,
            unit="%",
            period=headline["period"] if headline else None,
            reference_value=headline_previous["value"] if headline_previous else None,
            reference_period=headline_previous["period"] if headline_previous else None,
            interpretation="观察居民消费价格总指数同比，并与3个月前比较。",
            formula="CPI同比",
            source_codes=["US_CPI"],
        ),
        _metric(
            key="core_cpi",
            label="核心CPI同比",
            value=core_value,
            unit="%",
            period=core["period"] if core else None,
            reference_value=core_previous["value"] if core_previous else None,
            reference_period=core_previous["period"] if core_previous else None,
            interpretation="剔除波动较大的分项后观察通胀黏性；2%只作政策基准，不是CPI机械阈值。",
            formula="核心CPI同比；参考值为3个月前",
            source_codes=["US_CORE_CPI"],
        ),
        _metric(
            key="wage_growth_inflation_context",
            label="工资同比",
            value=wage_value,
            unit="%",
            period=labour.get("wage_period"),
            reference_value=None,
            reference_period=None,
            interpretation="工资是服务价格压力的背景证据，不直接等同单位劳动力成本。",
            formula="私营非农平均时薪同比",
            source_codes=["BLS:CES0500000003"],
        ),
    ]
    return {
        "key": "inflation",
        "title": "通胀环境",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": summary,
        "confidence": _confidence(
            sum(
                value is not None
                for value in (headline_current, core_current, current_wage)
            ),
            3,
        ),
        "metrics": metrics,
    }


def _build_policy(series: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], float | None]:
    ffr = _latest(series, "US_FFR")
    ffr_previous = _reference(series, "US_FFR", 6)
    core = _latest(series, "US_CORE_CPI")
    two_year = _latest(series, "US_2Y")
    two_year_previous = _reference(series, "US_2Y", 20)
    ffr_value = ffr["value"] if ffr else None
    core_value = core["value"] if core else None
    two_year_value = two_year["value"] if two_year else None
    real_proxy = ffr_value - core_value if ffr_value is not None and core_value is not None else None
    market_gap = two_year_value - ffr_value if two_year_value is not None and ffr_value is not None else None

    ffr_change = ffr_value - ffr_previous["value"] if ffr_value is not None and ffr_previous else None
    ffr_freshness = (
        _freshness(ffr["period"] if ffr else None, "monthly")
        if ffr_value is not None
        else "missing"
    )
    core_freshness = (
        _freshness(core["period"] if core else None, "monthly")
        if core_value is not None
        else "missing"
    )
    two_year_freshness = (
        _freshness(two_year["period"] if two_year else None, "daily")
        if two_year_value is not None
        else "missing"
    )
    real_proxy_freshness = _combined_freshness(ffr_freshness, core_freshness)
    market_gap_freshness = _combined_freshness(
        two_year_freshness,
        ffr_freshness,
    )
    current_ffr = ffr_value if ffr_freshness == "current" else None
    current_real_proxy = real_proxy if real_proxy_freshness == "current" else None
    current_ffr_change = ffr_change if ffr_freshness == "current" else None
    if (
        current_real_proxy is not None
        and current_real_proxy > 1
        and current_ffr_change is not None
        and abs(current_ffr_change) < 0.25
    ):
        state_key, state_label, tone = "stable_restrictive_proxy", "利率稳定，限制性代理偏高", "caution"
    elif current_real_proxy is not None and current_real_proxy > 0.5:
        state_key, state_label, tone = "restrictive", "政策仍具限制性", "caution"
    elif current_real_proxy is not None and current_real_proxy < 0:
        state_key, state_label, tone = "accommodative_proxy", "实际利率代理偏宽松", "neutral"
    elif current_ffr is not None:
        state_key, state_label, tone = "nominal_rate_only", "仅有名义利率证据", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    summary = (
        f"有效联邦基金利率 {_fmt(ffr_value, suffix='%')}，减核心CPI的事后实际利率代理 "
        f"{_fmt(real_proxy, suffix='个百分点')}；2年期收益率相对EFFR {_fmt(market_gap, suffix='个百分点')}。"
    )
    metrics = [
        _metric(
            key="effective_federal_funds_rate",
            label="有效联邦基金利率",
            value=ffr_value,
            unit="%",
            period=ffr["period"] if ffr else None,
            reference_value=ffr_previous["value"] if ffr_previous else None,
            reference_period=ffr_previous["period"] if ffr_previous else None,
            interpretation="月均有效利率反映政策执行结果；以6个月变化区分稳定、上调或下调。",
            formula="EFFR月均值；参考值为6个月前",
            source_codes=["US_FFR"],
        ),
        _metric(
            key="ex_post_real_rate_proxy",
            label="事后实际利率代理",
            value=real_proxy,
            unit="百分点",
            period=_combined_period(
                ffr["period"] if ffr else None,
                core["period"] if core else None,
            ),
            reference_value=None,
            reference_period=None,
            interpretation="为方向性限制程度代理；未使用通胀预期，不能视为精确中性利率缺口。",
            formula="有效联邦基金利率 − 核心CPI同比",
            source_codes=["US_FFR", "US_CORE_CPI"],
            freshness_override=real_proxy_freshness,
        ),
        _metric(
            key="two_year_policy_gap",
            label="2年期收益率−EFFR",
            value=market_gap,
            unit="百分点",
            period=_combined_period(
                two_year["period"] if two_year else None,
                ffr["period"] if ffr else None,
            ),
            reference_value=(
                two_year_previous["value"] - ffr_value
                if two_year_previous is not None and ffr_value is not None
                else None
            ),
            reference_period=two_year_previous["period"] if two_year_previous else None,
            interpretation="反映短端市场定价与当前有效利率的差距，不单独解释为加息或降息概率。",
            formula="2年期国债收益率 − 有效联邦基金利率",
            source_codes=["US_2Y", "US_FFR"],
            frequency="daily",
            freshness_override=market_gap_freshness,
        ),
    ]
    available = sum(
        metric["value"] is not None and metric["freshness"] == "current"
        for metric in metrics
    )
    return {
        "key": "monetary_policy",
        "title": "货币政策",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": summary,
        "confidence": _confidence(available, 3),
        "metrics": metrics,
    }, current_real_proxy


def _build_financial_conditions(series: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    ten_year = _latest(series, "US_10Y")
    ten_year_previous = _reference(series, "US_10Y", 20)
    curve = _latest(series, "US_10Y2Y")
    curve_previous = _reference(series, "US_10Y2Y", 20)
    money = _latest(series, "US_M2_YOY")
    money_previous = _reference(series, "US_M2_YOY", 3)
    equity = _latest(series, "DJI")
    equity_previous = _reference(series, "DJI", 60)

    ten_year_value = ten_year["value"] if ten_year else None
    curve_value = curve["value"] if curve else None
    money_value = money["value"] if money else None
    equity_return = _bounded_daily_return(equity, equity_previous)
    ten_year_current = (
        ten_year_value
        if _freshness(ten_year["period"] if ten_year else None, "daily") == "current"
        else None
    )
    curve_current = (
        curve_value
        if _freshness(curve["period"] if curve else None, "daily") == "current"
        else None
    )
    money_current = (
        money_value
        if _freshness(money["period"] if money else None, "monthly") == "current"
        else None
    )
    equity_return_current = (
        equity_return
        if _freshness(equity["period"] if equity else None, "daily") == "current"
        else None
    )

    restrictive = sum(
        condition
        for condition in (
            ten_year_current is not None and ten_year_current >= 4.5,
            curve_current is not None and curve_current < 0,
            money_current is not None and money_current < 0,
            equity_return_current is not None and equity_return_current <= -10,
        )
    )
    supportive = sum(
        condition
        for condition in (
            curve_current is not None and curve_current > 0,
            money_current is not None and money_current >= 3,
            equity_return_current is not None and equity_return_current > 0,
        )
    )
    if restrictive >= 2 and restrictive > supportive:
        state_key, state_label, tone = "tight", "金融条件偏紧", "negative"
    elif supportive >= 2 and (ten_year_current is None or ten_year_current < 4.5):
        state_key, state_label, tone = "supportive", "金融条件有支撑", "positive"
    elif any(
        value is not None
        for value in (
            ten_year_current,
            curve_current,
            money_current,
            equity_return_current,
        )
    ):
        state_key, state_label, tone = "mixed", "部分金融条件信号混合", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "证据不足", "unavailable"

    summary = (
        f"10年期收益率 {_fmt(ten_year_value, suffix='%')}，10年−2年利差 {_fmt(curve_value, suffix='个百分点')}；"
        f"M2同比 {_fmt(money_value, suffix='%')}，道指约3个月回报 {_fmt(equity_return, suffix='%')}。"
    )
    metrics = [
        _metric(
            key="ten_year_yield",
            label="10年期国债收益率",
            value=ten_year_value,
            unit="%",
            period=ten_year["period"] if ten_year else None,
            reference_value=ten_year_previous["value"] if ten_year_previous else None,
            reference_period=ten_year_previous["period"] if ten_year_previous else None,
            interpretation="长期无风险利率是融资条件的重要价格，但也含增长、通胀和期限溢价。",
            formula="10年期国债收益率；参考值约20个交易日前",
            source_codes=["US_10Y"],
            frequency="daily",
        ),
        _metric(
            key="yield_curve",
            label="10年−2年利差",
            value=curve_value,
            unit="百分点",
            period=curve["period"] if curve else None,
            reference_value=curve_previous["value"] if curve_previous else None,
            reference_period=curve_previous["period"] if curve_previous else None,
            interpretation="负值为曲线倒挂；转正并不自动表示衰退风险消失。",
            formula="10年期收益率 − 2年期收益率",
            source_codes=["US_10Y2Y", "US_10Y", "US_2Y"],
            frequency="daily",
        ),
        _metric(
            key="money_growth",
            label="M2同比",
            value=money_value,
            unit="%",
            period=money["period"] if money else None,
            reference_value=money_previous["value"] if money_previous else None,
            reference_period=money_previous["period"] if money_previous else None,
            interpretation="货币存量增速提供数量背景，不直接等同信贷供给或总需求。",
            formula="M2同比；参考值为3个月前",
            source_codes=["US_M2_YOY"],
        ),
        _metric(
            key="equity_return_3m",
            label="道指约3个月回报",
            value=equity_return,
            unit="%",
            period=equity["period"] if equity else None,
            reference_value=0,
            reference_period=equity_previous["period"] if equity_previous else None,
            interpretation="约60个交易日价格变化，仅作为风险偏好背景。",
            formula="最新道指 ÷ 约60个交易日前道指 − 1",
            source_codes=["DJI"],
            frequency="daily",
            tolerance=0.5,
        ),
    ]
    return {
        "key": "financial_conditions",
        "title": "部分金融条件",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": summary,
        "confidence": _confidence(
            sum(
                value is not None
                for value in (
                    ten_year_current,
                    curve_current,
                    money_current,
                    equity_return_current,
                )
            ),
            4,
        ),
        "metrics": metrics,
    }


def _recession_breadth(
    series: dict[str, list[dict[str, Any]]],
    labour: dict[str, Any],
) -> dict[str, Any]:
    gdp = _latest(series, "US_GDP")
    industrial = _latest(series, "US_IP")
    cli = _latest(series, "US_CLI")
    cli_previous = _reference(series, "US_CLI", 3)
    curve = _latest(series, "US_10Y2Y")
    payroll_average = labour.get("payroll_average")
    sahm_gap = labour.get("sahm_gap")
    gdp_current = gdp is not None and _freshness(gdp["period"], "quarterly") == "current"
    industrial_current = (
        industrial is not None
        and _freshness(industrial["period"], "monthly") == "current"
    )
    cli_current = cli is not None and _freshness(cli["period"], "monthly") == "current"
    curve_current = curve is not None and _freshness(curve["period"], "daily") == "current"
    payroll_current = bool(labour.get("payroll_current"))
    unemployment_current = bool(labour.get("unemployment_current"))

    checks: list[tuple[bool | None, str, str]] = [
        (
            gdp["value"] < 0 if gdp_current else None,
            "实际GDP环比折年率为负",
            "实际GDP仍为正增长",
        ),
        (
            industrial["value"] < 0 if industrial_current else None,
            "工业生产同比为负",
            "工业生产同比仍为正",
        ),
        (
            (
                cli["value"] < 100 and cli["value"] < cli_previous["value"]
                if cli_current and cli_previous
                else None
            ),
            "CLI低于100且近3个月下行",
            "CLI未同时出现低于100和下行",
        ),
        (
            payroll_average < 0
            if payroll_current and payroll_average is not None
            else None,
            "近3个月非农均值为负",
            "近3个月非农均值仍为正",
        ),
        (
            sahm_gap >= 0.5
            if unemployment_current and sahm_gap is not None
            else None,
            "失业率三月均值缺口达到0.50个百分点",
            "失业率三月均值缺口未触发0.50阈值",
        ),
        (
            curve["value"] < 0 if curve_current else None,
            "10年−2年收益率曲线倒挂",
            "10年−2年收益率曲线未倒挂",
        ),
    ]
    available = [item for item in checks if item[0] is not None]
    triggers = [trigger for active, trigger, _ in available if active]
    offsets = [offset for active, _, offset in available if not active]
    active_count = len(triggers)
    total = len(available)

    sahm_triggered = (
        unemployment_current and sahm_gap is not None and sahm_gap >= 0.5
    )
    if total < 4:
        state_key, state_label, tone = "unavailable", "覆盖不足", "unavailable"
    elif sahm_triggered or active_count >= 3:
        state_key, state_label, tone = "broad", "收缩证据广泛", "negative"
    elif active_count >= 2:
        state_key, state_label, tone = "elevated", "下行证据上升", "caution"
    else:
        state_key, state_label, tone = "limited", "广泛衰退证据有限", "positive"

    summary = (
        f"{active_count}/{total}项可用收缩检查被触发。"
        "这是证据广度，不是衰退概率，也不替代NBER事后认定。"
    )
    return {
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "active_signals": active_count,
        "total_signals": total,
        "triggers": triggers,
        "offsets": offsets,
        "summary": summary,
        "methodology": (
            "等权检查GDP、工业生产、CLI、非农三月均值、失业率三月均值缺口和10年−2年利差；"
            "不合成概率。Sahm式检查使用当前修订数据。"
        ),
    }


def _latest_period(series: dict[str, list[dict[str, Any]]], codes: tuple[str, ...]) -> str | None:
    periods = [
        point["period"]
        for code in codes
        if (point := _latest(series, code)) is not None and point["period"] is not None
    ]
    return max(periods) if periods else None


def _build_us_macro_overview(
    rows: Iterable[Any],
    employment: Mapping[str, Any] | None = None,
    *,
    employment_error: str | None = None,
) -> dict[str, Any]:
    series = _series(rows)
    growth = _build_growth(series)
    labour, labour_diagnostics = _build_labour(series, employment)
    inflation = _build_inflation(series, labour_diagnostics)
    financial = _build_financial_conditions(series)
    policy, real_rate_proxy = _build_policy(series)
    pillars = [growth, labour, inflation, financial, policy]
    recession = _recession_breadth(series, labour_diagnostics)
    metrics = [metric for pillar in pillars for metric in pillar["metrics"]]

    missing_codes = [code for code in US_OVERVIEW_CODES if not series.get(code)]
    stale_metrics = [metric["label"] for metric in metrics if metric["freshness"] == "stale"]
    warnings = [
        (
            "本页使用当前修订后的最终快照；核心美国序列尚缺完整发布日期、可用时间与历史版本，"
            "因此不能解读为伪实时回测或衰退概率。"
        ),
        "部分序列经FRED、OECD、行情聚合渠道分发；原始机构、更新周期和限制见美国数据来源页。",
    ]
    if employment_error:
        warnings.append(
            "美国就业专题本次加载失败；就业模块仅使用已入库非农序列降级展示，失败不解释为中性。"
        )
    if missing_codes:
        warnings.append(f"当前缺少指标：{'、'.join(missing_codes)}。缺失值未按0处理。")
    if stale_metrics:
        warnings.append(f"当前数据偏旧：{'、'.join(dict.fromkeys(stale_metrics))}；覆盖率已排除这些读数。")

    available_pillars = [pillar for pillar in pillars if pillar["confidence"] != "unavailable"]
    coverage = (
        sum(metric["value"] is not None and metric["freshness"] != "stale" for metric in metrics)
        / len(metrics)
        if metrics
        else 0.0
    )
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

    if recession["state_key"] == "broad":
        tone = "negative"
    elif inflation["tone"] in {"negative", "caution"} or policy["tone"] == "caution":
        tone = "caution"
    else:
        tone = "neutral"

    headline = (
        f"{growth['state_label']}；{labour['state_label']}；{inflation['state_label']}。"
        f"{recession['state_label']}，{policy['state_label']}。"
    )

    transmission = [
        {
            "key": "policy",
            "title": "政策起点",
            "state_label": policy["state_label"],
            "tone": policy["tone"],
            "detail": (
                f"事后实际利率代理为 {_fmt(real_rate_proxy, suffix='个百分点')}；"
                "它只描述方向，不估计中性利率。"
            ),
            "periods": [metric["period"] for metric in policy["metrics"] if metric["period"]],
        },
        {
            "key": "financial",
            "title": "市场传导",
            "state_label": financial["state_label"],
            "tone": financial["tone"],
            "detail": financial["summary"],
            "periods": [metric["period"] for metric in financial["metrics"] if metric["period"]],
        },
        {
            "key": "activity",
            "title": "实体活动",
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

    watch_items = []
    if (
        labour_diagnostics.get("payroll_current")
        and labour_diagnostics.get("payroll_average") is not None
        and labour_diagnostics.get("payroll_prior") is not None
    ):
        if labour_diagnostics["payroll_average"] < labour_diagnostics["payroll_prior"]:
            watch_items.append("非农三个月均值能否企稳，避免招聘降温扩散为就业收缩。")
    core = _latest(series, "US_CORE_CPI")
    if (
        core
        and _freshness(core["period"], "monthly") == "current"
        and core["value"] > 2.3
    ):
        watch_items.append("核心通胀能否继续回落，而不是在高于2%基准的位置重新加速。")
    ten_year = _latest(series, "US_10Y")
    if (
        ten_year
        and _freshness(ten_year["period"], "daily") == "current"
        and ten_year["value"] >= 4.5
    ):
        watch_items.append("长期国债收益率高位是否继续压制住房、投资和利率敏感需求。")
    curve = _latest(series, "US_10Y2Y")
    if (
        curve
        and _freshness(curve["period"], "daily") == "current"
        and curve["value"] >= 0
    ):
        watch_items.append("收益率曲线转正后，要区分增长预期改善与长端期限溢价上升。")
    if not watch_items:
        watch_items.append("等待下一批增长、就业和通胀数据，按各自发布节奏更新判断。")

    market_period = _latest_period(series, ("DJI", "US_2Y", "US_10Y", "US_10Y2Y"))
    monthly_period = _latest_period(
        series,
        ("US_CPI", "US_CORE_CPI", "US_IP", "US_CLI", "US_NFP", "US_FFR", "US_M2_YOY"),
    )
    quarterly_period = _latest_period(series, ("US_GDP",))
    employment_period = str(employment.get("latest_month")) if employment and employment.get("latest_month") else None

    return {
        "region": "US",
        "country": "美国",
        "title": "美国宏观综合分析",
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
            "缺失值不填0。当前版本是最终修订快照，待发布日与vintage证据补齐后再建设伪实时周期回测。"
        ),
        "pillars": pillars,
        "recession_breadth": recession,
        "transmission": transmission,
        "watch_items": watch_items,
        "data_source_path": "/data-sources/us",
        "warnings": warnings,
    }


def build_us_macro_overview(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(DataPoint)
        .where(DataPoint.indicator_code.in_(US_OVERVIEW_CODES))
        .order_by(DataPoint.indicator_code, DataPoint.date)
    ).scalars().all()

    employment, employment_error = _load_employment_enrichment()

    return _build_us_macro_overview(
        rows,
        employment,
        employment_error=employment_error,
    )

"""Transparent current-snapshot macro overviews for Japan and South Korea.

The two dashboards share a response contract, but not an economic shortcut.
Japan's monthly call-money rate is labelled as a market/policy-transmission
proxy, while Korea uses the Bank of Korea's official event history and keeps
the last change date separate from the latest verification date.  GDP gaps,
stale observations and incomplete financial-condition channels are never
zero-filled or hidden behind a composite score.
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

from app.fetchers.oecd_employment import fetch_oecd_employment_dashboard
from app.models import DataPoint, RefreshResult
from app.services.indicator_series import is_current_series_point


METHODOLOGY_VERSION = "1.0.0"
EMPLOYMENT_ENRICHMENT_TIMEOUT_SECONDS = 5.0
EMPLOYMENT_ENRICHMENT_TTL_SECONDS = 6 * 60 * 60

_employment_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="east-asia-employment")
_employment_futures: dict[str, Future[dict[str, Any]]] = {}
_employment_future_started_at: dict[str, float] = {}
_employment_lock = Lock()

JP_OVERVIEW_CODES = (
    "JP_IP",
    "JP_CLI",
    "JP_CPI",
    "JP_CORE_CPI",
    "JP_BOJ",
    "JP_BOJ_ASSETS",
    "NKY",
    "JPYCNY",
)

KR_OVERVIEW_CODES = (
    "KR_IP",
    "KR_CLI",
    "KR_CORE_CPI",
    "KR_BOK",
    "KR_RESERVES",
    "KOSPI",
    "KRWCNY",
)

MARKET_CODES = {"NKY", "JPYCNY", "KOSPI", "KRWCNY"}
EVENT_CODES = {"KR_BOK"}


def _load_employment_enrichment(region: str) -> tuple[Mapping[str, Any] | None, str | None]:
    """Bound external labour-data latency while one shared request warms."""

    with _employment_lock:
        now = time.monotonic()
        future = _employment_futures.get(region)
        started_at = _employment_future_started_at.get(region)
        expired = (
            future is not None
            and future.done()
            and started_at is not None
            and now - started_at >= EMPLOYMENT_ENRICHMENT_TTL_SECONDS
        )
        if future is None or expired:
            future = _employment_executor.submit(fetch_oecd_employment_dashboard, region)
            _employment_futures[region] = future
            _employment_future_started_at[region] = now
    try:
        return future.result(timeout=EMPLOYMENT_ENRICHMENT_TIMEOUT_SECONDS), None
    except FutureTimeoutError:
        return None, "TimeoutError"
    except Exception as exc:
        with _employment_lock:
            if _employment_futures.get(region) is future:
                _employment_futures.pop(region, None)
                _employment_future_started_at.pop(region, None)
        return None, type(exc).__name__


def _field(row: Any, name: str, default: Any = None) -> Any:
    return row.get(name, default) if isinstance(row, Mapping) else getattr(row, name, default)


def _period(code: str, observed: date | str | None) -> str | None:
    if observed is None:
        return None
    raw = observed if isinstance(observed, str) else observed.isoformat()
    return raw[:10] if code in MARKET_CODES | EVENT_CODES else raw[:7]


def _series(
    rows: Iterable[Any], codes: tuple[str, ...]
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {code: [] for code in codes}
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


def _latest(series: Mapping[str, list[dict[str, Any]]], code: str) -> dict[str, Any] | None:
    values = series.get(code, [])
    return values[-1] if values else None


def _reference(
    series: Mapping[str, list[dict[str, Any]]], code: str, observations_back: int
) -> dict[str, Any] | None:
    """Return an observation-count lag for daily/market series only."""

    values = series.get(code, [])
    index = len(values) - 1 - observations_back
    return values[index] if index >= 0 else None


def _monthly_point_reference(
    points: list[dict[str, Any]], months_back: int
) -> dict[str, Any] | None:
    """Return the exact calendar-month lag, never a row-count substitute."""

    if not points:
        return None
    try:
        target_index = _month_index(str(points[-1]["period"])) - months_back
        return next(
            point
            for point in reversed(points)
            if _month_index(str(point["period"])) == target_index
        )
    except (KeyError, StopIteration, TypeError, ValueError):
        return None


def _monthly_reference(
    series: Mapping[str, list[dict[str, Any]]], code: str, months_back: int
) -> dict[str, Any] | None:
    return _monthly_point_reference(series.get(code, []), months_back)


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
    """Use the older observation period as the derived metric's watermark."""

    available = [period for period in periods if period]
    return min(available) if len(available) == len(periods) and available else None


def _month_index(period: str) -> int:
    year_text, month_text = period[:7].split("-")
    return int(year_text) * 12 + int(month_text) - 1


def _is_consecutive_months(points: list[dict[str, Any]]) -> bool:
    if len(points) < 2:
        return True
    try:
        indices = [_month_index(str(point["period"])) for point in points]
    except (KeyError, TypeError, ValueError):
        return False
    return all(right - left == 1 for left, right in zip(indices, indices[1:]))


def _trailing_mean(points: list[dict[str, Any]], count: int) -> float | None:
    trailing = points[-count:]
    if len(trailing) != count or not _is_consecutive_months(trailing):
        return None
    return mean(float(point["value"]) for point in trailing)


def _prior_trailing_mean(points: list[dict[str, Any]], count: int) -> float | None:
    window = points[-2 * count :]
    if len(window) != 2 * count or not _is_consecutive_months(window):
        return None
    return mean(float(point["value"]) for point in window[:count])


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
        if item.get("key") != key:
            continue
        points = [
            {"period": str(point["period"]), "value": float(point["value"])}
            for point in item.get("points", [])
            if point.get("period") is not None and point.get("value") is not None
        ]
        return sorted(points, key=lambda point: point["period"])
    return []


def _build_growth(
    series: Mapping[str, list[dict[str, Any]]], *, prefix: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    industrial_code = f"{prefix}_IP"
    cli_code = f"{prefix}_CLI"
    industrial_points = series.get(industrial_code, [])
    industrial = _latest(series, industrial_code)
    industrial_average = _trailing_mean(industrial_points, 3)
    industrial_previous_average = _prior_trailing_mean(industrial_points, 3)
    cli = _latest(series, cli_code)
    cli_previous = _monthly_reference(series, cli_code, 3)
    cli_value = cli["value"] if cli else None
    cli_delta = cli_value - cli_previous["value"] if cli_value is not None and cli_previous else None

    current_ip = industrial_average is not None and _freshness(
        industrial["period"] if industrial else None, "monthly"
    ) == "current"
    current_cli = cli_value is not None and _freshness(
        cli["period"] if cli else None, "monthly"
    ) == "current"

    if current_ip and current_cli and industrial_average < 0 and cli_value < 100 and (cli_delta or 0) < 0:
        state_key, state_label, tone = "contracting_partial", "生产与领先指标同步偏弱，GDP缺失", "negative"
    elif current_ip and current_cli and industrial_average >= 0 and cli_value >= 100 and (cli_delta or 0) > 0:
        state_key, state_label, tone = "improving_partial", "工业与领先信号改善，缺少GDP确认", "positive"
    elif current_ip and industrial_average < 0:
        state_key, state_label, tone = "industrial_weak_partial", "工业生产偏弱，GDP证据缺失", "caution"
    elif current_cli and cli_value >= 100 and (cli_delta or 0) > 0:
        state_key, state_label, tone = "leading_improving_partial", "领先指标改善，增长证据仍不完整", "neutral"
    elif current_ip or current_cli:
        state_key, state_label, tone = "mixed_partial", "增长信号分化，缺少GDP确认", "caution"
    else:
        state_key, state_label, tone = "unavailable", "增长证据不足", "unavailable"

    metrics = [
        _metric(
            key="real_gdp_yoy",
            label="实际GDP同比",
            value=None,
            unit="%",
            period=None,
            reference_value=None,
            reference_period=None,
            interpretation="项目尚未接入可持续更新的季度GDP；空缺不由工业生产代填。",
            formula="当前无可用输入",
            source_codes=[],
            frequency="quarterly",
        ),
        _metric(
            key="industrial_production_three_month_average",
            label="工业生产同比近3个月均值",
            value=industrial_average,
            unit="%",
            period=industrial["period"] if industrial else None,
            reference_value=industrial_previous_average,
            reference_period=(
                industrial_points[-4]["period"] if len(industrial_points) >= 6 else None
            ),
            interpretation="三个月均值降低单月噪声；只覆盖工业，不能代替GDP或服务业活动。",
            formula="最近连续3个月工业生产同比的算术平均",
            source_codes=[industrial_code],
        ),
        _metric(
            key="composite_leading_indicator",
            label="综合领先指标",
            value=cli_value,
            unit="点",
            period=cli["period"] if cli else None,
            reference_value=cli_previous["value"] if cli_previous else None,
            reference_period=cli_previous["period"] if cli_previous else None,
            interpretation="OECD振幅调整指标以100为长期趋势基准；方向与水平需结合解读。",
            formula="CLI最新值；参考值为3个月前",
            source_codes=[cli_code],
        ),
    ]
    current_available = sum(metric["value"] is not None and metric["freshness"] == "current" for metric in metrics)
    summary = (
        f"工业生产近3个月同比均值 {_fmt(industrial_average, suffix='%')}；CLI "
        f"{_fmt(cli_value)}，较3个月前变化 {_fmt(cli_delta, suffix='点')}。"
        "GDP尚未接入，不能据此宣称总量经济全面扩张或收缩。"
    )
    return {
        "key": "growth",
        "title": "增长与领先信号",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": summary,
        "confidence": _confidence(current_available, 3),
        "metrics": metrics,
    }, {
        "industrial_average": industrial_average,
        "industrial_current": current_ip,
        "cli": cli_value,
        "cli_delta": cli_delta,
        "cli_current": current_cli,
    }


def _build_labour(
    employment: Mapping[str, Any] | None, *, region: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    unemployment_points = _employment_points(employment, "unemployment")
    youth_points = _employment_points(employment, "youth_unemployment")
    participation_points = _employment_points(employment, "labor_participation")
    employment_points = _employment_points(employment, "employment_ratio")

    def latest(points: list[dict[str, Any]]) -> dict[str, Any] | None:
        return points[-1] if points else None

    unemployment = latest(unemployment_points)
    unemployment_reference = _monthly_point_reference(unemployment_points, 3)
    youth = latest(youth_points)
    youth_year_ago = _monthly_point_reference(youth_points, 12)
    participation = latest(participation_points)
    participation_year_ago = _monthly_point_reference(participation_points, 12)
    employment_rate = latest(employment_points)
    employment_year_ago = _monthly_point_reference(employment_points, 12)

    unemployment_value = unemployment["value"] if unemployment else None
    unemployment_delta = (
        unemployment_value - unemployment_reference["value"]
        if unemployment_value is not None and unemployment_reference
        else None
    )
    youth_value = youth["value"] if youth else None
    youth_change = youth_value - youth_year_ago["value"] if youth_value is not None and youth_year_ago else None
    participation_value = participation["value"] if participation else None
    participation_change = (
        participation_value - participation_year_ago["value"]
        if participation_value is not None and participation_year_ago
        else None
    )
    employment_value = employment_rate["value"] if employment_rate else None
    employment_change = (
        employment_value - employment_year_ago["value"]
        if employment_value is not None and employment_year_ago
        else None
    )

    unemployment_current = (
        unemployment_value is not None
        and _freshness(unemployment["period"] if unemployment else None, "monthly")
        == "current"
    )
    youth_current = (
        youth_value is not None
        and _freshness(youth["period"] if youth else None, "monthly") == "current"
    )
    employment_current = (
        employment_value is not None
        and _freshness(
            employment_rate["period"] if employment_rate else None,
            "monthly",
        )
        == "current"
    )
    current_unemployment_delta = unemployment_delta if unemployment_current else None
    current_youth_change = youth_change if youth_current else None
    current_employment_change = employment_change if employment_current else None

    if current_unemployment_delta is None and current_employment_change is None:
        state_key, state_label, tone = "unavailable", "就业证据不足", "unavailable"
    elif (
        current_unemployment_delta is not None
        and current_unemployment_delta >= 0.3 - 1e-9
        and current_employment_change is not None
        and current_employment_change <= -0.3 + 1e-9
    ):
        state_key, state_label, tone = "weakening", "就业市场明显走弱", "negative"
    elif current_youth_change is not None and current_youth_change >= 1.0 - 1e-9:
        state_key, state_label, tone = "stable_youth_weakness", "就业总量稳定，青年压力上升", "caution"
    elif (
        current_unemployment_delta is not None
        and current_unemployment_delta <= -0.2 + 1e-9
        and current_employment_change is not None
        and current_employment_change >= 0
    ):
        state_key, state_label, tone = "resilient", "就业稳健，劳动力市场仍偏紧", "positive"
    else:
        state_key, state_label, tone = "stable", "就业总量基本稳定", "neutral"

    metrics = [
        _metric(
            key="unemployment_rate",
            label="15—64岁失业率",
            value=unemployment_value,
            unit="%",
            period=unemployment["period"] if unemployment else None,
            reference_value=unemployment_reference["value"] if unemployment_reference else None,
            reference_period=unemployment_reference["period"] if unemployment_reference else None,
            interpretation="采用国家劳动力调查经OECD协调的15—64岁季调口径。",
            formula="最新失业率；参考值为3个月前",
            source_codes=[f"{region}_EMPLOYMENT_UNEMPLOYMENT"],
        ),
        _metric(
            key="youth_unemployment_rate",
            label="15—24岁失业率",
            value=youth_value,
            unit="%",
            period=youth["period"] if youth else None,
            reference_value=youth_year_ago["value"] if youth_year_ago else None,
            reference_period=youth_year_ago["period"] if youth_year_ago else None,
            interpretation="青年就业更易波动，用于识别总量指标掩盖的年龄分化。",
            formula="最新青年失业率；参考值为12个月前",
            source_codes=[f"{region}_EMPLOYMENT_YOUTH"],
        ),
        _metric(
            key="labour_participation_rate",
            label="15—64岁劳动参与率",
            value=participation_value,
            unit="%",
            period=participation["period"] if participation else None,
            reference_value=participation_year_ago["value"] if participation_year_ago else None,
            reference_period=participation_year_ago["period"] if participation_year_ago else None,
            interpretation="参与率是劳动力供给背景，不单独作为景气好坏信号。",
            formula="最新劳动参与率；参考值为12个月前",
            source_codes=[f"{region}_EMPLOYMENT_PARTICIPATION"],
        ),
        _metric(
            key="employment_ratio",
            label="15—64岁就业率",
            value=employment_value,
            unit="%",
            period=employment_rate["period"] if employment_rate else None,
            reference_value=employment_year_ago["value"] if employment_year_ago else None,
            reference_period=employment_year_ago["period"] if employment_year_ago else None,
            interpretation="同年龄口径的就业率用于交叉核对失业率，避免只看一个比率。",
            formula="最新就业率；参考值为12个月前",
            source_codes=[f"{region}_EMPLOYMENT_RATIO"],
        ),
    ]
    available = sum(metric["value"] is not None and metric["freshness"] == "current" for metric in metrics)
    return {
        "key": "labour",
        "title": "就业与劳动力供给",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"失业率 {_fmt(unemployment_value, suffix='%')}，较3个月前变化 "
            f"{_fmt(unemployment_delta, suffix='个百分点')}；就业率同比变化 "
            f"{_fmt(employment_change, suffix='个百分点')}；青年失业率同比变化 "
            f"{_fmt(youth_change, suffix='个百分点')}。"
        ),
        "confidence": _confidence(available, 4),
        "metrics": metrics,
    }, {
        "unemployment_delta": unemployment_delta,
        "unemployment_current": unemployment_current,
        "employment_change": employment_change,
        "employment_current": employment_current,
        "youth_change": youth_change,
        "youth_current": youth_current,
    }


def _build_inflation_jp(series: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    headline = _latest(series, "JP_CPI")
    headline_previous = _monthly_reference(series, "JP_CPI", 3)
    core = _latest(series, "JP_CORE_CPI")
    core_previous = _monthly_reference(series, "JP_CORE_CPI", 3)
    headline_value = headline["value"] if headline else None
    core_value = core["value"] if core else None
    headline_delta = headline_value - headline_previous["value"] if headline_value is not None and headline_previous else None
    core_delta = core_value - core_previous["value"] if core_value is not None and core_previous else None
    current_headline = (
        headline_value
        if _freshness(headline["period"] if headline else None, "monthly") == "current"
        else None
    )
    current_core = (
        core_value
        if _freshness(core["period"] if core else None, "monthly") == "current"
        else None
    )
    current_headline_delta = headline_delta if current_headline is not None else None
    current_core_delta = core_delta if current_core is not None else None

    if current_headline is None and current_core is None:
        state_key, state_label, tone = "unavailable", "通胀证据不足", "unavailable"
    elif current_headline is not None and current_core is not None and current_headline < 0 and current_core < 0:
        state_key, state_label, tone = "deflation_risk", "总体与核心价格同步下降", "negative"
    elif (
        current_headline is not None
        and 1.5 <= current_headline <= 2.5
        and ((current_headline_delta or 0) >= 0.2 or (current_core_delta or 0) >= 0.2)
    ):
        state_key, state_label, tone = "near_target_rising", "通胀接近2%，近期回升", "caution"
    elif current_headline is not None and current_headline > 2.5 and (current_core or 0) > 2.5:
        state_key, state_label, tone = "above_target_broad", "总体与可比核心通胀均偏高", "negative"
    else:
        state_key, state_label, tone = "mixed", "通胀接近目标但内部信号分化", "neutral"

    metrics = [
        _metric(
            key="headline_cpi_yoy",
            label="总体CPI同比",
            value=headline_value,
            unit="%",
            period=headline["period"] if headline else None,
            reference_value=headline_previous["value"] if headline_previous else None,
            reference_period=headline_previous["period"] if headline_previous else None,
            interpretation="以日本银行2%物价稳定目标作背景，但单月读数不是政策承诺的达成判定。",
            formula="总体CPI同比；参考值为3个月前",
            source_codes=["JP_CPI"],
        ),
        _metric(
            key="comparable_core_cpi_yoy",
            label="剔除食品和能源CPI同比",
            value=core_value,
            unit="%",
            period=core["period"] if core else None,
            reference_value=core_previous["value"] if core_previous else None,
            reference_period=core_previous["period"] if core_previous else None,
            interpretation="这是OECD可比核心口径，不是日本国内通常所称的“剔除生鲜食品”核心CPI。",
            formula="OECD剔除食品和能源CPI同比；参考值为3个月前",
            source_codes=["JP_CORE_CPI"],
        ),
    ]
    available = sum(metric["value"] is not None and metric["freshness"] == "current" for metric in metrics)
    return {
        "key": "inflation",
        "title": "通胀与价格结构",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"总体CPI同比 {_fmt(headline_value, suffix='%')}，较3个月前变化 "
            f"{_fmt(headline_delta, suffix='个百分点')}；剔除食品和能源CPI同比 "
            f"{_fmt(core_value, suffix='%')}，变化 {_fmt(core_delta, suffix='个百分点')}。"
        ),
        "confidence": _confidence(available, 2),
        "metrics": metrics,
    }


def _build_inflation_kr(series: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    core = _latest(series, "KR_CORE_CPI")
    core_previous = _monthly_reference(series, "KR_CORE_CPI", 3)
    core_value = core["value"] if core else None
    core_delta = core_value - core_previous["value"] if core_value is not None and core_previous else None
    core_current = (
        core_value is not None
        and _freshness(core["period"] if core else None, "monthly") == "current"
    )
    if not core_current:
        state_key, state_label, tone = "unavailable", "通胀证据不足", "unavailable"
    elif core_value >= 3 and (core_delta or 0) >= 0.3:
        state_key, state_label, tone = "core_reaccelerating_partial", "核心通胀明显上升，缺少总体CPI", "caution"
    elif (core_delta or 0) >= 0.3:
        state_key, state_label, tone = "core_rising_partial", "核心通胀升温，缺少总体CPI", "caution"
    else:
        state_key, state_label, tone = "core_only_partial", "仅有核心通胀，整体价格压力待确认", "neutral"

    metrics = [
        _metric(
            key="headline_cpi_yoy",
            label="总体CPI同比",
            value=None,
            unit="%",
            period=None,
            reference_value=None,
            reference_period=None,
            interpretation="项目尚未接入可持续更新的韩国总体CPI；核心CPI不能代填。",
            formula="当前无可用输入",
            source_codes=[],
        ),
        _metric(
            key="core_cpi_yoy",
            label="核心CPI同比",
            value=core_value,
            unit="%",
            period=core["period"] if core else None,
            reference_value=core_previous["value"] if core_previous else None,
            reference_period=core_previous["period"] if core_previous else None,
            interpretation="核心CPI用于观察基础价格压力，但韩国银行2%目标针对总体CPI，二者不能等同。",
            formula="OECD核心CPI同比；参考值为3个月前",
            source_codes=["KR_CORE_CPI"],
        ),
    ]
    available = sum(metric["value"] is not None and metric["freshness"] == "current" for metric in metrics)
    return {
        "key": "inflation",
        "title": "通胀与价格结构",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"核心CPI同比 {_fmt(core_value, suffix='%')}，较3个月前变化 "
            f"{_fmt(core_delta, suffix='个百分点')}。总体CPI缺失，不能将核心读数直接写成整体通胀。"
        ),
        "confidence": _confidence(available, 2),
        "metrics": metrics,
    }


def _build_policy_jp(series: Mapping[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, Any]]:
    rate = _latest(series, "JP_BOJ")
    six_months = _monthly_reference(series, "JP_BOJ", 6)
    twelve_months = _monthly_reference(series, "JP_BOJ", 12)
    core = _latest(series, "JP_CORE_CPI")
    rate_value = rate["value"] if rate else None
    six_month_change = rate_value - six_months["value"] if rate_value is not None and six_months else None
    twelve_month_change = rate_value - twelve_months["value"] if rate_value is not None and twelve_months else None
    real_proxy = rate_value - core["value"] if rate_value is not None and core else None
    rate_freshness = (
        _freshness(rate["period"] if rate else None, "monthly")
        if rate_value is not None
        else "missing"
    )
    core_freshness = (
        _freshness(core["period"] if core else None, "monthly")
        if core is not None
        else "missing"
    )
    real_proxy_freshness = _combined_freshness(rate_freshness, core_freshness)
    current_real_proxy = real_proxy if real_proxy_freshness == "current" else None

    if rate_value is None:
        state_key, state_label, tone = "unavailable", "短端政策传导证据不足", "unavailable"
    elif rate_freshness == "stale":
        state_key, state_label, tone = "stale_policy_input", "短端利率观察期偏旧", "caution"
    elif six_month_change is not None and six_month_change >= 0.15 - 1e-9:
        if current_real_proxy is not None and current_real_proxy < 0:
            state_key, state_label, tone = "normalising_real_negative", "短端利率上行，事后实际代理仍为负", "caution"
        else:
            state_key, state_label, tone = "normalising", "短端利率继续上行", "caution"
    elif six_month_change is not None and six_month_change <= -0.15 + 1e-9:
        state_key, state_label, tone = "easing", "短端利率回落", "positive"
    else:
        state_key, state_label, tone = "steady", "短端利率近半年基本稳定", "neutral"

    metrics = [
        _metric(
            key="call_money_rate",
            label="隔夜拆借利率月均",
            value=rate_value,
            unit="%",
            period=rate["period"] if rate else None,
            reference_value=six_months["value"] if six_months else None,
            reference_period=six_months["period"] if six_months else None,
            interpretation="这是<24小时拆借/同业市场利率月均，不是日本银行最新会议的官方目标利率。",
            formula="OECD经FRED分发的月均短端市场利率",
            source_codes=["JP_BOJ"],
        ),
        _metric(
            key="six_month_rate_change",
            label="短端利率近6个月变化",
            value=six_month_change,
            unit="百分点",
            period=rate["period"] if rate else None,
            reference_value=0 if six_month_change is not None else None,
            reference_period=six_months["period"] if six_months else None,
            interpretation="用日历月窗口识别正常化方向，不按政策会议事件日解释。",
            formula="最新月均短端利率 − 6个月前",
            source_codes=["JP_BOJ"],
        ),
        _metric(
            key="twelve_month_rate_change",
            label="短端利率近12个月变化",
            value=twelve_month_change,
            unit="百分点",
            period=rate["period"] if rate else None,
            reference_value=0 if twelve_month_change is not None else None,
            reference_period=twelve_months["period"] if twelve_months else None,
            interpretation="较长窗口用于区分短期波动和持续正常化。",
            formula="最新月均短端利率 − 12个月前",
            source_codes=["JP_BOJ"],
        ),
        _metric(
            key="ex_post_real_rate_proxy",
            label="事后实际短端利率代理",
            value=real_proxy,
            unit="百分点",
            period=_combined_period(
                rate["period"] if rate else None,
                core["period"] if core else None,
            ),
            reference_value=0 if real_proxy is not None else None,
            reference_period=core["period"] if core else None,
            interpretation="名义月均短端利率减OECD可比核心CPI；不是前瞻实际利率，也不是政策中性利率。",
            formula="JP_BOJ月均值 − JP_CORE_CPI同比",
            source_codes=["JP_BOJ", "JP_CORE_CPI"],
            freshness_override=real_proxy_freshness,
        ),
    ]
    available = sum(metric["value"] is not None and metric["freshness"] == "current" for metric in metrics)
    return {
        "key": "monetary_policy",
        "title": "日本短端利率与政策传导代理",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"隔夜拆借月均 {_fmt(rate_value, suffix='%')}，近6个月变化 "
            f"{_fmt(six_month_change, suffix='个百分点')}，近12个月变化 "
            f"{_fmt(twelve_month_change, suffix='个百分点')}；事后实际代理 "
            f"{_fmt(real_proxy, suffix='个百分点')}。"
        ),
        "confidence": _confidence(available, 4),
        "metrics": metrics,
    }, {
        "six_month_change": six_month_change,
        "real_proxy": real_proxy,
    }


def _shift_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, min(value.day, 28))


def _event_value_at(points: list[dict[str, Any]], cutoff: date) -> float | None:
    eligible = [
        point
        for point in points
        if date.fromisoformat(str(point["period"])[:10]) <= cutoff
    ]
    return eligible[-1]["value"] if eligible else None


def _build_policy_kr(
    series: Mapping[str, list[dict[str, Any]]],
    policy_verified_at: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    points = series.get("KR_BOK", [])
    rate = points[-1] if points else None
    core = _latest(series, "KR_CORE_CPI")
    rate_value = rate["value"] if rate else None
    core_value = core["value"] if core else None
    verification_value = policy_verified_at
    verified_period = (
        verification_value.isoformat()[:10]
        if hasattr(verification_value, "isoformat")
        else str(verification_value or "")[:10]
    )
    try:
        verified_date = date.fromisoformat(verified_period)
        policy_freshness = (
            "current"
            if max((date.today() - verified_date).days, 0) <= 30
            else "stale"
        )
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
        if rate_value is not None and six_month_rate is not None
        else None
    )
    twelve_month_change = (
        rate_value - twelve_month_rate
        if rate_value is not None and twelve_month_rate is not None
        else None
    )
    real_proxy = (
        rate_value - core_value
        if rate_value is not None and core_value is not None
        else None
    )
    core_freshness = (
        _freshness(core["period"] if core else None, "monthly")
        if core_value is not None
        else "missing"
    )
    real_proxy_freshness = _combined_freshness(policy_freshness, core_freshness)
    current_real_proxy = real_proxy if real_proxy_freshness == "current" else None

    if policy_freshness == "stale":
        state_key, state_label, tone = (
            "stale_policy_input",
            "政策利率核验日期偏旧",
            "caution",
        )
    elif six_month_change is not None and six_month_change >= 0.25:
        if current_real_proxy is not None and current_real_proxy < 0:
            state_key, state_label, tone = (
                "tightening_real_negative",
                "近6个月加息，事后实际代理仍为负",
                "caution",
            )
        else:
            state_key, state_label, tone = "tightening", "近6个月加息", "caution"
    elif six_month_change is not None and six_month_change <= -0.25:
        state_key, state_label, tone = "easing", "近6个月降息", "neutral"
    elif twelve_month_change is not None and twelve_month_change > 0:
        state_key, state_label, tone = "tightening_then_hold", "加息后持稳", "caution"
    elif twelve_month_change is not None and twelve_month_change < 0:
        state_key, state_label, tone = "easing_then_hold", "降息后持稳", "neutral"
    elif rate_value is not None:
        state_key, state_label, tone = "steady", "基准利率近半年持稳", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "政策利率证据不足", "unavailable"

    common_freshness = policy_freshness if rate_value is not None else "missing"
    metrics = [
        _metric(
            key="bok_base_rate",
            label="韩国银行基准利率",
            value=rate_value,
            unit="%",
            period=rate["period"] if rate else None,
            reference_value=six_month_rate,
            reference_period=(
                six_month_cutoff.isoformat() if six_month_rate is not None else None
            ),
            interpretation="该日期是最近一次利率变动生效日，不是数据过期日；持平会议不会新增变动事件。",
            formula="韩国银行官方Base Rate变更历史；当前值按最近抓取日期复核",
            source_codes=["KR_BOK"],
            frequency="event",
            freshness_override=common_freshness,
        ),
        _metric(
            key="six_month_rate_change",
            label="基准利率近6个月变化",
            value=six_month_change,
            unit="百分点",
            period=verified_period or (rate["period"] if rate else None),
            reference_value=0 if six_month_change is not None else None,
            reference_period=(
                six_month_cutoff.isoformat() if six_month_rate is not None else None
            ),
            interpretation="按日历时点的有效利率比较；持平会议即使没有新事件，也会正确得到0。",
            formula="核验日有效基准利率 − 6个月前有效基准利率",
            source_codes=["KR_BOK"],
            frequency="mixed",
            freshness_override=common_freshness,
        ),
        _metric(
            key="twelve_month_rate_change",
            label="基准利率近12个月变化",
            value=twelve_month_change,
            unit="百分点",
            period=verified_period or (rate["period"] if rate else None),
            reference_value=0 if twelve_month_change is not None else None,
            reference_period=(
                twelve_month_cutoff.isoformat()
                if twelve_month_rate is not None
                else None
            ),
            interpretation="按12个月日历窗口观察较完整的政策方向，不以事件条数代替时间跨度。",
            formula="核验日有效基准利率 − 12个月前有效基准利率",
            source_codes=["KR_BOK"],
            frequency="mixed",
            freshness_override=common_freshness,
        ),
        _metric(
            key="ex_post_real_rate_proxy",
            label="事后实际政策利率代理",
            value=real_proxy,
            unit="百分点",
            period=_combined_period(
                verified_period or (rate["period"] if rate else None),
                core["period"] if core else None,
            ),
            reference_value=0 if real_proxy is not None else None,
            reference_period=core["period"] if core else None,
            interpretation="基准利率减OECD可比核心CPI；韩国银行2%目标针对总体CPI，因此只作事后方向性代理。",
            formula="KR_BOK当前值 − KR_CORE_CPI同比",
            source_codes=["KR_BOK", "KR_CORE_CPI"],
            frequency="mixed",
            freshness_override=real_proxy_freshness,
        ),
    ]
    available = sum(
        metric["value"] is not None and metric["freshness"] == "current"
        for metric in metrics
    )
    confidence = (
        "low"
        if policy_freshness == "stale" and available
        else _confidence(available, 4)
    )
    return {
        "key": "monetary_policy",
        "title": "韩国银行货币政策",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"基准利率 {_fmt(rate_value, suffix='%')}，最近变动日 "
            f"{rate['period'] if rate else '暂无'}，数据核验至 {verified_period or '未知'}；"
            f"近6个月变化 {_fmt(six_month_change, suffix='个百分点')}，近12个月变化 "
            f"{_fmt(twelve_month_change, suffix='个百分点')}；事后实际代理 "
            f"{_fmt(real_proxy, suffix='个百分点')}。"
        ),
        "confidence": confidence,
        "metrics": metrics,
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


def _build_financial_jp(series: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    assets = _latest(series, "JP_BOJ_ASSETS")
    assets_year_ago = _monthly_reference(series, "JP_BOJ_ASSETS", 12)
    equity = _latest(series, "NKY")
    equity_previous = _reference(series, "NKY", 60)
    fx = _latest(series, "JPYCNY")
    fx_previous = _reference(series, "JPYCNY", 60)
    assets_level = assets["value"] / 10_000 if assets else None
    assets_reference_level = assets_year_ago["value"] / 10_000 if assets_year_ago else None
    assets_change = _percentage_change(assets, assets_year_ago)
    equity_return = _percentage_change(equity, equity_previous)
    fx_change = _percentage_change(fx, fx_previous)
    assets_current = (
        assets_change is not None
        and _freshness(assets["period"] if assets else None, "monthly") == "current"
    )
    equity_current = (
        equity_return is not None
        and _freshness(equity["period"] if equity else None, "daily") == "current"
    )
    current_assets_change = assets_change if assets_current else None
    current_equity_return = equity_return if equity_current else None

    if (
        current_assets_change is not None
        and current_assets_change <= -2
        and current_equity_return is not None
        and current_equity_return <= -10
    ):
        state_key, state_label, tone = "tight_visible", "资产负债表收缩，股市明显承压", "negative"
    elif current_assets_change is not None and current_assets_change <= -2:
        state_key, state_label, tone = "balance_sheet_contracting", "资产负债表明显收缩，股市短期回撤", "caution"
    elif (
        current_assets_change is not None
        and current_assets_change >= 2
        and current_equity_return is not None
        and current_equity_return >= 5
    ):
        state_key, state_label, tone = "supportive_visible", "可见流动性与市场信号有支撑", "positive"
    elif current_assets_change is not None or current_equity_return is not None:
        state_key, state_label, tone = "mixed_limited", "央行资产与市场信号分化", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "货币与市场证据不足", "unavailable"

    metrics = [
        _metric(
            key="boj_total_assets",
            label="日本银行总资产",
            value=assets_level,
            unit="万亿日元",
            period=assets["period"] if assets else None,
            reference_value=assets_reference_level,
            reference_period=assets_year_ago["period"] if assets_year_ago else None,
            interpretation="总资产是央行资产负债表规模，不是M1/M2，也不等同银行对实体经济的新增信贷。",
            formula="JPNASSETS（亿日元）÷ 10,000",
            source_codes=["JP_BOJ_ASSETS"],
        ),
        _metric(
            key="boj_assets_yoy",
            label="日本银行总资产约一年变化",
            value=assets_change,
            unit="%",
            period=assets["period"] if assets else None,
            reference_value=0 if assets_change is not None else None,
            reference_period=assets_year_ago["period"] if assets_year_ago else None,
            interpretation="资产变化还受到到期、操作和估值影响，不能机械等同量化紧缩强度。",
            formula="最新总资产 ÷ 12个月前 − 1",
            source_codes=["JP_BOJ_ASSETS"],
        ),
        _metric(
            key="equity_return_60d",
            label="日经225约3个月回报",
            value=equity_return,
            unit="%",
            period=equity["period"] if equity else None,
            reference_value=0 if equity_return is not None else None,
            reference_period=equity_previous["period"] if equity_previous else None,
            interpretation="股指包含全球盈利和汇率效应，只代表市场风险偏好的一部分。",
            formula="最新日经225 ÷ 60个交易观察前 − 1",
            source_codes=["NKY"],
            frequency="daily",
        ),
        _metric(
            key="jpy_cny_change_60d",
            label="日元兑人民币约3个月变化",
            value=fx_change,
            unit="%",
            period=fx["period"] if fx else None,
            reference_value=0 if fx_change is not None else None,
            reference_period=fx_previous["period"] if fx_previous else None,
            interpretation="每100日元兑人民币的双边牌价，只作背景，不进入金融状态与下行打分。",
            formula="最新JPYCNY ÷ 60个观测前 − 1",
            source_codes=["JPYCNY"],
            frequency="daily",
        ),
    ]
    return {
        "key": "financial_conditions",
        "title": "央行资产负债表与市场背景",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"日本银行总资产约 {_fmt(assets_level, suffix='万亿日元')}，约一年变化 "
            f"{_fmt(assets_change, suffix='%')}；日经225约3个月回报 {_fmt(equity_return, suffix='%')}。"
            "缺JGB收益率曲线、信用利差、银行贷款和持续更新的广义货币，因此不称完整金融条件指数。"
        ),
        "confidence": _confidence(
            sum((assets_current, equity_current)), 6
        ),
        "metrics": metrics,
    }


def _build_financial_kr(series: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    reserves = _latest(series, "KR_RESERVES")
    reserves_year_ago = _monthly_reference(series, "KR_RESERVES", 12)
    equity = _latest(series, "KOSPI")
    equity_previous = _reference(series, "KOSPI", 60)
    fx = _latest(series, "KRWCNY")
    fx_previous = _reference(series, "KRWCNY", 60)
    reserves_level = reserves["value"] / 1_000 if reserves else None
    reserves_reference_level = reserves_year_ago["value"] / 1_000 if reserves_year_ago else None
    reserves_change = _percentage_change(reserves, reserves_year_ago)
    equity_return = _percentage_change(equity, equity_previous)
    fx_change = _percentage_change(fx, fx_previous)
    reserves_current = (
        reserves_change is not None
        and _freshness(reserves["period"] if reserves else None, "monthly") == "current"
    )
    equity_current = (
        equity_return is not None
        and _freshness(equity["period"] if equity else None, "daily") == "current"
    )
    current_reserves_change = reserves_change if reserves_current else None
    current_equity_return = equity_return if equity_current else None

    if (
        current_equity_return is not None
        and current_equity_return <= -10
        and current_reserves_change is not None
        and current_reserves_change >= 0
    ):
        state_key, state_label, tone = "buffer_stable_market_stress", "外部缓冲稳健，股市明显承压", "caution"
    elif current_equity_return is not None and current_equity_return <= -10:
        state_key, state_label, tone = "market_stress", "股市明显承压，外部缓冲待确认", "negative"
    elif (
        current_reserves_change is not None
        and current_reserves_change >= 0
        and current_equity_return is not None
        and current_equity_return >= 5
    ):
        state_key, state_label, tone = "supportive_visible", "外储与股市信号有支撑", "positive"
    elif current_reserves_change is not None or current_equity_return is not None:
        state_key, state_label, tone = "mixed_limited", "外部缓冲与市场信号分化", "neutral"
    else:
        state_key, state_label, tone = "unavailable", "外部与市场证据不足", "unavailable"

    metrics = [
        _metric(
            key="foreign_reserves",
            label="外汇储备",
            value=reserves_level,
            unit="十亿美元",
            period=reserves["period"] if reserves else None,
            reference_value=reserves_reference_level,
            reference_period=reserves_year_ago["period"] if reserves_year_ago else None,
            interpretation="外储代表外部缓冲，不是货币供应、央行总资产或国内融资条件。",
            formula="KR_RESERVES（百万美元）÷ 1,000",
            source_codes=["KR_RESERVES"],
        ),
        _metric(
            key="foreign_reserves_yoy",
            label="外汇储备约一年变化",
            value=reserves_change,
            unit="%",
            period=reserves["period"] if reserves else None,
            reference_value=0 if reserves_change is not None else None,
            reference_period=reserves_year_ago["period"] if reserves_year_ago else None,
            interpretation="变化受干预、估值与资产配置影响，不直接代表国内流动性宽松或收紧。",
            formula="最新外汇储备 ÷ 12个月前 − 1",
            source_codes=["KR_RESERVES"],
        ),
        _metric(
            key="equity_return_60d",
            label="KOSPI约3个月回报",
            value=equity_return,
            unit="%",
            period=equity["period"] if equity else None,
            reference_value=0 if equity_return is not None else None,
            reference_period=equity_previous["period"] if equity_previous else None,
            interpretation="KOSPI反映部分风险偏好和盈利预期，不能替代债券、信贷或银行融资条件。",
            formula="最新KOSPI ÷ 60个交易观察前 − 1",
            source_codes=["KOSPI"],
            frequency="daily",
        ),
        _metric(
            key="krw_cny_change_60d",
            label="韩元兑人民币约3个月变化",
            value=fx_change,
            unit="%",
            period=fx["period"] if fx else None,
            reference_value=0 if fx_change is not None else None,
            reference_period=fx_previous["period"] if fx_previous else None,
            interpretation="每100韩元兑人民币的双边牌价，只作背景，不进入金融状态与下行打分。",
            formula="最新KRWCNY ÷ 60个观测前 − 1",
            source_codes=["KRWCNY"],
            frequency="daily",
        ),
    ]
    return {
        "key": "financial_conditions",
        "title": "外部缓冲与市场背景",
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "summary": (
            f"外汇储备约 {_fmt(reserves_level, suffix='十亿美元')}，约一年变化 "
            f"{_fmt(reserves_change, suffix='%')}；KOSPI约3个月回报 {_fmt(equity_return, suffix='%')}。"
            "缺国内货币量、国债收益率曲线、信用利差和银行贷款，因此不称完整金融条件指数。"
        ),
        "confidence": _confidence(
            sum((reserves_current, equity_current)), 6
        ),
        "metrics": metrics,
    }


def _downturn_breadth(
    *,
    region: str,
    growth: Mapping[str, Any],
    labour: Mapping[str, Any],
    series: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    checks: list[tuple[bool, str, str]] = []

    if growth.get("industrial_current") and growth.get("industrial_average") is not None:
        checks.append(
            (
                growth["industrial_average"] < 0,
                f"工业生产近3个月均值为 {_fmt(growth['industrial_average'], suffix='%')}",
                f"工业生产近3个月均值为 {_fmt(growth['industrial_average'], suffix='%')}",
            )
        )
    if (
        growth.get("cli_current")
        and growth.get("cli") is not None
        and growth.get("cli_delta") is not None
    ):
        active = growth["cli"] < 100 and growth["cli_delta"] < 0
        checks.append(
            (
                active,
                f"CLI低于100且近3个月下降 {_fmt(abs(growth['cli_delta']), suffix='点')}",
                f"CLI为 {_fmt(growth['cli'])}，近3个月变化 {_fmt(growth['cli_delta'], suffix='点')}",
            )
        )
    if labour.get("unemployment_current") and labour.get("unemployment_delta") is not None:
        checks.append(
            (
                labour["unemployment_delta"] >= 0.3 - 1e-9,
                f"失业率较3个月前上升 {_fmt(labour['unemployment_delta'], suffix='个百分点')}",
                f"失业率较3个月前变化 {_fmt(labour['unemployment_delta'], suffix='个百分点')}",
            )
        )
    if labour.get("employment_current") and labour.get("employment_change") is not None:
        checks.append(
            (
                labour["employment_change"] <= -0.3 + 1e-9,
                f"就业率较一年前下降 {_fmt(abs(labour['employment_change']), suffix='个百分点')}",
                f"就业率较一年前变化 {_fmt(labour['employment_change'], suffix='个百分点')}",
            )
        )
    # Korea lacks GDP in the current catalog.  Youth unemployment is a stated,
    # country-relevant fragmentation check there; Japan keeps the six-part
    # cross-country design with GDP explicitly unavailable instead.
    if (
        region == "KR"
        and labour.get("youth_current")
        and labour.get("youth_change") is not None
    ):
        checks.append(
            (
                labour["youth_change"] >= 1.0 - 1e-9,
                f"青年失业率较一年前上升 {_fmt(labour['youth_change'], suffix='个百分点')}",
                f"青年失业率较一年前变化 {_fmt(labour['youth_change'], suffix='个百分点')}",
            )
        )

    equity_code = "NKY" if region == "JP" else "KOSPI"
    equity = _latest(series, equity_code)
    equity_reference = _reference(series, equity_code, 60)
    equity_return = _percentage_change(equity, equity_reference)
    equity_current = (
        equity_return is not None
        and _freshness(equity["period"] if equity else None, "daily") == "current"
    )
    if equity_current:
        checks.append(
            (
                equity_return <= -10,
                f"股指约3个月下跌 {_fmt(abs(equity_return), suffix='%')}",
                f"股指约3个月回报 {_fmt(equity_return, suffix='%')}",
            )
        )

    total = len(checks)
    triggers = [trigger for active, trigger, _ in checks if active]
    offsets = [offset for active, _, offset in checks if not active]
    active_count = len(triggers)
    if total < 4:
        state_key, state_label, tone = "unavailable", "下行证据覆盖不足", "unavailable"
    elif active_count >= 3:
        state_key, state_label, tone = "broad", "下行证据广泛", "negative"
    elif active_count >= 2:
        state_key, state_label, tone = "elevated", "下行证据增加，尚未广泛化", "caution"
    else:
        state_key, state_label, tone = "limited", "下行证据有限", "positive"

    if region == "JP":
        methodology = (
            "六项设计包括GDP同比<0、工业生产近3个月均值<0、CLI低于100且继续下降、"
            "失业率3个月上升至少0.3个百分点、就业率同比下降至少0.3个百分点、"
            "日经225约3个月跌幅至少10%；GDP当前不可用，total仅统计其余可用信号。"
        )
    else:
        methodology = (
            "韩国当前六项可用检查为工业生产近3个月均值<0、CLI低于100且继续下降、"
            "失业率3个月上升至少0.3个百分点、就业率同比下降至少0.3个百分点、"
            "青年失业率同比上升至少1个百分点、KOSPI约3个月跌幅至少10%。"
        )
    return {
        "state_key": state_key,
        "state_label": state_label,
        "tone": tone,
        "active_signals": active_count,
        "total_signals": total,
        "triggers": triggers,
        "offsets": offsets,
        "summary": (
            f"当前可用的{total}项增长、就业与市场检查中有{active_count}项触发。"
            "这是证据广度，不是衰退概率。"
        ),
        "methodology": methodology,
    }


def _latest_period(
    series: Mapping[str, list[dict[str, Any]]], codes: tuple[str, ...]
) -> str | None:
    periods = [
        point["period"]
        for code in codes
        if (point := _latest(series, code)) is not None and point["period"] is not None
    ]
    return max(periods) if periods else None


def _build_overview(
    *,
    region: str,
    rows: Iterable[Any],
    employment: Mapping[str, Any] | None,
    employment_error: str | None = None,
    policy_verified_at: Any = None,
) -> dict[str, Any]:
    is_japan = region == "JP"
    codes = JP_OVERVIEW_CODES if is_japan else KR_OVERVIEW_CODES
    series = _series(rows, codes)
    growth, growth_diagnostics = _build_growth(series, prefix=region)
    labour, labour_diagnostics = _build_labour(employment, region=region)
    inflation = _build_inflation_jp(series) if is_japan else _build_inflation_kr(series)
    policy, policy_diagnostics = (
        _build_policy_jp(series)
        if is_japan
        else _build_policy_kr(series, policy_verified_at)
    )
    financial = _build_financial_jp(series) if is_japan else _build_financial_kr(series)
    pillars = [growth, labour, inflation, financial, policy]
    downturn = _downturn_breadth(
        region=region,
        growth=growth_diagnostics,
        labour=labour_diagnostics,
        series=series,
    )
    metrics = [metric for pillar in pillars for metric in pillar["metrics"]]
    missing_codes = [code for code in codes if not series.get(code)]
    stale_metrics = [metric["label"] for metric in metrics if metric["freshness"] == "stale"]

    warnings = [
        "本页使用当前修订后的最终快照；核心序列缺少完整发布日期、可用时间和历史版本，不能解读为伪实时回测或衰退概率。",
        "人民币双边汇率只作外部背景，不进入政策、金融状态或下行证据判断。",
    ]
    if is_japan:
        warnings.extend(
            [
                "日本GDP尚未接入；工业生产与CLI只能构成局部增长证据，不能替代完整总量判断。",
                "JP_BOJ是<24小时拆借/同业利率月均，并非日本银行最新会议目标利率或事件序列。",
                "OECD可比核心CPI剔除食品和能源，不等同日本国内常称的剔除生鲜食品核心口径。",
                "日本银行总资产不是M1/M2或银行信贷；金融模块仍缺JGB收益率、信用利差和贷款条件。",
                "15—64岁就业口径不含日本重要的65岁以上劳动供给；低失业也会受到人口结构影响。",
            ]
        )
    else:
        warnings.extend(
            [
                "韩国GDP与总体CPI尚未接入；工业、CLI和核心CPI不能分别代填总量增长与整体通胀。",
                "外汇储备是外部缓冲，不是国内货币量、韩国银行总资产或完整金融条件。",
            ]
        )
        rate = _latest(series, "KR_BOK")
        if policy_diagnostics.get("policy_stale"):
            warnings.append(
                f"韩国银行基准利率最近一次变动发生于{rate['period'] if rate else '未知日期'}，"
                "但最近抓取核验日期偏旧；已从覆盖率中排除，不能作为当前政策利率。"
            )
        elif rate:
            warnings.append(
                f"韩国银行基准利率是事件型变更历史：{rate['period']}是最近一次变动生效日，"
                f"不是数据过期日期；当前值已核验至"
                f"{policy_diagnostics.get('verified_period') or '未知日期'}。"
            )
    if employment:
        warnings.extend(
            str(item) for item in employment.get("warnings", []) if str(item) not in warnings
        )
    if employment_error:
        warnings.append("就业专题本次未在时限内完成；就业模块保留为缺失，失败不解释为中性或零。")
    if missing_codes:
        warnings.append(f"当前缺少已配置指标：{'、'.join(missing_codes)}。缺失值未按0处理。")
    policy_labels = {
        "韩国银行基准利率",
        "基准利率近6个月变化",
        "基准利率近12个月变化",
        "事后实际政策利率代理",
    }
    specially_reported_stale = (
        policy_labels if policy_diagnostics.get("policy_stale") else set()
    )
    other_stale_metrics = [
        label for label in stale_metrics if label not in specially_reported_stale
    ]
    if other_stale_metrics:
        warnings.append(
            f"当前数据偏旧：{'、'.join(dict.fromkeys(other_stale_metrics))}；覆盖率已排除这些读数。"
        )

    available_pillars = [pillar for pillar in pillars if pillar["confidence"] != "unavailable"]
    non_financial_metrics = [
        metric
        for pillar in pillars
        if pillar["key"] != "financial_conditions"
        for metric in pillar["metrics"]
    ]
    financial_core_keys = (
        {"boj_assets_yoy", "equity_return_60d"}
        if is_japan
        else {"foreign_reserves_yoy", "equity_return_60d"}
    )
    financial_core = [metric for metric in financial["metrics"] if metric["key"] in financial_core_keys]
    covered_evidence = sum(
        metric["value"] is not None and metric["freshness"] != "stale"
        for metric in non_financial_metrics + financial_core
    )
    expected_evidence = len(non_financial_metrics) + 6
    coverage = covered_evidence / expected_evidence if expected_evidence else 0.0
    if not available_pillars:
        status = "unavailable"
    elif (
        len(available_pillars) < len(pillars)
        or any(pillar["confidence"] == "low" for pillar in pillars)
        or employment_error
        or missing_codes
        or stale_metrics
        or covered_evidence < expected_evidence
    ):
        status = "partial"
    else:
        status = "ok"

    if downturn["state_key"] == "broad":
        tone = "negative"
    elif (
        downturn["tone"] == "caution"
        or inflation["tone"] == "caution"
        or policy["tone"] == "caution"
        or financial["tone"] == "caution"
        or labour["tone"] == "caution"
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
            "key": "policy",
            "title": "日本短端政策传导起点" if is_japan else "韩国银行政策起点",
            "state_label": policy["state_label"],
            "tone": policy["tone"],
            "detail": policy["summary"],
            "periods": [metric["period"] for metric in policy["metrics"] if metric["period"]],
        },
        {
            "key": "financial",
            "title": "央行资产与市场传导" if is_japan else "外部缓冲与市场传导",
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

    if is_japan:
        watch_items = [
            "补齐季度GDP、消费、工资与Tankan后，再判断局部生产改善能否扩散到总量需求。",
            "观察总体与核心CPI回升能否持续，同时区分OECD可比核心与日本官方除生鲜口径。",
            "关注短端利率继续正常化时，JGB收益率、银行贷款与日本银行资产负债表是否同步收紧。",
        ]
    else:
        watch_items = [
            "青年失业率能否回落，避免就业分化从青年群体扩散到总量就业率。",
            "核心CPI升温是否得到总体CPI与服务价格确认，而不是单一可比口径波动。",
            "跟踪韩国银行基准利率变化，并继续补齐国内货币量、国债收益率和银行贷款，完善融资链。",
            "KOSPI明显回撤能否企稳，以及是否向工业生产与就业扩散。",
        ]

    market_codes = ("NKY", "JPYCNY") if is_japan else ("KOSPI", "KRWCNY")
    monthly_codes = (
        ("JP_IP", "JP_CLI", "JP_CPI", "JP_CORE_CPI", "JP_BOJ", "JP_BOJ_ASSETS")
        if is_japan
        else ("KR_IP", "KR_CLI", "KR_CORE_CPI", "KR_RESERVES")
    )
    market_period = _latest_period(series, market_codes)
    monthly_period = _latest_period(series, monthly_codes)
    employment_period = (
        str(employment.get("latest_month"))
        if employment and employment.get("latest_month")
        else None
    )
    return {
        "region": region,
        "country": "日本" if is_japan else "韩国",
        "title": "日本宏观综合分析" if is_japan else "韩国宏观综合分析",
        "status": status,
        "confidence": _confidence(len(available_pillars), len(pillars)),
        "coverage": round(coverage, 4),
        "realtime_ready": False,
        "tone": tone,
        "headline": headline,
        "as_of": market_period or monthly_period,
        "freshness": {
            "market_observation_date": market_period,
            "monthly_observation_period": monthly_period,
            "quarterly_observation_period": None,
            "employment_observation_period": employment_period,
        },
        "methodology_version": METHODOLOGY_VERSION,
        "methodology_note": (
            "五个分项分别按公开、可复核规则判断，不合成总分；不同频率保留各自观察期，"
            "缺失与过期值不填0。下行证据广度只统计可用检查，不输出未经回测的衰退概率。"
        ),
        "pillars": pillars,
        "downturn_breadth": downturn,
        "transmission": transmission,
        "watch_items": watch_items,
        "data_source_path": "/data-sources/jp" if is_japan else "/data-sources/kr",
        "warnings": warnings,
    }


def _build_japan_macro_overview(
    rows: Iterable[Any],
    employment: Mapping[str, Any] | None = None,
    *,
    employment_error: str | None = None,
) -> dict[str, Any]:
    return _build_overview(
        region="JP",
        rows=rows,
        employment=employment,
        employment_error=employment_error,
    )


def _build_korea_macro_overview(
    rows: Iterable[Any],
    employment: Mapping[str, Any] | None = None,
    *,
    employment_error: str | None = None,
    policy_verified_at: Any = None,
) -> dict[str, Any]:
    return _build_overview(
        region="KR",
        rows=rows,
        employment=employment,
        employment_error=employment_error,
        policy_verified_at=policy_verified_at,
    )


def build_japan_macro_overview(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(DataPoint)
        .where(DataPoint.indicator_code.in_(JP_OVERVIEW_CODES))
        .order_by(DataPoint.indicator_code, DataPoint.date)
    ).scalars().all()
    employment, employment_error = _load_employment_enrichment("JP")
    return _build_japan_macro_overview(rows, employment, employment_error=employment_error)


def build_korea_macro_overview(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(DataPoint)
        .where(DataPoint.indicator_code.in_(KR_OVERVIEW_CODES))
        .order_by(DataPoint.indicator_code, DataPoint.date)
    ).scalars().all()
    policy_refresh = db.scalar(
        select(RefreshResult)
        .where(
            RefreshResult.indicator_code == "KR_BOK",
            RefreshResult.status.in_(("success", "no_change")),
        )
        .order_by(RefreshResult.finished_at.desc(), RefreshResult.id.desc())
        .limit(1)
    )
    employment, employment_error = _load_employment_enrichment("KR")
    return _build_korea_macro_overview(
        rows,
        employment,
        employment_error=employment_error,
        policy_verified_at=(
            policy_refresh.source_verified_through if policy_refresh else None
        ),
    )

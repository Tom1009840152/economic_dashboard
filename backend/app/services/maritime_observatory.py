"""High-frequency maritime observatory built from IMF PortWatch.

This is intentionally an evidence panel, not a single proprietary score.  It
keeps physical quantity, chokepoint transit and price evidence separate.  The
first release covers quantity and a pilot chokepoint deviation layer; freight
prices are explicitly left unimplemented until a defensible licensed source is
available.
"""

from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from datetime import date, timedelta
from typing import Any, Iterable

import pandas as pd

from app.fetchers.portwatch_source import (
    PORTWATCH_SOURCE_URL,
    fetch_portwatch_comparison_panel,
    fetch_portwatch_trade_panel,
    portwatch_get,
)


METHODOLOGY_VERSION = "maritime-observatory-1.1.0"
WINDOW_DAYS = 28
COMPARISON_DAYS = 364
MIN_WINDOW_COVERAGE = 0.85
_DASHBOARD_CACHE_TTL_SECONDS = 15 * 60
_DASHBOARD_FAILURE_CACHE_TTL_SECONDS = 60

PORTWATCH_CHOKEPOINT_LAYER_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0"
)
PORTWATCH_CHOKEPOINT_QUERY_URL = f"{PORTWATCH_CHOKEPOINT_LAYER_URL}/query"

_VESSEL_TYPES = (
    ("container", "集装箱船"),
    ("dry_bulk", "干散货船"),
    ("tanker", "油轮"),
    ("general_cargo", "杂货船"),
    ("roro", "滚装船"),
)
_KEY_CHOKEPOINTS = (
    ("chokepoint1", "苏伊士运河"),
    ("chokepoint2", "巴拿马运河"),
    ("chokepoint5", "马六甲海峡"),
    ("chokepoint6", "霍尔木兹海峡"),
    ("chokepoint4", "曼德海峡"),
    ("chokepoint7", "好望角"),
)
GEOGRAPHY_OPTIONS = (
    ("110", "发达经济体", "region"),
    ("119", "七国集团（G7）", "region"),
    ("163", "欧元区", "region"),
    ("998", "欧盟", "region"),
    ("200", "新兴市场与发展中经济体", "region"),
    ("205", "拉丁美洲与加勒比", "region"),
    ("400", "中东与中亚", "region"),
    ("505", "新兴与发展中亚洲", "region"),
    ("510", "东盟五国", "region"),
    ("513", "东盟十国", "region"),
    ("603", "撒哈拉以南非洲", "region"),
    ("903", "新兴与发展中欧洲", "region"),
    ("CHN", "中国", "country"),
    ("USA", "美国", "country"),
    ("JPN", "日本", "country"),
    ("KOR", "韩国", "country"),
    ("GBR", "英国", "country"),
    ("DEU", "德国", "country"),
    ("FRA", "法国", "country"),
    ("IND", "印度", "country"),
    ("BRA", "巴西", "country"),
    ("CAN", "加拿大", "country"),
    ("AUS", "澳大利亚", "country"),
    ("MEX", "墨西哥", "country"),
    ("VNM", "越南", "country"),
)
_GEOGRAPHY_META = {
    code: {"code": code, "name": name, "kind": kind}
    for code, name, kind in GEOGRAPHY_OPTIONS
}
_COUNTRY_NAMES = {code: item["name"] for code, item in _GEOGRAPHY_META.items()}
_dashboard_cache_lock = threading.Lock()
_dashboard_caches: dict[str, tuple[float, dict[str, Any]]] = {}


def normalize_country_code(country_code: str) -> str:
    code = country_code.strip().upper()
    if code not in _COUNTRY_NAMES:
        raise ValueError(f"unsupported PortWatch country: {country_code}")
    return code


def _country_options() -> list[dict[str, str]]:
    return [dict(item) for item in _GEOGRAPHY_META.values()]


def _source_rows() -> list[dict[str, str]]:
    return [
        {
            "name": "IMF PortWatch / UN Global Platform",
            "url": PORTWATCH_SOURCE_URL,
            "description": (
                "基于AIS、港口边界与船舶吃水估算的日度靠港、进口、出口和咽喉水道通行数据；"
                "本页只返回聚合后的派生观察值。"
            ),
            "update_frequency": "日度；近期通常存在约一周处理时滞",
            "access_level": "public_limited",
        }
    ]


def _arcgis_json(response: Any) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        details = payload["error"].get("details") or []
        message = payload["error"].get("message") or "ArcGIS query failed"
        raise RuntimeError(f"{message}: {'; '.join(map(str, details))}".rstrip(": "))
    return payload


def _chokepoint_window(
    start: date,
    end: date,
    *,
    request_get=None,
) -> dict[str, dict[str, Any]]:
    get = request_get or portwatch_get
    statistics = [
        {
            "statisticType": "sum",
            "onStatisticField": "n_total",
            "outStatisticFieldName": "calls_sum",
        },
        {
            "statisticType": "sum",
            "onStatisticField": "capacity",
            "outStatisticFieldName": "capacity_sum",
        },
        {
            "statisticType": "count",
            "onStatisticField": "date",
            "outStatisticFieldName": "days_count",
        },
    ]
    response = get(
        PORTWATCH_CHOKEPOINT_QUERY_URL,
        params={
            "f": "json",
            "where": (
                f"date >= DATE '{start.isoformat()}' AND "
                f"date <= DATE '{end.isoformat()}'"
            ),
            "outStatistics": json.dumps(statistics, separators=(",", ":")),
            "groupByFieldsForStatistics": "portid,portname",
            "orderByFields": "portid",
            "returnGeometry": "false",
            "resultRecordCount": 100,
        },
        timeout=20,
    )
    payload = _arcgis_json(response)
    result: dict[str, dict[str, Any]] = {}
    for feature in payload.get("features") or []:
        attributes = feature.get("attributes") or {}
        port_id = str(attributes.get("portid") or "").strip()
        if port_id:
            result[port_id] = attributes
    return result


def _download_chokepoint_comparison(
    end: date,
    *,
    request_get=None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    current_start = end - timedelta(days=WINDOW_DAYS - 1)
    previous_end = end - timedelta(days=COMPARISON_DAYS)
    previous_start = previous_end - timedelta(days=WINDOW_DAYS - 1)
    current = _chokepoint_window(current_start, end, request_get=request_get)
    previous = _chokepoint_window(previous_start, previous_end, request_get=request_get)
    return current, previous


def _daily_series(frame: pd.DataFrame, fields: Iterable[str]) -> pd.Series:
    fields = tuple(fields)
    if frame.empty:
        return pd.Series(dtype="float64")
    values = frame.loc[:, fields].apply(pd.to_numeric, errors="coerce").sum(
        axis=1,
        min_count=len(fields),
    )
    index = pd.DatetimeIndex(pd.to_datetime(frame["date"]))
    series = pd.Series(values.to_numpy(dtype=float), index=index).sort_index()
    if series.index.has_duplicates:
        raise ValueError("PortWatch panel contains duplicate dates")
    full_index = pd.date_range(series.index.min(), series.index.max(), freq="D")
    return series.reindex(full_index)


def _rolling(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    minimum = max(1, int(WINDOW_DAYS * MIN_WINDOW_COVERAGE + 0.999))
    average = series.rolling(WINDOW_DAYS, min_periods=minimum).mean()
    count = series.notna().astype(int).rolling(WINDOW_DAYS, min_periods=1).sum()
    return average, count


def _number(value: Any, digits: int = 3) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _optional_float(value: Any) -> float | None:
    """Coerce an ArcGIS aggregate without turning missing data into zero."""

    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current: Any, previous: Any) -> float | None:
    if current is None or previous is None or pd.isna(current) or pd.isna(previous):
        return None
    previous = float(previous)
    if previous == 0:
        return None
    return round((float(current) / previous - 1.0) * 100.0, 2)


def _state(yoy: float | None) -> str:
    if yoy is None:
        return "unavailable"
    if yoy >= 3.0:
        return "stronger"
    if yoy <= -3.0:
        return "weaker"
    return "steady"


def _metric(
    *,
    key: str,
    label: str,
    series: pd.Series,
    end: pd.Timestamp,
    unit: str,
    scale: float,
    interpretation: str,
) -> dict[str, Any]:
    average, count = _rolling(series)
    previous_end = end - pd.Timedelta(days=COMPARISON_DAYS)
    current = average.get(end)
    previous = average.get(previous_end)
    current_count = count.get(end, 0)
    previous_count = count.get(previous_end, 0)
    coverage = min(float(current_count), float(previous_count)) / WINDOW_DAYS
    yoy = _pct_change(current, previous)
    return {
        "key": key,
        "label": label,
        "value": _number(current / scale if current is not None and not pd.isna(current) else None),
        "unit": unit,
        "comparison_value": _number(
            previous / scale if previous is not None and not pd.isna(previous) else None
        ),
        "yoy_pct": yoy,
        "state": _state(yoy),
        "coverage": round(min(max(coverage, 0.0), 1.0), 4),
        "interpretation": interpretation,
    }


def _trend_point(
    timestamp: pd.Timestamp,
    averages: dict[str, pd.Series],
) -> dict[str, Any]:
    previous = timestamp - pd.Timedelta(days=COMPARISON_DAYS)
    return {
        "date": timestamp.date().isoformat(),
        "world_flow_yoy": _pct_change(
            averages["world"].get(timestamp), averages["world"].get(previous)
        ),
        "country_exports_yoy": _pct_change(
            averages["exports"].get(timestamp), averages["exports"].get(previous)
        ),
        "country_inputs_yoy": _pct_change(
            averages["inputs"].get(timestamp), averages["inputs"].get(previous)
        ),
    }


def _chokepoints(
    current: dict[str, dict[str, Any]] | None,
    previous: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not current or not previous:
        return []
    result: list[dict[str, Any]] = []
    for key, label in _KEY_CHOKEPOINTS:
        now = current.get(key)
        before = previous.get(key)
        if not now or not before:
            result.append(
                {
                    "key": key,
                    "name": label,
                    "current_daily_calls": None,
                    "current_daily_capacity_mn_t": None,
                    "yoy_pct": None,
                    "coverage": 0.0,
                    "state": "unavailable",
                }
            )
            continue
        current_days = _optional_float(now.get("days_count"))
        previous_days = _optional_float(before.get("days_count"))
        minimum_days = max(1, int(WINDOW_DAYS * MIN_WINDOW_COVERAGE + 0.999))
        current_complete = current_days is not None and current_days >= minimum_days
        previous_complete = previous_days is not None and previous_days >= minimum_days
        coverage = (
            min(current_days, previous_days) / WINDOW_DAYS
            if current_days is not None and previous_days is not None
            else 0.0
        )
        calls = _optional_float(now.get("calls_sum"))
        capacity = _optional_float(now.get("capacity_sum"))
        previous_capacity = _optional_float(before.get("capacity_sum"))
        yoy = (
            _pct_change(capacity / current_days, previous_capacity / previous_days)
            if current_complete
            and previous_complete
            and capacity is not None
            and previous_capacity is not None
            and current_days
            and previous_days
            else None
        )
        state = "unavailable"
        if yoy is not None:
            state = "above" if yoy >= 15 else "below" if yoy <= -15 else "normal"
        result.append(
            {
                "key": key,
                "name": label,
                "current_daily_calls": _number(
                    calls / current_days
                    if calls is not None and current_complete and current_days
                    else None,
                    1,
                ),
                "current_daily_capacity_mn_t": _number(
                    capacity / current_days / 1_000_000
                    if capacity is not None and current_complete and current_days
                    else None,
                    3,
                ),
                "yoy_pct": yoy,
                "coverage": round(min(max(coverage, 0.0), 1.0), 4),
                "state": state,
            }
        )
    return result


def _freshness(as_of: date, today: date) -> str:
    lag = (today - as_of).days
    if lag < 0:
        return "stale"
    if lag <= 10:
        return "current"
    if lag <= 21:
        return "stale"
    return "stale"


def _format_yoy(value: float | None) -> str:
    if value is None:
        return "暂无可比值"
    return f"{value:+.1f}%"


def _unavailable(
    error: BaseException | str,
    *,
    country_code: str = "CHN",
) -> dict[str, Any]:
    country_code = normalize_country_code(country_code)
    return {
        "title": "全球航运与贸易高频观测",
        "status": "unavailable",
        "as_of": None,
        "source_history_start": None,
        "trend_start": None,
        "freshness": "missing",
        "window_days": WINDOW_DAYS,
        "comparison_days": COMPARISON_DAYS,
        "methodology_version": METHODOLOGY_VERSION,
        "realtime_ready": False,
        "selected_country_code": country_code,
        "selected_country_name": _COUNTRY_NAMES[country_code],
        "available_countries": _country_options(),
        "headline": "PortWatch 当前不可用；页面不会用旧值、零值或运价替代实物量。",
        "metrics": [],
        "trend": [],
        "vessel_mix": [],
        "chokepoints": [],
        "layers": [
            {"key": "quantity", "title": "实物数量", "status": "pilot", "detail": "等待PortWatch恢复。"},
            {"key": "congestion", "title": "拥堵与绕行", "status": "pilot", "detail": "等待PortWatch恢复。"},
            {"key": "price", "title": "运价", "status": "planned", "detail": "尚未接入，不与数量信号混合。"},
        ],
        "sources": _source_rows(),
        "warnings": [f"PortWatch读取失败：{error}"],
    }


def _build_maritime_observatory(
    panel: pd.DataFrame,
    *,
    country_code: str = "CHN",
    country_name: str | None = None,
    chokepoint_current: dict[str, dict[str, Any]] | None = None,
    chokepoint_previous: dict[str, dict[str, Any]] | None = None,
    today: date | None = None,
    chokepoint_error: str | None = None,
) -> dict[str, Any]:
    """Pure dashboard builder used by the live endpoint and deterministic tests."""

    country_code = normalize_country_code(country_code)
    country_name = country_name or _COUNTRY_NAMES[country_code]
    world = panel.loc[panel["ISO3"] == "WLD"].copy()
    country = panel.loc[panel["ISO3"] == country_code].copy()
    if world.empty or country.empty:
        raise ValueError(f"PortWatch panel must include WLD and {country_code}")

    world_latest = max(pd.to_datetime(world["date"]))
    country_latest = max(pd.to_datetime(country["date"]))
    end = min(world_latest, country_latest).normalize()
    source_start = max(
        min(pd.to_datetime(world["date"])),
        min(pd.to_datetime(country["date"])),
    ).normalize()
    world = world.loc[pd.to_datetime(world["date"]) <= end]
    country = country.loc[pd.to_datetime(country["date"]) <= end]

    world_container = _daily_series(world, ("import_container", "export_container"))
    world_calls = _daily_series(world, ("portcalls_container",))
    country_container_exports = _daily_series(country, ("export_container",))
    country_all_exports = _daily_series(country, ("export",))
    country_inputs = _daily_series(country, ("import_dry_bulk", "import_tanker"))

    metrics = [
        _metric(
            key="world_container_flow",
            label="全球集装箱装卸脉冲",
            series=world_container,
            end=end,
            unit="百万吨/日",
            scale=1_000_000,
            interpretation="全球港口集装箱进口与出口估算吨位之和的28日均值；不是全部海运货量。",
        ),
        _metric(
            key="world_container_calls",
            label="全球集装箱船靠港",
            series=world_calls,
            end=end,
            unit="艘次/日",
            scale=1,
            interpretation="全球集装箱船靠港次数的28日均值，用于和吨位信号交叉核验。",
        ),
        _metric(
            key="country_container_exports",
            label=f"{country_name}集装箱出口装船",
            series=country_container_exports,
            end=end,
            unit="百万吨/日",
            scale=1_000_000,
            interpretation=f"{country_name}港口集装箱出口估算吨位；是装船代理，不是海关出口额。",
        ),
        _metric(
            key="country_all_exports",
            label=f"{country_name}全船型出口装船",
            series=country_all_exports,
            end=end,
            unit="百万吨/日",
            scale=1_000_000,
            interpretation="集装箱、干散货、杂货、滚装和油轮出口估算吨位合计的28日均值。",
        ),
        _metric(
            key="country_bulk_energy_inputs",
            label=f"{country_name}大宗投入到港代理",
            series=country_inputs,
            end=end,
            unit="百万吨/日",
            scale=1_000_000,
            interpretation=f"{country_name}干散货与油轮进口估算吨位之和；无法由AIS识别具体商品。",
        ),
    ]
    quantity_available = any(metric["value"] is not None for metric in metrics)
    quantity_comparable = any(metric["yoy_pct"] is not None for metric in metrics)

    averages = {
        "world": _rolling(world_container)[0],
        "exports": _rolling(country_all_exports)[0],
        "inputs": _rolling(country_inputs)[0],
    }
    first_valid = [series.first_valid_index() for series in averages.values()]
    comparable_starts = [timestamp for timestamp in first_valid if timestamp is not None]
    trend_start = (
        max(comparable_starts) + pd.Timedelta(days=COMPARISON_DAYS)
        if comparable_starts
        else end + pd.Timedelta(days=1)
    )
    trend = [
        _trend_point(timestamp, averages)
        for timestamp in pd.date_range(trend_start, end, freq="D")
    ]

    vessel_values: list[tuple[str, str, float | None, float | None]] = []
    for key, label in _VESSEL_TYPES:
        series = _daily_series(world, (f"import_{key}", f"export_{key}"))
        average, _ = _rolling(series)
        current = average.get(end)
        previous = average.get(end - pd.Timedelta(days=COMPARISON_DAYS))
        vessel_values.append(
            (
                key,
                label,
                None if current is None or pd.isna(current) else float(current),
                _pct_change(current, previous),
            )
        )
    vessel_mix_complete = all(value is not None for _, _, value, _ in vessel_values)
    vessel_total = (
        sum(value for _, _, value, _ in vessel_values if value is not None)
        if vessel_mix_complete
        else None
    )
    vessel_mix = [
        {
            "key": key,
            "label": label,
            "current_daily_mn_t": _number(value / 1_000_000 if value is not None else None),
            "yoy_pct": yoy,
            "share_pct": (
                _number(value / vessel_total * 100, 1)
                if value is not None and vessel_total
                else None
            ),
        }
        for key, label, value, yoy in vessel_values
    ]

    choke_rows = _chokepoints(chokepoint_current, chokepoint_previous)
    chokepoints_available = any(
        row["current_daily_calls"] is not None
        or row["current_daily_capacity_mn_t"] is not None
        for row in choke_rows
    )
    chokepoints_comparable = any(row["state"] != "unavailable" for row in choke_rows)
    freshness = _freshness(end.date(), today or date.today())
    world_yoy = next(metric["yoy_pct"] for metric in metrics if metric["key"] == "world_container_flow")
    exports_yoy = next(metric["yoy_pct"] for metric in metrics if metric["key"] == "country_all_exports")
    inputs_yoy = next(metric["yoy_pct"] for metric in metrics if metric["key"] == "country_bulk_energy_inputs")

    warnings = [
        "PortWatch为实验性AIS估算：船舶吃水、AIS接收、港口边界、转运和压载航行都会带来误差；不替代海关统计。",
        "全球集装箱装卸量按进口与出口港口处理量相加，因此同一国际航次可能在两端各出现一次；只比较其自身历史变化。",
        f"{country_name}大宗投入到港代理仅合计干散货与油轮，无法在无提单数据时区分铁矿石、煤炭、粮食、原油或成品油。",
        "历史逐日发布时间不可得；虽然项目会从接入日起保存抓取vintage，当前仍不能宣称完成伪实时回测。",
    ]
    if not quantity_available:
        warnings.append("数量层最新窗口没有达到最低覆盖要求；页面保留缺失状态，不将空值补成零。")
    elif not quantity_comparable:
        warnings.append("数量层同期窗口覆盖不足，当前值仅作观察，不给出同比方向判断。")
    if not vessel_mix_complete:
        warnings.append("船型结构存在缺项，所有占比暂不归一化，避免把剩余船型误显示为完整的100%。")
    if chokepoint_error:
        warnings.append(f"咽喉水道层本次读取失败：{chokepoint_error}")
    elif chokepoints_available:
        warnings.append("咽喉水道展示的是船舶名义运力通过量偏离，不是实际载货量，也不等同港口拥堵。")
    else:
        warnings.append("咽喉水道本次没有可用聚合值；页面保留缺失状态，不将空值按零通行处理。")

    return {
        "title": "全球航运与贸易高频观测",
        "status": (
            "ok"
            if quantity_comparable and chokepoints_comparable and freshness == "current"
            else "partial"
        ),
        "as_of": end.date().isoformat(),
        "source_history_start": source_start.date().isoformat(),
        "trend_start": trend[0]["date"] if trend else None,
        "freshness": freshness,
        "window_days": WINDOW_DAYS,
        "comparison_days": COMPARISON_DAYS,
        "methodology_version": METHODOLOGY_VERSION,
        "realtime_ready": False,
        "selected_country_code": country_code,
        "selected_country_name": country_name,
        "available_countries": _country_options(),
        "headline": (
            f"全球集装箱装卸脉冲同比 {_format_yoy(world_yoy)}；"
            f"{country_name}全船型出口装船 {_format_yoy(exports_yoy)}；"
            f"{country_name}大宗投入到港代理 {_format_yoy(inputs_yoy)}。"
        ),
        "metrics": metrics,
        "trend": trend,
        "vessel_mix": vessel_mix,
        "chokepoints": choke_rows,
        "layers": [
            {
                "key": "quantity",
                "title": "实物数量",
                "status": "available" if quantity_comparable else "pilot",
                "detail": (
                    "已接入集装箱、全船型出口及干散货/油轮到港代理，统一采用28日均值和364日同期比较。"
                    if quantity_comparable
                    else "已接入数量源，但当前或同期窗口覆盖不足，暂不输出可比较方向。"
                ),
            },
            {
                "key": "congestion",
                "title": "拥堵与绕行",
                "status": "pilot" if chokepoints_available else "planned",
                "detail": "当前只观察主要咽喉名义运力通行偏离；港外等待时长和锚地船数尚未接入。",
            },
            {
                "key": "price",
                "title": "运价",
                "status": "planned",
                "detail": "尚未接入可合法持续使用的运价源；价格不会被混入实物量脉冲。",
            },
        ],
        "sources": _source_rows(),
        "warnings": warnings,
    }


def _build_maritime_comparison(
    panel: pd.DataFrame,
    geography_codes: Iterable[str],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Build a global-fixed, multi-geography daily comparison dashboard."""

    codes = tuple(dict.fromkeys(normalize_country_code(code) for code in geography_codes))
    if not codes:
        raise ValueError("at least one geography is required")
    if len(codes) > 5:
        raise ValueError("maritime comparison supports at most five geographies")

    required = ("WLD", *codes)
    frames = {code: panel.loc[panel["ISO3"] == code].copy() for code in required}
    missing = [code for code, frame in frames.items() if frame.empty]
    if missing:
        raise ValueError(f"PortWatch comparison panel missing: {missing}")

    end = min(max(pd.to_datetime(frame["date"])) for frame in frames.values()).normalize()
    source_start = max(min(pd.to_datetime(frame["date"])) for frame in frames.values()).normalize()
    frames = {
        code: frame.loc[pd.to_datetime(frame["date"]) <= end]
        for code, frame in frames.items()
    }

    world_container = _rolling(
        _daily_series(frames["WLD"], ("import_container", "export_container"))
    )[0]
    geography_averages: dict[str, dict[str, pd.Series]] = {}
    for code in codes:
        frame = frames[code]
        geography_averages[code] = {
            "container": _rolling(
                _daily_series(frame, ("import_container", "export_container"))
            )[0],
            "exports": _rolling(_daily_series(frame, ("export",)))[0],
            "inputs": _rolling(
                _daily_series(frame, ("import_dry_bulk", "import_tanker"))
            )[0],
        }

    averages = [world_container]
    averages.extend(
        series
        for code in codes
        for series in geography_averages[code].values()
    )
    first_valid = [series.first_valid_index() for series in averages]
    comparable_starts = [timestamp for timestamp in first_valid if timestamp is not None]
    trend_start = (
        max(comparable_starts) + pd.Timedelta(days=COMPARISON_DAYS)
        if comparable_starts
        else end + pd.Timedelta(days=1)
    )
    timestamps = pd.date_range(trend_start, end, freq="D")

    global_trend = [
        {
            "date": timestamp.date().isoformat(),
            "container_yoy": _pct_change(
                world_container.get(timestamp),
                world_container.get(timestamp - pd.Timedelta(days=COMPARISON_DAYS)),
            ),
        }
        for timestamp in timestamps
    ]
    series_rows: list[dict[str, Any]] = []
    for code in codes:
        series = geography_averages[code]
        points = []
        for timestamp in timestamps:
            previous = timestamp - pd.Timedelta(days=COMPARISON_DAYS)
            points.append(
                {
                    "date": timestamp.date().isoformat(),
                    "container_yoy": _pct_change(
                        series["container"].get(timestamp),
                        series["container"].get(previous),
                    ),
                    "exports_yoy": _pct_change(
                        series["exports"].get(timestamp),
                        series["exports"].get(previous),
                    ),
                    "inputs_yoy": _pct_change(
                        series["inputs"].get(timestamp),
                        series["inputs"].get(previous),
                    ),
                }
            )
        latest = points[-1] if points else {}
        series_rows.append(
            {
                **_GEOGRAPHY_META[code],
                "latest_container_yoy": latest.get("container_yoy"),
                "latest_exports_yoy": latest.get("exports_yoy"),
                "latest_inputs_yoy": latest.get("inputs_yoy"),
                "points": points,
            }
        )

    freshness = _freshness(end.date(), today or date.today())
    return {
        "status": "ok" if freshness == "current" else "partial",
        "as_of": end.date().isoformat(),
        "source_history_start": source_start.date().isoformat(),
        "trend_start": timestamps[0].date().isoformat() if len(timestamps) else None,
        "freshness": freshness,
        "window_days": WINDOW_DAYS,
        "comparison_days": COMPARISON_DAYS,
        "methodology_version": METHODOLOGY_VERSION,
        "global_trend": global_trend,
        "series": series_rows,
        "warnings": [
            "全球基准固定为WLD聚合；所选国家和地区使用PortWatch官方REG聚合，不由本项目自行拼接。",
            "所有曲线均为28日均值相对364天前同长度窗口的变化；不替代海关贸易统计。",
        ],
    }


def build_maritime_comparison(geography_codes: Iterable[str]) -> dict[str, Any]:
    codes = tuple(dict.fromkeys(normalize_country_code(code) for code in geography_codes))
    if not codes:
        raise ValueError("at least one geography is required")
    if len(codes) > 5:
        raise ValueError("maritime comparison supports at most five geographies")
    panel = fetch_portwatch_comparison_panel(codes)
    return _build_maritime_comparison(panel, codes)


def _load_maritime_observatory(country_code: str = "CHN") -> dict[str, Any]:
    """Load current sources, degrading the optional chokepoint layer safely."""

    country_code = normalize_country_code(country_code)
    try:
        panel = fetch_portwatch_trade_panel(country_code=country_code)
    except Exception as exc:  # the API returns an explicit unavailable state
        return _unavailable(exc, country_code=country_code)

    trade_dates = pd.to_datetime(panel["date"])
    world_latest = trade_dates[panel["ISO3"] == "WLD"].max()
    country_latest = trade_dates[panel["ISO3"] == country_code].max()
    aligned_end = min(world_latest, country_latest).date()
    try:
        current, previous = _download_chokepoint_comparison(aligned_end)
        return _build_maritime_observatory(
            panel,
            country_code=country_code,
            chokepoint_current=current,
            chokepoint_previous=previous,
        )
    except Exception as exc:
        return _build_maritime_observatory(
            panel,
            country_code=country_code,
            chokepoint_error=str(exc),
        )


def clear_maritime_observatory_cache() -> None:
    """Clear the short in-process response cache for tests and operations."""

    with _dashboard_cache_lock:
        _dashboard_caches.clear()


def build_maritime_observatory(country_code: str = "CHN") -> dict[str, Any]:
    """Return a country-keyed cached dashboard and avoid repeated queries."""

    country_code = normalize_country_code(country_code)
    now = time.monotonic()
    with _dashboard_cache_lock:
        cached = _dashboard_caches.get(country_code)
        cached_payload = cached[1] if cached is not None else None
        cache_ttl = (
            _DASHBOARD_FAILURE_CACHE_TTL_SECONDS
            if cached_payload is not None
            and cached_payload.get("status") == "unavailable"
            else _DASHBOARD_CACHE_TTL_SECONDS
        )
        if cached is not None and now - cached[0] < cache_ttl:
            return deepcopy(cached_payload)

        fresh = _load_maritime_observatory(country_code)
        if (
            fresh["status"] == "unavailable"
            and cached_payload is not None
            and cached_payload.get("status") != "unavailable"
        ):
            stale = deepcopy(cached_payload)
            stale["status"] = "partial"
            stale["freshness"] = "stale"
            fallback_warnings = [
                *stale.get("warnings", []),
                "本次上游读取失败，当前仅回退到本进程上一份成功快照；服务重启后仍需持久化最后成功快照。",
                *fresh.get("warnings", []),
            ]
            stale["warnings"] = list(dict.fromkeys(fallback_warnings))
            _dashboard_caches[country_code] = (time.monotonic(), deepcopy(stale))
            return stale

        _dashboard_caches[country_code] = (time.monotonic(), deepcopy(fresh))
        return fresh

"""Euro-area labour-market dashboard from the Eurostat dissemination API."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any

import requests


EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
_CACHE_TTL = 6 * 60 * 60
_REQUEST_ATTEMPTS = 3
_cache: tuple[float, dict[str, Any]] | None = None

SERIES_META = {
    "unemployment": (
        "une_rt_m",
        {"geo": "EA21", "s_adj": "SA", "sex": "T", "age": "TOTAL", "unit": "PC_ACT", "sinceTimePeriod": "2000-01"},
        "失业率",
        "%",
        "月度",
        "EU-LFS",
        "15—74岁劳动力；季调",
    ),
    "youth_unemployment": (
        "une_rt_m",
        {"geo": "EA21", "s_adj": "SA", "sex": "T", "age": "Y_LT25", "unit": "PC_ACT", "sinceTimePeriod": "2000-01"},
        "青年失业率",
        "%",
        "月度",
        "EU-LFS",
        "15—24岁劳动力；季调",
    ),
    "labor_participation": (
        "lfsi_emp_q",
        {"geo": "EA21", "indic_em": "ACT", "s_adj": "SA", "sex": "T", "age": "Y20-64", "unit": "PC_POP", "sinceTimePeriod": "2000-Q1"},
        "劳动参与率",
        "%",
        "季度",
        "EU-LFS",
        "20—64岁人口；季调",
    ),
    "employment_ratio": (
        "lfsi_emp_q",
        {"geo": "EA21", "indic_em": "EMP_LFS", "s_adj": "SA", "sex": "T", "age": "Y20-64", "unit": "PC_POP", "sinceTimePeriod": "2000-Q1"},
        "就业率",
        "%",
        "季度",
        "EU-LFS",
        "20—64岁人口；季调",
    ),
    "female_employment": (
        "lfsi_emp_q",
        {"geo": "EA21", "indic_em": "EMP_LFS", "s_adj": "SA", "sex": "F", "age": "Y20-64", "unit": "PC_POP", "sinceTimePeriod": "2000-Q1"},
        "女性就业率",
        "%",
        "季度",
        "EU-LFS",
        "20—64岁女性；季调",
    ),
    "male_employment": (
        "lfsi_emp_q",
        {"geo": "EA21", "indic_em": "EMP_LFS", "s_adj": "SA", "sex": "M", "age": "Y20-64", "unit": "PC_POP", "sinceTimePeriod": "2000-Q1"},
        "男性就业率",
        "%",
        "季度",
        "EU-LFS",
        "20—64岁男性；季调",
    ),
    "labour_slack": (
        "lfsi_sla_q",
        {"geo": "EA21", "wstatus": "SLACK", "s_adj": "SA", "sex": "T", "age": "Y20-64", "unit": "PC_ELF", "sinceTimePeriod": "2000-Q1"},
        "劳动力市场闲置率",
        "%",
        "季度",
        "EU-LFS扩展劳动力",
        "失业者及其他未满足就业需求人群；20—64岁；季调",
    ),
}


def _fetch_eurostat(dataset: str, params: dict[str, str]) -> list[dict[str, Any]]:
    last_error: requests.RequestException | None = None
    for attempt in range(_REQUEST_ATTEMPTS):
        try:
            response = requests.get(
                f"{EUROSTAT_API}/{dataset}",
                params={"lang": "en", **params},
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < _REQUEST_ATTEMPTS:
                time.sleep(0.35 * (attempt + 1))
    else:
        assert last_error is not None
        raise last_error
    time_index = (
        payload.get("dimension", {})
        .get("time", {})
        .get("category", {})
        .get("index", {})
    )
    values = payload.get("value", {})
    if not time_index:
        raise ValueError(f"Eurostat returned no time dimension for {dataset}")
    points = [
        {"period": period, "value": float(values[str(position)])}
        for period, position in sorted(time_index.items(), key=lambda item: item[1])
        if str(position) in values
    ]
    if not points:
        raise ValueError(f"Eurostat returned no observations for {dataset}")
    return points


def _map(points: list[dict[str, Any]]) -> dict[str, float]:
    return {point["period"]: point["value"] for point in points}


def fetch_eu_employment_dashboard() -> dict[str, Any]:
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]

    with ThreadPoolExecutor(max_workers=len(SERIES_META)) as executor:
        futures = {
            key: executor.submit(_fetch_eurostat, meta[0], meta[1])
            for key, meta in SERIES_META.items()
        }
        point_sets: dict[str, list[dict[str, Any]]] = {}
        failures: dict[str, str] = {}
        for key, future in futures.items():
            try:
                point_sets[key] = future.result()
            except (ValueError, requests.RequestException) as exc:
                failures[key] = type(exc).__name__

    if not point_sets:
        raise ValueError("Eurostat returned no usable employment series")

    series = [
        {
            "key": key,
            "name": name,
            "unit": unit,
            "frequency": frequency,
            "source_type": source_type,
            "scope": scope,
            "points": point_sets[key],
        }
        for key, (_, _, name, unit, frequency, source_type, scope) in SERIES_META.items()
        if key in point_sets
    ]

    if "female_employment" in point_sets and "male_employment" in point_sets:
        female = _map(point_sets["female_employment"])
        male = _map(point_sets["male_employment"])
        gap_periods = sorted(set(female) & set(male))
        series.append(
            {
                "key": "gender_employment_gap",
                "name": "男女就业率差",
                "unit": "百分点",
                "frequency": "季度",
                "source_type": "派生指标",
                "scope": "男性减女性；20—64岁",
                "points": [
                    {"period": period, "value": male[period] - female[period]}
                    for period in gap_periods
                ],
            }
        )

    if "labor_participation" in point_sets and "employment_ratio" in point_sets:
        participation = _map(point_sets["labor_participation"])
        employment = _map(point_sets["employment_ratio"])
        implied_periods = sorted(set(participation) & set(employment))
        series.append(
            {
                "key": "implied_unemployment",
                "name": "20—64岁恒等式隐含失业率",
                "unit": "%",
                "frequency": "季度",
                "source_type": "派生指标",
                "scope": "1－就业率/劳动参与率；与头条15—74岁失业率年龄口径不同",
                "points": [
                    {
                        "period": period,
                        "value": (1 - employment[period] / participation[period]) * 100,
                    }
                    for period in implied_periods
                    if participation[period] > 0
                ],
            }
        )

    unemployment = point_sets.get("unemployment", [])
    warnings = ["欧元区为21个成员国的聚合值；总量改善可能与成员国之间的分化同时存在。"]
    if failures:
        labels = "、".join(SERIES_META[key][2] for key in failures)
        warnings.append(
            f"Eurostat本次未返回：{labels}。其余序列继续展示，缺失值未按0处理。"
        )
    result = {
        "region": "EU",
        "country": "欧元区",
        "latest_month": unemployment[-1]["period"] if unemployment else None,
        "series": series,
        "sources": [
            {
                "name": "Eurostat：月度失业率",
                "url": "https://ec.europa.eu/eurostat/databrowser/view/une_rt_m/default/table",
                "description": "EA21欧元区聚合口径，失业率覆盖15—74岁，青年失业率覆盖15—24岁。",
            },
            {
                "name": "Eurostat：季度就业与劳动力闲置",
                "url": "https://ec.europa.eu/eurostat/databrowser/view/lfsi_emp_q/default/table",
                "description": "EU-LFS季调数据；参与率和就业率覆盖20—64岁，闲置率采用扩展劳动力口径。",
            },
        ],
        "warnings": warnings,
    }
    if not failures:
        _cache = (now, result)
    return result

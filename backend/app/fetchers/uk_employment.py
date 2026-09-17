"""United Kingdom labour-market dashboard from official ONS time series."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any

import requests


_HEADERS = {"User-Agent": "economic-dashboard/1.0"}
_CACHE_TTL = 6 * 60 * 60
_REQUEST_ATTEMPTS = 3
_cache: tuple[float, dict[str, Any]] | None = None

SERIES_META = {
    "unemployment": (
        "/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms/data",
        "失业率",
        "16岁及以上劳动力；季调；滚动三个月",
    ),
    "youth_unemployment": (
        "/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgwy/lms/data",
        "青年失业率",
        "16—24岁劳动力；季调；滚动三个月",
    ),
    "labor_participation": (
        "/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/timeseries/lf22/lms/data",
        "劳动参与率",
        "16—64岁人口；季调；滚动三个月",
    ),
    "employment_ratio": (
        "/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/timeseries/lf24/lms/data",
        "就业率",
        "16—64岁人口；季调；滚动三个月",
    ),
    "female_employment": (
        "/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/timeseries/lf25/lms/data",
        "女性就业率",
        "16—64岁女性；季调；滚动三个月",
    ),
    "male_employment": (
        "/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/timeseries/mgsv/lms/data",
        "男性就业率",
        "16—64岁男性；季调；滚动三个月",
    ),
}


def _fetch_ons_points(path: str) -> list[dict[str, Any]]:
    last_error: requests.RequestException | None = None
    for attempt in range(_REQUEST_ATTEMPTS):
        try:
            response = requests.get(
                f"https://www.ons.gov.uk{path}",
                headers=_HEADERS,
                timeout=45,
            )
            response.raise_for_status()
            rows = response.json().get("months", [])
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < _REQUEST_ATTEMPTS:
                time.sleep(0.35 * (attempt + 1))
    else:
        assert last_error is not None
        raise last_error

    points = [
        {
            "period": f"{row['year']}-{row['date'][-3:].title()}",
            "value": float(row["value"]),
        }
        for row in rows
        if row.get("year") and row.get("date") and row.get("value") not in (None, "")
        and int(row["year"]) >= 1990
    ]
    if not points:
        raise ValueError("ONS returned no employment observations")

    month_number = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    for point in points:
        year, month = point["period"].split("-")
        point["period"] = f"{year}-{month_number[month]}"
    return points


def _map(points: list[dict[str, Any]]) -> dict[str, float]:
    return {point["period"]: point["value"] for point in points}


def fetch_uk_employment_dashboard() -> dict[str, Any]:
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]

    with ThreadPoolExecutor(max_workers=len(SERIES_META)) as executor:
        futures = {
            key: executor.submit(_fetch_ons_points, meta[0])
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
        raise ValueError("ONS returned no usable employment series")

    series = [
        {
            "key": key,
            "name": name,
            "unit": "%",
            "frequency": "滚动三个月（月度发布）",
            "source_type": "英国劳动力调查",
            "scope": scope,
            "points": point_sets[key],
        }
        for key, (_, name, scope) in SERIES_META.items()
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
                "frequency": "滚动三个月（月度发布）",
                "source_type": "派生指标",
                "scope": "男性减女性；16—64岁",
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
                "name": "16—64岁恒等式隐含失业率",
                "unit": "%",
                "frequency": "滚动三个月（月度发布）",
                "source_type": "派生指标",
                "scope": "1－就业率/劳动参与率；与头条16岁以上失业率年龄口径不同",
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
    warnings = [
        "英国劳动力调查近期存在抽样波动；单月发布值应结合连续数期趋势和行政就业数据判断。"
    ]
    if failures:
        labels = "、".join(SERIES_META[key][1] for key in failures)
        warnings.append(
            f"ONS本次未返回：{labels}。其余序列继续展示，缺失值未按0处理。"
        )
    result = {
        "region": "GB",
        "country": "英国",
        "latest_month": unemployment[-1]["period"] if unemployment else None,
        "series": series,
        "sources": [
            {
                "name": "英国国家统计局：Labour market statistics time series",
                "url": "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/datasets/labourmarketstatistics",
                "description": "直接使用英国劳动力调查的季调序列；没有混用Eurostat欧盟或欧元区聚合值。",
            }
        ],
        "warnings": warnings,
    }
    if not failures:
        _cache = (now, result)
    return result

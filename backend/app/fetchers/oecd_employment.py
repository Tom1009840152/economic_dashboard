"""Comparable monthly labour-market dashboards for Japan and Korea.

The underlying national labour-force surveys are harmonised by the OECD and
distributed as public CSV series by FRED. All headline rates use ages 15–64 so
participation, employment and unemployment can be connected consistently.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any

from app.fetchers.fred_source import _cached_fred_raw


_CACHE_TTL = 6 * 60 * 60
_cache: dict[str, tuple[float, dict[str, Any]]] = {}

REGION_META = {
    "JP": {
        "country": "日本",
        "fred_country": "JP",
        "source_name": "日本总务省劳动力调查（经OECD与FRED分发）",
        "source_url": "https://www.stat.go.jp/english/data/roudou/index.html",
    },
    "KR": {
        "country": "韩国",
        "fred_country": "KR",
        "source_name": "韩国统计厅经济活动人口调查（经OECD与FRED分发）",
        "source_url": "https://kostat.go.kr/",
    },
}


def _series_meta(country_code: str) -> dict[str, tuple[str, str, str, str, str]]:
    return {
        "unemployment": (
            f"LRUN64TT{country_code}M156S",
            "15—64岁失业率",
            "%",
            "劳动力调查",
            "15—64岁劳动力；季调",
        ),
        "youth_unemployment": (
            f"LRHU24TT{country_code}M156S",
            "15—24岁失业率",
            "%",
            "劳动力调查",
            "15—24岁劳动力；季调",
        ),
        "labor_participation": (
            f"LRAC64TT{country_code}M156S",
            "劳动参与率",
            "%",
            "劳动力调查",
            "15—64岁人口；季调",
        ),
        "employment_ratio": (
            f"LREM64TT{country_code}M156S",
            "就业率",
            "%",
            "劳动力调查",
            "15—64岁人口；季调",
        ),
        "female_participation": (
            f"LRAC64FE{country_code}M156S",
            "女性劳动参与率",
            "%",
            "劳动力调查",
            "15—64岁女性；季调",
        ),
        "male_participation": (
            f"LRAC64MA{country_code}M156S",
            "男性劳动参与率",
            "%",
            "劳动力调查",
            "15—64岁男性；季调",
        ),
    }


def _points(frame) -> list[dict[str, Any]]:
    return [
        {"period": row.date.strftime("%Y-%m"), "value": float(row.value)}
        for row in frame.itertuples(index=False)
        if row.date.year >= 1990
    ]


def _derived_series(frames: dict[str, Any]) -> list[dict[str, Any]]:
    derived: list[dict[str, Any]] = []

    if {"female_participation", "male_participation"} <= frames.keys():
        female = frames["female_participation"][["date", "value"]].rename(
            columns={"value": "female"}
        )
        male = frames["male_participation"][["date", "value"]].rename(
            columns={"value": "male"}
        )
        gender_gap = male.merge(female, on="date", how="inner")
        gender_gap["value"] = gender_gap["male"] - gender_gap["female"]
        points = _points(gender_gap[["date", "value"]])
        if points:
            derived.append(
                {
                    "key": "gender_participation_gap",
                    "name": "男女劳动参与率差",
                    "unit": "百分点",
                    "frequency": "月度",
                    "source_type": "派生指标",
                    "scope": "男性减女性；差距缩小意味着更多女性劳动供给被动员",
                    "points": points,
                }
            )

    if {"labor_participation", "employment_ratio"} <= frames.keys():
        participation = frames["labor_participation"][["date", "value"]].rename(
            columns={"value": "participation"}
        )
        employment = frames["employment_ratio"][["date", "value"]].rename(
            columns={"value": "employment"}
        )
        implied = participation.merge(employment, on="date", how="inner")
        implied = implied[implied["participation"] > 0].copy()
        implied["value"] = (1 - implied["employment"] / implied["participation"]) * 100
        points = _points(implied[["date", "value"]])
        if points:
            derived.append(
                {
                    "key": "implied_unemployment",
                    "name": "恒等式隐含失业率",
                    "unit": "%",
                    "frequency": "月度",
                    "source_type": "派生指标",
                    "scope": "1－就业率/劳动参与率；用于核对同年龄口径下的失业率",
                    "points": points,
                }
            )

    return derived


def fetch_oecd_employment_dashboard(region: str) -> dict[str, Any]:
    normalized = region.upper()
    region_meta = REGION_META.get(normalized)
    if region_meta is None:
        raise ValueError(f"unsupported OECD employment region: {region}")

    now = time.time()
    cached = _cache.get(normalized)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    metadata = _series_meta(region_meta["fred_country"])
    with ThreadPoolExecutor(max_workers=len(metadata)) as executor:
        futures = {
            key: executor.submit(_cached_fred_raw, item[0])
            for key, item in metadata.items()
        }
        frames: dict[str, Any] = {}
        point_sets: dict[str, list[dict[str, Any]]] = {}
        failures: dict[str, str] = {}
        for key, future in futures.items():
            try:
                frame = future.result()
                points = _points(frame)
                if not points:
                    raise ValueError("FRED returned no observations from 1990 onward")
                frames[key] = frame
                point_sets[key] = points
            except Exception as exc:  # Isolate failures from independent remote series.
                failures[key] = type(exc).__name__

    if not point_sets:
        raise ValueError(f"FRED returned no usable employment series for {normalized}")

    series = [
        {
            "key": key,
            "name": name,
            "unit": unit,
            "frequency": "月度",
            "source_type": source_type,
            "scope": scope,
            "points": point_sets[key],
        }
        for key, (_, name, unit, source_type, scope) in metadata.items()
        if key in point_sets
    ]
    series.extend(_derived_series(frames))

    unemployment = point_sets.get("unemployment", [])
    warnings = [
        (
            f"FRED本次未返回{metadata[key][1]}（{metadata[key][0]}；{error_type}）。"
            "该序列未展示；依赖它的派生指标（如有）也不生成，缺失值未按0处理。"
        )
        for key, error_type in failures.items()
    ]
    result = {
        "region": normalized,
        "country": region_meta["country"],
        "latest_month": unemployment[-1]["period"] if unemployment else None,
        "series": series,
        "sources": [
            {
                "name": region_meta["source_name"],
                "url": region_meta["source_url"],
                "description": "失业率、就业率和劳动参与率源自国家劳动力调查，并由OECD统一年龄和季调口径。",
            },
            {
                "name": "OECD Infra-Annual Labour Statistics（FRED分发）",
                "url": "https://fred.stlouisfed.org/categories/32287",
                "description": "页面采用15—64岁月度季调序列，便于跨国比较；最新月份可能因发布日不同而错位。",
            },
        ],
        "warnings": warnings,
    }
    if not failures:
        _cache[normalized] = (now, result)
    return result

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
    female = frames["female_participation"][["date", "value"]].rename(columns={"value": "female"})
    male = frames["male_participation"][["date", "value"]].rename(columns={"value": "male"})
    gender_gap = male.merge(female, on="date", how="inner")
    gender_gap["value"] = gender_gap["male"] - gender_gap["female"]

    participation = frames["labor_participation"][["date", "value"]].rename(columns={"value": "participation"})
    employment = frames["employment_ratio"][["date", "value"]].rename(columns={"value": "employment"})
    implied = participation.merge(employment, on="date", how="inner")
    implied = implied[implied["participation"] > 0].copy()
    implied["value"] = (1 - implied["employment"] / implied["participation"]) * 100

    definitions = [
        (
            "gender_participation_gap",
            "男女劳动参与率差",
            "百分点",
            "派生指标",
            "男性减女性；差距缩小意味着更多女性劳动供给被动员",
            gender_gap[["date", "value"]],
        ),
        (
            "implied_unemployment",
            "恒等式隐含失业率",
            "%",
            "派生指标",
            "1－就业率/劳动参与率；用于核对同年龄口径下的失业率",
            implied[["date", "value"]],
        ),
    ]
    return [
        {
            "key": key,
            "name": name,
            "unit": unit,
            "frequency": "月度",
            "source_type": source_type,
            "scope": scope,
            "points": _points(frame),
        }
        for key, name, unit, source_type, scope, frame in definitions
    ]


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
        frames = {key: future.result() for key, future in futures.items()}

    series = [
        {
            "key": key,
            "name": name,
            "unit": unit,
            "frequency": "月度",
            "source_type": source_type,
            "scope": scope,
            "points": _points(frames[key]),
        }
        for key, (_, name, unit, source_type, scope) in metadata.items()
    ]
    series.extend(_derived_series(frames))

    unemployment = next(item["points"] for item in series if item["key"] == "unemployment")
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
        "warnings": [],
    }
    _cache[normalized] = (now, result)
    return result

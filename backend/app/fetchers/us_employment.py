"""United States labour-market dashboard from BLS series distributed by FRED."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any

from app.fetchers.fred_source import _cached_fred_raw, _calendar_month_change


_CACHE_TTL = 6 * 60 * 60
_cache: tuple[float, dict[str, Any]] | None = None

SERIES_META = {
    "unemployment": (
        "UNRATE",
        "U-3失业率",
        "%",
        "家庭调查",
        "16岁以上非机构平民人口；季调",
    ),
    "underemployment": (
        "U6RATE",
        "U-6劳动利用不足率",
        "%",
        "家庭调查",
        "失业、边缘依附劳动力及因经济原因兼职；季调",
    ),
    "youth_unemployment": (
        "LNS14024887",
        "16—24岁失业率",
        "%",
        "家庭调查",
        "16—24岁劳动力；季调",
    ),
    "labor_participation": (
        "CIVPART",
        "劳动参与率",
        "%",
        "家庭调查",
        "16岁以上非机构平民人口；季调",
    ),
    "employment_ratio": (
        "EMRATIO",
        "就业人口比",
        "%",
        "家庭调查",
        "16岁以上非机构平民人口；季调",
    ),
    "nonfarm_payrolls": (
        "PAYEMS",
        "非农就业人数",
        "千人",
        "企业调查",
        "非农机构就业岗位；季调",
    ),
    "weekly_hours": (
        "AWHAETP",
        "私营部门周平均工时",
        "小时",
        "企业调查",
        "私营非农全部雇员；季调",
    ),
    "hourly_earnings": (
        "CES0500000003",
        "私营部门平均时薪",
        "美元/小时",
        "企业调查",
        "私营非农全部雇员；季调",
    ),
    "job_openings": (
        "JTSJOL",
        "非农职位空缺",
        "千个",
        "JOLTS企业调查",
        "月末仍在主动招聘的非农职位；季调",
    ),
    "unemployed_people": (
        "UNEMPLOY",
        "失业人数",
        "千人",
        "家庭调查",
        "16岁以上失业人口；季调",
    ),
}


def _points(frame) -> list[dict[str, Any]]:
    return [
        {"period": row.date.strftime("%Y-%m"), "value": float(row.value)}
        for row in frame.itertuples(index=False)
        if row.date.year >= 1990
    ]


def _derived_series(frames: dict[str, Any]) -> list[dict[str, Any]]:
    payrolls = _calendar_month_change(
        frames["nonfarm_payrolls"],
        months=1,
        percent=False,
    )

    earnings = _calendar_month_change(
        frames["hourly_earnings"],
        months=12,
        percent=True,
    )

    openings = frames["job_openings"][["date", "value"]].rename(columns={"value": "openings"})
    unemployed = frames["unemployed_people"][["date", "value"]].rename(columns={"value": "unemployed"})
    tightness = openings.merge(unemployed, on="date", how="inner")
    tightness["value"] = tightness["openings"] / tightness["unemployed"]

    derived = [
        (
            "payroll_change",
            "非农就业月度变化",
            "千人",
            "企业调查",
            "当月非农就业人数减上月；初值会在随后月份修订",
            payrolls.dropna(subset=["value"]),
        ),
        (
            "hourly_earnings_yoy",
            "平均时薪同比",
            "%",
            "企业调查",
            "私营非农全部雇员平均时薪同比；存在就业构成效应",
            earnings.dropna(subset=["value"]),
        ),
        (
            "vacancy_unemployed_ratio",
            "职位空缺/失业人数",
            "倍",
            "混合调查",
            "JOLTS职位空缺除以家庭调查失业人数；衡量劳动力市场紧张度",
            tightness[["date", "value"]],
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
        for key, name, unit, source_type, scope, frame in derived
    ]


def fetch_us_employment_dashboard() -> dict[str, Any]:
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]

    with ThreadPoolExecutor(max_workers=len(SERIES_META)) as executor:
        futures = {
            key: executor.submit(_cached_fred_raw, meta[0])
            for key, meta in SERIES_META.items()
        }
        frames = {key: future.result() for key, future in futures.items()}

    series = []
    for key, (_, name, unit, source_type, scope) in SERIES_META.items():
        series.append(
            {
                "key": key,
                "name": name,
                "unit": unit,
                "frequency": "月度",
                "source_type": source_type,
                "scope": scope,
                "points": _points(frames[key]),
            }
        )
    series.extend(_derived_series(frames))

    unemployment_points = next(item["points"] for item in series if item["key"] == "unemployment")
    result = {
        "region": "US",
        "country": "美国",
        "latest_month": unemployment_points[-1]["period"] if unemployment_points else None,
        "series": series,
        "sources": [
            {
                "name": "美国劳工统计局就业形势报告（经FRED分发）",
                "url": "https://fred.stlouisfed.org/release?rid=50",
                "description": "家庭调查提供失业和参与率，企业调查提供非农就业、工时和工资。",
            },
            {
                "name": "美国劳工统计局 JOLTS（经FRED分发）",
                "url": "https://fred.stlouisfed.org/release?rid=192",
                "description": "职位空缺与劳动力流动调查，发布时间晚于就业形势报告。",
            },
        ],
        "warnings": [],
    }
    _cache = (now, result)
    return result

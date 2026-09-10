"""China employment dashboard data assembled from NBS and World Bank WDI.

The two sources answer different questions and must not be blended silently:
NBS monthly rates describe the urban labour market, while WDI contains annual,
national ILO-modelled estimates.  Every series carries its own scope and source
type so the frontend can keep those definitions visible.
"""

from __future__ import annotations

import time
from typing import Any

import akshare as ak
import requests


WORLD_BANK_API = "https://api.worldbank.org/v2"
_CACHE_TTL = 6 * 60 * 60
_cache: tuple[float, dict[str, Any]] | None = None

NBS_SERIES = {
    "全国城镇调查失业率": (
        "urban_unemployment",
        "全国城镇调查失业率",
        "城镇常住人口",
        "官方抽样调查",
    ),
    "全国城镇25—59岁劳动力失业率": (
        "prime_age_unemployment_old_definition",
        "25—59岁劳动力失业率（旧口径）",
        "城镇常住人口；该序列已停止更新",
        "官方抽样调查",
    ),
    "全国城镇外来户籍劳动力失业率": (
        "migrant_registration_unemployment",
        "外来户籍劳动力失业率",
        "城镇外来户籍劳动力",
        "官方抽样调查",
    ),
    "全国城镇本地户籍劳动力失业率": (
        "local_registration_unemployment",
        "本地户籍劳动力失业率",
        "城镇本地户籍劳动力",
        "官方抽样调查",
    ),
}

WDI_SERIES = {
    "labor_force": (
        "SL.TLF.TOTL.IN",
        "劳动力总人数",
        "人",
        "全国15岁以上",
    ),
    "labor_participation": (
        "SL.TLF.CACT.ZS",
        "劳动参与率",
        "%",
        "全国15岁以上；ILO模型估计",
    ),
    "employment_ratio": (
        "SL.EMP.TOTL.SP.ZS",
        "就业人口比",
        "%",
        "全国15岁以上；ILO模型估计",
    ),
    "unemployment_modelled": (
        "SL.UEM.TOTL.ZS",
        "失业率（ILO模型）",
        "%",
        "全国劳动力；ILO模型估计",
    ),
    "youth_unemployment_modelled": (
        "SL.UEM.1524.ZS",
        "青年失业率（ILO模型）",
        "%",
        "全国15—24岁劳动力；包含学生口径影响",
    ),
    "female_participation": (
        "SL.TLF.CACT.FE.ZS",
        "女性劳动参与率",
        "%",
        "全国15岁以上女性；ILO模型估计",
    ),
    "male_participation": (
        "SL.TLF.CACT.MA.ZS",
        "男性劳动参与率",
        "%",
        "全国15岁以上男性；ILO模型估计",
    ),
    "vulnerable_employment": (
        "SL.EMP.VULN.ZS",
        "脆弱就业占比",
        "%",
        "自营劳动者与无酬家庭帮工；ILO模型估计",
    ),
}

# The annual bulletin is authoritative but not exposed as a stable machine API.
# Keep this small snapshot explicit and update it when the next bulletin arrives.
OFFICIAL_SNAPSHOT = {
    "year": 2025,
    "employment_total": 725_040_000,
    "urban_employment": 475_350_000,
    "rural_employment": 249_690_000,
    "migrant_workers": 301_150_000,
    "annual_urban_unemployment": 5.2,
    "weekly_hours": 48.6,
    "source": "中华人民共和国2025年国民经济和社会发展统计公报",
    "source_url": "https://www.stats.gov.cn/sj/zxfb/202602/t20260228_1962662.html",
}


def _point_map(records: list[dict[str, Any]]) -> dict[int, float]:
    return {
        int(row["date"]): float(row["value"])
        for row in records
        if row.get("value") is not None and str(row.get("date", "")).isdigit()
    }


def _fetch_nbs_series() -> list[dict[str, Any]]:
    frame = ak.macro_china_urban_unemployment()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in frame.to_dict("records"):
        raw_name = str(row.get("item", "")).strip()
        meta = NBS_SERIES.get(raw_name)
        if not meta:
            continue
        key, _, _, _ = meta
        raw_period = str(row.get("date", "")).strip()
        if len(raw_period) != 6 or not raw_period.isdigit():
            continue
        try:
            value = float(str(row.get("value", "")).strip())
        except ValueError:
            continue
        grouped.setdefault(key, []).append(
            {"period": f"{raw_period[:4]}-{raw_period[4:]}", "value": value}
        )

    result = []
    for _, (key, name, scope, source_type) in NBS_SERIES.items():
        points = sorted(grouped.get(key, []), key=lambda point: point["period"])
        if points:
            result.append(
                {
                    "key": key,
                    "name": name,
                    "unit": "%",
                    "frequency": "月度",
                    "source_type": source_type,
                    "scope": scope,
                    "points": points,
                }
            )
    return result


def _fetch_wdi_series() -> tuple[list[dict[str, Any]], str | None]:
    codes = [meta[0] for meta in WDI_SERIES.values()]
    response = requests.get(
        f"{WORLD_BANK_API}/country/CHN/indicator/{';'.join(codes)}",
        params={
            "format": "json",
            "source": 2,
            "date": "1990:2025",
            "per_page": 10000,
        },
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        raise ValueError("World Bank returned no employment data")

    meta, records = payload[0], payload[1]
    by_indicator: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        by_indicator.setdefault(row["indicator"]["id"], []).append(row)

    result = []
    for key, (indicator_id, name, unit, scope) in WDI_SERIES.items():
        values = _point_map(by_indicator.get(indicator_id, []))
        result.append(
            {
                "key": key,
                "name": name,
                "unit": unit,
                "frequency": "年度",
                "source_type": "ILO模型估计",
                "scope": scope,
                "points": [
                    {"period": str(year), "value": values[year]}
                    for year in sorted(values)
                ],
            }
        )
    return result, meta.get("lastupdated")


def fetch_china_employment_dashboard() -> dict[str, Any]:
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]

    warnings: list[str] = []
    monthly_series: list[dict[str, Any]] = []
    annual_series: list[dict[str, Any]] = []
    wdi_updated = None

    try:
        monthly_series = _fetch_nbs_series()
    except Exception:
        warnings.append("国家统计局月度接口暂时不可用，年度结构数据仍可查看。")

    try:
        annual_series, wdi_updated = _fetch_wdi_series()
    except (requests.RequestException, ValueError):
        warnings.append("世界银行年度接口暂时不可用，月度就业景气数据仍可查看。")

    if not monthly_series and not annual_series:
        raise ValueError("all employment data sources are unavailable")

    latest_month = max(
        (
            point["period"]
            for series in monthly_series
            for point in series["points"]
        ),
        default=None,
    )
    result = {
        "region": "CN",
        "country": "中国",
        "latest_month": latest_month,
        "wdi_updated": wdi_updated,
        "monthly_series": monthly_series,
        "annual_series": annual_series,
        "official_snapshot": OFFICIAL_SNAPSHOT,
        "sources": [
            {
                "name": "国家统计局月度数据",
                "url": "https://data.stats.gov.cn/dg/website/page.html#/pc/national/monthData",
                "description": "城镇调查失业率及户籍分组，官方抽样调查口径。",
            },
            {
                "name": "世界银行 WDI / ILO",
                "url": "https://data.worldbank.org/country/china",
                "description": "全国年度劳动力、参与率、就业率及结构性模型估计。",
            },
            {
                "name": OFFICIAL_SNAPSHOT["source"],
                "url": OFFICIAL_SNAPSHOT["source_url"],
                "description": "年度就业总量、城乡就业、农民工与工时快照。",
            },
        ],
        "warnings": warnings,
    }
    _cache = (now, result)
    return result

"""World Bank WDI population data for the structural population dashboard.

The Indicators API is public and does not require a key. Population data moves
slowly, so the assembled response is cached for 24 hours instead of joining the
six-hour market-data refresh cycle.
"""

from __future__ import annotations

import time

import requests


WORLD_BANK_API = "https://api.worldbank.org/v2"
_CACHE_TTL = 24 * 60 * 60
_cache: dict[str, tuple[float, dict]] = {}

REGION_META = {
    "CN": {"world_bank_code": "CHN", "country": "中国", "slug": "china"},
    "US": {"world_bank_code": "USA", "country": "美国", "slug": "united-states"},
    "JP": {"world_bank_code": "JPN", "country": "日本", "slug": "japan"},
    "EU": {"world_bank_code": "EUU", "country": "欧盟", "slug": "european-union"},
    "KR": {"world_bank_code": "KOR", "country": "韩国", "slug": "korea-rep"},
}

SERIES_META = {
    "total_population": ("SP.POP.TOTL", "总人口", "人"),
    "population_growth": ("SP.POP.GROW", "人口增长率", "%"),
    "birth_rate": ("SP.DYN.CBRT.IN", "粗出生率", "‰"),
    "death_rate": ("SP.DYN.CDRT.IN", "粗死亡率", "‰"),
    "fertility_rate": ("SP.DYN.TFRT.IN", "总和生育率", "个/妇女"),
    "life_expectancy": ("SP.DYN.LE00.IN", "预期寿命", "岁"),
    "net_migration": ("SM.POP.NETM", "净迁移", "人"),
    "young_share": ("SP.POP.0014.TO.ZS", "0—14岁人口占比", "%"),
    "working_age_share": ("SP.POP.1564.TO.ZS", "15—64岁人口占比", "%"),
    "old_share": ("SP.POP.65UP.TO.ZS", "65岁及以上人口占比", "%"),
    "dependency_ratio": ("SP.POP.DPND", "总抚养比", "%"),
    "old_dependency_ratio": ("SP.POP.DPND.OL", "老年抚养比", "%"),
    "urban_share": ("SP.URB.TOTL.IN.ZS", "城镇人口占比", "%"),
    "labor_participation": ("SL.TLF.CACT.ZS", "劳动参与率", "%"),
    "labor_force": ("SL.TLF.TOTL.IN", "劳动力总人数", "人"),
    "real_gdp": ("NY.GDP.MKTP.KD", "实际GDP", "2015年不变价美元"),
}

AGE_GROUPS = [
    ("0—4", "0004"),
    ("5—9", "0509"),
    ("10—14", "1014"),
    ("15—19", "1519"),
    ("20—24", "2024"),
    ("25—29", "2529"),
    ("30—34", "3034"),
    ("35—39", "3539"),
    ("40—44", "4044"),
    ("45—49", "4549"),
    ("50—54", "5054"),
    ("55—59", "5559"),
    ("60—64", "6064"),
    ("65—69", "6569"),
    ("70—74", "7074"),
    ("75—79", "7579"),
    ("80+", "80UP"),
]


def _point_map(records: list[dict]) -> dict[int, float]:
    return {
        int(row["date"]): float(row["value"])
        for row in records
        if row.get("value") is not None and str(row.get("date", "")).isdigit()
    }


def fetch_population_dashboard(region: str = "CN") -> dict:
    cache_key = region.upper()
    country_meta = REGION_META.get(cache_key)
    if country_meta is None:
        raise ValueError(f"unsupported population region: {region}")
    country_code = country_meta["world_bank_code"]
    cached = _cache.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    pyramid_codes = [
        f"SP.POP.{age_code}.{sex}.5Y"
        for _, age_code in AGE_GROUPS
        for sex in ("MA", "FE")
    ]
    support_codes = ["SP.POP.TOTL.MA.IN", "SP.POP.TOTL.FE.IN"]
    requested_codes = [meta[0] for meta in SERIES_META.values()] + pyramid_codes + support_codes
    url = f"{WORLD_BANK_API}/country/{country_code}/indicator/{';'.join(requested_codes)}"
    response = requests.get(
        url,
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
        raise ValueError("World Bank returned no population data")

    meta, records = payload[0], payload[1]
    by_indicator: dict[str, list[dict]] = {}
    for row in records:
        by_indicator.setdefault(row["indicator"]["id"], []).append(row)

    series = []
    for key, (indicator_id, name, unit) in SERIES_META.items():
        values = _point_map(by_indicator.get(indicator_id, []))
        series.append(
            {
                "key": key,
                "name": name,
                "unit": unit,
                "points": [
                    {"year": year, "value": values[year]}
                    for year in sorted(values)
                ],
            }
        )

    birth = _point_map(by_indicator.get(SERIES_META["birth_rate"][0], []))
    death = _point_map(by_indicator.get(SERIES_META["death_rate"][0], []))
    natural_years = sorted(set(birth) & set(death))
    series.append(
        {
            "key": "natural_growth_rate",
            "name": "自然增长率",
            "unit": "‰",
            "points": [
                {"year": year, "value": birth[year] - death[year]}
                for year in natural_years
            ],
        }
    )

    male_total = _point_map(by_indicator.get("SP.POP.TOTL.MA.IN", []))
    female_total = _point_map(by_indicator.get("SP.POP.TOTL.FE.IN", []))
    pyramid_maps: dict[str, dict[int, float]] = {
        code: _point_map(by_indicator.get(code, [])) for code in pyramid_codes
    }
    pyramid_years = set(male_total) & set(female_total)
    for code in pyramid_codes:
        pyramid_years &= set(pyramid_maps[code])
    pyramid_year = max(pyramid_years) if pyramid_years else None

    pyramid = []
    if pyramid_year is not None:
        for label, age_code in AGE_GROUPS:
            male_pct = pyramid_maps[f"SP.POP.{age_code}.MA.5Y"][pyramid_year]
            female_pct = pyramid_maps[f"SP.POP.{age_code}.FE.5Y"][pyramid_year]
            male = male_total[pyramid_year] * male_pct / 100
            female = female_total[pyramid_year] * female_pct / 100
            pyramid.append(
                {
                    "age_group": label,
                    "male": male,
                    "female": female,
                    "total": male + female,
                }
            )

    result = {
        "region": cache_key,
        "country": country_meta["country"],
        "source": "世界银行 WDI（主要底层来源为联合国人口司 WPP 与国际劳工组织）",
        "source_url": f"https://data.worldbank.org/country/{country_meta['slug']}",
        "last_updated": meta.get("lastupdated"),
        "series": series,
        "pyramid_year": pyramid_year,
        "pyramid": pyramid,
    }
    _cache[cache_key] = (now, result)
    return result

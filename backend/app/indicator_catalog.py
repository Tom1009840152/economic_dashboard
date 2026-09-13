"""Authoritative modelling metadata for every dashboard indicator.

The database keeps only the small subset of fields required by the public API.
This module is the richer data dictionary used by validation and analytical
models.  Source groups are deliberately exhaustive: adding an indicator without
classifying it makes application import fail instead of silently falling back to
``akshare`` or an unknown frequency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Iterable, Mapping


VALID_FREQUENCIES = frozenset({"daily", "weekly", "monthly", "quarterly", "event"})
VALID_SEASONAL_ADJUSTMENTS = frozenset(
    {"not_applicable", "seasonally_adjusted", "not_seasonally_adjusted", "source_reported"}
)
VALID_MEASURE_TYPES = frozenset(
    {
        "price", "exchange_rate", "index", "rate", "spread", "stock", "flow", "yoy",
        "mom", "change", "ratio", "duration", "qoq_annualized",
    }
)
VALID_AGGREGATIONS = frozenset(
    {"point_in_time", "end_of_period", "period_average", "period_sum", "cumulative_ytd", "derived"}
)
VALID_DIRECTIONS = frozenset({"positive", "negative", "contextual"})
VALID_TRANSFORMS = frozenset(
    {"level", "yoy", "mom", "difference", "spread", "rolling_12m_gdp_ratio", "year_over_year_difference", "qoq_annualized"}
)


@dataclass(frozen=True, slots=True)
class IndicatorCatalogEntry:
    """Metadata needed to interpret and model one stored series.

    ``release_lag_months`` is a conservative typical lag, not a substitute for
    each observation's real ``available_at`` timestamp.  ``direction`` describes
    the usual near-term relationship with activity; ``contextual`` means the
    sign must not be hard-coded in a composite cycle score.
    """

    code: str
    source: str
    frequency: str
    seasonal_adjustment: str
    measure_type: str
    aggregation: str
    cumulative: bool
    direction: str
    transform: str
    release_lag_months: int
    valid_min: float | None
    valid_max: float | None
    notes: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_SOURCE_GROUPS: Mapping[str, frozenset[str]] = {
    "Sina Finance": frozenset(
        {"SSE", "DJI", "NKY", "STOXX50", "FTSE100", "KOSPI", "GOLD", "WTI"}
    ),
    "Bank of China": frozenset(
        {
            "USDCNY", "EURCNY", "JPYCNY", "GBPCNY", "CADCNY", "AUDCNY",
            "KRWCNY", "CHFCNY", "SEKCNY", "THBCNY", "SGDCNY", "NOKCNY",
        }
    ),
    "NBS": frozenset(
        {
            "CN_CPI", "CN_CORE_CPI", "CN_PPI", "CN_PMI", "CN_NMI", "CN_IP",
            "CN_GDP", "CN_RETAIL", "CN_FAI",
            "CN_PMI_PRODUCTION", "CN_PMI_NEW_ORDERS", "CN_PMI_NEW_EXPORT_ORDERS",
            "CN_PMI_EMPLOYMENT", "CN_PMI_RAW_MATERIAL_INVENTORY",
            "CN_PMI_FINISHED_GOODS_INVENTORY", "CN_PMI_EXPECTATIONS",
            "CN_NMI_NEW_ORDERS", "CN_NMI_EMPLOYMENT", "CN_NMI_EXPECTATIONS",
            "CN_IND_REVENUE_YTD", "CN_IND_REVENUE_YTD_YOY", "CN_IND_PROFIT_YTD",
            "CN_IND_PROFIT_YTD_YOY", "CN_IND_PROFIT_MONTHLY_YOY",
            "CN_IND_FINISHED_INVENTORY_YOY", "CN_IND_INVENTORY_DAYS",
            "CN_GDP_NOMINAL_YTD", "CN_RE_INVEST_YTD", "CN_RE_INVEST_YTD_YOY",
            "CN_RE_SALES_AREA_YTD", "CN_RE_SALES_AREA_YTD_YOY",
            "CN_RE_SALES_VALUE_YTD", "CN_RE_SALES_VALUE_YTD_YOY",
            "CN_RE_STARTS_YTD", "CN_RE_STARTS_YTD_YOY", "CN_RE_CONSTRUCTION",
            "CN_RE_CONSTRUCTION_YOY",
        }
    ),
    "PBOC": frozenset(
        {
            "CN_TSF", "CN_M2_ABS", "CN_M2_YOY", "CN_M2_MOM", "CN_M1_ABS",
            "CN_M1_YOY", "CN_M1_MOM", "CN_M0_ABS", "CN_M0_YOY", "CN_M0_MOM",
            "CN_TSF_STOCK_YOY", "CN_TSF_RMB_LOAN_STOCK_YOY",
            "CN_TSF_RMB_LOANS_FLOW", "CN_CORP_BOND_FINANCING",
            "CN_GOV_BOND_FINANCING_YTD", "CN_GOV_BOND_FINANCING",
        }
    ),
    "GACC": frozenset({"CN_EXPORTS", "CN_EXPORTS_ABS"}),
    "Hangqingbao": frozenset({"CN_HOG"}),
    "Eastmoney": frozenset(
        {
            "CN_REALESTATE", "CN_ENERGY", "CN_2Y", "CN_5Y", "CN_10Y", "CN_30Y",
            "CN_10Y2Y", "US_2Y", "US_5Y", "US_10Y", "US_30Y", "US_10Y2Y",
            "CN_RE_PRICE_RISING_SHARE", "CN_RE_PRICE_MOM_MEDIAN",
            "CN_CONSUMER_CONFIDENCE", "CN_CONSUMER_SATISFACTION",
            "CN_CONSUMER_EXPECTATIONS", "CN_ENTERPRISE_BOOM",
        }
    ),
    "MOF": frozenset(
        {
            "CN_FISCAL_GENERAL_SPEND_YTD", "CN_FISCAL_GENERAL_SPEND_YOY",
            "CN_FISCAL_FUND_EXPENDITURE_YTD", "CN_FISCAL_FUND_EXPENDITURE_YOY",
            "CN_FISCAL_BROAD_EXPENDITURE_YTD", "CN_FISCAL_BROAD_EXPENDITURE_YOY",
            "CN_LOCAL_SPECIAL_BOND_ISSUANCE",
        }
    ),
    "Derived": frozenset(
        {
            "CN_M1M2", "CN_CREDIT_INTENSITY", "CN_CREDIT_IMPULSE",
            "CN_FISCAL_SPEND_INTENSITY", "CN_FISCAL_IMPULSE_PROXY",
        }
    ),
    "OECD": frozenset(
        {"CN_CLI", "US_CLI", "US_CORE_CPI", "JP_CORE_CPI", "JP_CLI", "KR_CORE_CPI", "KR_CLI"}
    ),
    "FRED": frozenset(
        {
            "US_NFP", "US_FFR", "US_GDP", "US_BASE_ABS", "US_BASE_YOY",
            "US_BASE_MOM", "US_M1_ABS", "US_M1_YOY", "US_M1_MOM", "US_M2_ABS",
            "US_M2_YOY", "US_M2_MOM", "US_IP", "JP_IP", "JP_BOJ", "JP_BOJ_ASSETS",
            "EU_CPI", "EU_CORE_CPI", "EU_ECB", "EU_ECB_ASSETS", "GB_CLI", "GB_M3",
            "KR_IP", "KR_RESERVES",
        }
    ),
    "Jin10/AkShare": frozenset({"US_CPI", "JP_CPI"}),
    "Eurostat/EC": frozenset({"EU_IP", "EU_CLI", "EU_GDP"}),
    "UK ONS": frozenset({"GB_CPI", "GB_CORE_CPI", "GB_IP", "GB_GDP"}),
    "Bank of England": frozenset({"GB_BOE"}),
}


_DAILY_CODES = frozenset(
    {
        "SSE", "DJI", "NKY", "STOXX50", "FTSE100", "KOSPI", "GOLD", "WTI",
        "USDCNY", "EURCNY", "JPYCNY", "GBPCNY", "CADCNY", "AUDCNY", "KRWCNY",
        "CHFCNY", "SEKCNY", "THBCNY", "SGDCNY", "NOKCNY", "CN_2Y", "CN_5Y",
        "CN_10Y", "CN_30Y", "CN_10Y2Y", "US_2Y", "US_5Y", "US_10Y", "US_30Y",
        "US_10Y2Y",
    }
)
_WEEKLY_CODES = frozenset({"CN_HOG", "EU_ECB_ASSETS"})
_QUARTERLY_CODES = frozenset(
    {
        "CN_GDP", "US_GDP", "EU_GDP", "GB_GDP", "CN_GDP_NOMINAL_YTD",
        "CN_FISCAL_SPEND_INTENSITY", "CN_FISCAL_IMPULSE_PROXY", "CN_ENTERPRISE_BOOM",
    }
)
_EVENT_CODES = frozenset({"GB_BOE"})
_CUMULATIVE_YOY_CODES = frozenset(
    {
        "CN_FISCAL_GENERAL_SPEND_YOY",
        "CN_FISCAL_FUND_EXPENDITURE_YOY",
        "CN_FISCAL_BROAD_EXPENDITURE_YOY",
    }
)


_SEASONALLY_ADJUSTED = frozenset(
    {
        "CN_PMI", "CN_NMI", "CN_PMI_PRODUCTION", "CN_PMI_NEW_ORDERS",
        "CN_PMI_NEW_EXPORT_ORDERS", "CN_PMI_EMPLOYMENT",
        "CN_PMI_RAW_MATERIAL_INVENTORY", "CN_PMI_FINISHED_GOODS_INVENTORY",
        "CN_PMI_EXPECTATIONS", "CN_NMI_NEW_ORDERS", "CN_NMI_EMPLOYMENT",
        "CN_NMI_EXPECTATIONS", "CN_CLI", "US_CLI", "JP_CLI", "KR_CLI", "EU_CLI",
        "US_IP", "JP_IP", "EU_IP", "GB_IP", "KR_IP", "US_GDP", "EU_GDP", "GB_GDP",
        "US_M1_ABS", "US_M1_YOY", "US_M1_MOM", "US_M2_ABS", "US_M2_YOY",
        "US_M2_MOM", "GB_M3",
    }
)
_NOT_SEASONALLY_ADJUSTED = frozenset(
    {
        "CN_CPI", "CN_CORE_CPI", "CN_PPI", "CN_TSF", "CN_GDP", "CN_RETAIL",
        "CN_FAI", "CN_EXPORTS", "CN_EXPORTS_ABS", "CN_M2_ABS", "CN_M2_YOY",
        "CN_M2_MOM", "CN_M1_ABS", "CN_M1_YOY", "CN_M1_MOM", "CN_M0_ABS",
        "CN_M0_YOY", "CN_M0_MOM", "CN_M1M2", "CN_GDP_NOMINAL_YTD",
        "CN_IND_REVENUE_YTD", "CN_IND_REVENUE_YTD_YOY", "CN_IND_PROFIT_YTD",
        "CN_IND_PROFIT_YTD_YOY", "CN_IND_PROFIT_MONTHLY_YOY",
        "CN_IND_FINISHED_INVENTORY_YOY", "CN_IND_INVENTORY_DAYS",
        "CN_TSF_STOCK_YOY", "CN_TSF_RMB_LOAN_STOCK_YOY", "CN_TSF_RMB_LOANS_FLOW",
        "CN_CORP_BOND_FINANCING", "CN_GOV_BOND_FINANCING_YTD",
        "CN_GOV_BOND_FINANCING", "CN_RE_INVEST_YTD", "CN_RE_INVEST_YTD_YOY",
        "CN_RE_SALES_AREA_YTD", "CN_RE_SALES_AREA_YTD_YOY",
        "CN_RE_SALES_VALUE_YTD", "CN_RE_SALES_VALUE_YTD_YOY", "CN_RE_STARTS_YTD",
        "CN_RE_STARTS_YTD_YOY", "CN_RE_CONSTRUCTION", "CN_RE_CONSTRUCTION_YOY",
        "CN_FISCAL_GENERAL_SPEND_YTD", "CN_FISCAL_GENERAL_SPEND_YOY",
        "CN_FISCAL_FUND_EXPENDITURE_YTD", "CN_FISCAL_FUND_EXPENDITURE_YOY",
        "CN_FISCAL_BROAD_EXPENDITURE_YTD", "CN_FISCAL_BROAD_EXPENDITURE_YOY",
        "CN_LOCAL_SPECIAL_BOND_ISSUANCE",
    }
)


_POSITIVE_DIRECTION = frozenset(
    {
        "CN_PMI", "CN_NMI", "CN_IP", "CN_CLI", "CN_GDP", "CN_RETAIL", "CN_FAI",
        "CN_EXPORTS", "CN_M1M2", "US_IP", "US_CLI", "US_NFP", "US_GDP", "JP_IP",
        "JP_CLI", "EU_IP", "EU_CLI", "EU_GDP", "GB_IP", "GB_CLI", "GB_GDP",
        "KR_IP", "KR_CLI", "CN_PMI_PRODUCTION", "CN_PMI_NEW_ORDERS",
        "CN_PMI_NEW_EXPORT_ORDERS", "CN_PMI_EMPLOYMENT", "CN_PMI_EXPECTATIONS",
        "CN_NMI_NEW_ORDERS", "CN_NMI_EMPLOYMENT", "CN_NMI_EXPECTATIONS",
        "CN_IND_REVENUE_YTD", "CN_IND_REVENUE_YTD_YOY", "CN_IND_PROFIT_YTD",
        "CN_IND_PROFIT_YTD_YOY", "CN_IND_PROFIT_MONTHLY_YOY", "CN_TSF_STOCK_YOY",
        "CN_TSF_RMB_LOAN_STOCK_YOY", "CN_TSF_RMB_LOANS_FLOW", "CN_CORP_BOND_FINANCING",
        "CN_GOV_BOND_FINANCING_YTD", "CN_GOV_BOND_FINANCING", "CN_GDP_NOMINAL_YTD",
        "CN_CREDIT_INTENSITY", "CN_CREDIT_IMPULSE", "CN_RE_INVEST_YTD",
        "CN_RE_INVEST_YTD_YOY", "CN_RE_SALES_AREA_YTD", "CN_RE_SALES_AREA_YTD_YOY",
        "CN_RE_SALES_VALUE_YTD", "CN_RE_SALES_VALUE_YTD_YOY", "CN_RE_STARTS_YTD",
        "CN_RE_STARTS_YTD_YOY", "CN_RE_CONSTRUCTION", "CN_RE_CONSTRUCTION_YOY",
        "CN_RE_PRICE_RISING_SHARE", "CN_RE_PRICE_MOM_MEDIAN", "CN_CONSUMER_CONFIDENCE",
        "CN_CONSUMER_SATISFACTION", "CN_CONSUMER_EXPECTATIONS", "CN_ENTERPRISE_BOOM",
    }
)
_NEGATIVE_DIRECTION = frozenset({"CN_IND_INVENTORY_DAYS"})


_NOTES: Mapping[str, str] = {
    "JPYCNY": "每100日元兑换的人民币金额；不是每1日元。",
    "KRWCNY": "每100韩元兑换的人民币金额；不是每1韩元。",
    "CN_GDP": "国家统计局累计实际GDP同比，季度内不是单季同比。",
    "CN_M1_ABS": "2025-01起采用人民银行新M1定义；2024使用官方可比回溯，2023及以前为旧口径，跨断点不可直接比较。",
    "CN_M1_YOY": "2025-01起采用人民银行新M1定义；2024同比已用官方可比值覆盖，2023及以前为旧口径。",
    "CN_M1_MOM": "2025-01起采用人民银行新M1定义；2024-01缺少新口径上月余额，环比不可比且采集时排除。",
    "CN_M1M2": "M1口径自2025-01修订；仅用人民银行回溯后的2024起可比M1构造，处理版本1.1.0。",
    "CN_FAI": "固定资产投资为年内累计同比，跨年直接比较水平变化需谨慎。",
    "CN_RE_CONSTRUCTION": "房屋施工面积为年内累计口径，代码为兼容旧接口未带YTD后缀。",
    "CN_RE_CONSTRUCTION_YOY": "房屋施工面积累计同比，代码为兼容旧接口未带YTD后缀。",
    "CN_CREDIT_INTENSITY": "滚动12个月社融增量除以最近可得的四季度滚动名义GDP。",
    "CN_CREDIT_IMPULSE": "信用强度相对12个月前的变化；单位为百分点。",
    "CN_FISCAL_SPEND_INTENSITY": "季度末广义财政累计支出除以同期累计名义GDP。",
    "CN_FISCAL_IMPULSE_PROXY": "财政支出强度相对上年同期的变化，不等同于结构性财政余额。",
    "US_BASE_ABS": "美国货币基础，不等同于中国M0。",
    "US_M1_ABS": "2020年5月定义调整造成结构断点，不应解释为单月真实暴增。",
    "US_M1_YOY": "跨越2020年5月定义调整的同比存在统计断点。",
    "US_M1_MOM": "2020年5月定义调整造成环比异常，不应用于周期打分。",
    "US_GDP": "当前FRED序列是实际GDP环比折年率，旧名称中的“同比”并不准确。",
    "JP_BOJ_ASSETS": "央行资产负债表规模，用于观察政策扩表，不是货币供应量。",
    "EU_ECB_ASSETS": "欧央行周度金融报表总资产，不是欧元区货币供应量。",
    "KR_RESERVES": "外汇储备不是货币供应量，只能作为外部缓冲指标。",
}


def _invert_groups(groups: Mapping[str, frozenset[str]], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    duplicates: set[str] = set()
    for value, codes in groups.items():
        for code in codes:
            if code in result:
                duplicates.add(code)
            result[code] = value
    if duplicates:
        raise ValueError(f"duplicate {label} classification: {sorted(duplicates)}")
    return result


_SOURCE_BY_CODE = _invert_groups(_SOURCE_GROUPS, "source")


def _frequency(code: str) -> str:
    if code in _DAILY_CODES:
        return "daily"
    if code in _WEEKLY_CODES:
        return "weekly"
    if code in _QUARTERLY_CODES:
        return "quarterly"
    if code in _EVENT_CODES:
        return "event"
    return "monthly"


def _seasonal_adjustment(code: str, category: str) -> str:
    if category in {"index", "forex", "commodity", "bond"} or code in {
        "CN_HOG", "CN_REALESTATE", "CN_ENERGY", "US_FFR", "JP_BOJ", "EU_ECB", "GB_BOE",
        "JP_BOJ_ASSETS", "EU_ECB_ASSETS", "KR_RESERVES",
    }:
        return "not_applicable"
    if code in _SEASONALLY_ADJUSTED:
        return "seasonally_adjusted"
    if code in _NOT_SEASONALLY_ADJUSTED:
        return "not_seasonally_adjusted"
    return "source_reported"


def _measure(code: str, category: str) -> str:
    if code == "CN_IND_INVENTORY_DAYS":
        return "duration"
    if category == "index" or "PMI" in code or code.endswith("_CLI") or code in {
        "CN_REALESTATE", "CN_ENERGY", "CN_HOG", "CN_CONSUMER_CONFIDENCE",
        "CN_CONSUMER_SATISFACTION", "CN_CONSUMER_EXPECTATIONS", "CN_ENTERPRISE_BOOM",
    }:
        return "index"
    if category == "forex":
        return "exchange_rate"
    if category == "commodity":
        return "price"
    if category == "bond":
        return "spread" if code.endswith("10Y2Y") else "rate"
    if code == "US_GDP":
        return "qoq_annualized"
    if code.endswith("_MOM") or code == "CN_RE_PRICE_MOM_MEDIAN":
        return "mom"
    if code.endswith("_YOY") or "CPI" in code or code in {
        "CN_PPI",
        "CN_IP", "CN_GDP", "CN_RETAIL", "CN_FAI", "CN_EXPORTS", "US_IP", "US_GDP",
        "JP_IP", "EU_IP", "EU_GDP", "GB_IP", "GB_GDP", "GB_M3", "KR_IP",
    }:
        return "yoy"
    if code.endswith("_IMPULSE") or code.endswith("_IMPULSE_PROXY") or code == "US_NFP":
        return "change"
    if code.endswith("_INTENSITY") or code == "CN_RE_PRICE_RISING_SHARE":
        return "ratio"
    if code in {"US_FFR", "JP_BOJ", "EU_ECB", "GB_BOE"}:
        return "rate"
    if code == "CN_M1M2":
        return "spread"
    if code == "CN_EXPORTS_ABS":
        return "flow"
    if code.endswith("_ABS") or code.endswith("_ASSETS") or code.endswith("_RESERVES") or code in {
        "CN_RE_CONSTRUCTION", "JP_BOJ_ASSETS", "EU_ECB_ASSETS", "KR_RESERVES",
    }:
        return "stock"
    if "YTD" in code or code in {
        "CN_TSF", "CN_TSF_RMB_LOANS_FLOW", "CN_CORP_BOND_FINANCING",
        "CN_GOV_BOND_FINANCING", "CN_LOCAL_SPECIAL_BOND_ISSUANCE", "CN_EXPORTS_ABS",
    }:
        return "flow"
    return "index"


def _transform(code: str) -> str:
    if code == "US_GDP":
        return "qoq_annualized"
    if code in {"CN_CREDIT_INTENSITY", "CN_FISCAL_SPEND_INTENSITY"}:
        return "rolling_12m_gdp_ratio" if code == "CN_CREDIT_INTENSITY" else "level"
    if code in {"CN_CREDIT_IMPULSE", "CN_FISCAL_IMPULSE_PROXY"}:
        return "year_over_year_difference"
    if code.endswith("_MOM") or code == "CN_RE_PRICE_MOM_MEDIAN":
        return "mom"
    if code.endswith("_YOY") or "CPI" in code or code in {
        "CN_PPI",
        "CN_IP", "CN_GDP", "CN_RETAIL", "CN_FAI", "CN_EXPORTS", "US_IP", "JP_IP",
        "EU_IP", "EU_GDP", "GB_IP", "GB_GDP", "GB_M3", "KR_IP",
    }:
        return "yoy"
    if code in {"CN_M1M2", "CN_10Y2Y", "US_10Y2Y"}:
        return "spread"
    if code == "US_NFP":
        return "difference"
    return "level"


def _aggregation(code: str, category: str, frequency: str) -> str:
    if code in {"CN_CREDIT_INTENSITY", "CN_CREDIT_IMPULSE", "CN_FISCAL_SPEND_INTENSITY", "CN_FISCAL_IMPULSE_PROXY", "CN_M1M2"}:
        return "derived"
    if "YTD" in code or code in _CUMULATIVE_YOY_CODES or code in {"CN_GDP", "CN_FAI", "CN_RE_CONSTRUCTION", "CN_RE_CONSTRUCTION_YOY"}:
        return "cumulative_ytd"
    if code in {"CN_TSF", "CN_TSF_RMB_LOANS_FLOW", "CN_CORP_BOND_FINANCING", "CN_GOV_BOND_FINANCING", "CN_LOCAL_SPECIAL_BOND_ISSUANCE", "CN_EXPORTS_ABS"}:
        return "period_sum"
    if code in {"US_FFR", "JP_BOJ"}:
        return "period_average"
    if code == "EU_ECB":
        return "end_of_period"
    if category in {"index", "forex", "commodity", "bond"} or frequency in {"daily", "weekly", "event"}:
        return "end_of_period"
    if _measure(code, category) == "stock":
        return "end_of_period"
    return "point_in_time"


def _release_lag(code: str, category: str, frequency: str) -> int:
    if frequency in {"daily", "weekly", "event"} or category in {"index", "forex", "commodity", "bond"}:
        return 0
    if "PMI" in code:
        return 0
    if code.endswith("_CLI"):
        return 2
    return 1


def _valid_range(
    code: str, category: str, measure_type: str
) -> tuple[float | None, float | None]:
    """Conservative hard bounds; statistical outliers remain warnings elsewhere."""

    if "PMI" in code or code == "CN_RE_PRICE_RISING_SHARE":
        return 0.0, 100.0
    if code in {
        "CN_CONSUMER_CONFIDENCE",
        "CN_CONSUMER_SATISFACTION",
        "CN_CONSUMER_EXPECTATIONS",
        "CN_ENTERPRISE_BOOM",
    }:
        return 0.0, 200.0
    if category in {"index", "forex"}:
        return 0.000001, None
    if measure_type == "duration":
        return 0.0, 3660.0
    if measure_type == "rate":
        return -20.0, 100.0
    if measure_type in {"yoy", "mom", "qoq_annualized"}:
        return -100.0, 1000.0
    if measure_type == "stock":
        return 0.0, None
    return None, None


def _default_note(code: str, source: str) -> str:
    if source in {"Eastmoney", "FRED", "Jin10/AkShare"}:
        return f"经{source}分发；建模时应结合观测级source_url核验原始机构口径。"
    return "以观测级发布日期、可用时间和来源链接为准。"


def build_indicator_catalog(definitions: Iterable[Mapping[str, Any]]) -> dict[str, IndicatorCatalogEntry]:
    definitions = list(definitions)
    codes = [str(item["code"]) for item in definitions]
    if len(codes) != len(set(codes)):
        raise ValueError("indicator definitions contain duplicate codes")
    missing_sources = set(codes) - set(_SOURCE_BY_CODE)
    stale_sources = set(_SOURCE_BY_CODE) - set(codes)
    if missing_sources or stale_sources:
        raise ValueError(
            f"indicator source catalog mismatch; missing={sorted(missing_sources)}, stale={sorted(stale_sources)}"
        )

    catalog: dict[str, IndicatorCatalogEntry] = {}
    for definition in definitions:
        code = str(definition["code"])
        category = str(definition["category"])
        source = _SOURCE_BY_CODE[code]
        frequency = _frequency(code)
        measure_type = _measure(code, category)
        valid_min, valid_max = _valid_range(code, category, measure_type)
        cumulative = "YTD" in code or code in _CUMULATIVE_YOY_CODES or code in {"CN_GDP", "CN_FAI", "CN_RE_CONSTRUCTION", "CN_RE_CONSTRUCTION_YOY"}
        direction = "positive" if code in _POSITIVE_DIRECTION else "negative" if code in _NEGATIVE_DIRECTION else "contextual"
        catalog[code] = IndicatorCatalogEntry(
            code=code,
            source=source,
            frequency=frequency,
            seasonal_adjustment=_seasonal_adjustment(code, category),
            measure_type=measure_type,
            aggregation=_aggregation(code, category, frequency),
            cumulative=cumulative,
            direction=direction,
            transform=_transform(code),
            release_lag_months=_release_lag(code, category, frequency),
            valid_min=valid_min,
            valid_max=valid_max,
            notes=_NOTES.get(code, _default_note(code, source)),
        )
    validate_indicator_catalog(catalog, codes)
    return catalog


def validate_indicator_catalog(
    catalog: Mapping[str, IndicatorCatalogEntry], expected_codes: Iterable[str]
) -> None:
    expected = set(expected_codes)
    if set(catalog) != expected:
        raise ValueError(
            f"indicator catalog coverage mismatch; missing={sorted(expected - set(catalog))}, stale={sorted(set(catalog) - expected)}"
        )
    for code, entry in catalog.items():
        if not entry.source.strip() or len(entry.source) > 32:
            raise ValueError(f"{code}: invalid source {entry.source!r}")
        if entry.frequency not in VALID_FREQUENCIES:
            raise ValueError(f"{code}: invalid frequency {entry.frequency!r}")
        if entry.seasonal_adjustment not in VALID_SEASONAL_ADJUSTMENTS:
            raise ValueError(f"{code}: invalid seasonal adjustment")
        if entry.measure_type not in VALID_MEASURE_TYPES:
            raise ValueError(f"{code}: invalid measure type")
        if entry.aggregation not in VALID_AGGREGATIONS:
            raise ValueError(f"{code}: invalid aggregation")
        if entry.direction not in VALID_DIRECTIONS:
            raise ValueError(f"{code}: invalid direction")
        if entry.transform not in VALID_TRANSFORMS:
            raise ValueError(f"{code}: invalid transform")
        if entry.release_lag_months < 0:
            raise ValueError(f"{code}: release lag cannot be negative")
        if (
            entry.valid_min is not None
            and entry.valid_max is not None
            and entry.valid_min > entry.valid_max
        ):
            raise ValueError(f"{code}: invalid hard range")
        if not entry.notes.strip():
            raise ValueError(f"{code}: notes cannot be blank")


def enrich_indicator_defs(definitions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy definitions and inject catalog-owned database fields."""

    definitions = list(definitions)
    catalog = build_indicator_catalog(definitions)
    return [
        {
            **definition,
            "source": catalog[str(definition["code"])].source,
            "frequency": catalog[str(definition["code"])].frequency,
        }
        for definition in definitions
    ]


@lru_cache(maxsize=1)
def get_indicator_catalog() -> Mapping[str, IndicatorCatalogEntry]:
    # Lazy import avoids a cycle while indicator_defs itself is being enriched.
    from app.indicator_defs import INDICATOR_DEFS

    return MappingProxyType(build_indicator_catalog(INDICATOR_DEFS))

"""中美宏观经济指标抓取，统一返回 DataFrame(date, value)。

月度指标发布当月数据时通常有一两个月的滞后，这是数据本身的性质，不是本项目的问题。
数据源都实测过，见各函数注释；中国的几个走国家统计局(通过 akshare 封装)，
美国的几个和中美国债收益率走东方财富 datacenter-web 接口
（不是之前连不上的 push2/push2his，是另一个host，本机网络环境下是通的）。
"""

import datetime as dt
import logging
import time

import akshare as ak
import pandas as pd

from app.services.derived_metrics import (
    DERIVED_METRIC_SPECS,
    calculate_spread,
)

from app.fetchers.akshare_source import DATA_FLOOR_DATE, _clean

logger = logging.getLogger(__name__)


def _clean_month_col(
    df: pd.DataFrame, month_col: str, value_col: str, fmt: str = "%Y年%m月份", scale: float = 1.0
) -> pd.DataFrame:
    """处理"2026年07月份"（或 fmt 指定的其它格式，比如日本数据是"2026年07月"）这种月份字符串列。"""
    out = df[[month_col, value_col]].rename(columns={month_col: "date", value_col: "value"})
    out = out.dropna(subset=["value"])
    out["date"] = pd.to_datetime(out["date"], format=fmt, errors="coerce")
    out = out.dropna(subset=["date"])
    out["date"] = out["date"].dt.date
    out = out[out["date"] >= DATA_FLOOR_DATE]
    out["value"] = out["value"].astype(float) * scale
    return out.reset_index(drop=True).sort_values("date").reset_index(drop=True)


def _clean_yyyymm_col(df: pd.DataFrame, month_col: str, value_col: str) -> pd.DataFrame:
    """处理"201501"这种 YYYYMM 字符串格式的列（社融数据用）。"""
    out = df[[month_col, value_col]].rename(columns={month_col: "date", value_col: "value"})
    out = out.dropna(subset=["value"])
    out["date"] = pd.to_datetime(out["date"], format="%Y%m", errors="coerce")
    out = out.dropna(subset=["date"])
    out["date"] = out["date"].dt.date
    out = out[out["date"] >= DATA_FLOOR_DATE]
    out["value"] = out["value"].astype(float)
    return out.reset_index(drop=True).sort_values("date").reset_index(drop=True)


def fetch_cn_cpi() -> pd.DataFrame:
    df = ak.macro_china_cpi()
    return _clean_month_col(df, "月份", "全国-同比增长")


def fetch_cn_ppi() -> pd.DataFrame:
    df = ak.macro_china_ppi()
    return _clean_month_col(df, "月份", "当月同比增长")


def fetch_cn_pmi() -> pd.DataFrame:
    df = ak.macro_china_pmi()
    return _clean_month_col(df, "月份", "制造业-指数")


def fetch_cn_non_manufacturing_pmi() -> pd.DataFrame:
    # 与制造业 PMI 来自同一张国家统计局月表，避免额外请求和商业 PMI 授权问题。
    df = ak.macro_china_pmi()
    return _clean_month_col(df, "月份", "非制造业-指数")


def fetch_cn_retail() -> pd.DataFrame:
    df = ak.macro_china_consumer_goods_retail()
    return _clean_month_col(df, "月份", "同比增长")


def fetch_cn_fai() -> pd.DataFrame:
    df = ak.macro_china_gdzctz()
    return _clean_month_col(df, "月份", "同比增长")


def fetch_cn_exports() -> pd.DataFrame:
    return _clean_month_col(_raw_cn_trade_df(), "月份", "当月出口额-同比增长")


def fetch_cn_exports_abs() -> pd.DataFrame:
    # 海关总署原始单位是千美元，除以 1e5 换算成媒体常用的"亿美元"口径
    return _clean_month_col(_raw_cn_trade_df(), "月份", "当月出口额-金额", scale=1 / 1e5)


_cn_trade_cache: dict = {"df": None, "ts": 0.0}
_CN_TRADE_CACHE_TTL = 60


def _raw_cn_trade_df() -> pd.DataFrame:
    """海关总署月表；出口金额与同比共用一次网络请求。"""
    now = time.time()
    if _cn_trade_cache["df"] is None or now - _cn_trade_cache["ts"] > _CN_TRADE_CACHE_TTL:
        _cn_trade_cache["df"] = ak.macro_china_hgjck()
        _cn_trade_cache["ts"] = now
    return _cn_trade_cache["df"]


def fetch_cn_hog() -> pd.DataFrame:
    # 生猪现货价格指数，周频；是判断"猪周期"最常用的原始价格序列
    df = ak.index_hog_spot_price()
    return _clean(df, "日期", "指数")


_money_supply_cache: dict = {"df": None, "ts": 0.0}
_MONEY_SUPPLY_CACHE_TTL = 60  # M2/M1/M0/剪刀差四个指标共用同一个接口调用，用短TTL缓存合并成一次请求

# 人民银行自 2025-01 起启用新 M1 定义，并只向前提供了 2024 年的
# 可比口径回溯。上游聚合表曾出现“余额已回溯、同比仍是旧口径”的混合状态，
# 所以不能原样用于 M1-M2 剪刀差。这里固定覆盖人民银行公布的 12 个月，
# 同时保存它们真正变得可用的日期；2023 年及以前仍属于旧统计口径。
_CN_M1_2024_COMPARABLE_SOURCE_URL = (
    "https://www.pbc.gov.cn/diaochatongjisi/attachDir/2025/11/"
    "2025111913535682666.pdf"
)
# Official page gives a publication date but no verified intraday timestamp.
# Use end-of-day conservatively so a strict as-of query cannot leak the table
# into the morning of 2025-02-14.
_CN_M1_2024_COMPARABLE_AVAILABLE_AT = dt.datetime(2025, 2, 14, 23, 59, 59)
_CN_M1_2024_COMPARABLE: tuple[tuple[dt.date, float, float], ...] = (
    (dt.date(2024, 1, 1), 1120120.0, 3.3),
    (dt.date(2024, 2, 1), 1093158.0, 2.6),
    (dt.date(2024, 3, 1), 1117433.0, 2.3),
    (dt.date(2024, 4, 1), 1075084.0, 0.6),
    (dt.date(2024, 5, 1), 1064391.0, -0.8),
    (dt.date(2024, 6, 1), 1089170.0, -1.7),
    (dt.date(2024, 7, 1), 1051800.0, -2.6),
    (dt.date(2024, 8, 1), 1049684.0, -3.0),
    (dt.date(2024, 9, 1), 1055410.0, -3.3),
    (dt.date(2024, 10, 1), 1054884.0, -2.3),
    (dt.date(2024, 11, 1), 1076379.0, -0.7),
    (dt.date(2024, 12, 1), 1113069.0, 1.2),
)


def _raw_money_supply_df() -> pd.DataFrame:
    now = time.time()
    if _money_supply_cache["df"] is None or now - _money_supply_cache["ts"] > _MONEY_SUPPLY_CACHE_TTL:
        _money_supply_cache["df"] = ak.macro_china_money_supply()
        _money_supply_cache["ts"] = now
    return _money_supply_cache["df"]


def _with_cn_m1_2024_comparable(
    frame: pd.DataFrame,
    *,
    field: str,
) -> pd.DataFrame:
    """Replace 2024 M1 observations with the PBOC's comparable backcast.

    ``mom`` intentionally starts in February: the new-definition December
    2023 balance was not published, so January 2024 month-on-month growth is
    not observable on a comparable basis.
    """

    rows: list[dict] = []
    previous_balance: float | None = None
    for observation_date, balance, yoy in _CN_M1_2024_COMPARABLE:
        if field == "balance":
            value = balance
        elif field == "yoy":
            value = yoy
        elif field == "mom":
            value = (
                None
                if previous_balance is None
                else (balance / previous_balance - 1.0) * 100.0
            )
        else:
            raise ValueError(f"unsupported M1 comparable field: {field}")
        previous_balance = balance
        if value is None:
            continue
        rows.append(
            {
                "date": observation_date,
                "value": value,
                "release_date": _CN_M1_2024_COMPARABLE_AVAILABLE_AT.date(),
                "available_at": _CN_M1_2024_COMPARABLE_AVAILABLE_AT,
                "source_url": _CN_M1_2024_COMPARABLE_SOURCE_URL,
            }
        )

    base = frame.loc[
        ~pd.to_datetime(frame["date"]).dt.year.eq(2024)
    ].copy()
    return (
        pd.concat([base, pd.DataFrame(rows)], ignore_index=True, sort=False)
        .sort_values("date")
        .reset_index(drop=True)
    )


def fetch_cn_m2_abs() -> pd.DataFrame:
    df = _raw_money_supply_df()
    return _clean_month_col(df, "月份", "货币和准货币(M2)-数量(亿元)")


def fetch_cn_m2_yoy() -> pd.DataFrame:
    df = _raw_money_supply_df()
    return _clean_month_col(df, "月份", "货币和准货币(M2)-同比增长")


def fetch_cn_m2_mom() -> pd.DataFrame:
    df = _raw_money_supply_df()
    return _clean_month_col(df, "月份", "货币和准货币(M2)-环比增长")


def fetch_cn_m1_abs() -> pd.DataFrame:
    df = _raw_money_supply_df()
    return _with_cn_m1_2024_comparable(
        _clean_month_col(df, "月份", "货币(M1)-数量(亿元)"), field="balance"
    )


def fetch_cn_m1_yoy() -> pd.DataFrame:
    df = _raw_money_supply_df()
    return _with_cn_m1_2024_comparable(
        _clean_month_col(df, "月份", "货币(M1)-同比增长"), field="yoy"
    )


def fetch_cn_m1_mom() -> pd.DataFrame:
    df = _raw_money_supply_df()
    return _with_cn_m1_2024_comparable(
        _clean_month_col(df, "月份", "货币(M1)-环比增长"), field="mom"
    )


def fetch_cn_m0_abs() -> pd.DataFrame:
    df = _raw_money_supply_df()
    return _clean_month_col(df, "月份", "流通中的现金(M0)-数量(亿元)")


def fetch_cn_m0_yoy() -> pd.DataFrame:
    df = _raw_money_supply_df()
    return _clean_month_col(df, "月份", "流通中的现金(M0)-同比增长")


def fetch_cn_m0_mom() -> pd.DataFrame:
    df = _raw_money_supply_df()
    return _clean_month_col(df, "月份", "流通中的现金(M0)-环比增长")


def fetch_cn_m1_m2_spread() -> pd.DataFrame:
    # 2024 是当前能够构造新定义 M1 可比剪刀差的最早月份；更早旧口径
    # 不与新口径拼接，以免统计定义切换被模型误判成经济拐点。
    m1 = fetch_cn_m1_yoy().set_index("date")["value"]
    m2 = fetch_cn_m2_yoy().set_index("date")["value"]
    spread = calculate_spread(m1, m2)
    result = spread.rename("value").rename_axis("date").reset_index()
    result = result.loc[result["date"] >= dt.date(2024, 1, 1)].copy()
    comparable_2024 = pd.to_datetime(result["date"]).dt.year.eq(2024)
    result["release_date"] = None
    result["available_at"] = None
    result["source_url"] = None
    result.loc[comparable_2024, "release_date"] = (
        _CN_M1_2024_COMPARABLE_AVAILABLE_AT.date()
    )
    result.loc[comparable_2024, "available_at"] = (
        _CN_M1_2024_COMPARABLE_AVAILABLE_AT
    )
    result.loc[comparable_2024, "source_url"] = _CN_M1_2024_COMPARABLE_SOURCE_URL
    result["status"] = "derived"
    result["formula_version"] = DERIVED_METRIC_SPECS["CN_M1M2"].version
    return result.reset_index(drop=True)


def fetch_cn_real_estate() -> pd.DataFrame:
    # 国房景气指数，全国综合房地产市场冷热程度
    df = ak.macro_china_real_estate()
    return _clean(df, "日期", "最新值")


def fetch_cn_energy() -> pd.DataFrame:
    df = ak.macro_china_energy_index()
    return _clean(df, "日期", "最新值")


def fetch_cn_tsf() -> pd.DataFrame:
    df = ak.macro_china_shrzgm()
    return _clean_yyyymm_col(df, "月份", "社会融资规模增量")


def fetch_us_cpi() -> pd.DataFrame:
    df = ak.macro_usa_cpi_yoy()
    return _clean(df, "时间", "现值")


def fetch_us_nfp() -> pd.DataFrame:
    df = ak.macro_usa_non_farm()
    return _clean(df, "日期", "今值")


def fetch_us_ffr() -> pd.DataFrame:
    df = ak.macro_bank_usa_interest_rate()
    return _clean(df, "日期", "今值")


_bond_cache: dict = {"df": None, "ts": 0.0}
_BOND_CACHE_TTL = 60  # 秒；同一次 refresh 里中美各期限一共要拆 10 个指标，
# 都指向同一个接口调用，用短 TTL 缓存把它们合并成一次网络请求，不然每次刷新要发10倍的请求


def _raw_bond_df() -> pd.DataFrame:
    now = time.time()
    if _bond_cache["df"] is None or now - _bond_cache["ts"] > _BOND_CACHE_TTL:
        _bond_cache["df"] = ak.bond_zh_us_rate()
        _bond_cache["ts"] = now
    return _bond_cache["df"]


def _bond_yield(column: str, floor: dt.date | None = None) -> pd.DataFrame:
    df = _clean(_raw_bond_df(), "日期", column)
    if floor is not None:
        df = df[df["date"] >= floor].reset_index(drop=True)
    return df


# 中国2年/30年期国债收益率在早期数据里不稳定（很可能是当时这两个期限交易稀疏，
# 数据源构建收益率曲线时不断"跳变"——单日能跳1~3个百分点又跳回去，明显不是真实行情），
# 用实测过的稳定起始日期把这段不可靠数据过滤掉，而不是用全局的 DATA_FLOOR_DATE。
_CN_2Y_FLOOR = dt.date(2003, 5, 1)
_CN_30Y_FLOOR = dt.date(2005, 3, 1)


def fetch_cn_2y() -> pd.DataFrame:
    return _bond_yield("中国国债收益率2年", floor=_CN_2Y_FLOOR)


def fetch_cn_5y() -> pd.DataFrame:
    return _bond_yield("中国国债收益率5年")


def fetch_cn_10y() -> pd.DataFrame:
    return _bond_yield("中国国债收益率10年")


def fetch_cn_30y() -> pd.DataFrame:
    return _bond_yield("中国国债收益率30年", floor=_CN_30Y_FLOOR)


def fetch_cn_10y_2y() -> pd.DataFrame:
    # 利差用到2年期，2年期早期数据不可靠，利差同样从那个日期之后才可信
    return _bond_yield("中国国债收益率10年-2年", floor=_CN_2Y_FLOOR)


def fetch_us_2y() -> pd.DataFrame:
    return _bond_yield("美国国债收益率2年")


def fetch_us_5y() -> pd.DataFrame:
    return _bond_yield("美国国债收益率5年")


def fetch_us_10y() -> pd.DataFrame:
    return _bond_yield("美国国债收益率10年")


def fetch_us_30y() -> pd.DataFrame:
    return _bond_yield("美国国债收益率30年")


def fetch_us_10y_2y() -> pd.DataFrame:
    return _bond_yield("美国国债收益率10年-2年")


def fetch_jp_cpi() -> pd.DataFrame:
    df = ak.macro_japan_cpi_yearly()
    return _clean_month_col(df, "时间", "现值", fmt="%Y年%m月")


def fetch_jp_boj() -> pd.DataFrame:
    df = ak.macro_bank_japan_interest_rate()
    return _clean(df, "日期", "今值")


def fetch_eu_cpi() -> pd.DataFrame:
    df = ak.macro_euro_cpi_yoy()
    return _clean(df, "日期", "今值")


def fetch_eu_ecb() -> pd.DataFrame:
    df = ak.macro_bank_euro_interest_rate()
    return _clean(df, "日期", "今值")


def fetch_cn_gdp() -> pd.DataFrame:
    # 国家统计局季度表当前使用“2026年1-2季度 / 2026年1季度”格式。
    # 直接读取表中的GDP累计同比，并把季度定位到对应季度的季末月。
    # “1-2季度”是上半年累计值，观察期属于二季度（6月），不能标成4月。
    df = ak.macro_china_gdp()
    out = df[["季度", "国内生产总值-同比增长"]].rename(
        columns={"季度": "period", "国内生产总值-同比增长": "value"}
    )
    parts = out["period"].astype(str).str.extract(
        r"(?P<year>\d{4})年(?:第)?(?:1-)?(?P<quarter>[1-4])季度"
    )
    out["date"] = pd.to_datetime(
        parts["year"]
        + "-"
        + parts["quarter"].map({"1": "03", "2": "06", "3": "09", "4": "12"}),
        format="%Y-%m",
        errors="coerce",
    )
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["date", "value"])
    out["date"] = out["date"].dt.date
    return (
        out.loc[out["date"] >= DATA_FLOOR_DATE, ["date", "value"]]
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def fetch_us_gdp() -> pd.DataFrame:
    df = ak.macro_usa_gdp_monthly()
    return _clean(df, "日期", "今值")


def fetch_eu_gdp() -> pd.DataFrame:
    df = ak.macro_euro_gdp_yoy()
    return _clean(df, "日期", "今值")


MACRO_FETCHERS = {
    "CN_CPI": fetch_cn_cpi,
    "CN_PPI": fetch_cn_ppi,
    "CN_PMI": fetch_cn_pmi,
    "CN_NMI": fetch_cn_non_manufacturing_pmi,
    "CN_TSF": fetch_cn_tsf,
    "CN_GDP": fetch_cn_gdp,
    "CN_RETAIL": fetch_cn_retail,
    "CN_FAI": fetch_cn_fai,
    "CN_EXPORTS": fetch_cn_exports,
    "CN_EXPORTS_ABS": fetch_cn_exports_abs,
    "CN_HOG": fetch_cn_hog,
    "CN_M2_ABS": fetch_cn_m2_abs,
    "CN_M2_YOY": fetch_cn_m2_yoy,
    "CN_M2_MOM": fetch_cn_m2_mom,
    "CN_M1_ABS": fetch_cn_m1_abs,
    "CN_M1_YOY": fetch_cn_m1_yoy,
    "CN_M1_MOM": fetch_cn_m1_mom,
    "CN_M0_ABS": fetch_cn_m0_abs,
    "CN_M0_YOY": fetch_cn_m0_yoy,
    "CN_M0_MOM": fetch_cn_m0_mom,
    "CN_M1M2": fetch_cn_m1_m2_spread,
    "CN_REALESTATE": fetch_cn_real_estate,
    "CN_ENERGY": fetch_cn_energy,
    "US_CPI": fetch_us_cpi,
    "US_NFP": fetch_us_nfp,
    "US_FFR": fetch_us_ffr,
    "US_GDP": fetch_us_gdp,
    "CN_2Y": fetch_cn_2y,
    "CN_5Y": fetch_cn_5y,
    "CN_10Y": fetch_cn_10y,
    "CN_30Y": fetch_cn_30y,
    "CN_10Y2Y": fetch_cn_10y_2y,
    "US_2Y": fetch_us_2y,
    "US_5Y": fetch_us_5y,
    "US_10Y": fetch_us_10y,
    "US_30Y": fetch_us_30y,
    "US_10Y2Y": fetch_us_10y_2y,
    "JP_CPI": fetch_jp_cpi,
    "JP_BOJ": fetch_jp_boj,
    "EU_ECB": fetch_eu_ecb,
}

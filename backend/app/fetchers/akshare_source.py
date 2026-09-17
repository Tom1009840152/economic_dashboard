"""akshare 数据抓取，每个函数对应一个指标，统一返回 DataFrame(date, value)。

具体接口选型说明（均已在本地实测验证）：
- 上证指数/道琼斯/黄金/原油走的是新浪财经接口，稳定可用
- 东方财富(eastmoney push2/push2his)的全球指数接口在本机网络环境下连不通，故不用
- 汇率走中国银行报价(currency_boc_sina)，该接口按月分页，常规刷新只取最近一段时间，
  更早的历史靠 scripts/backfill_currencies.py 一次性回补，避免每次定时刷新都发几百个请求

各指标免费数据源的历史长度上限（早于此日期该源就没有数据了，不是本项目截断的）：
- SSE 上证指数：1990-12-19 起
- DJI 道琼斯（新浪美股接口）：2004-01-02 起，拿不到更早的
- 汇率（中行报价）：1994 年起，本项目回补到 2000-01-01
- GOLD COMEX黄金（新浪外盘期货）：2016-09-06 起，拿不到更早的
- WTI 原油（新浪外盘期货）：1996-09-06 起

汇率覆盖范围说明：中行牌价只提供 {美元,英镑,欧元,澳门元,泰国铢,菲律宾比索,港币,瑞士法郎,
新加坡元,瑞典克朗,丹麦克朗,挪威克朗,日元,加拿大元,澳大利亚元,新西兰元,韩国元} 这些货币，
按名义GDP排名选取了美元/欧元(代表德法意等欧元区)/日元/英镑/加元/澳元/韩元/瑞士法郎/
瑞典克朗/泰铢/新加坡元/挪威克朗，覆盖不到印度卢比、巴西雷亚尔、俄罗斯卢布——
这几个是GDP靠前但该免费接口没有报价的国家；港币/澳门元因为是地区而非主权国家没有选用。
"""

import datetime as dt
import logging

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)

DATA_FLOOR_DATE = dt.date(2000, 1, 1)
CURRENCY_REFRESH_DAYS = 90  # 常规刷新只需要覆盖上次刷新之后的空档

# code -> (中行牌价的中文符号, 是否按每1单位折算(*0.01)；日元/韩元习惯上按每100单位报价，不折算)
BOC_CURRENCIES: dict[str, tuple[str, float]] = {
    "USDCNY": ("美元", 0.01),
    "EURCNY": ("欧元", 0.01),
    "JPYCNY": ("日元", 1.0),
    "GBPCNY": ("英镑", 0.01),
    "CADCNY": ("加拿大元", 0.01),
    "AUDCNY": ("澳大利亚元", 0.01),
    "KRWCNY": ("韩国元", 1.0),
    "CHFCNY": ("瑞士法郎", 0.01),
    "SEKCNY": ("瑞典克朗", 0.01),
    "THBCNY": ("泰国铢", 0.01),
    "SGDCNY": ("新加坡元", 0.01),
    "NOKCNY": ("挪威克朗", 0.01),
}


def _clean(df: pd.DataFrame, date_col: str, value_col: str, scale: float = 1.0) -> pd.DataFrame:
    out = df[[date_col, value_col]].rename(columns={date_col: "date", value_col: "value"})
    out = out.dropna(subset=["value"])
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out = out[out["date"] >= DATA_FLOOR_DATE]
    out["value"] = out["value"].astype(float) * scale
    return out.reset_index(drop=True)


def fetch_sse() -> pd.DataFrame:
    df = ak.stock_zh_index_daily(symbol="sh000001")
    return _clean(df, "date", "close")


def fetch_dji() -> pd.DataFrame:
    df = ak.index_us_stock_sina(symbol=".DJI")
    return _clean(df, "date", "close")


def fetch_nikkei() -> pd.DataFrame:
    # 新浪这个接口只保留最近约4年数据，比其它指数短，是数据源本身的限制
    df = ak.index_global_hist_sina(symbol="日经225指数")
    return _clean(df, "date", "close")


def fetch_stoxx50() -> pd.DataFrame:
    # 同上，新浪接口只保留最近约4年数据
    df = ak.index_global_hist_sina(symbol="欧洲Stoxx50指数")
    return _clean(df, "date", "close")


def fetch_kospi() -> pd.DataFrame:
    # 同上，新浪接口只保留最近约4年数据
    df = ak.index_global_hist_sina(symbol="首尔综合指数")
    return _clean(df, "date", "close")


def fetch_ftse100() -> pd.DataFrame:
    # 新浪全球指数接口保留最近约4年；名称明确对应英国富时100，而非泛欧洲指数。
    df = ak.index_global_hist_sina(symbol="英国富时100指数")
    return _clean(df, "date", "close")


def _fetch_boc_currency(cn_symbol: str, scale: float) -> pd.DataFrame:
    """常规刷新：只拉最近 CURRENCY_REFRESH_DAYS 天，补历史用 backfill_boc_currency()。"""
    end = dt.date.today()
    start = end - dt.timedelta(days=CURRENCY_REFRESH_DAYS)
    df = ak.currency_boc_sina(
        symbol=cn_symbol,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    # 央行中间价当天可能还没发布，中行折算价每天都有，更适合做每日取值
    return _clean(df, "日期", "中行折算价", scale=scale)


def backfill_boc_currency(cn_symbol: str, scale: float, start_date: dt.date = DATA_FLOOR_DATE) -> pd.DataFrame:
    """一次性回补某个货币的历史，按年分段请求，单年失败可重试且不影响已成功的年份。"""
    end = dt.date.today()
    frames: list[pd.DataFrame] = []
    cursor = start_date
    while cursor <= end:
        chunk_end = min(dt.date(cursor.year, 12, 31), end)
        for attempt in range(3):
            try:
                df = ak.currency_boc_sina(
                    symbol=cn_symbol,
                    start_date=cursor.strftime("%Y%m%d"),
                    end_date=chunk_end.strftime("%Y%m%d"),
                )
                frames.append(df)
                break
            except Exception:
                logger.exception(
                    "backfill %s failed for %s~%s (attempt %d)", cn_symbol, cursor, chunk_end, attempt + 1
                )
        cursor = dt.date(cursor.year + 1, 1, 1)

    if not frames:
        return pd.DataFrame(columns=["date", "value"])
    full = pd.concat(frames, ignore_index=True)
    return _clean(full, "日期", "中行折算价", scale=scale)


def fetch_gold() -> pd.DataFrame:
    df = ak.futures_foreign_hist(symbol="GC")
    return _clean(df, "date", "close")


def fetch_wti() -> pd.DataFrame:
    df = ak.futures_foreign_hist(symbol="CL")
    return _clean(df, "date", "close")


def _make_currency_fetcher(cn_symbol: str, scale: float):
    def _fetch() -> pd.DataFrame:
        return _fetch_boc_currency(cn_symbol, scale)

    return _fetch


def _all_fetchers() -> dict:
    # 延迟导入，避免它们反过来导入本模块时出现循环导入
    from app.fetchers.macro_source import MACRO_FETCHERS
    from app.fetchers.fred_source import FRED_FETCHERS
    from app.fetchers.china_cycle_data import CHINA_CYCLE_FETCHERS
    from app.fetchers.bok_source import BOK_FETCHERS
    from app.fetchers.nbs_cycle import NBS_CYCLE_FETCHERS
    from app.fetchers.oecd_cycle import CYCLE_FETCHERS
    from app.fetchers.uk_source import UK_MACRO_FETCHERS

    return {
        "SSE": fetch_sse,
        "DJI": fetch_dji,
        "NKY": fetch_nikkei,
        "STOXX50": fetch_stoxx50,
        "KOSPI": fetch_kospi,
        "FTSE100": fetch_ftse100,
        "GOLD": fetch_gold,
        "WTI": fetch_wti,
        **{
            code: _make_currency_fetcher(cn_symbol, scale)
            for code, (cn_symbol, scale) in BOC_CURRENCIES.items()
        },
        **MACRO_FETCHERS,
        **CHINA_CYCLE_FETCHERS,
        **FRED_FETCHERS,
        **NBS_CYCLE_FETCHERS,
        **CYCLE_FETCHERS,
        **UK_MACRO_FETCHERS,
        **BOK_FETCHERS,
    }


FETCHERS = _all_fetchers()


def fetch_all() -> dict[str, pd.DataFrame]:
    results: dict[str, pd.DataFrame] = {}
    for code, fn in FETCHERS.items():
        try:
            results[code] = fn()
        except Exception:
            logger.exception("fetch failed for %s", code)
            results[code] = None
    return results

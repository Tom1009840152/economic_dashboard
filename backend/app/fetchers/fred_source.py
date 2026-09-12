"""美联储 FRED（Federal Reserve Economic Data）数据抓取。

这是本项目第一个不走 akshare 的数据源——akshare 完全没有美国货币供给（M1/M2）的接口，
但 FRED 有个不需要 API Key 的公开 CSV 端点，本机网络环境下实测能连通，数据一路到1959年。

统计口径提醒：
- 美国没有官方常规发布的"流通中现金 M0"这个概念，最接近的是"货币基础"
  （Monetary Base = 流通中现金 + 银行在美联储的准备金），比中国 M0 概念略宽，
  这里拿它顶替"美国 M0"的位置，前端词条里会说明这个概念差异，不会含糊过去。
- 美联储在2020年5月重新定义了 M1（把储蓄存款也纳入 M1，此前储蓄存款只算在 M2 里），
  导致 M1 原始数据在 2020-04 到 2020-05 之间出现一次统计口径断层（不是真实的货币暴增），
  这里如实保留原始数据（不删除、不做特殊处理），但会在词条里明确解释这次断层的成因。
"""

import io
import logging
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

_SESSION = requests.Session()
_SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
    ),
)

# 美国"M0/M1/M2"对应的 FRED 官方序列 ID
US_MONEY_SERIES = {
    "BASE": "BOGMBASE",  # 货币基础，本项目里用来顶替"M0"的位置
    "M1": "M1SL",
    "M2": "M2SL",
}

_fred_cache: dict[str, dict] = {}
_FRED_CACHE_TTL = 60


def _fetch_fred_raw(series_id: str) -> pd.DataFrame:
    resp = _SESSION.get(FRED_CSV_URL, params={"id": series_id}, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)


def _cached_fred_raw(series_id: str) -> pd.DataFrame:
    now = time.time()
    entry = _fred_cache.get(series_id)
    if entry is None or now - entry["ts"] > _FRED_CACHE_TTL:
        entry = {"df": _fetch_fred_raw(series_id), "ts": now}
        _fred_cache[series_id] = entry
    return entry["df"]


def _fred_abs(series_id: str) -> pd.DataFrame:
    return _cached_fred_raw(series_id)[["date", "value"]].reset_index(drop=True)


def _fred_yoy(series_id: str) -> pd.DataFrame:
    df = _cached_fred_raw(series_id).copy()
    df["value"] = df["value"].pct_change(12) * 100  # 月度数据，12期前=去年同月
    return df.dropna(subset=["value"])[["date", "value"]].reset_index(drop=True)


def _fred_mom(series_id: str) -> pd.DataFrame:
    df = _cached_fred_raw(series_id).copy()
    df["value"] = df["value"].pct_change(1) * 100
    return df.dropna(subset=["value"])[["date", "value"]].reset_index(drop=True)


def _fred_monthly_last(series_id: str) -> pd.DataFrame:
    """把日频政策利率压成月末观测，避免数据库重复保存大量不变值。"""
    df = _cached_fred_raw(series_id).copy()
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M")
    out = df.groupby("month", as_index=False).tail(1).copy()
    out["date"] = out["month"].dt.to_timestamp(how="start").dt.date
    return out[["date", "value"]].sort_values("date").reset_index(drop=True)


def _fetch_us_nfp_change() -> pd.DataFrame:
    """美国非农就业月增量；PAYEMS 原始单位为千人，这里换算为万人。"""
    df = _cached_fred_raw("PAYEMS").copy()
    df["value"] = df["value"].diff() / 10
    return df.dropna(subset=["value"])[["date", "value"]].reset_index(drop=True)


def _make_fetcher(series_id: str, view: str):
    fn = {"ABS": _fred_abs, "YOY": _fred_yoy, "MOM": _fred_mom}[view]

    def _fetch() -> pd.DataFrame:
        return fn(series_id)

    return _fetch


FRED_FETCHERS = {
    f"US_{group}_{view}": _make_fetcher(series_id, view)
    for group, series_id in US_MONEY_SERIES.items()
    for view in ("ABS", "YOY", "MOM")
}

# 这些核心指标原先依赖 akshare 的财经日历接口。该接口只保留较短历史且会因上游
# 字段变化失效，改用 FRED 的官方来源序列，保证历史口径稳定并持续更新。
FRED_FETCHERS["US_NFP"] = _fetch_us_nfp_change
FRED_FETCHERS["US_FFR"] = _make_fetcher("FEDFUNDS", "ABS")
FRED_FETCHERS["US_GDP"] = _make_fetcher("A191RL1Q225SBEA", "ABS")
FRED_FETCHERS["JP_BOJ"] = _make_fetcher("IRSTCI01JPM156N", "ABS")
FRED_FETCHERS["EU_ECB"] = lambda: _fred_monthly_last("ECBMRRFR")

# 日本央行总资产（资产负债表规模）。日本的M1/M2在FRED上虽然查得到，
# 但数据源（OECD转载）已经停止更新（M1停在2023年11月，M2停在2017年2月，
# 相当于死数据），放弃使用；这个总资产序列是唯一还在持续更新的日本央行相关规模指标，
# 原始单位是"亿日元"，跟真实的日本央行资产负债表规模（数百万亿日元级别）吻合。
FRED_FETCHERS["JP_BOJ_ASSETS"] = _make_fetcher("JPNASSETS", "ABS")

# 欧元区同样没有能持续更新的M1/M2：FRED上 MANMM101EZM189S（M1）停在2023年11月，
# MYAGM2EZM189S（M2）根本查不到（404），月度口径的欧央行总资产 ECBASSETS 也已停更
# （停在2020年1月）。唯一还在正常更新的是欧央行"周度金融报表"里的总资产
# 序列 ECBASSETSW，每周五更新，原始单位是"百万欧元"，用它顶替观察欧央行的货币扩张力度。
FRED_FETCHERS["EU_ECB_ASSETS"] = _make_fetcher("ECBASSETSW", "ABS")

# 2026年欧洲栏目统一改为“欧元区”。通胀使用Eurostat经FRED分发的“随成员变化”
# 欧元区序列，而不是固定19国序列；原始指数在这里转换为同比。
FRED_FETCHERS["EU_CPI"] = _make_fetcher("CP0000EZCCM086NEST", "YOY")

# 英国使用独立的英国序列：OECD综合领先指标与广义货币M3同比。
# M3序列本身已经是同比增速，不能再次做pct_change。
FRED_FETCHERS["GB_CLI"] = _make_fetcher("GBRLOLITOAASTSAM", "ABS")
FRED_FETCHERS["GB_M3"] = _make_fetcher("GBRMABMM301GYSAM", "ABS")

# 韩国的情况比日本、欧元区更差：FRED上M1（MANMM101KRM189S）停在2023年10月，
# M2（MYAGM2KRM189S）停在2017年5月；连"央行总资产"这类替代指标都没找到能持续更新的
# ——唯一查到的"央行总资产/GDP"（DDDI06KRA156NWDB）是年度数据且停在2021年，同样是死数据。
# 韩国唯一还在新鲜更新的央行相关序列，是"外汇储备"（Reserves Excluding Gold），
# 但这个概念上是外储规模，不是货币供给/央行扩表——展示时前端词条会明确写明这一点，
# 不会把它包装成"韩国的M0/M1/M2替代品"来误导。原始单位是"百万美元"。
FRED_FETCHERS["KR_RESERVES"] = _make_fetcher("TRESEGKRM052N", "ABS")

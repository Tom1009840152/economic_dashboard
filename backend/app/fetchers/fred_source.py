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

logger = logging.getLogger(__name__)

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# 美国"M0/M1/M2"对应的 FRED 官方序列 ID
US_MONEY_SERIES = {
    "BASE": "BOGMBASE",  # 货币基础，本项目里用来顶替"M0"的位置
    "M1": "M1SL",
    "M2": "M2SL",
}

_fred_cache: dict[str, dict] = {}
_FRED_CACHE_TTL = 60


def _fetch_fred_raw(series_id: str) -> pd.DataFrame:
    resp = requests.get(FRED_CSV_URL, params={"id": series_id}, timeout=30)
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

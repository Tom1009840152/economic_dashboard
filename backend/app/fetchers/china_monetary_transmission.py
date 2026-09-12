"""Transparent China monetary-policy and credit-transmission diagnostics.

The dashboard intentionally keeps three signals separate instead of collapsing
them into an opaque score.  Policy rates are month-end settings, FDR007 is the
official DR007 fixing proxy, and credit impulse is a scale-adjusted change in
the rolling social-financing flow.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

import akshare as ak
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataPoint


_CACHE_TTL = 6 * 60 * 60
_cache: tuple[float, dict[str, Any]] | None = None

# Month-end policy-rate settings.  The 2024 Q3 PBOC report documents both
# 2024 changes; the PBOC 2025 monetary-policy chronicle documents the May cut.
# A baseline point is included so the monthly real-rate series starts cleanly.
POLICY_RATE_CHANGES = (
    (date(2024, 1, 1), 1.80),
    (date(2024, 7, 22), 1.70),
    (date(2024, 9, 27), 1.50),
    (date(2025, 5, 8), 1.40),
)


def _database_series(db: Session, code: str) -> pd.Series:
    rows = db.execute(
        select(DataPoint.date, DataPoint.value)
        .where(DataPoint.indicator_code == code)
        .order_by(DataPoint.date)
    ).all()
    if not rows:
        raise ValueError(f"{code} has no stored observations")
    series = pd.Series(
        [float(row.value) for row in rows],
        index=pd.to_datetime([row.date for row in rows]),
        dtype="float64",
    )
    series.index = series.index.to_period("M")
    return series.groupby(level=0).last().sort_index()


def _policy_rate(periods: pd.PeriodIndex) -> pd.Series:
    changes = [(pd.Timestamp(day), rate) for day, rate in POLICY_RATE_CHANGES]
    values = []
    for period in periods:
        month_end = period.to_timestamp(how="end")
        applicable = [rate for change_date, rate in changes if change_date <= month_end]
        values.append(applicable[-1] if applicable else float("nan"))
    return pd.Series(values, index=periods, dtype="float64")


def _fetch_fdr007() -> tuple[pd.Series, str]:
    frame = ak.repo_rate_query(symbol="银银间回购定盘利率").copy()
    if frame.empty or "date" not in frame or "FDR007" not in frame:
        raise ValueError("FDR007 source returned no usable observations")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["FDR007"] = pd.to_numeric(frame["FDR007"], errors="coerce")
    frame = frame.dropna(subset=["date", "FDR007"]).sort_values("date")
    if frame.empty:
        raise ValueError("FDR007 source returned no numeric observations")
    last_day = frame["date"].iloc[-1].date().isoformat()
    frame["period"] = frame["date"].dt.to_period("M")
    return frame.groupby("period")["FDR007"].mean().sort_index(), last_day


def _fetch_nominal_gdp_ttm() -> pd.Series:
    frame = ak.macro_china_gdp().copy()
    if frame.empty:
        raise ValueError("GDP source returned no observations")

    period_column = frame.columns[0]
    value_column = next(
        (column for column in frame.columns if "国内生产总值" in str(column) and "绝对值" in str(column)),
        frame.columns[1],
    )
    cumulative: dict[tuple[int, int], float] = {}
    for raw_period, raw_value in zip(frame[period_column], frame[value_column]):
        match = re.search(r"(\d{4}).*?([1-4])季度", str(raw_period))
        if not match:
            continue
        value = pd.to_numeric(raw_value, errors="coerce")
        if pd.isna(value):
            continue
        cumulative[(int(match.group(1)), int(match.group(2)))] = float(value)

    quarterly: dict[pd.Period, float] = {}
    for (year, quarter), value in sorted(cumulative.items()):
        previous = cumulative.get((year, quarter - 1), 0.0) if quarter > 1 else 0.0
        individual = value - previous
        if individual > 0:
            quarterly[pd.Period(year=year, quarter=quarter, freq="Q-DEC")] = individual
    if len(quarterly) < 4:
        raise ValueError("GDP source has insufficient quarterly observations")
    return pd.Series(quarterly, dtype="float64").sort_index().rolling(4, min_periods=4).sum().dropna()


def _credit_metrics(tsf: pd.Series, gdp_ttm: pd.Series) -> tuple[pd.Series, pd.Series]:
    rolling_tsf = tsf.rolling(12, min_periods=12).sum()
    ratios: dict[pd.Period, float] = {}
    for month, flow in rolling_tsf.dropna().items():
        completed_quarters = gdp_ttm[gdp_ttm.index.end_time <= month.end_time]
        if completed_quarters.empty:
            continue
        ratios[month] = float(flow / completed_quarters.iloc[-1] * 100)
    ratio = pd.Series(ratios, dtype="float64").sort_index()
    impulse = ratio - ratio.shift(12)
    return ratio.dropna(), impulse.dropna()


def _points(series: pd.Series, digits: int = 3) -> list[dict[str, Any]]:
    return [
        {"period": str(period), "value": round(float(value), digits)}
        for period, value in series.dropna().items()
    ]


def _real_rate_state(value: float) -> tuple[str, str]:
    if value <= 0:
        return "宽松", "核心通胀高于政策利率，事后实际融资基准偏低。"
    if value <= 1:
        return "中性偏宽", "实际政策利率为正但不高，价格维度对需求的约束有限。"
    return "偏紧", "实际政策利率处于较高水平，价格维度对需求形成约束。"


def _liquidity_state(value_bps: float) -> tuple[str, str]:
    if value_bps < -10:
        return "偏松", "银行间7天资金价格低于政策利率，短端流动性相对宽裕。"
    if value_bps <= 10:
        return "平稳", "银行间7天资金价格贴近政策利率，流动性传导基本顺畅。"
    return "偏紧", "银行间7天资金价格高于政策利率，短端存在阶段性收紧。"


def _credit_state(value: float) -> tuple[str, str]:
    if value > 1:
        return "明显增强", "新增信用相对经济体量加速扩张，信用对后续需求形成正向推动。"
    if value > 0:
        return "边际改善", "新增信用相对经济体量温和回升，宽货币正在向宽信用传递。"
    if value >= -1:
        return "边际走弱", "新增信用相对经济体量小幅回落，实体信用传导仍偏弱。"
    return "明显收缩", "新增信用相对经济体量下降较快，信用对需求构成拖累。"


def fetch_china_monetary_transmission(db: Session) -> dict[str, Any]:
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]

    core_cpi = _database_series(db, "CN_CORE_CPI")
    tsf = _database_series(db, "CN_TSF")
    fdr007, fdr_last_day = _fetch_fdr007()
    gdp_ttm = _fetch_nominal_gdp_ttm()

    real_periods = core_cpi.index[core_cpi.index >= pd.Period("2024-01", freq="M")]
    policy_for_real = _policy_rate(real_periods)
    real_rate = policy_for_real - core_cpi.reindex(real_periods)

    policy_for_liquidity = _policy_rate(fdr007.index)
    liquidity_gap_bps = (fdr007 - policy_for_liquidity) * 100

    credit_ratio, credit_impulse = _credit_metrics(tsf, gdp_ttm)
    if real_rate.dropna().empty or liquidity_gap_bps.dropna().empty or credit_impulse.empty:
        raise ValueError("insufficient observations for monetary-transmission diagnostics")

    latest_real = float(real_rate.dropna().iloc[-1])
    latest_gap = float(liquidity_gap_bps.dropna().iloc[-1])
    latest_impulse = float(credit_impulse.iloc[-1])
    real_state, real_text = _real_rate_state(latest_real)
    liquidity_state, liquidity_text = _liquidity_state(latest_gap)
    credit_state, credit_text = _credit_state(latest_impulse)

    if latest_impulse > 0 and latest_gap <= 10:
        status = "宽货币正向信用传导"
        tone = "positive"
    elif latest_impulse < 0 and latest_gap > 10:
        status = "流动性与信用同步承压"
        tone = "caution"
    else:
        status = "传导信号仍有分化"
        tone = "neutral"

    summary = (
        f"实际政策利率{real_state}，银行间流动性{liquidity_state}；"
        f"信用脉冲{credit_state}。三项信号分开判断，不合成为黑箱分数。"
    )
    result = {
        "region": "CN",
        "country": "中国",
        "title": "货币政策与信用传导",
        "status": status,
        "tone": tone,
        "summary": summary,
        "as_of": fdr_last_day,
        "signals": [
            {
                "key": "real_policy_rate",
                "name": "事后实际政策利率",
                "value": round(latest_real, 2),
                "unit": "%",
                "period": str(real_rate.dropna().index[-1]),
                "state": real_state,
                "interpretation": real_text,
                "formula": "7天逆回购利率 − 核心CPI同比",
            },
            {
                "key": "liquidity_gap",
                "name": "流动性偏离（代理）",
                "value": round(latest_gap, 1),
                "unit": "bp",
                "period": str(liquidity_gap_bps.dropna().index[-1]),
                "state": liquidity_state,
                "interpretation": liquidity_text,
                "formula": "FDR007月均 − 7天逆回购利率",
            },
            {
                "key": "credit_impulse",
                "name": "信用脉冲",
                "value": round(latest_impulse, 2),
                "unit": "pp",
                "period": str(credit_impulse.index[-1]),
                "state": credit_state,
                "interpretation": credit_text,
                "formula": "滚动12个月社融/GDP − 一年前该比重",
            },
        ],
        "series": [
            {
                "key": "policy_rate",
                "name": "7天逆回购利率（月末）",
                "unit": "%",
                "points": _points(_policy_rate(real_periods), 2),
            },
            {
                "key": "core_cpi",
                "name": "核心CPI同比",
                "unit": "%",
                "points": _points(core_cpi.reindex(real_periods), 2),
            },
            {
                "key": "real_policy_rate",
                "name": "事后实际政策利率",
                "unit": "%",
                "points": _points(real_rate, 2),
            },
            {
                "key": "fdr007",
                "name": "FDR007月均",
                "unit": "%",
                "points": _points(fdr007, 3),
            },
            {
                "key": "liquidity_policy_rate",
                "name": "同期7天逆回购利率",
                "unit": "%",
                "points": _points(policy_for_liquidity, 2),
            },
            {
                "key": "liquidity_gap",
                "name": "流动性偏离",
                "unit": "bp",
                "points": _points(liquidity_gap_bps, 1),
            },
            {
                "key": "credit_gdp_ratio",
                "name": "滚动12个月社融/GDP",
                "unit": "%",
                "points": _points(credit_ratio, 2),
            },
            {
                "key": "credit_impulse",
                "name": "信用脉冲",
                "unit": "pp",
                "points": _points(credit_impulse, 2),
            },
        ],
        "sources": [
            {
                "name": "中国人民银行货币政策执行报告",
                "url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125227/125957/5347949/afbfa5df25ee45889d916a2819b60a43/2024110815410752868.pdf",
                "description": "7天逆回购政策利率定位与2024年调整记录。",
            },
            {
                "name": "中国人民银行2025年前三季度货币政策大事记",
                "url": "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/5896228/index.html",
                "description": "2025年5月政策利率由1.50%降至1.40%的官方记录。",
            },
            {
                "name": "中国外汇交易中心 FDR 定盘利率",
                "url": "https://www.chinamoney.com.cn/chinese/bkfrr/",
                "description": "FDR007由上午DR007成交样本计算，用作可稳定获取的DR007代理。",
            },
            {
                "name": "国家统计局",
                "url": "https://data.stats.gov.cn/",
                "description": "核心CPI同比与季度名义GDP。",
            },
            {
                "name": "中国人民银行社会融资规模",
                "url": "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html",
                "description": "月度社会融资规模增量。",
            },
        ],
        "warnings": [
            "实际利率使用当期核心CPI，是事后实际政策利率，不等同于使用通胀预期的前瞻实际利率。",
            f"FDR007为DR007定盘代理；最新月份是截至 {fdr_last_day} 的月内均值，并非完整月均。",
            f"信用脉冲最新至 {credit_impulse.index[-1]}，受社融与GDP发布时滞、口径调整和季节性影响。",
        ],
    }
    _cache = (now, result)
    return result

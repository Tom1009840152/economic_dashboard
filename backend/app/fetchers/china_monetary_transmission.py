"""Transparent China monetary-policy and credit-transmission diagnostics.

The dashboard intentionally keeps three signals separate instead of collapsing
them into an opaque score.  Policy rates are month-end settings, FDR007 is the
official DR007 fixing proxy, and credit impulse is a scale-adjusted change in
the rolling social-financing flow.
"""

from __future__ import annotations

import time
from typing import Any

import akshare as ak
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DataPoint
from app.services.derived_metrics import (
    DERIVED_METRIC_SPECS,
    PBOC_7D_REVERSE_REPO,
    calculate_credit_metrics,
    policy_rate_for_periods,
)


_CACHE_TTL = 6 * 60 * 60
_cache: tuple[float, tuple[tuple[str, object], ...], dict[str, Any]] | None = None

_CACHE_INPUT_CODES = (
    "CN_CORE_CPI",
    "CN_TSF",
    "CN_GDP_NOMINAL_YTD",
    "CN_CREDIT_INTENSITY",
    "CN_CREDIT_IMPULSE",
)


def _database_signature(db: Session) -> tuple[tuple[str, object], ...]:
    rows = db.execute(
        select(DataPoint.indicator_code, func.max(DataPoint.retrieved_at))
        .where(DataPoint.indicator_code.in_(_CACHE_INPUT_CODES))
        .group_by(DataPoint.indicator_code)
        .order_by(DataPoint.indicator_code)
    ).all()
    return tuple((row.indicator_code, row[1]) for row in rows)


def _database_series(
    db: Session,
    code: str,
    *,
    formula_version: str | None = None,
) -> pd.Series:
    query = select(DataPoint.date, DataPoint.value).where(
        DataPoint.indicator_code == code
    )
    if formula_version is not None:
        query = query.where(DataPoint.formula_version == formula_version)
    rows = db.execute(query.order_by(DataPoint.date)).all()
    if not rows:
        raise ValueError(f"{code} has no stored observations")
    series = pd.Series(
        [float(row.value) for row in rows],
        index=pd.to_datetime([row.date for row in rows]),
        dtype="float64",
    )
    series.index = series.index.to_period("M")
    return series.groupby(level=0).last().sort_index()


def _fetch_fdr007() -> tuple[pd.Series, pd.Series, str]:
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
    daily_periods = pd.PeriodIndex(frame["date"], freq="D")
    frame["policy_rate"] = policy_rate_for_periods(daily_periods).to_numpy()
    grouped = frame.groupby("period")
    return (
        grouped["FDR007"].mean().sort_index(),
        grouped["policy_rate"].mean().sort_index(),
        last_day,
    )


def _credit_metrics_from_database(db: Session) -> tuple[pd.Series, pd.Series, str]:
    """Prefer the versioned stored outputs; use the same canonical formula as fallback."""

    try:
        intensity = _database_series(
            db,
            "CN_CREDIT_INTENSITY",
            formula_version=DERIVED_METRIC_SPECS["CN_CREDIT_INTENSITY"].version,
        )
        impulse = _database_series(
            db,
            "CN_CREDIT_IMPULSE",
            formula_version=DERIVED_METRIC_SPECS["CN_CREDIT_IMPULSE"].version,
        )
        tsf_latest = db.scalar(
            select(func.max(DataPoint.date)).where(DataPoint.indicator_code == "CN_TSF")
        )
        gdp_latest = db.scalar(
            select(func.max(DataPoint.date)).where(
                DataPoint.indicator_code == "CN_GDP_NOMINAL_YTD"
            )
        )
        latest_input_period = (
            pd.Period(tsf_latest, freq="M") if tsf_latest is not None else None
        )
        if (
            not intensity.empty
            and not impulse.empty
            and gdp_latest is not None
            and intensity.index[-1] == latest_input_period
            and impulse.index[-1] == latest_input_period
        ):
            return intensity, impulse, "stored"
    except ValueError:
        pass

    tsf = _database_series(db, "CN_TSF")
    nominal_gdp_ytd = _database_series(db, "CN_GDP_NOMINAL_YTD")
    calculated_intensity, calculated_impulse = calculate_credit_metrics(
        tsf, nominal_gdp_ytd
    )
    if calculated_intensity.empty or calculated_impulse.empty:
        raise ValueError("stored inputs are insufficient for canonical credit impulse")
    return calculated_intensity, calculated_impulse, "calculated_fallback"


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
    signature = _database_signature(db)
    if _cache and now - _cache[0] < _CACHE_TTL and _cache[1] == signature:
        return _cache[2]

    core_cpi = _database_series(db, "CN_CORE_CPI")
    fdr007, policy_for_liquidity, fdr_last_day = _fetch_fdr007()

    real_periods = core_cpi.index[core_cpi.index >= pd.Period("2024-01", freq="M")]
    policy_for_real = policy_rate_for_periods(real_periods)
    real_rate = policy_for_real - core_cpi.reindex(real_periods)

    liquidity_gap_bps = (fdr007 - policy_for_liquidity) * 100

    credit_ratio, credit_impulse, credit_origin = _credit_metrics_from_database(db)
    if real_rate.dropna().empty or liquidity_gap_bps.dropna().empty or credit_impulse.empty:
        raise ValueError("insufficient observations for monetary-transmission diagnostics")

    latest_real = float(real_rate.dropna().iloc[-1])
    latest_gap = float(liquidity_gap_bps.dropna().iloc[-1])
    latest_impulse = float(credit_impulse.iloc[-1])
    real_state, real_text = _real_rate_state(latest_real)
    liquidity_state, liquidity_text = _liquidity_state(latest_gap)
    credit_state, credit_text = _credit_state(latest_impulse)

    policy_stale = (
        pd.Timestamp(fdr_last_day).date() > PBOC_7D_REVERSE_REPO.verified_through
    )
    if latest_impulse > 0 and latest_gap <= 10:
        status = "宽货币正向信用传导"
        tone = "positive"
    elif latest_impulse < 0 and latest_gap > 10:
        status = "流动性与信用同步承压"
        tone = "caution"
    else:
        status = "传导信号仍有分化"
        tone = "neutral"

    if policy_stale:
        status = "政策利率日程待核验"
        tone = "caution"

    summary = (
        f"实际政策利率{real_state}，银行间流动性{liquidity_state}；"
        f"信用脉冲{credit_state}。三项信号分开判断，不合成为黑箱分数。"
    )
    if policy_stale:
        summary = (
            f"政策利率相关判断仅核验至 {PBOC_7D_REVERSE_REPO.verified_through.isoformat()}；"
            f"信用脉冲{credit_state}。更新政策日程前不输出当前综合松紧结论。"
        )
    result = {
        "region": "CN",
        "country": "中国",
        "title": "货币政策与信用传导",
        "status": status,
        "tone": tone,
        "summary": summary,
        "as_of": min(
            pd.Timestamp(fdr_last_day).date(), PBOC_7D_REVERSE_REPO.verified_through
        ).isoformat(),
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
                "formula": "FDR007与同日7天逆回购利率之差的月均值",
            },
            {
                "key": "credit_impulse",
                "name": "信用脉冲",
                "value": round(latest_impulse, 2),
                "unit": "pp",
                "period": str(credit_impulse.index[-1]),
                "state": credit_state,
                "interpretation": credit_text,
                "formula": DERIVED_METRIC_SPECS["CN_CREDIT_IMPULSE"].formula,
                "formula_version": DERIVED_METRIC_SPECS["CN_CREDIT_IMPULSE"].version,
                "data_origin": credit_origin,
            },
        ],
        "series": [
            {
                "key": "policy_rate",
                "name": "7天逆回购利率（月末）",
                "unit": "%",
                "points": _points(policy_rate_for_periods(real_periods), 2),
                "maintenance": PBOC_7D_REVERSE_REPO.maintenance,
                "verified_through": PBOC_7D_REVERSE_REPO.verified_through.isoformat(),
                "source": PBOC_7D_REVERSE_REPO.source_name,
                "source_url": PBOC_7D_REVERSE_REPO.source_url,
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
                "name": "同期7天逆回购利率（日度对齐月均）",
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
                "name": PBOC_7D_REVERSE_REPO.source_name,
                "url": PBOC_7D_REVERSE_REPO.source_url,
                "description": (
                    "人工维护的7天期逆回购利率日程；"
                    f"目录更新于 {PBOC_7D_REVERSE_REPO.catalog_updated_at.isoformat()}，"
                    f"当前核验截至 {PBOC_7D_REVERSE_REPO.verified_through.isoformat()}。"
                ),
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
            (
                "政策利率来自人工维护日程，不是动态行情；"
                f"最后核验至 {PBOC_7D_REVERSE_REPO.verified_through.isoformat()}，"
                "超过该日期的政策利率、实际利率与流动性偏离不参与当前判断。"
            ),
            f"FDR007为DR007定盘代理；最新月份是截至 {fdr_last_day} 的月内均值，并非完整月均。",
            (
                f"信用脉冲最新至 {credit_impulse.index[-1]}，来自"
                f"{'版本化存库序列' if credit_origin == 'stored' else '存库原始数据的即时兜底计算'}；"
                "受社融与GDP发布时滞、口径调整和季节性影响。"
            ),
        ],
    }
    _cache = (now, signature, result)
    return result

"""任意两个货币之间的换算：所有汇率数据都是"1外币=X人民币"，
以人民币为枢纽做交叉换算，不用再单独维护每一对货币的数据。
"""

from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fetchers.akshare_source import BOC_CURRENCIES
from app.models import DataPoint

CURRENCY_NAMES: dict[str, str] = {"CNY": "人民币"}
for _indicator_code, (_cn_name, _scale) in BOC_CURRENCIES.items():
    CURRENCY_NAMES[_indicator_code[:-3]] = _cn_name

CURRENCY_ORDER: list[str] = ["CNY"] + [code[:-3] for code in BOC_CURRENCIES]


def list_currencies() -> list[dict]:
    return [{"code": code, "name": CURRENCY_NAMES[code]} for code in CURRENCY_ORDER]


def _to_cny_series(db: Session, currency: str) -> dict[date_type, float]:
    """currency 对人民币的每日汇率，统一换算成"1 currency = X CNY"。

    CNY 自身返回空字典，由调用方按常数 1.0 处理。日元/韩元入库时按每100单位存储
    （见 akshare_source.BOC_CURRENCIES 的 scale），这里要换算回每1单位，
    否则和其它按1单位存储的货币直接相除会差100倍。
    """
    if currency == "CNY":
        return {}
    indicator_code = f"{currency}CNY"
    _, ingest_scale = BOC_CURRENCIES[indicator_code]
    per_unit_factor = 1 / (ingest_scale * 100)
    rows = db.execute(
        select(DataPoint.date, DataPoint.value).where(DataPoint.indicator_code == indicator_code)
    ).all()
    return {row.date: float(row.value) * per_unit_factor for row in rows}


def cross_rate_history(db: Session, base: str, target: str) -> list[tuple[date_type, float]]:
    """返回 1 base = X target 的历史序列，按人民币做枢纽换算对齐日期。"""
    if base == target:
        return []

    base_map = _to_cny_series(db, base)
    target_map = _to_cny_series(db, target)

    if base == "CNY":
        dates = sorted(target_map.keys())
        base_map = {d: 1.0 for d in dates}
    elif target == "CNY":
        dates = sorted(base_map.keys())
        target_map = {d: 1.0 for d in dates}
    else:
        dates = sorted(set(base_map) & set(target_map))

    points: list[tuple[date_type, float]] = []
    for d in dates:
        b = base_map.get(d)
        t = target_map.get(d)
        if b is not None and t:
            points.append((d, b / t))
    return points

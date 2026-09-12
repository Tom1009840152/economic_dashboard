from datetime import date
from statistics import median

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DataPoint, DataPointVintage, Indicator
from app.schemas import DataPointOut, DataPointVintageOut, IndicatorHistory, IndicatorSummary
from app.services.indicator_service import refresh_all_indicators

router = APIRouter(prefix="/api", tags=["indicators"])


def _freshness(points: list[DataPoint], code: str) -> tuple[str, str]:
    """按指标自身发布频率判断数据是否明显滞后。"""
    if not points:
        return "missing", "等待数据更新"
    if code == "GB_BOE":
        # 英格兰银行利率历史只在决议改变利率时新增记录。
        return "event", "按决议更新"

    recent_dates = [point.date for point in points[-13:]]
    gaps = [(right - left).days for left, right in zip(recent_dates, recent_dates[1:])]
    typical_gap = median(gaps) if gaps else 30
    if typical_gap <= 3:
        current_limit = 10
    elif typical_gap <= 10:
        current_limit = 21
    elif typical_gap <= 45:
        current_limit = 90
    elif typical_gap <= 135:
        current_limit = 190
    else:
        current_limit = 550

    age = max((date.today() - points[-1].date).days, 0)
    if age <= current_limit:
        return "current", "数据正常"
    if age <= current_limit * 2:
        return "delayed", "更新偏慢"
    return "stale", "数据陈旧"


@router.get("/indicators", response_model=list[IndicatorSummary])
def list_indicators(
    region: str | None = Query(default=None, description="按国家/地区筛选：CN/US/JP/GLOBAL"),
    include_hidden: bool = Query(default=False, description="包含仅供分析模型使用的底层指标"),
    db: Session = Depends(get_db),
):
    query = select(Indicator).order_by(Indicator.sort_order)
    if region:
        query = query.where(Indicator.region == region)
    if not include_hidden:
        query = query.where(Indicator.is_visible.is_(True))
    indicators = db.execute(query).scalars().all()
    summaries = []
    for ind in indicators:
        points = sorted(ind.data_points, key=lambda p: p.date)
        latest = points[-1] if points else None
        prev = points[-2] if len(points) > 1 else None
        change_pct = None
        if latest and prev:
            if ind.unit == "%":
                # 本身已经是百分比的指标（CPI/PPI/利率/收益率），涨跌幅显示成"变动了多少个百分点"，
                # 不要再算"百分比的百分比变化"——那样在数值接近0时会剧烈失真、容易误导
                change_pct = float(latest.value - prev.value)
            elif prev.value:
                change_pct = float((latest.value - prev.value) / prev.value * 100)
        freshness, freshness_label = _freshness(points, ind.code)
        summaries.append(
            IndicatorSummary(
                code=ind.code,
                name=ind.name,
                category=ind.category,
                region=ind.region,
                unit=ind.unit,
                latest_date=latest.date if latest else None,
                latest_value=float(latest.value) if latest else None,
                change_pct=change_pct,
                recent_values=[float(p.value) for p in points[-30:]],
                freshness=freshness,
                freshness_label=freshness_label,
            )
        )
    return summaries


@router.get("/indicators/{code}/history", response_model=IndicatorHistory)
def get_history(
    code: str,
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    indicator = db.get(Indicator, code)
    if not indicator:
        raise HTTPException(status_code=404, detail="indicator not found")

    query = select(DataPoint).where(DataPoint.indicator_code == code)
    if start:
        query = query.where(DataPoint.date >= start)
    if end:
        query = query.where(DataPoint.date <= end)
    query = query.order_by(DataPoint.date)

    points = db.execute(query).scalars().all()
    return IndicatorHistory(
        code=indicator.code,
        name=indicator.name,
        unit=indicator.unit,
        points=[DataPointOut.model_validate(p) for p in points],
    )


@router.get("/indicators/{code}/vintages", response_model=list[DataPointVintageOut])
def get_vintages(
    code: str,
    observation_date: date | None = None,
    db: Session = Depends(get_db),
):
    if not db.get(Indicator, code):
        raise HTTPException(status_code=404, detail="indicator not found")

    query = select(DataPointVintage).where(DataPointVintage.indicator_code == code)
    if observation_date:
        query = query.where(DataPointVintage.date == observation_date)
    query = query.order_by(DataPointVintage.date, DataPointVintage.version)
    return [
        DataPointVintageOut.model_validate(point)
        for point in db.execute(query).scalars().all()
    ]


@router.post("/refresh")
def refresh(db: Session = Depends(get_db)):
    results = refresh_all_indicators(db)
    return {"refreshed": results}

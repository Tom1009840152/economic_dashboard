from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DataPoint, Indicator
from app.schemas import DataPointOut, IndicatorHistory, IndicatorSummary
from app.services.indicator_service import refresh_all_indicators

router = APIRouter(prefix="/api", tags=["indicators"])


@router.get("/indicators", response_model=list[IndicatorSummary])
def list_indicators(
    region: str | None = Query(default=None, description="按国家/地区筛选：CN/US/JP/GLOBAL"),
    db: Session = Depends(get_db),
):
    query = select(Indicator).order_by(Indicator.sort_order)
    if region:
        query = query.where(Indicator.region == region)
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
        points=[DataPointOut(date=p.date, value=float(p.value)) for p in points],
    )


@router.post("/refresh")
def refresh(db: Session = Depends(get_db)):
    results = refresh_all_indicators(db)
    return {"refreshed": results}

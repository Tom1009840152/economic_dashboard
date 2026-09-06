from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DataPoint, Indicator
from app.schemas import DataPointOut, ForecastOut
from app.services.forecast_service import forecast_series

router = APIRouter(prefix="/api", tags=["forecast"])


@router.get("/indicators/{code}/forecast", response_model=ForecastOut)
def get_forecast(
    code: str,
    horizon: int = Query(default=30, ge=1, le=180),
    db: Session = Depends(get_db),
):
    indicator = db.get(Indicator, code)
    if not indicator:
        raise HTTPException(status_code=404, detail="indicator not found")

    points = db.execute(
        select(DataPoint).where(DataPoint.indicator_code == code).order_by(DataPoint.date)
    ).scalars().all()

    if len(points) < 10:
        raise HTTPException(status_code=422, detail="not enough history to forecast yet")

    dates = [p.date for p in points]
    values = [float(p.value) for p in points]
    forecast_points = forecast_series(dates, values, horizon=horizon)

    return ForecastOut(
        code=indicator.code,
        name=indicator.name,
        history=[DataPointOut(date=d, value=v) for d, v in zip(dates, values)],
        forecast=forecast_points,
    )

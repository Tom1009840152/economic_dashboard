from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DataPoint, Indicator
from app.schemas import DataPointOut, ForecastOut
from app.services.forecast_service import forecast_series, infer_forecast_cadence
from app.services.indicator_series import constrain_current_series

router = APIRouter(prefix="/api", tags=["forecast"])

# JP_BOJ is an OECD/FRED monthly average of the <24-hour call/interbank
# market rate.  It is not an event series and may use the normal monthly
# forecast cadence.  GB_BOE and KR_BOK, by contrast, store actual decision events.
EVENT_DRIVEN_POLICY_RATES = {"US_FFR", "EU_ECB", "GB_BOE", "KR_BOK"}


@router.get("/indicators/{code}/forecast", response_model=ForecastOut)
def get_forecast(
    code: str,
    horizon: int | None = Query(default=None, ge=1, le=180),
    db: Session = Depends(get_db),
):
    indicator = db.get(Indicator, code)
    if not indicator:
        raise HTTPException(status_code=404, detail="indicator not found")
    if code in EVENT_DRIVEN_POLICY_RATES:
        raise HTTPException(
            status_code=422,
            detail="event-driven policy rates are not suitable for mechanical ARIMA forecasts",
        )
    if code.startswith("PW_"):
        raise HTTPException(
            status_code=422,
            detail="rolling PortWatch pulses are observation signals, not suitable for mechanical ARIMA forecasts",
        )

    query = select(DataPoint).where(DataPoint.indicator_code == code)
    query = constrain_current_series(query, code).order_by(DataPoint.date)
    points = db.execute(query).scalars().all()

    if len(points) < 10:
        raise HTTPException(status_code=422, detail="not enough history to forecast yet")

    dates = [p.date for p in points]
    values = [float(p.value) for p in points]
    cadence = infer_forecast_cadence(dates)
    steps = horizon or cadence.default_horizon
    forecast_points = forecast_series(dates, values, horizon=steps)

    return ForecastOut(
        code=indicator.code,
        name=indicator.name,
        forecast_unit=cadence.label,
        history=[DataPointOut(date=d, value=v) for d, v in zip(dates, values)],
        forecast=forecast_points,
    )

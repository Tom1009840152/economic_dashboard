from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import CurrencyOption, DataPointOut, ForexForecastOut, ForexHistoryOut
from app.services.forecast_service import forecast_series
from app.services.forex_service import CURRENCY_NAMES, cross_rate_history, list_currencies

router = APIRouter(prefix="/api/forex", tags=["forex"])


def _validate(base: str, target: str) -> None:
    if base not in CURRENCY_NAMES or target not in CURRENCY_NAMES:
        raise HTTPException(status_code=404, detail="unknown currency")
    if base == target:
        raise HTTPException(status_code=400, detail="base and target must differ")


@router.get("/currencies", response_model=list[CurrencyOption])
def get_currencies():
    return list_currencies()


@router.get("/history", response_model=ForexHistoryOut)
def get_history(base: str, target: str, db: Session = Depends(get_db)):
    _validate(base, target)
    points = cross_rate_history(db, base, target)
    return ForexHistoryOut(
        base=base,
        target=target,
        points=[DataPointOut(date=d, value=v) for d, v in points],
    )


@router.get("/forecast", response_model=ForexForecastOut)
def get_forecast(
    base: str,
    target: str,
    horizon: int = Query(default=30, ge=1, le=180),
    db: Session = Depends(get_db),
):
    _validate(base, target)
    points = cross_rate_history(db, base, target)
    if len(points) < 10:
        raise HTTPException(status_code=422, detail="not enough history to forecast yet")

    dates = [d for d, _ in points]
    values = [v for _, v in points]
    forecast_points = forecast_series(dates, values, horizon=horizon)

    return ForexForecastOut(
        base=base,
        target=target,
        history=[DataPointOut(date=d, value=v) for d, v in points],
        forecast=forecast_points,
    )

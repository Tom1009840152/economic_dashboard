from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.fetchers.china_monetary_transmission import fetch_china_monetary_transmission
from app.schemas import MonetaryTransmissionDashboardOut


router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get(
    "/cn/monetary-transmission",
    response_model=MonetaryTransmissionDashboardOut,
)
def get_china_monetary_transmission(db: Session = Depends(get_db)):
    try:
        return fetch_china_monetary_transmission(db)
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="monetary-transmission data sources are unavailable",
        ) from exc

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.fetchers.china_monetary_transmission import fetch_china_monetary_transmission
from app.schemas import ChinaBusinessCycleMatrixOut, MonetaryTransmissionDashboardOut
from app.services.china_business_cycle import (
    DEFAULT_OUTPUT_MONTHS,
    MAX_OUTPUT_MONTHS,
    build_china_business_cycle_matrix,
)


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


@router.get(
    "/cn/business-cycle/matrix",
    response_model=ChinaBusinessCycleMatrixOut,
)
def get_china_business_cycle_matrix(
    start: date | None = Query(default=None, description="返回区间起点；计算仍保留更早历史"),
    end: date | None = Query(default=None, description="返回区间终点"),
    months: int = Query(
        default=DEFAULT_OUTPUT_MONTHS,
        ge=1,
        le=MAX_OUTPUT_MONTHS,
        description="未指定start时返回的月份数",
    ),
    db: Session = Depends(get_db),
):
    """Return the read-only, current-snapshot A1 activity matrix."""

    try:
        return build_china_business_cycle_matrix(
            db,
            start=start,
            end=end,
            months=months,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

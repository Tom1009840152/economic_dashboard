from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.fetchers.china_monetary_transmission import fetch_china_monetary_transmission
from app.schemas import (
    ChinaBusinessCycleMatrixOut,
    ChinaCycleBacktestAttributionOut,
    ChinaCycleBacktestOut,
    ChinaCycleRegimeOut,
    MonetaryTransmissionDashboardOut,
)
from app.services.china_business_cycle import (
    DEFAULT_OUTPUT_MONTHS,
    MAX_OUTPUT_MONTHS,
    build_china_business_cycle_matrix,
)
from app.services.china_cycle_regime import build_china_cycle_regime
from app.services.china_cycle_backtest import (
    DEFAULT_BACKTEST_MONTHS,
    MAX_BACKTEST_MONTHS,
    build_china_cycle_backtest,
    build_china_cycle_backtest_attribution,
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


@router.get(
    "/cn/business-cycle/regime",
    response_model=ChinaCycleRegimeOut,
)
def get_china_cycle_regime(
    start: date | None = Query(default=None, description="返回区间起点；状态机仍尽量预热"),
    end: date | None = Query(default=None, description="返回区间终点"),
    months: int = Query(
        default=DEFAULT_OUTPUT_MONTHS,
        ge=1,
        le=MAX_OUTPUT_MONTHS,
        description="未指定start时返回的月份数",
    ),
    db: Session = Depends(get_db),
):
    """Return A2 relative-growth phases, confirmations and diagnostics."""

    try:
        return build_china_cycle_regime(
            db,
            start=start,
            end=end,
            months=months,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/cn/business-cycle/backtest",
    response_model=ChinaCycleBacktestOut,
)
def get_china_cycle_backtest(
    start: date | None = Query(default=None, description="回测观察月起点"),
    end: date | None = Query(
        default=None,
        description="回测观察月终点；其固定历史判断时点必须已经发生",
    ),
    months: int = Query(
        default=DEFAULT_BACKTEST_MONTHS,
        ge=1,
        le=MAX_BACKTEST_MONTHS,
        description="未指定start时回放的观察月数",
    ),
    db: Session = Depends(get_db),
):
    """Replay A1/A2 at fixed historical timestamps with strict vintages."""

    try:
        return build_china_cycle_backtest(
            db,
            start=start,
            end=end,
            months=months,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/cn/business-cycle/backtest/attribution",
    response_model=ChinaCycleBacktestAttributionOut,
)
def get_china_cycle_backtest_attribution(
    period: str = Query(description="需要拆解的观察月，格式为YYYY-MM"),
    db: Session = Depends(get_db),
):
    """Build one lazy R→H→F model-output attribution audit."""

    try:
        return build_china_cycle_backtest_attribution(db, period=period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

from fastapi import APIRouter, HTTPException

from app.schemas import MaritimeComparisonOut, MaritimeObservatoryOut
from app.services.maritime_observatory import (
    build_maritime_comparison,
    build_maritime_observatory,
)


router = APIRouter(prefix="/api/observatory", tags=["observatory"])


@router.get("/maritime/comparison", response_model=MaritimeComparisonOut)
def get_maritime_comparison(geographies: str = "CHN,USA"):
    """Return global-fixed comparison curves for up to five geographies."""

    codes = [code.strip() for code in geographies.split(",") if code.strip()]
    try:
        return build_maritime_comparison(codes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PortWatch comparison unavailable: {exc}") from exc


@router.get("/maritime", response_model=MaritimeObservatoryOut)
def get_maritime_observatory(country: str = "CHN"):
    """Return the first high-frequency physical-economy observation panel."""

    try:
        return build_maritime_observatory(country)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

from fastapi import APIRouter, HTTPException

from app.fetchers.china_employment import fetch_china_employment_dashboard
from app.schemas import EmploymentDashboardOut


router = APIRouter(prefix="/api", tags=["employment"])


@router.get("/employment/{region}", response_model=EmploymentDashboardOut)
def get_employment_dashboard(region: str):
    if region.upper() != "CN":
        raise HTTPException(
            status_code=404,
            detail="employment dashboard is currently available for CN only",
        )
    try:
        return fetch_china_employment_dashboard()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="employment data sources unavailable") from exc

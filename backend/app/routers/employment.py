from fastapi import APIRouter, HTTPException

from app.fetchers.china_employment import fetch_china_employment_dashboard
from app.fetchers.eu_employment import fetch_eu_employment_dashboard
from app.fetchers.oecd_employment import fetch_oecd_employment_dashboard
from app.fetchers.us_employment import fetch_us_employment_dashboard
from app.fetchers.uk_employment import fetch_uk_employment_dashboard
from app.schemas import EmploymentDashboardOut, InternationalEmploymentDashboardOut


router = APIRouter(prefix="/api", tags=["employment"])


@router.get(
    "/employment/{region}",
    response_model=EmploymentDashboardOut | InternationalEmploymentDashboardOut,
)
def get_employment_dashboard(region: str):
    normalized = region.upper()
    if normalized not in {"CN", "US", "JP", "EU", "GB", "KR"}:
        raise HTTPException(
            status_code=404,
            detail="employment dashboard is unavailable for this region",
        )
    try:
        if normalized == "CN":
            return fetch_china_employment_dashboard()
        if normalized == "US":
            return fetch_us_employment_dashboard()
        if normalized == "EU":
            return fetch_eu_employment_dashboard()
        if normalized == "GB":
            return fetch_uk_employment_dashboard()
        return fetch_oecd_employment_dashboard(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="employment data sources unavailable") from exc

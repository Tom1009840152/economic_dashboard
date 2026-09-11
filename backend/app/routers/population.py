from fastapi import APIRouter, HTTPException
import requests

from app.fetchers.world_bank_population import fetch_population_dashboard
from app.schemas import PopulationDashboardOut


router = APIRouter(prefix="/api", tags=["population"])


@router.get("/population/{region}", response_model=PopulationDashboardOut)
def get_population_dashboard(region: str):
    normalized = region.upper()
    if normalized not in {"CN", "US", "JP", "EU", "KR"}:
        raise HTTPException(status_code=404, detail="population dashboard is unavailable for this region")
    try:
        return fetch_population_dashboard(normalized)
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(status_code=502, detail="population data source unavailable") from exc

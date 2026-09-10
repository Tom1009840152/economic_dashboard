from fastapi import APIRouter, HTTPException
import requests

from app.fetchers.world_bank_population import fetch_population_dashboard
from app.schemas import PopulationDashboardOut


router = APIRouter(prefix="/api", tags=["population"])


@router.get("/population/{region}", response_model=PopulationDashboardOut)
def get_population_dashboard(region: str):
    if region.upper() != "CN":
        raise HTTPException(status_code=404, detail="population dashboard is currently available for CN only")
    try:
        return fetch_population_dashboard("CHN")
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(status_code=502, detail="population data source unavailable") from exc

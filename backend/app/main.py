import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import SessionLocal
from app.routers import forecast, forex, indicators
from app.scheduler import scheduled_refresh, start_scheduler
from app.services.indicator_service import ensure_indicators_seeded

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="经济学看板 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(indicators.router)
app.include_router(forecast.router)
app.include_router(forex.router)


@app.on_event("startup")
def on_startup():
    db = SessionLocal()
    try:
        ensure_indicators_seeded(db)
    finally:
        db.close()

    start_scheduler()
    threading.Thread(target=scheduled_refresh, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ok"}

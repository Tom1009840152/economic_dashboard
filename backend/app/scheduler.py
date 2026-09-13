import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.db import SessionLocal
from app.services.indicator_service import refresh_all_indicators

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def scheduled_refresh(trigger: str = "scheduled"):
    db = SessionLocal()
    try:
        results = refresh_all_indicators(db, trigger=trigger)
        logger.info("scheduled refresh done: %s", results)
    except Exception:
        logger.exception("scheduled refresh failed")
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(scheduled_refresh, "interval", hours=6, id="refresh_indicators", replace_existing=True)
    scheduler.start()

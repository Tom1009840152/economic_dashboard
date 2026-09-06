import logging

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.fetchers.akshare_source import fetch_all
from app.indicator_defs import INDICATOR_DEFS
from app.models import DataPoint, Indicator

logger = logging.getLogger(__name__)


def ensure_indicators_seeded(db: Session) -> None:
    existing = {row.code: row for row in db.execute(select(Indicator)).scalars()}
    for meta in INDICATOR_DEFS:
        row = existing.get(meta["code"])
        if row is None:
            db.add(Indicator(**meta))
        else:
            for key, value in meta.items():
                setattr(row, key, value)
    db.commit()


def upsert_points(db: Session, code: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0

    rows = [
        {"indicator_code": code, "date": row.date, "value": float(row.value)}
        for row in df.itertuples(index=False)
    ]
    stmt = insert(DataPoint).values(rows)
    stmt = stmt.on_duplicate_key_update(value=stmt.inserted.value)
    db.execute(stmt)
    db.commit()
    logger.info("upserted %s: %d points", code, len(rows))
    return len(rows)


def refresh_all_indicators(db: Session) -> dict[str, int]:
    ensure_indicators_seeded(db)
    results: dict[str, int] = {}
    data_by_code = fetch_all()

    for code, df in data_by_code.items():
        results[code] = upsert_points(db, code, df)

    return results

"""Backfill strict China Customs export-release evidence.

Default execution is a read-only dry run.  ``--apply`` appends qualified
``ReleaseEvidence`` rows in one transaction and never writes ``DataPoint`` or
``DataPointVintage``.
"""

from __future__ import annotations

import argparse
import json

import pandas as pd
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.china_trade_evidence import (
    TRADE_EVIDENCE_CODES,
    collect_gacc_trade_evidence,
    validate_production_trade_evidence,
)
from app.services.indicator_service import ensure_indicators_seeded
from app.services.release_evidence import upsert_release_evidence


def evidence_summary(evidence: dict[str, pd.DataFrame]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for code in TRADE_EVIDENCE_CODES:
        frame = evidence.get(code, pd.DataFrame())
        observed = pd.to_datetime(
            frame.get("date", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        availability = pd.to_datetime(
            frame.get("available_at", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        precisions = (
            frame.get("availability_precision", pd.Series(dtype=object))
            .fillna("missing")
            .value_counts()
            .sort_index()
            .to_dict()
        )
        summary[code] = {
            "rows": int(len(frame)),
            "first_observation": (
                observed.min().date().isoformat() if not observed.empty else None
            ),
            "last_observation": (
                observed.max().date().isoformat() if not observed.empty else None
            ),
            "first_available_at": (
                availability.min().isoformat(timespec="seconds")
                if not availability.empty
                else None
            ),
            "last_available_at": (
                availability.max().isoformat(timespec="seconds")
                if not availability.empty
                else None
            ),
            "availability_precision_counts": {
                str(key): int(value) for key, value in precisions.items()
            },
        }
    return summary


def store_trade_evidence(
    db: Session,
    evidence: dict[str, pd.DataFrame],
) -> dict[str, int]:
    """Append evidence atomically after the fixed production safety gate."""

    gate = validate_production_trade_evidence(evidence)
    if gate["invalid"]:
        raise RuntimeError(
            "GACC trade evidence contains unsafe rows; refusing storage: "
            + json.dumps(gate["invalid"], ensure_ascii=False)
        )
    if not gate["ready"]:
        raise RuntimeError(
            "GACC trade evidence is incomplete or unsafe; refusing storage: "
            + json.dumps(gate, ensure_ascii=False)
        )
    inserted: dict[str, int] = {}
    try:
        for code in TRADE_EVIDENCE_CODES:
            inserted[code] = upsert_release_evidence(
                db,
                code,
                evidence.get(code, pd.DataFrame()),
                commit=False,
            )
        db.commit()
        return inserted
    except Exception:
        db.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=30)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="append validated evidence; without this flag the run is read-only",
    )
    args = parser.parse_args()

    evidence = collect_gacc_trade_evidence(
        page_count=args.pages,
        start_page=args.start_page,
    )
    gate = validate_production_trade_evidence(evidence)
    result = {
        "mode": "apply" if args.apply else "check_only",
        "coverage": evidence_summary(evidence),
        "apply_gate": gate,
    }
    if args.apply:
        if not gate["ready"]:
            raise RuntimeError(
                "GACC trade evidence is incomplete or unsafe; refusing --apply: "
                + json.dumps(gate, ensure_ascii=False)
            )
        with SessionLocal() as db:
            ensure_indicators_seeded(db)
            result["inserted"] = store_trade_evidence(
                db,
                evidence,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

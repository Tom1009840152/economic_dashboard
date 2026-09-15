"""Backfill verified CEI release evidence for China's consumer indices.

The default mode only crawls and validates.  ``--apply`` is guarded by a
continuous three-series monthly chain from 2015-12 through the newest month
found and appends all rows atomically without touching ``DataPoint``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

import pandas as pd
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.china_consumer_evidence import (
    CEI_CONSUMER_EVIDENCE_CODES,
    CEI_CONSUMER_FIRST_INDEX_YEAR,
    collect_consumer_release_evidence,
)
from app.services.indicator_service import ensure_indicators_seeded
from app.services.release_evidence import upsert_release_evidence


_REQUIRED_START = pd.Period("2015-12", freq="M")
_MINIMUM_MONTHS = 24
_MAX_EXPECTED_LAG_MONTHS = 3


def expected_consumer_observation_through(
    as_of: dt.date | None = None,
) -> pd.Period:
    """Return the oldest acceptable latest observation for a production run.

    CEI/NBS consumer survey pages normally arrive after the observation month.
    A three-month allowance avoids treating a normally delayed publication as
    a failure while ensuring that a complete-but-years-old cache cannot pass
    the storage gate.
    """

    current_month = pd.Period(as_of or dt.date.today(), freq="M")
    return current_month - _MAX_EXPECTED_LAG_MONTHS


def evidence_summary(evidence: dict[str, pd.DataFrame]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for code in CEI_CONSUMER_EVIDENCE_CODES:
        frame = evidence.get(code, pd.DataFrame())
        observations = pd.to_datetime(
            frame.get("date", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        availability = pd.to_datetime(
            frame.get("available_at", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        summary[code] = {
            "rows": int(len(frame)),
            "months": int(pd.PeriodIndex(observations, freq="M").nunique()),
            "first_observation": (
                observations.min().date().isoformat()
                if not observations.empty
                else None
            ),
            "last_observation": (
                observations.max().date().isoformat()
                if not observations.empty
                else None
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
        }
    return summary


def _periods(frame: pd.DataFrame) -> set[pd.Period]:
    observations = pd.to_datetime(
        frame.get("date", pd.Series(dtype=object)), errors="coerce"
    ).dropna()
    return set(pd.PeriodIndex(observations, freq="M"))


def validate_apply_coverage(
    evidence: dict[str, pd.DataFrame],
    *,
    expected_through: str | dt.date | pd.Period,
) -> dict:
    """Require an aligned, gap-free and sufficiently fresh monthly chain."""

    expected = pd.Period(expected_through, freq="M")
    if expected < _REQUIRED_START:
        raise ValueError(
            f"expected_through must not be earlier than {_REQUIRED_START}"
        )

    periods_by_code = {
        code: _periods(evidence.get(code, pd.DataFrame()))
        for code in CEI_CONSUMER_EVIDENCE_CODES
    }
    union = set().union(*periods_by_code.values())
    latest = max(union) if union else None
    required_through = max(expected, latest) if latest is not None else expected
    required = set(pd.period_range(_REQUIRED_START, required_through, freq="M"))
    missing = {
        code: [str(period) for period in sorted(required - periods)]
        for code, periods in periods_by_code.items()
        if required - periods
    }
    aligned = len({frozenset(periods) for periods in periods_by_code.values()}) == 1
    starts_correctly = all(
        _REQUIRED_START in periods for periods in periods_by_code.values()
    )
    distinct_months = min(
        (len(periods) for periods in periods_by_code.values()), default=0
    )
    fresh_enough = latest is not None and latest >= expected
    ready = bool(
        fresh_enough
        and starts_correctly
        and aligned
        and not missing
        and distinct_months >= _MINIMUM_MONTHS
    )
    return {
        "ready": ready,
        "required_from": str(_REQUIRED_START),
        "required_through": str(required_through),
        "expected_through": str(expected),
        "latest_found": str(latest) if latest is not None else None,
        "fresh_enough": fresh_enough,
        "maximum_expected_lag_months": _MAX_EXPECTED_LAG_MONTHS,
        "minimum_months": _MINIMUM_MONTHS,
        "distinct_months": distinct_months,
        "series_aligned": aligned,
        "starts_at_required_month": starts_correctly,
        "missing": missing,
    }


def store_consumer_evidence(
    db: Session,
    evidence: dict[str, pd.DataFrame],
    *,
    expected_through: str | dt.date | pd.Period,
) -> dict[str, int]:
    """Append a complete consumer chain atomically; never update current data."""

    gate = validate_apply_coverage(evidence, expected_through=expected_through)
    if not gate["ready"]:
        raise RuntimeError(
            "CEI consumer release crawl is incomplete; refusing storage: "
            + json.dumps(gate, ensure_ascii=False)
        )
    inserted: dict[str, int] = {}
    try:
        for code in CEI_CONSUMER_EVIDENCE_CODES:
            inserted[code] = upsert_release_evidence(
                db, code, evidence[code], commit=False
            )
        db.commit()
        return inserted
    except Exception:
        db.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--start-year", type=int, default=CEI_CONSUMER_FIRST_INDEX_YEAR
    )
    parser.add_argument("--end-year", type=int, default=dt.date.today().year)
    parser.add_argument(
        "--expected-through",
        help=(
            "oldest acceptable latest observation (YYYY-MM); default is the "
            "current month minus three months"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="append a fully validated chain; default mode is read-only",
    )
    args = parser.parse_args()

    evidence = collect_consumer_release_evidence(
        start_year=args.start_year, end_year=args.end_year
    )
    expected_through = (
        pd.Period(args.expected_through, freq="M")
        if args.expected_through
        else expected_consumer_observation_through()
    )
    gate = validate_apply_coverage(
        evidence, expected_through=expected_through
    )
    result = {
        "mode": "apply" if args.apply else "check_only",
        "coverage": evidence_summary(evidence),
        "apply_gate": gate,
    }
    if args.apply:
        if not gate["ready"]:
            raise RuntimeError(
                "CEI consumer release crawl is incomplete; refusing --apply: "
                + json.dumps(gate, ensure_ascii=False)
            )
        with SessionLocal() as db:
            ensure_indicators_seeded(db)
            result["inserted"] = store_consumer_evidence(
                db,
                evidence,
                expected_through=expected_through,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

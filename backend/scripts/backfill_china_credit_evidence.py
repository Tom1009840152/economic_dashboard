"""Backfill ordered PBOC evidence for China's credit-cycle block.

The default mode is read-only. ``--apply`` requires a fresh, continuous model
chain and appends all evidence atomically; it never writes ``DataPoint``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

import pandas as pd
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.fetchers.china_cycle_data import _load_nbs_nominal_gdp_history
from app.services.china_credit_evidence import (
    CREDIT_EVIDENCE_CODES,
    build_credit_release_evidence,
    collect_pboc_credit_release_inputs,
)
from app.services.indicator_service import ensure_indicators_seeded
from app.services.release_evidence import upsert_release_evidence


_TSF_REQUIRED_START = pd.Period("2015-01", freq="M")
_M1M2_REQUIRED_START = pd.Period("2024-01", freq="M")
_RECENT_DECOMPOSITION_MONTHS = 24
_MINIMUM_IMPULSE_MONTHS = 24


def collect_credit_evidence(
    *,
    pboc_pages: int,
    nbs_pages: int,
    nbs_start_page: int,
    nbs_shard: int,
) -> dict[str, pd.DataFrame]:
    if pboc_pages < 1 or nbs_pages < 1:
        raise ValueError("archive page counts must be at least 1")
    if nbs_start_page < 0:
        raise ValueError("nbs_start_page must not be negative")
    inputs = collect_pboc_credit_release_inputs(page_count=pboc_pages)
    nominal_gdp = _load_nbs_nominal_gdp_history(
        page_count=nbs_pages,
        start_page=nbs_start_page,
        archive_shard=nbs_shard,
    )["CN_GDP_NOMINAL_YTD"]
    return build_credit_release_evidence(inputs, nominal_gdp)


def evidence_summary(evidence: dict[str, pd.DataFrame]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for code in CREDIT_EVIDENCE_CODES:
        frame = evidence.get(code, pd.DataFrame())
        observed = pd.to_datetime(
            frame.get("date", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        availability = pd.to_datetime(
            frame.get("available_at", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        statuses = (
            frame.get("status", pd.Series(dtype=object))
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
            "status_counts": {str(key): int(value) for key, value in statuses.items()},
        }
    return summary


def expected_credit_observation_through(
    as_of: dt.date | None = None,
) -> pd.Period:
    current = pd.Period(as_of or dt.date.today(), freq="M")
    effective = as_of or dt.date.today()
    # Releases normally arrive around the middle of the following month.  The
    # twentieth is a conservative freshness deadline, not a fabricated release
    # instant; any newer observation actually discovered raises the gate below.
    return current - (1 if effective.day >= 20 else 2)


def _periods(frame: pd.DataFrame) -> set[pd.Period]:
    observed = pd.to_datetime(
        frame.get("date", pd.Series(dtype=object)), errors="coerce"
    ).dropna()
    return set(pd.PeriodIndex(observed, freq="M"))


def validate_apply_coverage(
    evidence: dict[str, pd.DataFrame],
    *,
    expected_through: str | dt.date | pd.Period,
) -> dict:
    expected = pd.Period(expected_through, freq="M")
    periods = {
        code: _periods(evidence.get(code, pd.DataFrame()))
        for code in CREDIT_EVIDENCE_CODES
    }
    observed_latest = [
        max(periods[code])
        for code in ("CN_TSF", "CN_M1M2")
        if periods[code]
    ]
    required_through = max([expected, *observed_latest])
    required: dict[str, set[pd.Period]] = {
        "CN_TSF": set(
            pd.period_range(_TSF_REQUIRED_START, required_through, freq="M")
        ),
        "CN_M1M2": set(
            pd.period_range(_M1M2_REQUIRED_START, required_through, freq="M")
        ),
    }
    recent_start = required_through - (_RECENT_DECOMPOSITION_MONTHS - 1)
    recent = set(pd.period_range(recent_start, required_through, freq="M"))
    for code in (
        "CN_TSF_RMB_LOANS_FLOW",
        "CN_CORP_BOND_FINANCING",
        "CN_GOV_BOND_FINANCING",
        "CN_TSF_STOCK_YOY",
        "CN_TSF_RMB_LOAN_STOCK_YOY",
    ):
        required[code] = recent

    impulse_periods = periods["CN_CREDIT_IMPULSE"]
    if impulse_periods:
        required["CN_CREDIT_IMPULSE"] = set(
            pd.period_range(min(impulse_periods), required_through, freq="M")
        )
    else:
        required["CN_CREDIT_IMPULSE"] = {required_through}

    missing = {
        code: [str(period) for period in sorted(wanted - periods[code])]
        for code, wanted in required.items()
        if wanted - periods[code]
    }
    impulse_count = len(impulse_periods)
    latest_by_code = {
        code: str(max(values)) if values else None
        for code, values in periods.items()
    }
    fresh = all(
        periods[code] and max(periods[code]) >= expected
        for code in ("CN_TSF", "CN_M1M2", "CN_CREDIT_IMPULSE")
    )
    ready = bool(
        fresh
        and not missing
        and impulse_count >= _MINIMUM_IMPULSE_MONTHS
    )
    return {
        "ready": ready,
        "required_through": str(required_through),
        "expected_through": str(expected),
        "tsf_required_from": str(_TSF_REQUIRED_START),
        "m1m2_required_from": str(_M1M2_REQUIRED_START),
        "recent_decomposition_months": _RECENT_DECOMPOSITION_MONTHS,
        "minimum_impulse_months": _MINIMUM_IMPULSE_MONTHS,
        "impulse_months": impulse_count,
        "fresh_enough": fresh,
        "latest_by_code": latest_by_code,
        "missing": missing,
    }


def store_credit_evidence(
    db: Session,
    evidence: dict[str, pd.DataFrame],
    *,
    expected_through: str | dt.date | pd.Period,
    allow_partial: bool = False,
) -> dict[str, int]:
    gate = validate_apply_coverage(evidence, expected_through=expected_through)
    if not allow_partial and not gate["ready"]:
        raise RuntimeError(
            "PBOC credit release crawl is incomplete; refusing storage: "
            + json.dumps(gate, ensure_ascii=False)
        )
    inserted: dict[str, int] = {}
    try:
        for code in CREDIT_EVIDENCE_CODES:
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
    parser.add_argument("--pboc-pages", type=int, default=30)
    parser.add_argument("--nbs-pages", type=int, default=70)
    parser.add_argument("--nbs-start-page", type=int, default=0)
    parser.add_argument(
        "--nbs-shard", type=int, choices=(0, 1000, 2000, 3000), default=0
    )
    parser.add_argument("--expected-through")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="append a fully validated chain; default mode is read-only",
    )
    args = parser.parse_args()

    evidence = collect_credit_evidence(
        pboc_pages=args.pboc_pages,
        nbs_pages=args.nbs_pages,
        nbs_start_page=args.nbs_start_page,
        nbs_shard=args.nbs_shard,
    )
    expected_through = (
        pd.Period(args.expected_through, freq="M")
        if args.expected_through
        else expected_credit_observation_through()
    )
    gate = validate_apply_coverage(evidence, expected_through=expected_through)
    result = {
        "mode": "apply" if args.apply else "check_only",
        "coverage": evidence_summary(evidence),
        "apply_gate": gate,
    }
    if args.apply:
        if not gate["ready"]:
            raise RuntimeError(
                "PBOC credit release crawl is incomplete; refusing --apply: "
                + json.dumps(gate, ensure_ascii=False)
            )
        with SessionLocal() as db:
            ensure_indicators_seeded(db)
            result["inserted"] = store_credit_evidence(
                db,
                evidence,
                expected_through=expected_through,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Backfill ordered official-release evidence for China's fiscal impulse.

The default mode is read-only: it crawls and validates the official NBS/MOF
releases, derives the fiscal chain, and prints a coverage plan.  ``--apply``
stores append-only ``ReleaseEvidence`` rows in one transaction; it never calls
the current-value upsert path and therefore cannot revise ``DataPoint``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

import pandas as pd
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.fetchers.china_cycle_data import (
    _load_fiscal,
    _load_nbs_nominal_gdp_history,
)
from app.services.china_fiscal_evidence import (
    FISCAL_EVIDENCE_CODES,
    build_fiscal_release_evidence,
)
from app.services.indicator_service import ensure_indicators_seeded
from app.services.release_evidence import upsert_release_evidence


def collect_fiscal_evidence(
    *,
    nbs_pages: int,
    nbs_start_page: int,
    nbs_shard: int,
    mof_pages: int,
) -> dict[str, pd.DataFrame]:
    """Collect official direct rows and build the canonical derived chain."""

    if nbs_pages < 1 or mof_pages < 1:
        raise ValueError("archive page counts must be at least 1")
    if nbs_start_page < 0:
        raise ValueError("nbs_start_page must not be negative")
    nominal_gdp = _load_nbs_nominal_gdp_history(
        page_count=nbs_pages,
        start_page=nbs_start_page,
        archive_shard=nbs_shard,
    )["CN_GDP_NOMINAL_YTD"]
    fiscal = _load_fiscal(page_count=mof_pages)
    return build_fiscal_release_evidence(fiscal, nominal_gdp)


def evidence_summary(evidence: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """Return stable, JSON-friendly coverage details for review and logs."""

    summary: dict[str, dict] = {}
    for code in FISCAL_EVIDENCE_CODES:
        frame = evidence.get(code, pd.DataFrame())
        observed = pd.to_datetime(
            frame.get("date", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        availability = pd.to_datetime(
            frame.get("available_at", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
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
        }
    return summary


def _latest_expected_quarter(as_of: dt.date) -> pd.Period:
    """Return the latest quarter whose official releases should be available."""

    current = pd.Period(as_of, freq="Q-DEC")
    latest = current - 1
    # GDP/fiscal quarter-end releases normally arrive during the first month of
    # the next quarter.  Before the conservative 25th-day deadline, do not
    # reject an apply merely because that fresh quarter has not published yet.
    if as_of.month in {1, 4, 7, 10} and as_of.day < 25:
        latest -= 1
    return latest


def validate_apply_coverage(
    evidence: dict[str, pd.DataFrame],
    *,
    as_of: dt.date | None = None,
) -> dict:
    """Refuse a partial crawl that would masquerade as a complete backfill."""

    effective_date = as_of or dt.date.today()
    latest = _latest_expected_quarter(effective_date)
    first_input = pd.Period("2021Q4", freq="Q-DEC")
    required_inputs = pd.period_range(first_input, latest, freq="Q-DEC").asfreq(
        "M", how="end"
    )
    first_impulse = first_input + 4
    required_impulses = pd.period_range(
        first_impulse, latest, freq="Q-DEC"
    ).asfreq("M", how="end")

    required_by_code = {
        code: required_inputs
        for code in (
            "CN_GDP_NOMINAL_YTD",
            "CN_FISCAL_GENERAL_SPEND_YTD",
            "CN_FISCAL_FUND_EXPENDITURE_YTD",
            "CN_FISCAL_BROAD_EXPENDITURE_YTD",
            "CN_FISCAL_SPEND_INTENSITY",
        )
    }
    required_by_code["CN_FISCAL_IMPULSE_PROXY"] = required_impulses

    missing_by_code: dict[str, list[str]] = {}
    for code, required in required_by_code.items():
        frame = evidence.get(code, pd.DataFrame())
        dates = pd.to_datetime(
            frame.get("date", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        present = set(pd.PeriodIndex(dates, freq="M"))
        missing = [str(period) for period in required if period not in present]
        if missing:
            missing_by_code[code] = missing

    return {
        "ready": not missing_by_code,
        "required_from": str(first_input),
        "required_through": str(latest),
        "missing": missing_by_code,
    }


def store_fiscal_evidence(
    db: Session,
    evidence: dict[str, pd.DataFrame],
    *,
    allow_partial: bool = False,
    coverage_as_of: dt.date | None = None,
) -> dict[str, int]:
    """Append all evidence atomically and leave current observations untouched."""

    gate = validate_apply_coverage(evidence, as_of=coverage_as_of)
    if not allow_partial and not gate["ready"]:
        raise RuntimeError(
            "official release crawl is incomplete; refusing storage: "
            + json.dumps(gate["missing"], ensure_ascii=False)
        )
    inserted: dict[str, int] = {}
    try:
        for code in FISCAL_EVIDENCE_CODES:
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
    parser.add_argument("--nbs-pages", type=int, default=70)
    parser.add_argument("--nbs-start-page", type=int, default=0)
    parser.add_argument(
        "--nbs-shard",
        type=int,
        choices=(0, 1000, 2000, 3000),
        default=0,
    )
    parser.add_argument("--mof-pages", type=int, default=10)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="append validated evidence; without this flag the run is read-only",
    )
    args = parser.parse_args()

    evidence = collect_fiscal_evidence(
        nbs_pages=args.nbs_pages,
        nbs_start_page=args.nbs_start_page,
        nbs_shard=args.nbs_shard,
        mof_pages=args.mof_pages,
    )
    result = {
        "mode": "apply" if args.apply else "check_only",
        "coverage": evidence_summary(evidence),
        "apply_gate": validate_apply_coverage(evidence),
    }
    if args.apply:
        if not result["apply_gate"]["ready"]:
            raise RuntimeError(
                "official release crawl is incomplete; refusing --apply: "
                + json.dumps(result["apply_gate"]["missing"], ensure_ascii=False)
            )
        with SessionLocal() as db:
            ensure_indicators_seeded(db)
            result["inserted"] = store_fiscal_evidence(db, evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Seed and backfill the hidden China business-cycle input series."""

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import select

from app.db import SessionLocal
from app.fetchers.akshare_source import FETCHERS as ALL_FETCHERS
from app.fetchers.china_cycle_data import (
    CHINA_CYCLE_FETCHERS,
    PBOC_YTD_DIFF_FORMULA_VERSION,
    _load_fiscal,
    _load_gacc_exports,
    _load_industrial_enterprises,
    _load_nbs_industry_history,
    _load_nbs_hard_activity_evidence,
    _load_nbs_property_history,
    _load_pboc_credit,
    _load_pmi,
    _load_real_estate_activity,
    _load_special_bonds,
    _build_fiscal_impulse,
    _merge_bundles,
)
from app.models import DataPoint
from app.services.indicator_service import ensure_indicators_seeded, upsert_points


BACKFILL_FETCHERS = {
    **CHINA_CYCLE_FETCHERS,
    "CN_RETAIL": ALL_FETCHERS["CN_RETAIL"],
    "CN_EXPORTS": ALL_FETCHERS["CN_EXPORTS"],
}


GROUPS = {
    "pmi": {
        code
        for code in CHINA_CYCLE_FETCHERS
        if code == "CN_NMI" or code.startswith(("CN_PMI_", "CN_NMI_"))
    },
    "industry": {
        code
        for code in CHINA_CYCLE_FETCHERS
        if code == "CN_IP" or code.startswith("CN_IND_")
    },
    "credit": {
        code
        for code in CHINA_CYCLE_FETCHERS
        if code == "CN_TSF" or code.startswith(
            ("CN_TSF_", "CN_CORP_BOND_", "CN_GOV_BOND_", "CN_GDP_NOMINAL", "CN_CREDIT_")
        )
    },
    "property": {code for code in CHINA_CYCLE_FETCHERS if code.startswith("CN_RE_")},
    "fiscal": {
        code
        for code in CHINA_CYCLE_FETCHERS
        if code.startswith(("CN_FISCAL_", "CN_LOCAL_SPECIAL_BOND_"))
    },
    "confidence": {
        code
        for code in CHINA_CYCLE_FETCHERS
        if code.startswith(("CN_CONSUMER_", "CN_ENTERPRISE_"))
    },
    "trade": {"CN_EXPORTS"},
}
GROUPS["activity"] = {
    *GROUPS["pmi"],
    "CN_IP",
    "CN_RETAIL",
}


_OFFICIAL_RELEASE_DOMAINS = (
    "stats.gov.cn",
    "pbc.gov.cn",
    "mof.gov.cn",
    "customs.gov.cn",
)
_STORED_VALUE_QUANTUM = Decimal("0.000001")


def _is_official_release_url(value: object) -> bool:
    """Return whether *value* points at an approved official release host."""

    try:
        parsed = urlparse(str(value))
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() != "https":
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in _OFFICIAL_RELEASE_DOMAINS
    )


def _has_precise_available_at(value: object) -> bool:
    """Reject date-only placeholders masquerading as publication timestamps."""

    if value is None or pd.isna(value):
        return False
    if isinstance(value, pd.Timestamp):
        return True
    return isinstance(value, dt.datetime)


def _matches_stored_value(current: object, candidate: object) -> bool:
    """Compare at the database's six-decimal storage precision."""

    try:
        stored = Decimal(str(current)).quantize(_STORED_VALUE_QUANTUM)
        incoming = Decimal(str(candidate)).quantize(_STORED_VALUE_QUANTUM)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return stored.is_finite() and incoming.is_finite() and stored == incoming


def _verified_release_evidence(
    db,
    code: str,
    frame: pd.DataFrame,
    *,
    require_existing: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    """Keep only exact, source-backed releases that cannot replace a final value.

    Historical publication pages may preserve an original value that was later
    revised. The existing upsert path represents the latest value, so D7 only
    enriches a stored observation when the official release value matches it.
    The sole derived exception is the versioned month-on-month difference of two
    official PBOC cumulative AFRE releases. Mismatches are reported and left
    untouched until a dedicated ordered-vintage importer can represent both
    versions without corrupting the current snapshot.

    ``require_existing`` is enabled by archive backfills, making the operation
    metadata-only: an archive page cannot create a new current observation.  The
    default remains compatible with callers that validate evidence before an
    initial seed.
    """

    required = {
        "date",
        "value",
        "release_date",
        "available_at",
        "source_url",
        "status",
    }
    if not required.issubset(frame.columns):
        missing = sorted(required - set(frame.columns))
        raise ValueError(f"{code} release evidence is missing columns: {missing}")
    precise_timestamp = frame["available_at"].map(_has_precise_available_at)
    official_source = frame["source_url"].map(_is_official_release_url)
    formula_version = (
        frame["formula_version"]
        if "formula_version" in frame.columns
        else pd.Series(None, index=frame.index, dtype=object)
    )
    trusted_status = frame["status"].eq("published") | (
        frame["status"].eq("derived")
        & formula_version.eq(PBOC_YTD_DIFF_FORMULA_VERSION)
    )
    evidence = frame.loc[
        frame["release_date"].notna()
        & precise_timestamp
        & official_source
        & trusted_status
    ].copy()
    if evidence.empty:
        return evidence, []

    dates = pd.to_datetime(evidence["date"], errors="coerce").dt.date.dropna().tolist()
    stored = {
        row.date: row.value
        for row in db.scalars(
            select(DataPoint).where(
                DataPoint.indicator_code == code,
                DataPoint.date.in_(dates),
            )
        )
    }
    accepted = []
    mismatches: list[str] = []
    for row in evidence.to_dict("records"):
        observed = pd.to_datetime(row["date"]).date()
        current = stored.get(observed)
        if current is None and require_existing:
            mismatches.append(observed.isoformat())
            continue
        if current is not None and not _matches_stored_value(current, row["value"]):
            mismatches.append(observed.isoformat())
            continue
        accepted.append(row)
    return pd.DataFrame(accepted, columns=evidence.columns), mismatches


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["all", *GROUPS], default="all")
    parser.add_argument(
        "--archive",
        action="store_true",
        help="crawl historical official release pages (slow; intended for one-time backfills)",
    )
    parser.add_argument(
        "--archive-pages",
        type=int,
        default=100,
        help="number of NBS archive index pages to inspect in one-time archive mode",
    )
    parser.add_argument(
        "--archive-start-page",
        type=int,
        default=0,
        help="first NBS archive index page to inspect; use it to resume a deep backfill",
    )
    parser.add_argument(
        "--archive-shard",
        type=int,
        choices=(0, 1000, 2000, 3000),
        default=0,
        help="NBS historical archive shard; 1000 starts around 2021-08",
    )
    parser.add_argument(
        "--pboc-archive-pages",
        type=int,
        default=38,
        help="number of PBOC news archive pages to inspect",
    )
    parser.add_argument(
        "--gacc-archive-pages",
        type=int,
        default=2,
        help="number of one-based China Customs preliminary-release pages to inspect",
    )
    parser.add_argument(
        "--gacc-archive-start-page",
        type=int,
        default=1,
        help="first one-based China Customs preliminary-release page to inspect",
    )
    parser.add_argument(
        "--pboc-archive-start-page",
        type=int,
        default=1,
        help="first one-based PBOC news archive page to inspect",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="fetch and validate evidence without writing database rows",
    )
    args = parser.parse_args()
    selected = set(BACKFILL_FETCHERS) if args.group == "all" else GROUPS[args.group]

    archive_frames = {}
    archive_failures: dict[str, Exception] = {}
    if args.archive:
        if args.group in {"all", "pmi", "activity"}:
            archive_frames.update(
                _load_pmi(
                    page_count=args.archive_pages,
                    start_page=args.archive_start_page,
                    archive_shard=args.archive_shard,
                )
            )
        if args.group in {"all", "industry"}:
            archive_frames.update(
                _merge_bundles(
                    _load_nbs_industry_history(),
                    _load_industrial_enterprises(
                        page_count=args.archive_pages,
                        start_page=args.archive_start_page,
                        archive_shard=args.archive_shard,
                    ),
                    _load_nbs_hard_activity_evidence(
                        page_count=args.archive_pages,
                        start_page=args.archive_start_page,
                        archive_shard=args.archive_shard,
                    ),
                )
            )
        elif args.group == "activity":
            archive_frames.update(
                _load_nbs_hard_activity_evidence(
                    page_count=args.archive_pages,
                    start_page=args.archive_start_page,
                    archive_shard=args.archive_shard,
                )
            )
        if args.group in {"all", "property"}:
            archive_frames.update(
                _merge_bundles(
                    _load_nbs_property_history(),
                    _load_real_estate_activity(
                        page_count=args.archive_pages,
                        start_page=args.archive_start_page,
                        archive_shard=args.archive_shard,
                    ),
                )
            )
        if args.group in {"all", "credit"}:
            archive_frames.update(
                _load_pboc_credit(
                    page_count=args.pboc_archive_pages,
                    archive=True,
                    start_page=args.pboc_archive_start_page,
                )
            )
        if args.group in {"all", "trade"}:
            try:
                archive_frames.update(
                    _load_gacc_exports(
                        page_count=args.gacc_archive_pages,
                        start_page=args.gacc_archive_start_page,
                    )
                )
            except Exception as exc:
                archive_failures["CN_EXPORTS"] = exc
        if args.group in {"all", "fiscal"}:
            fiscal = _load_fiscal(page_count=10)
            archive_frames.update(fiscal)
            archive_frames.update(_build_fiscal_impulse(fiscal))
            archive_frames["CN_LOCAL_SPECIAL_BOND_ISSUANCE"] = _load_special_bonds(page_count=10)

    db = SessionLocal()
    try:
        if not args.check_only:
            ensure_indicators_seeded(db)
        for code, fetcher in BACKFILL_FETCHERS.items():
            if code not in selected:
                continue
            try:
                if code in archive_failures:
                    print(f"{code}: FAILED: {archive_failures[code]}")
                    continue
                if args.archive:
                    frame = archive_frames.get(code)
                    if frame is None:
                        print(f"{code}: skipped; no official archive evidence")
                        continue
                else:
                    frame = fetcher()
                mismatches: list[str] = []
                if args.archive:
                    frame, mismatches = _verified_release_evidence(
                        db,
                        code,
                        frame,
                        require_existing=True,
                    )
                if args.check_only:
                    print(
                        f"{code}: {len(frame)} verified releases, "
                        f"{len(mismatches)} value mismatches, check only"
                    )
                    if mismatches:
                        print(f"  mismatch periods: {', '.join(mismatches)}")
                    continue
                if frame.empty:
                    print(
                        f"{code}: 0 verified releases, 0 new vintages, "
                        f"{len(mismatches)} value mismatches skipped"
                    )
                    if mismatches:
                        print(f"  mismatch periods: {', '.join(mismatches)}")
                    continue
                changed = upsert_points(db, code, frame)
                print(
                    f"{code}: {len(frame)} verified releases, {changed} new vintages, "
                    f"{len(mismatches)} value mismatches skipped"
                )
                if mismatches:
                    print(f"  mismatch periods: {', '.join(mismatches)}")
            except Exception as exc:
                db.rollback()
                print(f"{code}: FAILED: {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

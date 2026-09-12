"""Seed and backfill the hidden China business-cycle input series."""

import argparse

from app.db import SessionLocal
from app.fetchers.china_cycle_data import (
    CHINA_CYCLE_FETCHERS,
    _load_fiscal,
    _load_industrial_enterprises,
    _load_nbs_industry_history,
    _load_nbs_pmi_history,
    _load_nbs_property_history,
    _load_pmi,
    _load_real_estate_activity,
    _load_special_bonds,
    _build_fiscal_impulse,
    _merge_bundles,
)
from app.services.indicator_service import ensure_indicators_seeded, upsert_points


GROUPS = {
    "pmi": {code for code in CHINA_CYCLE_FETCHERS if code.startswith(("CN_PMI_", "CN_NMI_"))},
    "industry": {code for code in CHINA_CYCLE_FETCHERS if code.startswith("CN_IND_")},
    "credit": {
        code
        for code in CHINA_CYCLE_FETCHERS
        if code.startswith(
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
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["all", *GROUPS], default="all")
    parser.add_argument(
        "--archive",
        action="store_true",
        help="crawl historical official release pages (slow; intended for one-time backfills)",
    )
    args = parser.parse_args()
    selected = set(CHINA_CYCLE_FETCHERS) if args.group == "all" else GROUPS[args.group]

    archive_frames = {}
    if args.archive:
        if args.group in {"all", "pmi"}:
            archive_frames.update(_merge_bundles(_load_nbs_pmi_history(), _load_pmi()))
        if args.group in {"all", "industry"}:
            archive_frames.update(
                _merge_bundles(_load_nbs_industry_history(), _load_industrial_enterprises())
            )
        if args.group in {"all", "property"}:
            archive_frames.update(
                _merge_bundles(_load_nbs_property_history(), _load_real_estate_activity())
            )
        if args.group in {"all", "fiscal"}:
            fiscal = _load_fiscal(page_count=10)
            archive_frames.update(fiscal)
            archive_frames.update(_build_fiscal_impulse(fiscal))
            archive_frames["CN_LOCAL_SPECIAL_BOND_ISSUANCE"] = _load_special_bonds(page_count=10)

    db = SessionLocal()
    try:
        ensure_indicators_seeded(db)
        for code, fetcher in CHINA_CYCLE_FETCHERS.items():
            if code not in selected:
                continue
            try:
                frame = archive_frames.get(code)
                if frame is None:
                    frame = fetcher()
                changed = upsert_points(db, code, frame)
                print(f"{code}: {len(frame)} observations, {changed} new vintages")
            except Exception as exc:
                db.rollback()
                print(f"{code}: FAILED: {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

"""Fill missing current fiscal-chain observations without revising any key.

The default invocation is a read-only dry run.  Pass ``--apply`` explicitly to
commit the validated plan.  The append-only fiscal evidence backfill remains a
separate command and keeps its promise never to modify current observations.
"""

from __future__ import annotations

import argparse
import json

from app.db import SessionLocal
from app.fetchers.china_cycle_data import _load_fiscal
from app.services.china_fiscal_current_gap import repair_fiscal_current_gap


MOF_DEEP_SCAN_PAGES = 10


def collect_fresh_fiscal_current() -> dict:
    """Deep-scan enough MOF archive pages to cover the fixed repair scope."""

    return _load_fiscal(
        page_count=MOF_DEEP_SCAN_PAGES,
        refresh_releases=True,
        strict_live=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the missing-only repair; without this flag the run is read-only",
    )
    args = parser.parse_args()

    fiscal = collect_fresh_fiscal_current()
    with SessionLocal() as db:
        result = repair_fiscal_current_gap(db, fiscal, apply=args.apply)
    result["mof_deep_scan_pages"] = MOF_DEEP_SCAN_PAGES
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

"""Validate or missing-only backfill CN_CORE_CPI current observations."""

from __future__ import annotations

import argparse
import json

from app.services.china_core_cpi_current_backfill import (
    backfill_core_cpi_current,
    inspect_core_cpi_current_candidates,
)
from app.services.china_core_cpi_table_evidence import (
    DEFAULT_CORE_CPI_INDEX_PAGE_COUNT,
    collect_core_cpi_table_evidence,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate official monthly core-CPI tables, then optionally inspect "
            "or atomically fill missing current keys."
        )
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=DEFAULT_CORE_CPI_INDEX_PAGE_COUNT,
        help="pages to scan in each mandatory NBS archive shard",
    )
    parser.add_argument("--start-page", type=int, default=0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-current",
        action="store_true",
        help="open the database and print the missing-only plan without writing",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="open the database and atomically insert only missing current keys",
    )
    args = parser.parse_args(argv)

    # Source collection and its completeness gate deliberately finish before
    # importing/opening the database.  The default dry-run is source-only.
    evidence = collect_core_cpi_table_evidence(
        page_count=args.pages,
        start_page=args.start_page,
    )
    if not args.check_current and not args.apply:
        result = inspect_core_cpi_current_candidates(evidence)
    else:
        from app.db import SessionLocal

        with SessionLocal() as db:
            result = backfill_core_cpi_current(
                db,
                evidence,
                apply=args.apply,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

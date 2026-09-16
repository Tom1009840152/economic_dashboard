"""Repair two audited core-CPI current keys; dry-run is the default."""

from __future__ import annotations

import argparse
import json

from app.db import SessionLocal
from app.services.china_core_cpi_current_repair import repair_core_cpi_current
from app.services.china_inflation_evidence import collect_inflation_release_evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    # The March-2025 commentary is the first proof of February's core CPI and
    # now sits just outside the old 14-page rolling window.
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the two-key repair; otherwise validate and print the plan",
    )
    args = parser.parse_args()

    evidence = collect_inflation_release_evidence(
        page_count=args.pages,
        start_page=args.start_page,
    )["CN_CORE_CPI"]
    with SessionLocal() as db:
        result = repair_core_cpi_current(db, evidence, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

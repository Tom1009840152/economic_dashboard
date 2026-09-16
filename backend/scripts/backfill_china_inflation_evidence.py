"""Backfill strict NBS core-CPI/PPI evidence; dry-run is the default."""

from __future__ import annotations

import argparse
import json

from app.db import SessionLocal
from app.services.china_inflation_evidence import (
    collect_inflation_release_evidence,
    evidence_summary,
    store_inflation_evidence,
    validate_inflation_coverage,
)
from app.services.indicator_service import ensure_indicators_seeded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=140)
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument(
        "--shards",
        type=int,
        nargs="+",
        choices=(0, 1000, 2000, 3000),
        default=(0,),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="append both complete evidence chains; otherwise read-only",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    evidence = collect_inflation_release_evidence(
        page_count=args.pages,
        start_page=args.start_page,
        archive_shards=tuple(args.shards),
    )
    gate = validate_inflation_coverage(evidence)
    result: dict[str, object] = {
        "mode": "apply" if args.apply else "dry_run",
        "coverage": evidence_summary(evidence),
        "apply_gate": gate,
    }
    if args.apply:
        if not gate["ready"]:
            raise RuntimeError(
                "refusing partial NBS inflation evidence apply: "
                + json.dumps(gate["missing"], ensure_ascii=False)
            )
        with SessionLocal() as db:
            ensure_indicators_seeded(db)
            result["inserted"] = store_inflation_evidence(db, evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

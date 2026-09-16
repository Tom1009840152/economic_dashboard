import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.fetchers import china_cycle_data as cycle
from scripts.repair_china_fiscal_current_snapshot import (
    MOF_DEEP_SCAN_PAGES,
    collect_fresh_fiscal_current,
    main,
)


class RepairChinaFiscalCurrentSnapshotCliTests(unittest.TestCase):
    def test_collector_always_uses_a_ten_page_mof_deep_scan(self) -> None:
        expected = {"fresh": "fiscal"}
        with patch(
            "scripts.repair_china_fiscal_current_snapshot._load_fiscal",
            return_value=expected,
        ) as loader:
            result = collect_fresh_fiscal_current()

        self.assertEqual(MOF_DEEP_SCAN_PAGES, 10)
        self.assertIs(result, expected)
        loader.assert_called_once_with(
            page_count=10,
            refresh_releases=True,
            strict_live=True,
        )

    def test_refresh_flag_reaches_every_mof_release_detail(self) -> None:
        source_url = "https://www.mof.gov.cn/202501/release.html"
        source = """
          <html><body>
          2024年，全国一般公共预算支出100亿元，比上年增长1.0%。
          全国政府性基金预算支出50亿元，比上年下降1.0%。
          </body></html>
        """
        with (
            patch.object(
                cycle,
                "_mof_catalog",
                return_value=((source_url, "2024年财政收支情况"),),
            ) as catalog,
            patch.object(
                cycle,
                "_get_mof_archive_text",
                return_value=source,
            ) as detail,
            patch.object(
                cycle,
                "_publication_metadata",
                return_value={
                    "release_date": None,
                    "available_at": None,
                    "source_url": source_url,
                },
            ),
        ):
            cycle._load_fiscal(
                page_count=10,
                refresh_releases=True,
                strict_live=True,
            )

        detail.assert_called_once_with(
            source_url,
            refresh=True,
            allow_cache_fallback=False,
        )
        catalog.assert_called_once_with(
            cycle._MOF_FISCAL_BASE,
            10,
            strict_live=True,
        )

    def test_strict_live_read_rejects_last_good_cache_on_network_failure(self) -> None:
        url = "https://www.mof.gov.cn/strict-live.html"
        cached = "<html><body>" + ("cached fiscal page " * 20) + "</body></html>"
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            cache_path = cache_root / (
                hashlib.sha256(url.encode("utf-8")).hexdigest() + ".html"
            )
            cache_path.write_text(cached, encoding="utf-8")
            with (
                patch.object(cycle, "_MOF_ARCHIVE_CACHE", cache_root),
                patch.object(
                    cycle,
                    "_request_text",
                    side_effect=OSError("live MOF unavailable"),
                ),
                patch.object(cycle.time, "sleep"),
            ):
                with self.assertRaisesRegex(OSError, "live MOF unavailable"):
                    cycle._get_mof_archive_text(
                        url,
                        refresh=True,
                        allow_cache_fallback=False,
                    )

    def test_cli_is_dry_run_by_default_and_apply_is_explicit(self) -> None:
        fiscal = {"fresh": "fiscal"}
        db = object()
        context = MagicMock()
        context.__enter__.return_value = db
        context.__exit__.return_value = False
        with (
            patch(
                "scripts.repair_china_fiscal_current_snapshot.collect_fresh_fiscal_current",
                return_value=fiscal,
            ),
            patch(
                "scripts.repair_china_fiscal_current_snapshot.SessionLocal",
                return_value=context,
            ),
            patch(
                "scripts.repair_china_fiscal_current_snapshot.repair_fiscal_current_gap",
                return_value={"mode": "dry_run"},
            ) as repair,
            patch("builtins.print"),
            patch("sys.argv", ["repair_china_fiscal_current_snapshot"]),
        ):
            main()

        repair.assert_called_once_with(db, fiscal, apply=False)

        repair.reset_mock()
        with (
            patch(
                "scripts.repair_china_fiscal_current_snapshot.collect_fresh_fiscal_current",
                return_value=fiscal,
            ),
            patch(
                "scripts.repair_china_fiscal_current_snapshot.SessionLocal",
                return_value=context,
            ),
            patch(
                "scripts.repair_china_fiscal_current_snapshot.repair_fiscal_current_gap",
                return_value={"mode": "apply"},
            ) as repair,
            patch("builtins.print"),
            patch("sys.argv", ["repair_china_fiscal_current_snapshot", "--apply"]),
        ):
            main()

        repair.assert_called_once_with(db, fiscal, apply=True)


if __name__ == "__main__":
    unittest.main()

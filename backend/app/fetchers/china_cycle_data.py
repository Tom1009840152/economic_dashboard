"""China cycle-model inputs with source and release metadata.

The collectors intentionally keep raw series separate from derived dashboard
signals. Official NBS/PBOC/MOF release pages are preferred. Eastmoney is used
only as a historical transport mirror where the official archive is not
machine-readable; those observations are explicitly marked ``mirror_backfill``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import time
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import akshare as ak
import pandas as pd
import pdfplumber
import requests
from curl_cffi import requests as curl_requests
from lxml import html as lxml_html

from app.services.derived_metrics import (
    DERIVED_METRIC_SPECS,
    calculate_credit_metrics,
    calculate_fiscal_metrics,
)


_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_EASTMONEY_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EASTMONEY_CONSUMER_URL = "https://data.eastmoney.com/cjsj/xfzxx.html"
_EASTMONEY_HOUSE_URL = "https://data.eastmoney.com/cjsj/newhouse.html"
_EASTMONEY_INDUSTRIAL_URL = "https://data.eastmoney.com/cjsj/gyzjz.html"
_MOFCOM_TSF_URL = "https://data.mofcom.gov.cn/gnmy/shrzgm.shtml"
_NBS_RELEASE_BASE = "https://www.stats.gov.cn/sj/zxfb/"
_PBOC_RELEASE_BASE = "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/"
_PBOC_NEWS_BASE = "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/"
PBOC_YTD_DIFF_FORMULA_VERSION = "pboc_ytd_diff_v1"
_MOF_FISCAL_BASE = "https://gks.mof.gov.cn/tongjishuju/"
_MOF_BOND_BASE = "https://yss.mof.gov.cn/zhuantilanmu/dfzgl/sjtj/"
_PBOC_TSF_STOCK_PDFS = (
    # This official backfill contains 36 monthly rows from 2017-01 to 2019-12.
    (
        "https://www.pbc.gov.cn/diaochatongjisi/attachDir/2025/11/"
        "2025110511314347909.pdf",
        dt.date(2017, 1, 1),
    ),
    (
        "https://www.pbc.gov.cn/diaochatongjisi/fileDir/resource/cms/2024/01/"
        "2024011510325158987.pdf",
        None,
    ),
    (
        "https://www.pbc.gov.cn/diaochatongjisi/fileDir/resource/cms/2024/02/"
        "2024021917193173502.pdf",
        None,
    ),
    (
        "https://www.pbc.gov.cn/diaochatongjisi/attachDir/2025/11/"
        "2025111416274070278.pdf",
        None,
    ),
    (
        "https://www.pbc.gov.cn/diaochatongjisi/attachDir/2025/11/"
        "2025111913535780670.pdf",
        None,
    ),
)
_CACHE_TTL = 6 * 60 * 60
_cache: dict[str, tuple[float, object]] = {}
logger = logging.getLogger(__name__)
_nbs_session = curl_requests.Session(impersonate="chrome")
_NBS_ARCHIVE_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "nbs-release"
_PBOC_ARCHIVE_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "pboc-release"
_NBS_ARCHIVE_REQUEST_INTERVAL = 0.45
_PBOC_BOUNDARY_SCAN_PAGES = 12
_last_nbs_archive_request = 0.0


def _cached(key: str, loader):
    now = time.time()
    cached = _cache.get(key)
    if cached is None or now - cached[0] > _CACHE_TTL:
        value = loader()
        _cache[key] = (now, value)
    return _cache[key][1]


def _text_from_html(source: str) -> str:
    return " ".join(lxml_html.fromstring(source).text_content().split())


def _request_text(url: str) -> str:
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def _request_bytes(url: str) -> bytes:
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    return response.content


def _is_pboc_https_url(value: object) -> bool:
    """Accept only HTTPS resources hosted by the PBOC or its subdomains."""

    try:
        parsed = urlparse(str(value))
    except (TypeError, ValueError):
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (
        hostname == "pbc.gov.cn" or hostname.endswith(".pbc.gov.cn")
    )


def _valid_pboc_release_source(source: str) -> bool:
    """Reject challenge, truncated and unrelated pages before caching them."""

    if len(source) < 500 or "Please enable JavaScript" in source:
        return False
    try:
        text = _text_from_html(source)
    except (TypeError, ValueError):
        return False
    return "社会融资规模增量" in text and any(
        marker in text or marker in source
        for marker in ("文章来源", "发布时间", "发布日期", "PubDate")
    )


def _get_nbs_text(url: str) -> str:
    response = _nbs_session.get(url, headers=_HEADERS, timeout=30)
    if response.status_code == 404:
        raise FileNotFoundError(url)
    response.raise_for_status()
    source = response.content.decode("utf-8", errors="replace")
    if "Please enable JavaScript and refresh the page" in source:
        # Some historical shards challenge the browser-impersonating session
        # while serving the same official static page to a plain HTTPS client.
        # Use one bounded fallback, but still reject any challenge body so it
        # can never enter the evidence cache.
        source = _request_text(url)
        if "Please enable JavaScript and refresh the page" in source:
            raise RuntimeError("NBS website returned a JavaScript verification page")
    return source


def _get_nbs_archive_text(url: str, *, cache: bool = True) -> str:
    """Read one official archive page slowly and cache it for resumable backfills.

    The NBS archive applies a JavaScript challenge when a crawler bursts through
    many pages. D7 needs dozens of distinct monthly releases, so one-time
    backfills use a small on-disk cache and a bounded delay. Only successfully
    decoded official HTML is cached; a challenge page is never evidence.
    """

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cache_path = _NBS_ARCHIVE_CACHE / f"{digest}.html"
    if cache and cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    global _last_nbs_archive_request
    last_error: Exception | None = None
    # Index pages are mutable manifests and intentionally are not persisted.
    # A failed index should be resumed later from a small page range rather
    # than retried four times in a burst, which worsens upstream throttling.
    attempts = 4 if cache else 1
    for attempt in range(attempts):
        wait_for = _NBS_ARCHIVE_REQUEST_INTERVAL - (
            time.monotonic() - _last_nbs_archive_request
        )
        if wait_for > 0:
            time.sleep(wait_for)
        try:
            source = _get_nbs_text(url)
            _last_nbs_archive_request = time.monotonic()
            if cache:
                _NBS_ARCHIVE_CACHE.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(source, encoding="utf-8")
            return source
        except Exception as exc:
            _last_nbs_archive_request = time.monotonic()
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
    assert last_error is not None
    raise last_error


def _get_pboc_archive_text(url: str) -> str:
    """Read and persist one official PBOC detail page for resumable backfills."""

    if not _is_pboc_https_url(url):
        raise ValueError(f"refusing non-official PBOC archive URL: {url}")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cache_path = _PBOC_ARCHIVE_CACHE / f"{digest}.html"
    if cache_path.exists():
        cached = cache_path.read_text(encoding="utf-8")
        if _valid_pboc_release_source(cached):
            return cached
    source = _request_text(url)
    if not _valid_pboc_release_source(source):
        raise RuntimeError("PBOC website returned an invalid archive detail page")
    _PBOC_ARCHIVE_CACHE.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(source, encoding="utf-8")
    temporary.replace(cache_path)
    return source


def _publication_metadata(source: str, source_url: str) -> dict:
    text = _text_from_html(source)
    parsed_url = urlparse(source_url)
    hostname = (parsed_url.hostname or "").lower().rstrip(".")
    is_pboc = hostname == "pbc.gov.cn" or hostname.endswith(".pbc.gov.cn")
    is_nbs = parsed_url.scheme.lower() == "https" and (
        hostname == "stats.gov.cn" or hostname.endswith(".stats.gov.cn")
    )

    # Prefer a visibly labelled clock time over metadata. Old PBOC articles
    # were migrated to new CMS URLs whose ``createDate`` is the migration time,
    # while the article byline still preserves the original release second.
    try:
        document = lxml_html.fromstring(source)
        dom_time_text = " ".join(
            " ".join(node.text_content().split())
            for node in document.xpath("//*[@id='shijian']")
        )
        # Migrated NBS releases often lost their PubDate metadata while the
        # original publication clock remains visibly rendered immediately
        # below the article title. Restrict this fallback to that named title
        # block on the official NBS host so dates mentioned in the article body
        # can never become availability evidence.
        nbs_title_time_text = (
            " ".join(
                " ".join(node.text_content().split())
                for node in document.xpath(
                    "//*[contains(concat(' ', normalize-space(@class), ' '), "
                    "' detail-title-des ')]//p"
                )
            )
            if is_nbs
            else ""
        )
    except (TypeError, ValueError):
        dom_time_text = ""
        nbs_title_time_text = ""

    exact_patterns: list[tuple[str, str, int]] = [
        (
            dom_time_text,
            r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+"
            r"(\d{1,2}):(\d{2})(?::\d{2})?",
            0,
        ),
        (
            dom_time_text,
            r"(20\d{2})年(\d{1,2})月(\d{1,2})日?\s+"
            r"(\d{1,2}):(\d{2})(?::\d{2})?",
            0,
        ),
        (
            nbs_title_time_text,
            r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+"
            r"(\d{1,2}):(\d{2})(?::\d{2})?",
            0,
        ),
        (
            nbs_title_time_text,
            r"(20\d{2})年(\d{1,2})月(\d{1,2})日?\s+"
            r"(\d{1,2}):(\d{2})(?::\d{2})?",
            0,
        ),
        (
            text,
            r"(?:文章来源|发布时间|发布日期)[:：]?\s*"
            r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+"
            r"(\d{1,2}):(\d{2})(?::\d{2})?",
            0,
        ),
        (
            text,
            r"(?:文章来源|发布时间|发布日期)[:：]?\s*"
            r"(20\d{2})年(\d{1,2})月(\d{1,2})日?\s+"
            r"(\d{1,2}):(\d{2})(?::\d{2})?",
            0,
        ),
        (
            source,
            r"PubDate[^>]*content=[\"']"
            r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+"
            r"(\d{1,2}):(\d{2})(?::\d{2})?",
            re.I,
        ),
    ]
    if not is_pboc:
        exact_patterns.append(
            (
                source,
                r"createDate[^>]*content=[\"']"
                r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+"
                r"(\d{1,2}):(\d{2})(?::\d{2})?",
                re.I,
            )
        )
    match = next(
        (
            found
            for haystack, pattern, flags in exact_patterns
            if (found := re.search(pattern, haystack, flags)) is not None
        ),
        None,
    )
    if match is not None:
        year, month, day, hour, minute = (
            int(match.group(index)) for index in range(1, 6)
        )
        published = dt.datetime(year, month, day, hour, minute)
        return {
            "release_date": published.date(),
            "available_at": published,
            "source_url": source_url,
        }

    date_patterns: list[tuple[str, str, int]] = [
        (
            source,
            r"PubDate[^>]*content=[\"']"
            r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})",
            re.I,
        ),
        (
            text,
            r"(?:文章来源|发布时间|发布日期)[:：]?\s*"
            r"(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})(?:日)?",
            0,
        ),
    ]
    if not is_pboc:
        date_patterns.insert(
            1,
            (
                source,
                r"createDate[^>]*content=[\"']"
                r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})",
                re.I,
            ),
        )
    match = next(
        (
            found
            for haystack, pattern, flags in date_patterns
            if (found := re.search(pattern, haystack, flags)) is not None
        ),
        None,
    )
    if match is None:
        return {"release_date": None, "available_at": None, "source_url": source_url}
    year, month, day = (int(match.group(index)) for index in range(1, 4))
    released = dt.date(year, month, day)
    # A date-only page does not prove that the observation was available at
    # midnight. Keep the date for display, but exclude it from strict intraday
    # as-of reconstruction until an actual publication time is known.
    return {
        "release_date": released,
        "available_at": None,
        "source_url": source_url,
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        "date",
        "value",
        "release_date",
        "available_at",
        "source_url",
        "status",
        "formula_version",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame(rows)
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.date
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result = result.dropna(subset=["date", "value"])
    for column in columns:
        if column not in result:
            result[column] = None
    # Prefer an observation's own publication record over a value repeated in
    # a later rolling table with no original release time.
    result["_status_priority"] = result["status"].map(
        {
            "mirror_backfill": 0,
            "historical_backfill": 1,
            "derived_backfill": 1,
            "published": 2,
            "derived": 2,
        }
    ).fillna(0)
    return (
        result.sort_values(["date", "_status_priority"], kind="stable")[columns]
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _series_frames(rows_by_code: dict[str, list[dict]]) -> dict[str, pd.DataFrame]:
    return {code: _frame(rows) for code, rows in rows_by_code.items()}


def _merge_bundles(*bundles: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    codes = set().union(*(bundle.keys() for bundle in bundles))
    return {
        code: _frame(
            [
                row
                for bundle in bundles
                for row in bundle.get(code, pd.DataFrame()).to_dict("records")
            ]
        )
        for code in codes
    }


def _load_cn_ip_mirror_backfill() -> pd.DataFrame:
    """Load the long industrial-production history from a transport mirror.

    AkShare exposes a column named ``发布时间`` for this dataset, but its values
    are the first day of the observation month rather than recoverable release
    timestamps. Deliberately ignore it: mirror observations can support the
    final-value matrix, but must remain unavailable to strict pseudo-real-time
    backtests.
    """
    raw = ak.macro_china_gyzjz()
    required = {"月份", "同比增长", "累计增长"}
    missing = required.difference(raw.columns)
    if missing:
        raise KeyError(f"industrial-production mirror missing columns: {sorted(missing)}")

    rows: list[dict] = []
    for _, row in raw.iterrows():
        match = re.fullmatch(r"(20\d{2})年(\d{1,2})月份?", str(row["月份"]).strip())
        if not match:
            continue
        observed = dt.date(int(match.group(1)), int(match.group(2)), 1)
        if observed < dt.date(2008, 2, 1):
            continue
        value = pd.to_numeric(row["同比增长"], errors="coerce")
        # Since 2015, January-February is released only as a combined rate. The
        # mirror leaves the single-month field blank and stores that legitimate
        # combined observation in the cumulative column. Keep it at February;
        # never manufacture a January value from it.
        if pd.isna(value) and observed.month == 2:
            value = pd.to_numeric(row["累计增长"], errors="coerce")
        if pd.isna(value):
            continue
        rows.append(
            {
                "date": observed,
                "value": float(value),
                "release_date": None,
                "available_at": None,
                "source_url": _EASTMONEY_INDUSTRIAL_URL,
                "status": "mirror_backfill",
            }
        )
    return _frame(rows)


def _merge_cn_ip_history(
    official: pd.DataFrame, mirror: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Join long mirror history to rolling official observations.

    The official frame is annotated before merging so ``_frame``'s status
    priority resolves every overlap in favour of the NBS observation, even if
    callers provide only the legacy ``date``/``value`` pair.
    """
    official_rows: list[dict] = []
    for row in official.to_dict("records"):
        has_release_metadata = (
            row.get("release_date") is not None
            and not pd.isna(row.get("release_date"))
            and row.get("available_at") is not None
            and not pd.isna(row.get("available_at"))
        )
        official_rows.append(
            {
                **row,
                "source_url": row.get("source_url") or _NBS_RELEASE_BASE,
                # Without the original publication timestamp this remains a
                # historical snapshot. Giving it published priority would
                # overwrite richer D7 provenance on every normal refresh.
                "status": row.get("status")
                or ("published" if has_release_metadata else "historical_backfill"),
            }
        )
    if mirror is None:
        mirror = _cached("china_ip_mirror_backfill", _load_cn_ip_mirror_backfill)
    return _frame([*mirror.to_dict("records"), *official_rows])


def fetch_cn_industrial_production_history() -> pd.DataFrame:
    """Return official recent CN_IP observations plus the mirror backfill."""
    # Lazy import avoids a module cycle while keeping the existing NBS parser as
    # the single owner of current official observations.
    from app.fetchers.nbs_cycle import _load_industrial

    return _merge_cn_ip_history(_load_industrial())


def _period_date(title: str, body: str = "") -> dt.date | None:
    source = f"{title} {body[:1500]}"
    year_match = re.search(r"(20\d{2})年", source)
    if not year_match:
        return None
    year = int(year_match.group(1))
    range_match = re.search(r"1\s*[—–-]\s*(\d{1,2})\s*月", source)
    if range_match:
        return dt.date(year, int(range_match.group(1)), 1)
    if "前三季度" in source:
        return dt.date(year, 9, 1)
    if "上半年" in source:
        return dt.date(year, 6, 1)
    if "一季度" in source:
        return dt.date(year, 3, 1)
    # Annual PBOC/MOF report titles normally omit ``全年``. Resolve those from
    # the title before scanning body text, whose first paragraph may mention a
    # particular month and otherwise misclassify the annual observation.
    if (
        not re.search(r"\d{1,2}\s*月份?|季度|半年", title)
        and re.search(r"(?:社会融资规模|金融统计|财政收支).*?(?:报告|情况)", title)
    ):
        return dt.date(year, 12, 1)
    month_match = re.search(r"(?:^|年)(\d{1,2})月份?", source)
    if month_match:
        return dt.date(year, int(month_match.group(1)), 1)
    if "全年" in source or re.search(rf"{year}年(?:全国|金融|财政)", title):
        return dt.date(year, 12, 1)
    return None


def _signed(direction: str, value: str) -> float:
    number = float(value)
    return -number if direction == "下降" else number


def _nbs_history_rows(table: pd.DataFrame, indicator_name: str) -> list[dict]:
    if indicator_name not in table.index:
        raise KeyError(f"NBS indicator missing: {indicator_name}")
    rows: list[dict] = []
    for period, raw_value in table.loc[indicator_name].items():
        match = re.fullmatch(r"(20\d{2})年(\d{1,2})月", str(period).strip())
        value = pd.to_numeric(raw_value, errors="coerce")
        if not match or pd.isna(value):
            continue
        rows.append(
            {
                "date": dt.date(int(match.group(1)), int(match.group(2)), 1),
                "value": float(value),
                "source_url": "https://data.stats.gov.cn/dg/website/page.html#/pc/national/monthData",
                "status": "historical_backfill",
            }
        )
    return rows


def _load_nbs_pmi_history() -> dict[str, pd.DataFrame]:
    manufacturing = ak.macro_china_nbs_nation(
        "月度数据", "采购经理指数 > 制造业采购经理指数", "2005-"
    )
    non_manufacturing = ak.macro_china_nbs_nation(
        "月度数据", "采购经理指数 > 非制造业采购经理指数", "2005-"
    )
    manufacturing_mapping = {
        "CN_PMI_PRODUCTION": "生产指数(%)",
        "CN_PMI_NEW_ORDERS": "新订单指数(%)",
        "CN_PMI_NEW_EXPORT_ORDERS": "新出口订单指数(%)",
        "CN_PMI_EMPLOYMENT": "从业人员指数(%)",
        "CN_PMI_RAW_MATERIAL_INVENTORY": "原材料库存指数(%)",
        "CN_PMI_FINISHED_GOODS_INVENTORY": "产成品库存指数(%)",
        "CN_PMI_EXPECTATIONS": "生产经营活动预期指数(%)",
    }
    non_manufacturing_mapping = {
        "CN_NMI": "商务活动指数(%)",
        "CN_NMI_NEW_ORDERS": "新订单指数(%)",
        "CN_NMI_EMPLOYMENT": "从业人员指数(%)",
        "CN_NMI_EXPECTATIONS": "业务活动预期指数(%)",
    }
    output = {
        code: _nbs_history_rows(manufacturing, name)
        for code, name in manufacturing_mapping.items()
    }
    output.update(
        {
            code: _nbs_history_rows(non_manufacturing, name)
            for code, name in non_manufacturing_mapping.items()
        }
    )
    return _series_frames(output)


def _load_nbs_industry_history() -> dict[str, pd.DataFrame]:
    table = ak.macro_china_nbs_nation(
        "月度数据", "工业 > 工业企业主要经济指标", "2005-"
    )
    output = {
        "CN_IND_REVENUE_YTD": (
            _nbs_history_rows(table, "主营业务收入_累计值(亿元)")
            + _nbs_history_rows(table, "营业收入_累计值(亿元)")
        ),
        "CN_IND_REVENUE_YTD_YOY": (
            _nbs_history_rows(table, "主营业务收入累计增长(%)")
            + _nbs_history_rows(table, "营业收入累计增长(%)")
        ),
        "CN_IND_PROFIT_YTD": _nbs_history_rows(table, "利润总额_累计值(亿元)"),
        "CN_IND_PROFIT_YTD_YOY": _nbs_history_rows(table, "利润总额累计增长(%)"),
        "CN_IND_FINISHED_INVENTORY_YOY": _nbs_history_rows(table, "产成品存货增减(%)"),
        "CN_IND_PROFIT_MONTHLY_YOY": [],
        "CN_IND_INVENTORY_DAYS": [],
    }

    current_ytd = {
        row["date"]: row["value"]
        for row in _nbs_history_rows(table, "利润总额_累计值(亿元)")
    }
    prior_ytd = {
        row["date"]: row["value"]
        for row in _nbs_history_rows(table, "利润总额上年同期_累计值(亿元)")
    }
    for observed, current_value in sorted(current_ytd.items()):
        if observed.month <= 2:
            continue
        previous_date = dt.date(observed.year, observed.month - 1, 1)
        if previous_date not in current_ytd or observed not in prior_ytd or previous_date not in prior_ytd:
            continue
        current_month = current_value - current_ytd[previous_date]
        prior_month = prior_ytd[observed] - prior_ytd[previous_date]
        if prior_month == 0:
            continue
        output["CN_IND_PROFIT_MONTHLY_YOY"].append(
            {
                "date": observed,
                "value": (current_month / prior_month - 1) * 100,
                "source_url": "https://data.stats.gov.cn/dg/website/page.html#/pc/national/monthData",
                "status": "derived_backfill",
            }
        )
    return _series_frames(output)


def _load_nbs_property_history() -> dict[str, pd.DataFrame]:
    investment = ak.macro_china_nbs_nation(
        "月度数据", "房地产 > 房地产开发投资情况", "2005-"
    )
    construction = ak.macro_china_nbs_nation(
        "月度数据", "房地产 > 房地产施工、竣工面积", "2005-"
    )
    sales_area = ak.macro_china_nbs_nation(
        "月度数据", "房地产 > 新建商品房销售面积", "2005-"
    )
    sales_value = ak.macro_china_nbs_nation(
        "月度数据", "房地产 > 新建商品房销售额", "2005-"
    )
    sources = {
        "CN_RE_INVEST_YTD": (investment, "房地产投资__累计值(亿元)"),
        "CN_RE_INVEST_YTD_YOY": (investment, "房地产投资_累计增长(%)"),
        "CN_RE_SALES_AREA_YTD": (sales_area, "新建商品房销售面积_累计值(万平方米)"),
        "CN_RE_SALES_AREA_YTD_YOY": (sales_area, "新建商品房销售面积累计增长(%)"),
        "CN_RE_SALES_VALUE_YTD": (sales_value, "新建商品房销售额_累计值(亿元)"),
        "CN_RE_SALES_VALUE_YTD_YOY": (sales_value, "新建商品房销售额累计增长(%)"),
        "CN_RE_STARTS_YTD": (construction, "房地产新开工施工面积_累计值(万平方米)"),
        "CN_RE_STARTS_YTD_YOY": (construction, "房地产新开工施工面积累计增长(%)"),
        "CN_RE_CONSTRUCTION": (construction, "房地产施工面积_累计值(万平方米)"),
        "CN_RE_CONSTRUCTION_YOY": (construction, "房地产施工面积累计增长(%)"),
    }
    return _series_frames(
        {code: _nbs_history_rows(table, name) for code, (table, name) in sources.items()}
    )


def _nbs_release_catalog(
    page_count: int = 14,
    *,
    archive: bool = False,
    start_page: int = 0,
    archive_shard: int = 0,
) -> tuple[tuple[str, str], ...]:
    if page_count < 1:
        raise ValueError("page_count must be at least 1")
    if start_page < 0:
        raise ValueError("start_page cannot be negative")
    if archive_shard < 0 or archive_shard % 1000:
        raise ValueError("archive_shard must be zero or a non-negative multiple of 1000")

    cache_key = f"nbs_release_catalog:{archive_shard}:{start_page}:{page_count}"
    if not archive:
        cached = _cache.get(cache_key)
        if cached is not None and time.time() - cached[0] <= _CACHE_TTL:
            return cached[1]

    found: dict[str, str] = {}
    # The NBS archive is paginated by release month. Fourteen pages cover the
    # rolling 13-month PMI tables and recent hard-data releases.
    for page in range(start_page, start_page + page_count):
        if archive_shard:
            filename = (
                f"index_{archive_shard}.html"
                if page == 0
                else f"index_{archive_shard}_{page}.html"
            )
            url = urljoin(_NBS_RELEASE_BASE, filename)
        else:
            url = (
                _NBS_RELEASE_BASE
                if page == 0
                else urljoin(_NBS_RELEASE_BASE, f"index_{page}.html")
            )
        try:
            # Archive index pages change as new releases arrive, so they must
            # never use the persistent detail-page cache.
            source = (
                _get_nbs_archive_text(url, cache=False)
                if archive
                else _get_nbs_text(url)
            )
        except FileNotFoundError:
            logger.info("NBS archive ends before page %d", page)
            break
        except Exception:
            logger.warning("NBS archive index failed: %s", url, exc_info=True)
            continue
        document = lxml_html.fromstring(source)
        for anchor in document.xpath("//a[@href]"):
            title = (anchor.get("title") or " ".join(anchor.text_content().split())).strip()
            href = anchor.get("href")
            if title and href:
                found[urljoin(url, href)] = title
        if page_count > 20:
            time.sleep(0.15)
    result = tuple(found.items())
    # Ordinary refreshes share one bounded catalog result across all PMI
    # signals, including an empty result during an upstream challenge. This
    # prevents one failed refresh from immediately hammering the same indexes
    # again; archive runs deliberately bypass this cache.
    if not archive:
        _cache[cache_key] = (time.time(), result)
    return result


def _nbs_links(
    pattern: str,
    page_count: int = 14,
    *,
    archive: bool = False,
    start_page: int = 0,
    archive_shard: int = 0,
) -> list[tuple[str, str]]:
    matcher = re.compile(pattern)
    return [
        (url, title)
        for url, title in _nbs_release_catalog(
            page_count,
            archive=archive,
            start_page=start_page,
            archive_shard=archive_shard,
        )
        if matcher.search(title)
    ]


def _pmi_rows_from_table(table: pd.DataFrame, mapping: dict[str, int]) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {code: [] for code in mapping}
    for _, item in table.iterrows():
        period_match = re.fullmatch(r"(20\d{2})年(\d{1,2})月", str(item.iloc[0]).strip())
        if not period_match:
            continue
        observed = dt.date(int(period_match.group(1)), int(period_match.group(2)), 1)
        for code, column in mapping.items():
            value = pd.to_numeric(item.iloc[column], errors="coerce")
            if pd.notna(value):
                rows[code].append({"date": observed, "value": float(value)})
    return rows


def _load_pmi(
    page_count: int = 14, *, start_page: int = 0, archive_shard: int = 0
) -> dict[str, pd.DataFrame]:
    archive = page_count > 14 or start_page > 0 or archive_shard > 0
    links = _nbs_links(
        r"中国采购经理指数运行情况|中国制造业采购经理指数|"
        r"中国非制造业商务活动指数",
        page_count,
        archive=archive,
        start_page=start_page,
        archive_shard=archive_shard,
    )
    if not links:
        raise RuntimeError("NBS PMI release not found")
    # One current release already contains a 13-month rolling table. Historical
    # archive mode reads every monthly release to recover the original release
    # dates needed for pseudo-real-time backtests.
    selected_links = links if archive else links[:1]
    result = {
        code: []
        for code in (
            "CN_PMI_PRODUCTION",
            "CN_PMI_NEW_ORDERS",
            "CN_PMI_RAW_MATERIAL_INVENTORY",
            "CN_PMI_EMPLOYMENT",
            "CN_PMI_NEW_EXPORT_ORDERS",
            "CN_PMI_FINISHED_GOODS_INVENTORY",
            "CN_PMI_EXPECTATIONS",
            "CN_NMI",
            "CN_NMI_NEW_ORDERS",
            "CN_NMI_EMPLOYMENT",
            "CN_NMI_EXPECTATIONS",
        )
    }
    for source_url, title in reversed(selected_links):
        try:
            source = (
                _get_nbs_archive_text(source_url)
                if archive
                else _get_nbs_text(source_url)
            )
            tables = pd.read_html(StringIO(source))
            basic = next(
                (
                    table
                    for table in tables
                    if table.shape[1] == 7
                    and table.astype(str)
                    .apply(lambda col: col.str.contains("生产"))
                    .any()
                    .any()
                ),
                None,
            )
            detail = next(
                (
                    table
                    for table in tables
                    if table.shape[1] == 9
                    and table.astype(str)
                    .apply(lambda col: col.str.contains("新出口"))
                    .any()
                    .any()
                ),
                None,
            )
            non_manufacturing = next(
                (
                    table
                    for table in tables
                    if table.shape[1] == 7
                    and table.astype(str)
                    .apply(lambda col: col.str.contains("商务活动"))
                    .any()
                    .any()
                ),
                None,
            )
            if basic is None and non_manufacturing is None:
                raise ValueError("PMI release has neither manufacturing nor NMI table")
        except Exception:
            logger.warning("NBS PMI release failed: %s", source_url, exc_info=True)
            continue
        page_rows: dict[str, list[dict]] = {}
        if basic is not None:
            page_rows.update(
                _pmi_rows_from_table(
                    basic,
                    {
                        "CN_PMI_PRODUCTION": 2,
                        "CN_PMI_NEW_ORDERS": 3,
                        "CN_PMI_RAW_MATERIAL_INVENTORY": 4,
                        "CN_PMI_EMPLOYMENT": 5,
                    },
                )
            )
        if detail is not None:
            for code, rows in _pmi_rows_from_table(
                detail,
                {
                    "CN_PMI_NEW_EXPORT_ORDERS": 1,
                    "CN_PMI_FINISHED_GOODS_INVENTORY": 6,
                    "CN_PMI_EXPECTATIONS": 8,
                },
            ).items():
                page_rows[code] = rows
        if non_manufacturing is not None:
            for code, rows in _pmi_rows_from_table(
                non_manufacturing,
                {
                    "CN_NMI": 1,
                    "CN_NMI_NEW_ORDERS": 2,
                    "CN_NMI_EMPLOYMENT": 5,
                    "CN_NMI_EXPECTATIONS": 6,
                },
            ).items():
                page_rows[code] = rows

        metadata = _publication_metadata(source, source_url)
        current_period = _period_date(title)
        for code, rows in page_rows.items():
            for row in rows:
                row["source_url"] = source_url
                if row["date"] == current_period:
                    row.update(metadata)
                    row["status"] = "published"
                else:
                    row["status"] = "historical_backfill"
                result[code].append(row)
        if archive:
            time.sleep(0.15)
    return _series_frames(result)


def _load_nmi_with_release_metadata() -> dict[str, pd.DataFrame]:
    """Keep the existing long NMI history while enriching recent releases.

    ``CN_NMI`` predates the cycle-model collectors and already has a longer
    AkShare/NBS history in the general macro registry. Replacing that collector
    with the rolling release table alone would shrink every ordinary refresh to
    roughly 13 months and trip the row-count quality gate. Preserve the original
    history, then let exact official release rows win where publication metadata
    is available.
    """

    # Import lazily because macro_source participates in the aggregate fetcher
    # registry that imports this module.
    from app.fetchers.macro_source import fetch_cn_non_manufacturing_pmi

    history = fetch_cn_non_manufacturing_pmi().copy()
    history["status"] = "historical_backfill"
    history_bundle = {"CN_NMI": _frame(history.to_dict("records"))}
    try:
        release_bundle = {
            "CN_NMI": _cached("china_pmi_detail", _load_pmi)["CN_NMI"]
        }
    except Exception:
        logger.warning(
            "NBS rolling NMI release unavailable; retaining long history",
            exc_info=True,
        )
        return history_bundle
    return _merge_bundles(history_bundle, release_bundle)


def _growth_from_release(
    title: str,
    text: str,
    *,
    label: str,
) -> tuple[dt.date, float] | None:
    """Extract the release's own monthly growth observation.

    January-February is an official combined observation and is stored at
    February. A longer 1-N title is cumulative, so for N>2 the parser requires
    the article body to state the current month's rate explicitly.
    """

    compact_title = re.sub(r"\s+", "", title)
    range_match = re.search(
        rf"(20\d{{2}})年1[—–-](\d{{1,2}})月份?{label}(?:同比)?"
        rf"(增长|下降)(\d+(?:\.\d+)?)%",
        compact_title,
    )
    if range_match:
        year, month = int(range_match.group(1)), int(range_match.group(2))
        if month == 2:
            return (
                dt.date(year, month, 1),
                _signed(range_match.group(3), range_match.group(4)),
            )
        body_match = re.search(
            # Exclude the trailing ``N月份`` embedded in ``1—N月份``. Only a
            # standalone month in the article body is single-month evidence.
            rf"(?<![0-9—–\-至到~～]){month}\s*月份[，,]?\s*{label}"
            # Retail releases often insert the current-month amount before the
            # growth rate: ``40732亿元，同比增长2.0%``.
            rf"(?:\s*\d+(?:\.\d+)?\s*亿元[，,])?\s*(?:同比)?\s*"
            rf"(增长|下降)\s*(\d+(?:\.\d+)?)%",
            text,
        )
        if body_match:
            return dt.date(year, month, 1), _signed(*body_match.groups())
        return None

    monthly_match = re.search(
        rf"(20\d{{2}})年(\d{{1,2}})月份?{label}(?:同比)?"
        rf"(增长|下降)(\d+(?:\.\d+)?)%",
        compact_title,
    )
    if monthly_match:
        return (
            dt.date(int(monthly_match.group(1)), int(monthly_match.group(2)), 1),
            _signed(monthly_match.group(3), monthly_match.group(4)),
        )
    return None


def _load_nbs_hard_activity_evidence(
    page_count: int = 60,
    *,
    start_page: int = 0,
    archive_shard: int = 0,
) -> dict[str, pd.DataFrame]:
    """Recover original NBS publication evidence for IP and retail sales.

    Values come from each observation month's own official release page. The
    rolling final-value database is deliberately not joined here: a mismatch is
    handled by the evidence backfill command rather than overwriting today's
    snapshot with an older vintage.
    """

    archive = page_count > 14 or start_page > 0 or archive_shard > 0
    candidates = {
        "CN_IP": (r"规模以上工业增加值", r"规模以上工业增加值"),
        "CN_RETAIL": (r"社会消费品零售总额", r"社会消费品零售总额"),
    }
    output: dict[str, list[dict]] = {code: [] for code in candidates}
    catalog = _nbs_release_catalog(
        page_count,
        archive=archive,
        start_page=start_page,
        archive_shard=archive_shard,
    )
    for code, (title_pattern, label) in candidates.items():
        matcher = re.compile(title_pattern)
        for source_url, title in catalog:
            if not matcher.search(title):
                continue
            try:
                source = (
                    _get_nbs_archive_text(source_url)
                    if archive
                    else _get_nbs_text(source_url)
                )
                text = _text_from_html(source)
                parsed = _growth_from_release(title, text, label=label)
                metadata = _publication_metadata(source, source_url)
            except Exception:
                logger.warning(
                    "NBS hard-activity release failed: %s", source_url, exc_info=True
                )
                continue
            if parsed is None or metadata["available_at"] is None:
                continue
            observed, value = parsed
            output[code].append(
                {
                    "date": observed,
                    "value": value,
                    **metadata,
                    "status": "published",
                }
            )
    return _series_frames(output)


def _load_industrial_enterprises(
    page_count: int = 14, *, start_page: int = 0, archive_shard: int = 0
) -> dict[str, pd.DataFrame]:
    output: dict[str, list[dict]] = {
        "CN_IND_REVENUE_YTD": [],
        "CN_IND_REVENUE_YTD_YOY": [],
        "CN_IND_PROFIT_YTD": [],
        "CN_IND_PROFIT_YTD_YOY": [],
        "CN_IND_PROFIT_MONTHLY_YOY": [],
        "CN_IND_FINISHED_INVENTORY_YOY": [],
        "CN_IND_INVENTORY_DAYS": [],
    }
    archive = page_count > 14 or start_page > 0 or archive_shard > 0
    for source_url, title in _nbs_links(
        r"规模以上工业企业利润",
        page_count,
        archive=archive,
        start_page=start_page,
        archive_shard=archive_shard,
    ):
        try:
            source = (
                _get_nbs_archive_text(source_url)
                if archive
                else _get_nbs_text(source_url)
            )
            text = _text_from_html(source)
            tables = pd.read_html(StringIO(source))
        except Exception:
            logger.warning("NBS industrial-enterprise release failed: %s", source_url, exc_info=True)
            continue
        observed = _period_date(title, text)
        if observed is None:
            continue
        metadata = {**_publication_metadata(source, source_url), "status": "published"}
        summary = next((table for table in tables if table.shape[1] == 7), None)
        if summary is not None:
            total = next(
                (row for _, row in summary.iterrows() if str(row.iloc[0]).strip() == "总计"), None
            )
            if total is not None:
                for code, column in {
                    "CN_IND_REVENUE_YTD": 1,
                    "CN_IND_REVENUE_YTD_YOY": 2,
                    "CN_IND_PROFIT_YTD": 5,
                    "CN_IND_PROFIT_YTD_YOY": 6,
                }.items():
                    value = pd.to_numeric(total.iloc[column], errors="coerce")
                    if pd.notna(value):
                        output[code].append({"date": observed, "value": float(value), **metadata})

        inventory = re.search(
            r"产成品存货\s*[\d.]+\s*万亿元[，,]\s*(?:同比)?(增长|下降)\s*([\d.]+)%", text
        )
        if inventory:
            output["CN_IND_FINISHED_INVENTORY_YOY"].append(
                {"date": observed, "value": _signed(*inventory.groups()), **metadata}
            )
        days = re.search(r"产成品存货周转天数为\s*([\d.]+)\s*天", text)
        if days:
            output["CN_IND_INVENTORY_DAYS"].append(
                {"date": observed, "value": float(days.group(1)), **metadata}
            )
        monthly_profit = re.search(
            rf"{observed.month}\s*月份[，,]\s*规模以上工业企业利润同比(增长|下降)\s*([\d.]+)%",
            text,
        )
        if monthly_profit:
            output["CN_IND_PROFIT_MONTHLY_YOY"].append(
                {"date": observed, "value": _signed(*monthly_profit.groups()), **metadata}
            )
        if archive:
            time.sleep(0.15)
    return _series_frames(output)


def _property_rows_from_tables(
    tables: list[pd.DataFrame], observed: dt.date, metadata: dict
) -> dict[str, list[dict]]:
    """Extract nationwide property totals across historical NBS label changes.

    Older releases use ``商品房`` while newer releases use ``新建商品房``.
    Matching only the stable semantic prefix keeps both vintages comparable and
    prevents the regional tables or the indented residential sub-rows from
    being mistaken for the nationwide total.
    """

    output: dict[str, list[dict]] = {
        code: []
        for code in (
            "CN_RE_INVEST_YTD",
            "CN_RE_INVEST_YTD_YOY",
            "CN_RE_SALES_AREA_YTD",
            "CN_RE_SALES_AREA_YTD_YOY",
            "CN_RE_SALES_VALUE_YTD",
            "CN_RE_SALES_VALUE_YTD_YOY",
            "CN_RE_STARTS_YTD",
            "CN_RE_STARTS_YTD_YOY",
            "CN_RE_CONSTRUCTION",
            "CN_RE_CONSTRUCTION_YOY",
        )
    }
    labels = {
        "房地产开发投资": ("CN_RE_INVEST_YTD", "CN_RE_INVEST_YTD_YOY"),
        "新建商品房销售面积": (
            "CN_RE_SALES_AREA_YTD",
            "CN_RE_SALES_AREA_YTD_YOY",
        ),
        "商品房销售面积": (
            "CN_RE_SALES_AREA_YTD",
            "CN_RE_SALES_AREA_YTD_YOY",
        ),
        "新建商品房销售额": (
            "CN_RE_SALES_VALUE_YTD",
            "CN_RE_SALES_VALUE_YTD_YOY",
        ),
        "商品房销售额": (
            "CN_RE_SALES_VALUE_YTD",
            "CN_RE_SALES_VALUE_YTD_YOY",
        ),
        "房屋新开工面积": ("CN_RE_STARTS_YTD", "CN_RE_STARTS_YTD_YOY"),
        "房地产新开工施工面积": (
            "CN_RE_STARTS_YTD",
            "CN_RE_STARTS_YTD_YOY",
        ),
        "房屋施工面积": ("CN_RE_CONSTRUCTION", "CN_RE_CONSTRUCTION_YOY"),
        "房地产施工面积": ("CN_RE_CONSTRUCTION", "CN_RE_CONSTRUCTION_YOY"),
    }
    for table in tables:
        if table.shape[1] < 3:
            continue
        for _, row in table.iterrows():
            raw_label = re.sub(r"\s+", "", str(row.iloc[0])).strip()
            label = re.split(r"[（(]", raw_label, maxsplit=1)[0]
            pair = labels.get(label)
            if not pair:
                continue
            absolute = pd.to_numeric(row.iloc[1], errors="coerce")
            yoy = pd.to_numeric(row.iloc[2], errors="coerce")
            if pd.notna(absolute):
                output[pair[0]].append(
                    {"date": observed, "value": float(absolute), **metadata}
                )
            if pd.notna(yoy):
                output[pair[1]].append(
                    {"date": observed, "value": float(yoy), **metadata}
                )
    return output


def _load_real_estate_activity(
    page_count: int = 14, *, start_page: int = 0, archive_shard: int = 0
) -> dict[str, pd.DataFrame]:
    output: dict[str, list[dict]] = {
        code: []
        for code in (
            "CN_RE_INVEST_YTD",
            "CN_RE_INVEST_YTD_YOY",
            "CN_RE_SALES_AREA_YTD",
            "CN_RE_SALES_AREA_YTD_YOY",
            "CN_RE_SALES_VALUE_YTD",
            "CN_RE_SALES_VALUE_YTD_YOY",
            "CN_RE_STARTS_YTD",
            "CN_RE_STARTS_YTD_YOY",
            "CN_RE_CONSTRUCTION",
            "CN_RE_CONSTRUCTION_YOY",
        )
    }
    archive = page_count > 14 or start_page > 0 or archive_shard > 0
    for source_url, title in _nbs_links(
        r"全国房地产市场基本情况|全国房地产开发投资",
        page_count,
        archive=archive,
        start_page=start_page,
        archive_shard=archive_shard,
    ):
        observed = _period_date(title)
        if observed is None:
            continue
        try:
            source = (
                _get_nbs_archive_text(source_url)
                if archive
                else _get_nbs_text(source_url)
            )
            tables = pd.read_html(StringIO(source))
        except Exception:
            logger.warning("NBS real-estate release failed: %s", source_url, exc_info=True)
            continue
        metadata = {**_publication_metadata(source, source_url), "status": "published"}
        page_rows = _property_rows_from_tables(tables, observed, metadata)
        for code, rows in page_rows.items():
            output[code].extend(rows)
        if archive:
            time.sleep(0.15)
    return _series_frames(output)


def _load_tsf_components() -> dict[str, pd.DataFrame]:
    raw = ak.macro_china_shrzgm()
    mapping = {
        "CN_TSF": "社会融资规模增量",
        "CN_TSF_RMB_LOANS_FLOW": "其中-人民币贷款",
        "CN_CORP_BOND_FINANCING": "其中-企业债券",
    }
    output: dict[str, list[dict]] = {code: [] for code in mapping}
    for _, row in raw.iterrows():
        observed = pd.to_datetime(str(row["月份"]), format="%Y%m", errors="coerce")
        if pd.isna(observed):
            continue
        for code, column in mapping.items():
            value = pd.to_numeric(row[column], errors="coerce")
            if pd.notna(value):
                output[code].append(
                    {
                        "date": observed.date(),
                        "value": float(value),
                        "source_url": _MOFCOM_TSF_URL,
                        "status": "mirror_backfill",
                    }
                )
    return _series_frames(output)


def _pboc_release_catalog(
    page_count: int = 2, *, archive: bool = False
) -> tuple[tuple[str, str], ...]:
    """Return current PBOC credit releases plus the immediately preceding page.

    The PBOC archive does not use ``index_1.html``. Its actual pagination is
    ``11871-1.html``. Two pages cover the overlap needed to join the official
    current releases to the longer transport-mirror history without crawling
    hundreds of old detail pages on every refresh.
    """
    if page_count < 1:
        raise ValueError("page_count must be at least 1")
    cache_key = f"pboc_release_catalog:{page_count}"
    if not archive:
        cached = _cache.get(cache_key)
        if cached is not None and time.time() - cached[0] <= _CACHE_TTL:
            return cached[1]

    found: dict[str, str] = {}
    index_urls = [urljoin(_PBOC_RELEASE_BASE, "index.html")]
    index_urls.extend(
        urljoin(_PBOC_RELEASE_BASE, f"11871-{page}.html")
        for page in range(1, page_count)
    )
    for page_url in index_urls:
        try:
            source = _request_text(page_url)
        except Exception:
            logger.warning("PBOC archive index failed: %s", page_url, exc_info=True)
            continue
        document = lxml_html.fromstring(source)
        for anchor in document.xpath("//a[@href]"):
            title = " ".join(anchor.text_content().split())
            is_credit_release = (
                "金融统计数据报告" in title
                or (
                    "社会融资规模" in title
                    and ("存量" in title or "增量" in title)
                )
            )
            if is_credit_release and "地区社会融资规模" not in title:
                found[urljoin(page_url, anchor.get("href"))] = title
    result = tuple(found.items())
    if not archive:
        _cache[cache_key] = (time.time(), result)
    return result


def _pboc_news_release_catalog(
    page_count: int = 10,
    *,
    start_page: int = 1,
    include_older_boundary: bool = False,
) -> tuple[tuple[str, str], ...]:
    """Discover original monthly AFRE releases in the PBOC news archive.

    News page numbers drift whenever newer articles are inserted. The current
    first page is therefore read on every archive run to discover ``totalpage``;
    detail URL and content, rather than the page number, identify evidence.
    """

    if page_count < 1:
        raise ValueError("page_count must be at least 1")
    if start_page < 1:
        raise ValueError("start_page must be at least 1")
    first_source = _request_text(urljoin(_PBOC_NEWS_BASE, "index.html"))
    total_match = re.search(r"totalpage=[\"'](\d+)[\"']", first_source, re.I)
    if total_match is None:
        visible_total = re.search(
            r"(?:^|\s)1\s*/\s*(\d{1,4})(?:\s|$)",
            _text_from_html(first_source),
        )
        if visible_total is None and start_page > 1:
            raise RuntimeError("PBOC news archive page count could not be discovered")
        total_pages = int(visible_total.group(1)) if visible_total else 1
    else:
        total_pages = int(total_match.group(1))
    final_page = min(start_page + page_count - 1, total_pages)
    if start_page > final_page:
        return ()

    found: dict[str, str] = {}

    def collect(page: int) -> None:
        page_url = (
            urljoin(_PBOC_NEWS_BASE, "index.html")
            if page == 1
            else urljoin(_PBOC_NEWS_BASE, f"11040-{page}.html")
        )
        try:
            source = first_source if page == 1 else _request_text(page_url)
        except Exception:
            logger.warning("PBOC news archive index failed: %s", page_url, exc_info=True)
            return
        document = lxml_html.fromstring(source)
        for anchor in document.xpath("//a[@href]"):
            title = (anchor.get("title") or " ".join(anchor.text_content().split())).strip()
            if (
                re.search(r"社会融资规模(?:增量)?统计数据报告", title)
                and "地区社会融资规模" not in title
                and "存量" not in title
            ):
                target = urljoin(page_url, anchor.get("href"))
                if _is_pboc_https_url(target):
                    found[target] = title

    for page in range(start_page, final_page + 1):
        collect(page)

    # A monthly flow reconstructed from cumulative releases needs the previous
    # month. Archive batches move from newer to older pages, so scan just far
    # enough beyond the requested range to include one older release. This
    # makes adjacent batches reproduce a single full-range run at the boundary.
    if include_older_boundary and found and final_page < total_pages:
        scan_end = min(final_page + _PBOC_BOUNDARY_SCAN_PAGES, total_pages)
        for page in range(final_page + 1, scan_end + 1):
            count_before = len(found)
            collect(page)
            if len(found) > count_before:
                break
    return tuple(found.items())


def _amount_in_100m(text: str, label: str) -> float | None:
    match = re.search(
        rf"{label}\s*(?:为)?\s*(增加|减少)?\s*([\d.]+)\s*(万亿元|亿元)",
        text,
    )
    if not match:
        return None
    value = float(match.group(2)) * (10000 if match.group(3) == "万亿元" else 1)
    return -value if match.group(1) == "减少" else value


def _credit_ytd_values(text: str) -> dict[str, float]:
    """Extract cumulative AFRE components, normalized to 100 million yuan."""
    anchor = re.search(r"社会融资规模增量累计为\s*[\d.]+\s*(?:万亿元|亿元)", text)
    if not anchor:
        return {}
    # Component values follow the headline. Limiting the segment prevents a
    # similarly named stock item or bank-loan paragraph from being captured.
    segment = text[anchor.start() : anchor.start() + 1800]
    patterns = {
        "CN_TSF": r"社会融资规模增量累计",
        "CN_TSF_RMB_LOANS_FLOW": r"对实体经济发放的人民币贷款",
        "CN_CORP_BOND_FINANCING": r"企业债券净融资",
        "CN_GOV_BOND_FINANCING": r"政府债券净融资",
    }
    return {
        code: value
        for code, pattern in patterns.items()
        if (value := _amount_in_100m(segment, pattern)) is not None
    }


def _credit_monthly_values(
    text: str, observed: dt.date | None = None
) -> dict[str, float]:
    """Extract a release's direct monthly AFRE values in 100 million yuan.

    Direct monthly figures are preferred to differencing year-to-date totals:
    they preserve the value the PBOC actually published for that month and do
    not amplify revisions to an earlier cumulative observation.
    """

    month_pattern = str(observed.month) if observed is not None else r"\d{1,2}"
    anchors = re.finditer(
        rf"(?:(?P<year>20\d{{2}})年)?{month_pattern}月(?:份)?"
        r"社会融资规模(?:增量)?为\s*[\d.]+\s*(?:万亿元|亿元)",
        text,
    )
    anchor = next(
        (
            candidate
            for candidate in anchors
            if observed is None
            or candidate.group("year") is None
            or int(candidate.group("year")) == observed.year
        ),
        None,
    )
    if not anchor:
        return {}
    segment = text[anchor.start() : anchor.start() + 1800]
    cumulative_boundary = segment.find("社会融资规模增量累计", anchor.end() - anchor.start())
    if cumulative_boundary >= 0:
        segment = segment[:cumulative_boundary]
    patterns = {
        "CN_TSF": r"社会融资规模增量",
        "CN_TSF_RMB_LOANS_FLOW": r"(?:当月)?对实体经济发放的人民币贷款",
        "CN_CORP_BOND_FINANCING": r"企业债券(?:融资净|净融资)",
        "CN_GOV_BOND_FINANCING": r"政府债券净融资",
    }
    return {
        code: value
        for code, pattern in patterns.items()
        if (value := _amount_in_100m(segment, pattern)) is not None
    }


def _derive_monthly_from_ytd(rows: list[dict]) -> list[dict]:
    """Difference only adjacent published cumulative observations.

    The resulting month's visibility is the current release's visibility, not
    the prior release's. Missing months are deliberately left missing rather
    than differencing across a gap.
    """
    by_period = {row["date"]: row for row in rows}
    monthly: list[dict] = []
    for observed, row in sorted(by_period.items()):
        if observed.month == 1:
            value = row["value"]
        else:
            previous = by_period.get(dt.date(observed.year, observed.month - 1, 1))
            if previous is None:
                continue
            value = row["value"] - previous["value"]
        monthly.append(
            {
                **row,
                "value": round(value, 6),
                "status": "derived",
                "formula_version": PBOC_YTD_DIFF_FORMULA_VERSION,
            }
        )
    return monthly


def _numeric_line_before(lines: list[str], anchor: str) -> list[float]:
    normalized_anchor = re.sub(r"\s+", "", anchor).casefold()
    for index, line in enumerate(lines):
        if re.sub(r"\s+", "", line).casefold() != normalized_anchor:
            continue
        for candidate in reversed(lines[:index]):
            values = [
                float(value)
                for value in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", candidate)
            ]
            if len(values) >= 2:
                return values
    return []


def _stock_rows_from_pdf_text(text: str, source_url: str) -> dict[str, list[dict]]:
    months: list[dt.date] = []
    for year, month in re.findall(r"(20\d{2})\.(\d{1,2})(?!\d)", text):
        observed = dt.date(int(year), int(month), 1)
        if observed not in months:
            months.append(observed)
        if len(months) == 12:
            break
    total = _numeric_line_before(text.splitlines(), "AFRE(stock)")
    rmb = _numeric_line_before(text.splitlines(), "RMB loans")
    total_growth = total[1::2]
    rmb_growth = rmb[1::2]
    output = {"CN_TSF_STOCK_YOY": [], "CN_TSF_RMB_LOAN_STOCK_YOY": []}
    for code, values in (
        ("CN_TSF_STOCK_YOY", total_growth),
        ("CN_TSF_RMB_LOAN_STOCK_YOY", rmb_growth),
    ):
        for observed, value in zip(months, values, strict=False):
            output[code].append(
                {
                    "date": observed,
                    "value": value,
                    "release_date": None,
                    "available_at": None,
                    "source_url": source_url,
                    "status": "historical_backfill",
                }
            )
    return output


def _stock_rows_from_legacy_tables(
    tables: list[list[list[str | None]]], source_url: str, start: dt.date
) -> dict[str, list[dict]]:
    candidates: list[list[tuple[float, float]]] = []
    for table in tables:
        values: list[tuple[float, float]] = []
        for row in table[1:]:
            numeric = [pd.to_numeric(cell, errors="coerce") for cell in row]
            numeric = [float(value) for value in numeric if pd.notna(value)]
            if len(numeric) >= 2:
                values.append((numeric[0], numeric[1]))
        if len(values) >= 24 and max(abs(pair[0]) for pair in values) < 200:
            candidates.append(values)
    output = {"CN_TSF_STOCK_YOY": [], "CN_TSF_RMB_LOAN_STOCK_YOY": []}
    if not candidates:
        return output
    values = max(candidates, key=len)
    for offset, (total_growth, rmb_growth) in enumerate(values):
        month_index = start.month - 1 + offset
        observed = dt.date(start.year + month_index // 12, month_index % 12 + 1, 1)
        metadata = {
            "date": observed,
            "release_date": None,
            "available_at": None,
            "source_url": source_url,
            "status": "historical_backfill",
        }
        output["CN_TSF_STOCK_YOY"].append({**metadata, "value": total_growth})
        output["CN_TSF_RMB_LOAN_STOCK_YOY"].append({**metadata, "value": rmb_growth})
    return output


def _parse_pboc_stock_pdf(
    content: bytes, source_url: str, legacy_start: dt.date | None = None
) -> dict[str, list[dict]]:
    with pdfplumber.open(BytesIO(content)) as document:
        if legacy_start is not None:
            tables = [table for page in document.pages for table in page.extract_tables()]
            return _stock_rows_from_legacy_tables(tables, source_url, legacy_start)
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    return _stock_rows_from_pdf_text(text, source_url)


def _load_pboc_stock_archives() -> dict[str, pd.DataFrame]:
    output: dict[str, list[dict]] = {
        "CN_TSF_STOCK_YOY": [],
        "CN_TSF_RMB_LOAN_STOCK_YOY": [],
    }
    for source_url, legacy_start in _PBOC_TSF_STOCK_PDFS:
        try:
            parsed = _parse_pboc_stock_pdf(
                _request_bytes(source_url), source_url, legacy_start
            )
        except Exception:
            logger.warning("PBOC stock PDF failed: %s", source_url, exc_info=True)
            continue
        for code in output:
            output[code].extend(parsed[code])
    return _series_frames(output)


def _load_pboc_credit(
    page_count: int = 2, *, archive: bool = False, start_page: int = 1
) -> dict[str, pd.DataFrame]:
    # The stock PDF bundle has no precise original publication timestamps and
    # therefore cannot contribute evidence to a strict archive backfill.
    archives = (
        _series_frames(
            {"CN_TSF_STOCK_YOY": [], "CN_TSF_RMB_LOAN_STOCK_YOY": []}
        )
        if archive
        else _load_pboc_stock_archives()
    )
    output: dict[str, list[dict]] = {
        "CN_TSF": [],
        "CN_TSF_RMB_LOANS_FLOW": [],
        "CN_CORP_BOND_FINANCING": [],
        "CN_TSF_STOCK_YOY": archives["CN_TSF_STOCK_YOY"].to_dict("records"),
        "CN_TSF_RMB_LOAN_STOCK_YOY": archives[
            "CN_TSF_RMB_LOAN_STOCK_YOY"
        ].to_dict("records"),
        "CN_GOV_BOND_FINANCING_YTD": [],
        "CN_GOV_BOND_FINANCING": [],
    }
    cumulative: dict[str, list[dict]] = {
        "CN_TSF": [],
        "CN_TSF_RMB_LOANS_FLOW": [],
        "CN_CORP_BOND_FINANCING": [],
        "CN_GOV_BOND_FINANCING": [],
    }
    direct: dict[str, list[dict]] = {code: [] for code in cumulative}
    catalog = (
        _pboc_news_release_catalog(
            page_count,
            start_page=start_page,
            include_older_boundary=True,
        )
        if archive
        else _pboc_release_catalog(page_count)
    )
    for source_url, title in catalog:
        try:
            source = (
                _get_pboc_archive_text(source_url)
                if archive
                else _request_text(source_url)
            )
        except Exception:
            logger.warning("PBOC credit release failed: %s", source_url, exc_info=True)
            continue
        text = _text_from_html(source)
        observed = _period_date(title, text)
        if observed is None:
            continue
        metadata = {**_publication_metadata(source, source_url), "status": "published"}
        stock = re.search(
            r"社会融资规模存量为[\d.]+万亿元[，,]\s*同比(增长|下降)\s*([\d.]+)%", text
        )
        if stock:
            output["CN_TSF_STOCK_YOY"].append(
                {"date": observed, "value": _signed(*stock.groups()), **metadata}
            )
        rmb_stock = re.search(
            r"对实体经济发放的人民币贷款余额[\d.]+万亿元[，,]\s*同比(增长|下降)\s*([\d.]+)%",
            text,
        )
        if rmb_stock:
            output["CN_TSF_RMB_LOAN_STOCK_YOY"].append(
                {"date": observed, "value": _signed(*rmb_stock.groups()), **metadata}
            )
        for code, value in _credit_monthly_values(text, observed).items():
            direct[code].append(
                {"date": observed, "value": value, **metadata}
            )
        for code, value in _credit_ytd_values(text).items():
            row = {"date": observed, "value": value, **metadata}
            cumulative[code].append(row)
            if code == "CN_GOV_BOND_FINANCING":
                output["CN_GOV_BOND_FINANCING_YTD"].append(row)

    for code, rows in cumulative.items():
        direct_periods = {row["date"] for row in direct[code]}
        output[code].extend(direct[code])
        output[code].extend(
            row
            for row in _derive_monthly_from_ytd(rows)
            if row["date"] not in direct_periods
        )
    return _series_frames(output)


def _load_credit_data() -> dict[str, pd.DataFrame]:
    return _merge_bundles(_load_tsf_components(), _load_pboc_credit())


def _mof_catalog(base_url: str, pages: int = 2) -> tuple[tuple[str, str], ...]:
    found: dict[str, str] = {}
    for page in range(pages):
        page_url = base_url if page == 0 else urljoin(base_url, f"index_{page}.htm")
        try:
            source = _request_text(page_url)
        except Exception:
            logger.warning("MOF archive index failed: %s", page_url, exc_info=True)
            continue
        document = lxml_html.fromstring(source)
        for anchor in document.xpath("//a[@href]"):
            title = (anchor.get("title") or " ".join(anchor.text_content().split())).strip()
            if title:
                found[urljoin(page_url, anchor.get("href"))] = title
    return tuple(found.items())


def _extract_yoy(text: str, label: str) -> tuple[float, float] | None:
    match = re.search(
        rf"{label}\s*([\d.]+)\s*亿元[，,]\s*同比(增长|下降)\s*([\d.]+)%", text
    )
    if not match:
        return None
    return float(match.group(1)), _signed(match.group(2), match.group(3))


def _load_fiscal(page_count: int = 2) -> dict[str, pd.DataFrame]:
    output: dict[str, list[dict]] = {
        "CN_FISCAL_GENERAL_SPEND_YTD": [],
        "CN_FISCAL_GENERAL_SPEND_YOY": [],
        "CN_FISCAL_FUND_EXPENDITURE_YTD": [],
        "CN_FISCAL_FUND_EXPENDITURE_YOY": [],
        "CN_FISCAL_BROAD_EXPENDITURE_YTD": [],
        "CN_FISCAL_BROAD_EXPENDITURE_YOY": [],
    }
    for source_url, title in _mof_catalog(_MOF_FISCAL_BASE, page_count):
        if "财政收支情况" not in title:
            continue
        try:
            source = _request_text(source_url)
        except Exception:
            logger.warning("MOF fiscal release failed: %s", source_url, exc_info=True)
            continue
        text = _text_from_html(source)
        observed = _period_date(title, text)
        if observed is None:
            continue
        general = _extract_yoy(text, r"全国一般公共预算支出")
        fund = _extract_yoy(text, r"全国政府性基金预算支出")
        metadata = {**_publication_metadata(source, source_url), "status": "published"}
        if general:
            output["CN_FISCAL_GENERAL_SPEND_YTD"].append(
                {"date": observed, "value": general[0], **metadata}
            )
            output["CN_FISCAL_GENERAL_SPEND_YOY"].append(
                {"date": observed, "value": general[1], **metadata}
            )
        if fund:
            output["CN_FISCAL_FUND_EXPENDITURE_YTD"].append(
                {"date": observed, "value": fund[0], **metadata}
            )
            output["CN_FISCAL_FUND_EXPENDITURE_YOY"].append(
                {"date": observed, "value": fund[1], **metadata}
            )
        if general and fund:
            broad = general[0] + fund[0]
            prior = general[0] / (1 + general[1] / 100) + fund[0] / (1 + fund[1] / 100)
            broad_yoy = (broad / prior - 1) * 100 if prior else None
            output["CN_FISCAL_BROAD_EXPENDITURE_YTD"].append(
                {"date": observed, "value": broad, **metadata}
            )
            if broad_yoy is not None:
                output["CN_FISCAL_BROAD_EXPENDITURE_YOY"].append(
                    {"date": observed, "value": broad_yoy, **metadata, "status": "derived"}
                )
    return _series_frames(output)


def _special_bond_monthly_value(text: str, observed: dt.date) -> float | None:
    """Extract the monthly special-bond issuance, never the YTD amount."""
    month_anchor = re.search(
        rf"{observed.year}年\s*{observed.month}月(?:份)?\s*[，,]", text
    )
    if not month_anchor:
        return None
    # MOF puts the current-month subsection before the cumulative subsection.
    # Keep the window deliberately short so a 1-N month value cannot leak in.
    segment = text[month_anchor.start() : month_anchor.start() + 900]
    cumulative = re.search(
        rf"(?:1\s*[-—–至]\s*{observed.month}月|{observed.year}年\s*1\s*[-—–至])",
        segment[1:],
    )
    if cumulative:
        segment = segment[: cumulative.start() + 1]

    # Newer releases first state the newly issued total and then its general /
    # special split. Older releases directly state total special issuance.
    patterns = (
        r"发行新增地方政府债券[\d.]+亿元.{0,80}?"
        r"(?:其中[，,]?)?\s*(?:发行)?一般债券[\d.]+亿元[、，,;；]\s*"
        r"(?:发行)?专项债券\s*([\d.]+)\s*亿元",
        r"(?:其中[，,]?)?\s*发行专项债券\s*([\d.]+)\s*亿元",
        r"(?:其中[，,]?)?\s*专项债券\s*([\d.]+)\s*亿元",
    )
    for pattern in patterns:
        match = re.search(pattern, segment)
        if match:
            return float(match.group(1))
    return None


def _load_special_bonds(page_count: int = 2) -> pd.DataFrame:
    rows: list[dict] = []
    for source_url, title in _mof_catalog(_MOF_BOND_BASE, page_count):
        if "地方政府债券发行和债务余额情况" not in title:
            continue
        try:
            source = _request_text(source_url)
        except Exception:
            logger.warning("MOF local-bond release failed: %s", source_url, exc_info=True)
            continue
        text = _text_from_html(source)
        observed = _period_date(title, text)
        if observed is None:
            continue
        value = _special_bond_monthly_value(text, observed)
        if value is not None:
            rows.append(
                {
                    "date": observed,
                    "value": value,
                    **_publication_metadata(source, source_url),
                    "status": "published",
                }
            )
    return _frame(rows)


def _load_consumer_confidence() -> dict[str, pd.DataFrame]:
    params = {
        "columns": (
            "REPORT_DATE,TIME,CONSUMERS_FAITH_INDEX,FAITH_INDEX_SAME,FAITH_INDEX_SEQUENTIAL,"
            "CONSUMERS_ASTIS_INDEX,ASTIS_INDEX_SAME,ASTIS_INDEX_SEQUENTIAL,"
            "CONSUMERS_EXPECT_INDEX,EXPECT_INDEX_SAME,EXPECT_INDEX_SEQUENTIAL"
        ),
        "pageNumber": "1",
        "pageSize": "2000",
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
        "reportName": "RPT_ECONOMY_FAITH_INDEX",
    }
    response = requests.get(_EASTMONEY_API, params=params, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json().get("result", {}).get("data", [])
    mapping = {
        "CN_CONSUMER_CONFIDENCE": "CONSUMERS_FAITH_INDEX",
        "CN_CONSUMER_SATISFACTION": "CONSUMERS_ASTIS_INDEX",
        # AkShare 1.18.94 maps this field to satisfaction by mistake; read the
        # raw named field so expectations are not silently duplicated.
        "CN_CONSUMER_EXPECTATIONS": "CONSUMERS_EXPECT_INDEX",
    }
    output: dict[str, list[dict]] = {code: [] for code in mapping}
    for item in data:
        observed = pd.to_datetime(item.get("REPORT_DATE") or item.get("TIME"), errors="coerce")
        if pd.isna(observed):
            continue
        for code, field in mapping.items():
            value = pd.to_numeric(item.get(field), errors="coerce")
            if pd.notna(value):
                output[code].append(
                    {
                        "date": observed.date(),
                        "value": float(value),
                        "source_url": _EASTMONEY_CONSUMER_URL,
                        "status": "mirror_backfill",
                    }
                )
    return _series_frames(output)


def _load_enterprise_boom() -> pd.DataFrame:
    raw = ak.macro_china_enterprise_boom_index()
    rows: list[dict] = []
    for _, item in raw.iterrows():
        match = re.fullmatch(r"(20\d{2})年第([1-4])季度", str(item["季度"]).strip())
        value = pd.to_numeric(item["企业景气指数-指数"], errors="coerce")
        if not match or pd.isna(value):
            continue
        month = int(match.group(2)) * 3
        rows.append(
            {
                "date": dt.date(int(match.group(1)), month, 1),
                "value": float(value),
                "source_url": "https://data.eastmoney.com/cjsj/qyjqzs.html",
                "status": "mirror_backfill",
            }
        )
    return _frame(rows)


def _load_house_price_diffusion() -> dict[str, pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    page = 1
    while True:
        params = {
            "reportName": "RPT_ECONOMY_HOUSE_PRICE",
            "columns": "REPORT_DATE,CITY,FIRST_COMHOUSE_SEQUENTIAL",
            "pageNumber": str(page),
            "pageSize": "500",
            "sortColumns": "REPORT_DATE,CITY",
            "sortTypes": "-1,-1",
            "source": "WEB",
            "client": "WEB",
        }
        response = requests.get(_EASTMONEY_API, params=params, headers=_HEADERS, timeout=30)
        response.raise_for_status()
        result = response.json().get("result") or {}
        data = result.get("data") or []
        if not data:
            break
        frames.append(pd.DataFrame(data))
        if page >= int(result.get("pages") or page):
            break
        page += 1
    if not frames:
        return _series_frames(
            {"CN_RE_PRICE_RISING_SHARE": [], "CN_RE_PRICE_MOM_MEDIAN": []}
        )
    raw = pd.concat(frames, ignore_index=True)
    raw["date"] = pd.to_datetime(raw["REPORT_DATE"], errors="coerce").dt.date
    raw["index"] = pd.to_numeric(raw["FIRST_COMHOUSE_SEQUENTIAL"], errors="coerce")
    raw = raw.dropna(subset=["date", "index", "CITY"]).drop_duplicates(["date", "CITY"])
    output = {"CN_RE_PRICE_RISING_SHARE": [], "CN_RE_PRICE_MOM_MEDIAN": []}
    for observed, group in raw.groupby("date"):
        if group["CITY"].nunique() < 50:
            continue
        metadata = {
            "source_url": _EASTMONEY_HOUSE_URL,
            "status": "mirror_backfill",
        }
        output["CN_RE_PRICE_RISING_SHARE"].append(
            {"date": observed, "value": float((group["index"] > 100).mean() * 100), **metadata}
        )
        output["CN_RE_PRICE_MOM_MEDIAN"].append(
            {"date": observed, "value": float(group["index"].median() - 100), **metadata}
        )
    return _series_frames(output)


def _load_nominal_gdp() -> pd.DataFrame:
    raw = ak.macro_china_gdp()
    rows: list[dict] = []
    for _, item in raw.iterrows():
        match = re.fullmatch(r"(20\d{2})年第(?:1-)?([1-4])季度", str(item["季度"]).strip())
        value = pd.to_numeric(item["国内生产总值-绝对值"], errors="coerce")
        if not match or pd.isna(value):
            continue
        rows.append(
            {
                "date": dt.date(int(match.group(1)), int(match.group(2)) * 3, 1),
                "value": float(value),
                "source_url": "https://data.stats.gov.cn/",
                "status": "historical_backfill",
            }
        )
    return _frame(rows)


def _load_credit_impulse() -> dict[str, pd.DataFrame]:
    raw = _cached("china_credit_data", _load_credit_data)["CN_TSF"]
    tsf = pd.Series(
        pd.to_numeric(raw["value"], errors="coerce").to_numpy(),
        index=pd.to_datetime(raw["date"], errors="coerce"),
        dtype="float64",
    ).dropna()
    nominal_gdp = _load_nominal_gdp()
    gdp = pd.Series(
        pd.to_numeric(nominal_gdp["value"], errors="coerce").to_numpy(),
        index=pd.to_datetime(nominal_gdp["date"], errors="coerce"),
        dtype="float64",
    ).dropna()
    if tsf.empty or gdp.empty:
        return _series_frames({"CN_CREDIT_INTENSITY": [], "CN_CREDIT_IMPULSE": []})
    intensity, impulse = calculate_credit_metrics(tsf, gdp)

    def rows(code: str, series: pd.Series) -> list[dict]:
        return [
            {
                "date": dt.date(period.year, period.month, 1),
                "value": float(value),
                "source_url": "https://www.pbc.gov.cn/diaochatongjisi/",
                "status": "derived_backfill",
                "formula_version": DERIVED_METRIC_SPECS[code].version,
            }
            for period, value in series.items()
        ]

    return _series_frames(
        {
            "CN_CREDIT_INTENSITY": rows("CN_CREDIT_INTENSITY", intensity),
            "CN_CREDIT_IMPULSE": rows("CN_CREDIT_IMPULSE", impulse),
        }
    )


def _build_fiscal_impulse(fiscal: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    broad = fiscal["CN_FISCAL_BROAD_EXPENDITURE_YTD"]
    nominal_gdp = _load_nominal_gdp()
    spending = pd.Series(
        pd.to_numeric(broad["value"], errors="coerce").to_numpy(),
        index=pd.to_datetime(broad["date"], errors="coerce"),
        dtype="float64",
    ).dropna()
    gdp = pd.Series(
        pd.to_numeric(nominal_gdp["value"], errors="coerce").to_numpy(),
        index=pd.to_datetime(nominal_gdp["date"], errors="coerce"),
        dtype="float64",
    ).dropna()
    intensity, impulse = calculate_fiscal_metrics(spending, gdp)

    def rows(code: str, series: pd.Series) -> list[dict]:
        return [
            {
                "date": dt.date(period.year, period.month, 1),
                "value": float(value),
                "source_url": _MOF_FISCAL_BASE,
                "status": "derived_backfill",
                "formula_version": DERIVED_METRIC_SPECS[code].version,
            }
            for period, value in series.items()
        ]

    return _series_frames(
        {
            "CN_FISCAL_SPEND_INTENSITY": rows(
                "CN_FISCAL_SPEND_INTENSITY", intensity
            ),
            "CN_FISCAL_IMPULSE_PROXY": rows("CN_FISCAL_IMPULSE_PROXY", impulse),
        }
    )


def _load_fiscal_impulse() -> dict[str, pd.DataFrame]:
    fiscal = _cached("china_fiscal", _load_fiscal)
    return _build_fiscal_impulse(fiscal)


def _from_bundle(bundle_key: str, loader, code: str) -> pd.DataFrame:
    bundle = _cached(bundle_key, loader)
    return bundle[code].copy()


def _make_bundle_fetcher(bundle_key: str, loader, code: str):
    return lambda: _from_bundle(bundle_key, loader, code)


CHINA_CYCLE_FETCHERS = {
    "CN_IP": fetch_cn_industrial_production_history,
    "CN_NMI": _make_bundle_fetcher(
        "china_nmi_with_release_metadata",
        _load_nmi_with_release_metadata,
        "CN_NMI",
    ),
    **{
        code: _make_bundle_fetcher("china_pmi_detail", _load_pmi, code)
        for code in (
            "CN_PMI_PRODUCTION",
            "CN_PMI_NEW_ORDERS",
            "CN_PMI_NEW_EXPORT_ORDERS",
            "CN_PMI_EMPLOYMENT",
            "CN_PMI_RAW_MATERIAL_INVENTORY",
            "CN_PMI_FINISHED_GOODS_INVENTORY",
            "CN_PMI_EXPECTATIONS",
            "CN_NMI_NEW_ORDERS",
            "CN_NMI_EMPLOYMENT",
            "CN_NMI_EXPECTATIONS",
        )
    },
    **{
        code: _make_bundle_fetcher("china_industrial_enterprises", _load_industrial_enterprises, code)
        for code in (
            "CN_IND_REVENUE_YTD",
            "CN_IND_REVENUE_YTD_YOY",
            "CN_IND_PROFIT_YTD",
            "CN_IND_PROFIT_YTD_YOY",
            "CN_IND_PROFIT_MONTHLY_YOY",
            "CN_IND_FINISHED_INVENTORY_YOY",
            "CN_IND_INVENTORY_DAYS",
        )
    },
    **{
        code: _make_bundle_fetcher("china_real_estate_activity", _load_real_estate_activity, code)
        for code in (
            "CN_RE_INVEST_YTD",
            "CN_RE_INVEST_YTD_YOY",
            "CN_RE_SALES_AREA_YTD",
            "CN_RE_SALES_AREA_YTD_YOY",
            "CN_RE_SALES_VALUE_YTD",
            "CN_RE_SALES_VALUE_YTD_YOY",
            "CN_RE_STARTS_YTD",
            "CN_RE_STARTS_YTD_YOY",
            "CN_RE_CONSTRUCTION",
            "CN_RE_CONSTRUCTION_YOY",
        )
    },
    **{
        code: _make_bundle_fetcher("china_credit_data", _load_credit_data, code)
        for code in (
            "CN_TSF",
            "CN_TSF_RMB_LOANS_FLOW",
            "CN_CORP_BOND_FINANCING",
            "CN_TSF_STOCK_YOY",
            "CN_TSF_RMB_LOAN_STOCK_YOY",
            "CN_GOV_BOND_FINANCING_YTD",
            "CN_GOV_BOND_FINANCING",
        )
    },
    **{
        code: _make_bundle_fetcher("china_credit_impulse", _load_credit_impulse, code)
        for code in ("CN_CREDIT_INTENSITY", "CN_CREDIT_IMPULSE")
    },
    **{
        code: _make_bundle_fetcher("china_fiscal", _load_fiscal, code)
        for code in (
            "CN_FISCAL_GENERAL_SPEND_YTD",
            "CN_FISCAL_GENERAL_SPEND_YOY",
            "CN_FISCAL_FUND_EXPENDITURE_YTD",
            "CN_FISCAL_FUND_EXPENDITURE_YOY",
            "CN_FISCAL_BROAD_EXPENDITURE_YTD",
            "CN_FISCAL_BROAD_EXPENDITURE_YOY",
        )
    },
    **{
        code: _make_bundle_fetcher("china_consumer_confidence", _load_consumer_confidence, code)
        for code in (
            "CN_CONSUMER_CONFIDENCE",
            "CN_CONSUMER_SATISFACTION",
            "CN_CONSUMER_EXPECTATIONS",
        )
    },
    **{
        code: _make_bundle_fetcher("china_fiscal_impulse", _load_fiscal_impulse, code)
        for code in ("CN_FISCAL_SPEND_INTENSITY", "CN_FISCAL_IMPULSE_PROXY")
    },
    **{
        code: _make_bundle_fetcher("china_house_price_diffusion", _load_house_price_diffusion, code)
        for code in ("CN_RE_PRICE_RISING_SHARE", "CN_RE_PRICE_MOM_MEDIAN")
    },
    "CN_LOCAL_SPECIAL_BOND_ISSUANCE": lambda: _cached(
        "china_special_bonds", _load_special_bonds
    ).copy(),
    "CN_ENTERPRISE_BOOM": lambda: _cached("china_enterprise_boom", _load_enterprise_boom).copy(),
    "CN_GDP_NOMINAL_YTD": lambda: _cached("china_nominal_gdp", _load_nominal_gdp).copy(),
}

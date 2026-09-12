"""China cycle-model inputs with source and release metadata.

The collectors intentionally keep raw series separate from derived dashboard
signals. Official NBS/PBOC/MOF release pages are preferred. Eastmoney is used
only as a historical transport mirror where the official archive is not
machine-readable; those observations are explicitly marked ``mirror_backfill``.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from bisect import bisect_right
from functools import lru_cache
from io import StringIO
from urllib.parse import urljoin

import akshare as ak
import pandas as pd
import requests
from curl_cffi import requests as curl_requests
from lxml import html as lxml_html


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
_MOFCOM_TSF_URL = "https://data.mofcom.gov.cn/gnmy/shrzgm.shtml"
_NBS_RELEASE_BASE = "https://www.stats.gov.cn/sj/zxfb/"
_PBOC_RELEASE_BASE = "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/"
_MOF_FISCAL_BASE = "https://gks.mof.gov.cn/tongjishuju/"
_MOF_BOND_BASE = "https://zwgls.mof.gov.cn/tjsj/"
_CACHE_TTL = 6 * 60 * 60
_cache: dict[str, tuple[float, object]] = {}
logger = logging.getLogger(__name__)
_nbs_session = curl_requests.Session(impersonate="chrome")


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


def _get_nbs_text(url: str) -> str:
    response = _nbs_session.get(url, headers=_HEADERS, timeout=30)
    if response.status_code == 404:
        raise FileNotFoundError(url)
    response.raise_for_status()
    source = response.content.decode("utf-8", errors="replace")
    if "Please enable JavaScript and refresh the page" in source:
        raise RuntimeError("NBS website returned a JavaScript verification page")
    return source


def _publication_metadata(source: str, source_url: str) -> dict:
    text = _text_from_html(source)
    match = re.search(
        r"(?:PubDate|createDate)[^>]*content=[\"']"
        r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?",
        source,
        re.I,
    ) or re.search(
        r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text
    ) or re.search(
        r"发布日期[:：]?\s*(20\d{2})年(\d{1,2})月(\d{1,2})日"
        r"(?:\s+(\d{1,2}):(\d{2}))?",
        text,
    )
    if not match:
        return {"release_date": None, "available_at": None, "source_url": source_url}
    year, month, day = (int(match.group(index)) for index in range(1, 4))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    published = dt.datetime(year, month, day, hour, minute)
    return {
        "release_date": published.date(),
        "available_at": published,
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


@lru_cache(maxsize=4)
def _nbs_release_catalog(page_count: int = 14) -> tuple[tuple[str, str], ...]:
    found: dict[str, str] = {}
    # The NBS archive is paginated by release month. Fourteen pages cover the
    # rolling 13-month PMI tables and recent hard-data releases.
    for page in range(page_count):
        url = _NBS_RELEASE_BASE if page == 0 else urljoin(_NBS_RELEASE_BASE, f"index_{page}.html")
        try:
            source = _get_nbs_text(url)
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
    return tuple(found.items())


def _nbs_links(pattern: str, page_count: int = 14) -> list[tuple[str, str]]:
    matcher = re.compile(pattern)
    return [
        (url, title)
        for url, title in _nbs_release_catalog(page_count)
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


def _load_pmi(page_count: int = 14) -> dict[str, pd.DataFrame]:
    links = _nbs_links(r"中国采购经理指数运行情况", page_count)
    if not links:
        raise RuntimeError("NBS PMI release not found")
    # One current release already contains a 13-month rolling table. Historical
    # archive mode reads every monthly release to recover the original release
    # dates needed for pseudo-real-time backtests.
    selected_links = links[:1] if page_count <= 14 else links
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
            "CN_NMI_NEW_ORDERS",
            "CN_NMI_EMPLOYMENT",
            "CN_NMI_EXPECTATIONS",
        )
    }
    for source_url, title in reversed(selected_links):
        try:
            source = _get_nbs_text(source_url)
            tables = pd.read_html(StringIO(source))
            basic = next(
                table
                for table in tables
                if table.shape[1] == 7
                and table.astype(str).apply(lambda col: col.str.contains("生产")).any().any()
            )
            detail = next(
                table
                for table in tables
                if table.shape[1] == 9
                and table.astype(str).apply(lambda col: col.str.contains("新出口")).any().any()
            )
            non_manufacturing = next(
                table
                for table in tables
                if table.shape[1] == 7
                and table.astype(str).apply(lambda col: col.str.contains("商务活动")).any().any()
            )
        except Exception:
            logger.warning("NBS PMI release failed: %s", source_url, exc_info=True)
            continue
        page_rows = _pmi_rows_from_table(
            basic,
            {
                "CN_PMI_PRODUCTION": 2,
                "CN_PMI_NEW_ORDERS": 3,
                "CN_PMI_RAW_MATERIAL_INVENTORY": 4,
                "CN_PMI_EMPLOYMENT": 5,
            },
        )
        for code, rows in _pmi_rows_from_table(
            detail,
            {
                "CN_PMI_NEW_EXPORT_ORDERS": 1,
                "CN_PMI_FINISHED_GOODS_INVENTORY": 6,
                "CN_PMI_EXPECTATIONS": 8,
            },
        ).items():
            page_rows[code] = rows
        for code, rows in _pmi_rows_from_table(
            non_manufacturing,
            {
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
        if page_count > 20:
            time.sleep(0.15)
    return _series_frames(result)


def _load_industrial_enterprises(page_count: int = 14) -> dict[str, pd.DataFrame]:
    output: dict[str, list[dict]] = {
        "CN_IND_REVENUE_YTD": [],
        "CN_IND_REVENUE_YTD_YOY": [],
        "CN_IND_PROFIT_YTD": [],
        "CN_IND_PROFIT_YTD_YOY": [],
        "CN_IND_PROFIT_MONTHLY_YOY": [],
        "CN_IND_FINISHED_INVENTORY_YOY": [],
        "CN_IND_INVENTORY_DAYS": [],
    }
    for source_url, title in _nbs_links(r"规模以上工业企业利润", page_count):
        try:
            source = _get_nbs_text(source_url)
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
        if page_count > 20:
            time.sleep(0.15)
    return _series_frames(output)


def _load_real_estate_activity(page_count: int = 14) -> dict[str, pd.DataFrame]:
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
        "房地产开发投资（亿元）": ("CN_RE_INVEST_YTD", "CN_RE_INVEST_YTD_YOY"),
        "新建商品房销售面积（万平方米）": (
            "CN_RE_SALES_AREA_YTD",
            "CN_RE_SALES_AREA_YTD_YOY",
        ),
        "新建商品房销售额（亿元）": (
            "CN_RE_SALES_VALUE_YTD",
            "CN_RE_SALES_VALUE_YTD_YOY",
        ),
        "房屋新开工面积（万平方米）": ("CN_RE_STARTS_YTD", "CN_RE_STARTS_YTD_YOY"),
        "房屋施工面积（万平方米）": ("CN_RE_CONSTRUCTION", "CN_RE_CONSTRUCTION_YOY"),
    }
    for source_url, title in _nbs_links(r"全国房地产市场基本情况", page_count):
        observed = _period_date(title)
        if observed is None:
            continue
        try:
            source = _get_nbs_text(source_url)
            tables = pd.read_html(StringIO(source))
        except Exception:
            logger.warning("NBS real-estate release failed: %s", source_url, exc_info=True)
            continue
        metadata = {**_publication_metadata(source, source_url), "status": "published"}
        summary = next((table for table in tables if table.shape[1] == 3), None)
        if summary is None:
            continue
        for _, row in summary.iterrows():
            pair = labels.get(str(row.iloc[0]).strip())
            if not pair:
                continue
            absolute = pd.to_numeric(row.iloc[1], errors="coerce")
            yoy = pd.to_numeric(row.iloc[2], errors="coerce")
            if pd.notna(absolute):
                output[pair[0]].append({"date": observed, "value": float(absolute), **metadata})
            if pd.notna(yoy):
                output[pair[1]].append({"date": observed, "value": float(yoy), **metadata})
        if page_count > 20:
            time.sleep(0.15)
    return _series_frames(output)


def _load_tsf_components() -> dict[str, pd.DataFrame]:
    raw = ak.macro_china_shrzgm()
    mapping = {
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


@lru_cache(maxsize=1)
def _pboc_release_catalog() -> tuple[tuple[str, str], ...]:
    source = _request_text(urljoin(_PBOC_RELEASE_BASE, "index.html"))
    document = lxml_html.fromstring(source)
    found: dict[str, str] = {}
    for anchor in document.xpath("//a[@href]"):
        title = " ".join(anchor.text_content().split())
        if "金融统计数据报告" in title or "社会融资规模" in title:
            found[urljoin(_PBOC_RELEASE_BASE, anchor.get("href"))] = title
    return tuple(found.items())


def _load_pboc_credit() -> dict[str, pd.DataFrame]:
    output: dict[str, list[dict]] = {
        "CN_TSF_STOCK_YOY": [],
        "CN_TSF_RMB_LOAN_STOCK_YOY": [],
        "CN_GOV_BOND_FINANCING_YTD": [],
        "CN_GOV_BOND_FINANCING": [],
    }
    ytd_rows: list[dict] = []
    for source_url, title in _pboc_release_catalog():
        source = _request_text(source_url)
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
        government = re.search(r"政府债券净融资\s*([\d.]+)\s*(万亿元|亿元)", text)
        if government:
            value = float(government.group(1)) * (10000 if government.group(2) == "万亿元" else 1)
            row = {"date": observed, "value": value, **metadata}
            output["CN_GOV_BOND_FINANCING_YTD"].append(row)
            ytd_rows.append(row)

    by_period = {row["date"]: row for row in ytd_rows}
    for observed, row in sorted(by_period.items()):
        if observed.month == 1:
            flow = row["value"]
        else:
            previous_date = dt.date(observed.year, observed.month - 1, 1)
            previous = by_period.get(previous_date)
            if previous is None:
                continue
            flow = row["value"] - previous["value"]
        output["CN_GOV_BOND_FINANCING"].append(
            {**row, "value": round(flow, 6), "status": "derived"}
        )
    return _series_frames(output)


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
        match = re.search(
            rf"{observed.year}年{observed.month}月[，,].{{0,30}}?发行新增地方政府债券[\d.]+亿元[，,]"
            r"其中一般债券[\d.]+亿元、专项债券([\d.]+)亿元",
            text,
        )
        if match:
            rows.append(
                {
                    "date": observed,
                    "value": float(match.group(1)),
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


def _trailing_nominal_gdp(gdp_ytd: pd.DataFrame) -> dict[dt.date, float]:
    values = {row.date: float(row.value) for row in gdp_ytd.itertuples(index=False)}
    trailing: dict[dt.date, float] = {}
    for observed, current_ytd in sorted(values.items()):
        previous_annual = values.get(dt.date(observed.year - 1, 12, 1))
        previous_same_period = values.get(dt.date(observed.year - 1, observed.month, 1))
        if previous_annual is None or previous_same_period is None:
            continue
        trailing[observed] = current_ytd + previous_annual - previous_same_period
    return trailing


def _load_credit_impulse() -> dict[str, pd.DataFrame]:
    raw = ak.macro_china_shrzgm()
    tsf = pd.DataFrame(
        {
            "period": pd.to_datetime(raw["月份"].astype(str), format="%Y%m", errors="coerce"),
            "value": pd.to_numeric(raw["社会融资规模增量"], errors="coerce"),
        }
    ).dropna()
    if tsf.empty:
        return _series_frames({"CN_CREDIT_INTENSITY": [], "CN_CREDIT_IMPULSE": []})
    tsf["period"] = tsf["period"].dt.to_period("M")
    monthly = tsf.drop_duplicates("period", keep="last").set_index("period")["value"].sort_index()
    complete_index = pd.period_range(monthly.index.min(), monthly.index.max(), freq="M")
    rolling_credit = monthly.reindex(complete_index).rolling(12, min_periods=12).sum()

    gdp_quarters = _trailing_nominal_gdp(_load_nominal_gdp())
    quarter_dates = sorted(gdp_quarters)
    intensity: dict[dt.date, float] = {}
    for period, credit in rolling_credit.dropna().items():
        observed = dt.date(period.year, period.month, 1)
        position = bisect_right(quarter_dates, observed) - 1
        if position < 0:
            continue
        denominator = gdp_quarters[quarter_dates[position]]
        if denominator:
            intensity[observed] = float(credit / denominator * 100)

    metadata = {
        "source_url": "https://www.pbc.gov.cn/diaochatongjisi/",
        "status": "derived_backfill",
    }
    intensity_rows = [
        {"date": observed, "value": value, **metadata}
        for observed, value in sorted(intensity.items())
    ]
    impulse_rows = []
    for observed, value in sorted(intensity.items()):
        previous = intensity.get(dt.date(observed.year - 1, observed.month, 1))
        if previous is not None:
            impulse_rows.append(
                {"date": observed, "value": value - previous, **metadata}
            )
    return _series_frames(
        {"CN_CREDIT_INTENSITY": intensity_rows, "CN_CREDIT_IMPULSE": impulse_rows}
    )


def _build_fiscal_impulse(fiscal: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    broad = fiscal["CN_FISCAL_BROAD_EXPENDITURE_YTD"]
    broad_values = {
        row.date: float(row.value)
        for row in broad.itertuples(index=False)
        if row.date.month in {3, 6, 9, 12}
    }
    gdp_values = {
        row.date: float(row.value) for row in _load_nominal_gdp().itertuples(index=False)
    }
    intensity = {
        observed: spending / gdp_values[observed] * 100
        for observed, spending in broad_values.items()
        if observed in gdp_values and gdp_values[observed]
    }
    metadata = {
        "source_url": _MOF_FISCAL_BASE,
        "status": "derived_backfill",
    }
    intensity_rows = [
        {"date": observed, "value": value, **metadata}
        for observed, value in sorted(intensity.items())
    ]
    impulse_rows = []
    for observed, value in sorted(intensity.items()):
        previous = intensity.get(dt.date(observed.year - 1, observed.month, 1))
        if previous is not None:
            impulse_rows.append(
                {"date": observed, "value": value - previous, **metadata}
            )
    return _series_frames(
        {
            "CN_FISCAL_SPEND_INTENSITY": intensity_rows,
            "CN_FISCAL_IMPULSE_PROXY": impulse_rows,
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
        code: _make_bundle_fetcher("china_tsf_components", _load_tsf_components, code)
        for code in ("CN_TSF_RMB_LOANS_FLOW", "CN_CORP_BOND_FINANCING")
    },
    **{
        code: _make_bundle_fetcher("china_pboc_credit", _load_pboc_credit, code)
        for code in (
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

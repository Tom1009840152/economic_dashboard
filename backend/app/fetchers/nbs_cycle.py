"""Recent China industrial production and core CPI from NBS release pages.

Core CPI is stated in the monthly NBS commentary but is not exposed as a stable
series in the public data table, so this fetcher reads the official releases.
Only a rolling recent window is requested; the dashboard database retains older
observations after each upsert.
"""

import datetime as dt
import html
import re
import time
from urllib.parse import urljoin

import pandas as pd
import requests

NBS_BASE = "https://www.stats.gov.cn/sj/"
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.stats.gov.cn/sj/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
_CACHE_TTL = 6 * 60 * 60
_PAGE_COUNT = 14
_cache: dict[str, dict] = {}


def _page_url(section: str, page: int) -> str:
    suffix = "" if page == 0 else f"index_{page}.html"
    return urljoin(NBS_BASE, f"{section}/{suffix}")


def _get_text(url: str) -> str:
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    if "Please enable JavaScript and refresh the page" in response.text:
        raise RuntimeError("NBS website returned a JavaScript verification page")
    return response.text


def _plain_text(source: str) -> str:
    source = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", source, flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", source))).strip()


def _links(section: str, title_pattern: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    title_re = re.compile(title_pattern)
    link_re = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
    for page in range(_PAGE_COUNT):
        page_url = _page_url(section, page)
        for href, body in link_re.findall(_get_text(page_url)):
            title = _plain_text(body)
            if title_re.search(title):
                found[urljoin(page_url, href)] = title
    return [(url, title) for url, title in found.items()]


def _cached(key: str, loader) -> pd.DataFrame:
    now = time.time()
    entry = _cache.get(key)
    if entry is None or now - entry["ts"] > _CACHE_TTL:
        frame = loader()
        if frame.empty:
            raise RuntimeError(f"NBS release parser returned no rows for {key}")
        entry = {"df": frame, "ts": now}
        _cache[key] = entry
    return entry["df"].copy()


def _load_industrial() -> pd.DataFrame:
    rows: list[tuple[dt.date, float]] = []
    monthly_pattern = re.compile(
        r"(20\d{2})年(\d{1,2})月份规模以上工业增加值(?:同比)?(增长|下降)(\d+(?:\.\d+)?)%"
    )
    range_pattern = re.compile(
        r"(20\d{2})年1[—–-](\d{1,2})月份规模以上工业增加值(?:同比)?(增长|下降)(\d+(?:\.\d+)?)%"
    )
    article_pattern = re.compile(
        r"(\d{1,2})\s*月份[，,]\s*规模以上工业增加值同比\s*(?:实际\s*)?(增长|下降)\s*(\d+(?:\.\d+)?)%"
    )
    for url, title in _links("zxfb", r"规模以上工业增加值.*(?:增长|下降)"):
        match = monthly_pattern.search(title)
        if match:
            sign = -1 if match.group(3) == "下降" else 1
            rows.append((dt.date(int(match.group(1)), int(match.group(2)), 1), sign * float(match.group(4))))
            continue

        range_match = range_pattern.search(title)
        if not range_match:
            continue
        year, end_month = int(range_match.group(1)), int(range_match.group(2))
        # NBS publishes January-February as one combined observation.  For later
        # cumulative-title releases, the body contains the current month's rate.
        if end_month == 2:
            sign = -1 if range_match.group(3) == "下降" else 1
            rows.append((dt.date(year, 2, 1), sign * float(range_match.group(4))))
            continue
        article_matches = article_pattern.findall(_plain_text(_get_text(url)))
        if article_matches:
            cumulative_value = float(range_match.group(4))
            article_match = next(
                (item for item in article_matches if float(item[2]) != cumulative_value),
                article_matches[1] if len(article_matches) > 1 else article_matches[0],
            )
            sign = -1 if article_match[1] == "下降" else 1
            rows.append((dt.date(year, int(article_match[0]), 1), sign * float(article_match[2])))
    return pd.DataFrame(rows, columns=["date", "value"]).drop_duplicates("date").sort_values("date")


def _load_core_cpi() -> pd.DataFrame:
    rows: list[tuple[dt.date, float]] = []
    date_re = re.compile(r"(20\d{2})年(\d{1,2})月份CPI和PPI数据")
    value_re = re.compile(
        r"(?:扣除食品和能源价格的)?核心\s*CPI.{0,30}?同比(?:涨幅[^\d-]{0,8})?(上涨|下降|回升至|扩大至|为)?\s*(-?\d+(?:\.\d+)?)%"
    )
    for url, title in _links("sjjd", r"解读20\d{2}年\d{1,2}月份CPI和PPI数据"):
        date_match = date_re.search(title)
        if not date_match:
            continue
        text = _plain_text(_get_text(url))
        value_match = value_re.search(text)
        if value_match:
            value = float(value_match.group(2))
            if value_match.group(1) == "下降" and value > 0:
                value = -value
            rows.append((dt.date(int(date_match.group(1)), int(date_match.group(2)), 1), value))
    return pd.DataFrame(rows, columns=["date", "value"]).drop_duplicates("date").sort_values("date")


def fetch_cn_industrial_production() -> pd.DataFrame:
    return _cached("industrial", _load_industrial).reset_index(drop=True)


def fetch_cn_core_cpi() -> pd.DataFrame:
    return _cached("core_cpi", _load_core_cpi).reset_index(drop=True)


NBS_CYCLE_FETCHERS = {
    "CN_IP": fetch_cn_industrial_production,
    "CN_CORE_CPI": fetch_cn_core_cpi,
}

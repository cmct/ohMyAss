#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apple TV / iTunes 全地区、全语言字幕下载器

用法示例：
    python atv_subs.py tt1375666
    python atv_subs.py "Oppenheimer"
    python atv_subs.py tt15398776 --list
    python atv_subs.py tt15398776 -o "E:\\Subtitles\\Oppenheimer" --lang en,zh,fr
    python atv_subs.py "https://tv.apple.com/us/movie/inception/umc.cmc.6loas01ow0w4lkatxxloz7a6e"

功能：
  1. 输入 IMDB 编号 / 影片名 / Apple TV 链接；
  2. 用 JustWatch 找出该片在 Apple TV Store（iTunes 购买/租赁）上架的全部国家/地区；
  3. 直接调用 Apple TV 接口，逐个地区抓取 HLS 字幕清单（不依赖 isubrip 命令行）；
  4. 合并所有地区、所有语言、所有字幕类型（普通 / Forced / SDH-CC），按内容去重；
  5. 统一转成 SRT，并输出清单 subtitles_report.json。

依赖：
    pip install -U "isubrip>=2.7.4"
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import logging
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 控制台编码（Windows 下避免中文/日文/韩文字幕名导致 UnicodeEncodeError）
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            # 保留控制台当前编码（中文 Windows 通常是 GBK），只把无法编码的字符替换掉，
            # 避免日文/韩文字幕名导致 UnicodeEncodeError。批处理里会额外 chcp 65001。
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

try:
    import httpx2
    from isubrip.scrapers.appletv_scraper import AppleTVScraper
    from isubrip.scrapers.itunes_scraper import ItunesScraper
    from isubrip.scrapers.scraper import Scraper, SubtitlesDownloadError
    from isubrip.subtitle_formats.webvtt import WebVTTCaptionBlock
    from isubrip.utils import sanitize_path_segment, slugify_title
except ImportError as exc:  # pragma: no cover
    print("缺少依赖：isubrip / httpx2")
    print("请先运行：")
    print('    python -m pip install -U "isubrip>=2.7.4"')
    print(f"原始错误：{exc}")
    raise SystemExit(1)

# isubrip 内部日志在 Windows GBK 控制台可能因为日文/韩文而报错，这里全部静音。
logging.getLogger("isubrip").setLevel(logging.CRITICAL)
logging.getLogger("isubrip").propagate = False

SCRIPT_VERSION = "1.0.0"
ISUBRIP_VERSION: tuple[int, ...] = ()
try:
    from importlib.metadata import version as _package_version
    ISUBRIP_VERSION = tuple(int(part) for part in re.findall(r"\d+", _package_version("isubrip"))[:3])
except Exception:
    pass

JW_URL = "https://apis.justwatch.com/graphql"
JW_LOCALES_URL = "https://apis.justwatch.com/content/locales/state"
IMDB_SUGGEST_URL = "https://v2.sg.media-imdb.com/suggestion/t/{imdb_id}.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

APPLE_UMC_RE = re.compile(r"umc\.cmc\.[a-z0-9]{18,30}", re.IGNORECASE)
APPLE_TV_URL_RE = re.compile(
    r"https?://tv\.apple\.com/(?:(?P<cc>[a-z]{2})/)?(?P<kind>movie|episode|season|show)/"
    r"(?:[^/?#]+/)?(?P<umc>umc\.cmc\.[a-z0-9]{18,30})",
    re.IGNORECASE,
)
ITUNES_URL_RE = re.compile(r"https?://itunes\.apple\.com/", re.IGNORECASE)
IMDB_ID_RE = re.compile(r"^tt\d{6,10}$", re.IGNORECASE)

# 探测顺序：常见地区优先，方便日志与“首选地区”命名。
COUNTRY_PRIORITY = [
    "US", "GB", "CA", "AU", "IE", "NZ", "DE", "AT", "CH", "FR", "BE", "NL", "LU",
    "SE", "NO", "DK", "FI", "IS", "ES", "MX", "AR", "CL", "CO", "PE", "BR", "PT",
    "IT", "MT", "PL", "CZ", "SK", "HU", "RO", "BG", "GR", "CY", "TR", "RU", "UA",
    "IL", "SA", "AE", "QA", "KW", "BH", "OM", "JO", "LB", "EG", "MA", "ZA", "NG",
    "IN", "PK", "SG", "MY", "TH", "VN", "ID", "PH", "JP", "KR", "HK", "TW", "MO",
]

LANGUAGE_HOME = {
    "en": ["US", "GB", "CA", "AU", "IE", "NZ"],
    "es": ["ES", "MX", "AR", "CL", "CO", "PE", "VE", "UY", "EC", "BO", "PY",
           "CR", "GT", "HN", "NI", "PA", "DO", "SV"],
    "pt": ["PT", "BR"],
    "fr": ["FR", "CA", "BE", "CH", "LU"],
    "de": ["DE", "AT", "CH", "LU"],
    "it": ["IT", "CH"],
    "nl": ["NL", "BE"],
    "sv": ["SE"], "no": ["NO"], "nb": ["NO"], "da": ["DK"], "fi": ["FI"],
    "is": ["IS"], "ja": ["JP"], "ko": ["KR"],
    "zh": ["TW", "HK", "CN", "SG"], "cmn": ["TW", "CN", "SG"], "yue": ["HK"],
    "ru": ["RU"], "uk": ["UA"], "pl": ["PL"], "cs": ["CZ"], "sk": ["SK"],
    "hu": ["HU"], "ro": ["RO"], "bg": ["BG"], "hr": ["HR"], "sr": ["RS"],
    "sl": ["SI"], "el": ["GR"], "tr": ["TR"], "he": ["IL"], "ar": ["SA", "AE", "EG", "MA"],
    "hi": ["IN"], "th": ["TH"], "id": ["ID"], "vi": ["VN"], "ms": ["MY", "SG"],
    "fa": ["IR"], "af": ["ZA"], "lt": ["LT"], "lv": ["LV"], "et": ["EE"],
}

# JustWatch 搜索时尝试的语言/地区（用于非英文片名、冷门片）
SEARCH_LOCALES = [
    ("US", "en"), ("GB", "en"), ("CA", "en"), ("AU", "en"),
    ("DE", "de"), ("FR", "fr"), ("ES", "es"), ("IT", "it"),
    ("JP", "ja"), ("KR", "ko"), ("TW", "zh"), ("HK", "zh"),
    ("BR", "pt"), ("MX", "es"), ("IN", "en"), ("RU", "ru"),
]

# 这些错误表示“这个地区确实没有”，不需要重试。
PERMANENT_ERROR_MARKERS = (
    "Media not found",
    "could not be found",
    "not currently supported",
    "No iTunes playables",
    "No usable iTunes",
    "Invalid media type",
    "Invalid AppleTV URL",
    "AppleTV redirect URL not found",
)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class JustWatchTitle:
    node_id: str
    title: str
    year: int | None
    object_type: str
    imdb_id: str | None = None
    full_path: str | None = None


@dataclass
class AppleTarget:
    umc: str
    countries: set[str] = field(default_factory=set)


@dataclass
class Rendition:
    """一条唯一的字幕轨（按 Apple 的 STABLE-RENDITION-ID 去重）。"""
    key: str
    language: str
    name: str
    forced: bool
    sdh: bool
    countries: set[str] = field(default_factory=set)
    media: Any = None
    playlist: str | None = None
    # 同一 STABLE-RENDITION-ID 在不同地区的候选 URL。
    alternates: list[tuple[Any, str | None, str]] = field(default_factory=list)

    @property
    def kind(self) -> str:
        if self.forced and self.sdh:
            return "forced+sdh"
        if self.forced:
            return "forced"
        if self.sdh:
            return "sdh"
        return "normal"


@dataclass
class SubtitleVariant:
    language: str
    name: str
    forced: bool
    sdh: bool
    countries: set[str]
    content: bytes
    norm_hash: str
    tag: str = ""
    source_umc: str = ""

    @property
    def kind(self) -> str:
        if self.forced and self.sdh:
            return "forced+sdh"
        if self.forced:
            return "forced"
        if self.sdh:
            return "sdh"
        return "normal"


class Progress:
    """简单的单行进度显示；重定向到文件时自动降级为每 10% 一行。"""

    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = max(total, 1)
        self._last_bucket = -1

    def update(self, done: int, extra: str = "") -> None:
        if sys.stdout.isatty():
            print(f"\r{self.label} {done}/{self.total} {extra}".rstrip(), end="", flush=True)
            return
        bucket = int(done * 10 / self.total)
        if bucket != self._last_bucket or done >= self.total:
            self._last_bucket = bucket
            print(f"{self.label} {done}/{self.total} {extra}".rstrip(), flush=True)

    def close(self) -> None:
        if sys.stdout.isatty():
            print()


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def log(msg: str = "") -> None:
    print(msg, flush=True)


def error(msg: str) -> None:
    print(f"[错误] {msg}", file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    print(f"[提示] {msg}", flush=True)


def is_permanent_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return any(marker.lower() in text.lower() for marker in PERMANENT_ERROR_MARKERS)


async def retry_async(func, *, attempts: int = 3, base_delay: float = 1.2,
                      label: str = "") -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except Exception as exc:
            last_exc = exc
            if is_permanent_error(exc) or attempt >= attempts:
                raise
            if label:
                log(f"  {label} 第 {attempt} 次失败（{exc}），{base_delay * attempt:.1f}s 后重试…")
            await asyncio.sleep(base_delay * attempt)
    assert last_exc is not None
    raise last_exc


def norm_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (value or "").casefold())


def parse_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    match = re.search(r"(19|20)\d{2}", text)
    return int(match.group(0)) if match else None


def pick_region_for_language(language: str, countries: Iterable[str]) -> str:
    countries = set(countries)
    base = (language or "").split("-")[0].lower()
    for cc in LANGUAGE_HOME.get(base, []):
        if cc in countries:
            return cc
    for cc in COUNTRY_PRIORITY:
        if cc in countries:
            return cc
    return sorted(countries)[0] if countries else "UNK"


def ordered_countries(countries: Iterable[str]) -> list[str]:
    priority = {cc: i for i, cc in enumerate(COUNTRY_PRIORITY)}
    return sorted(countries, key=lambda cc: (priority.get(cc, 10_000), cc))


def canonical_srt_hash(content: bytes) -> str:
    """
    生成“内容指纹”：忽略 SRT 序号、换行符、HTML 标签（<i> 等）的拆分差异。
    文本和时间轴相同即视为重复。
    """
    text = content.decode("utf-8-sig", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text)
    canonical: list[tuple[str, str]] = []

    for block in blocks:
        lines = [line.rstrip() for line in block.strip().split("\n")]
        if not lines:
            continue
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines:
            continue
        timing = lines[0].strip()
        timing_match = re.search(
            r"(\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})",
            timing,
        )
        if timing_match:
            timing = f"{timing_match.group(1)}-->{timing_match.group(2)}"
        body = " ".join(lines[1:])
        body = html.unescape(re.sub(r"<[^>]*>", "", body))
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        canonical.append((timing, body))

    if not canonical:
        return hashlib.sha256(content).hexdigest()
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_base_name(title: str, year: int | None) -> str:
    slug = slugify_title(title=title, separator=".").strip(".")
    if not slug:
        slug = "Unknown"
    parts = [slug]
    if year:
        parts.append(str(year))
    parts.extend(["iT", "WEB"])
    return sanitize_path_segment(".".join(parts))


# ---------------------------------------------------------------------------
# JustWatch
# ---------------------------------------------------------------------------
SEARCH_QUERY = """
query SearchTitles($q: String!, $country: Country!, $language: Language!, $first: Int!) {
  popularTitles(
    country: $country
    filter: {searchQuery: $q}
    first: $first
    sortBy: POPULAR
  ) {
    edges {
      node {
        id
        objectType
        content(country: $country, language: $language) {
          title
          originalReleaseYear
          fullPath
          externalIds { imdbId }
        }
      }
    }
  }
}
"""

OFFERS_QUERY = """
query TitleOffers($id: ID!, $country: Country!) {
  node(id: $id) {
    ... on MovieOrShow {
      offers(country: $country, platform: WEB) {
        monetizationType
        presentationType
        standardWebURL
        package { packageId clearName shortName }
        subtitleLanguages
        audioLanguages
      }
    }
  }
}
"""


async def jw_graphql(client: httpx2.AsyncClient, query: str, variables: dict,
                     *, attempts: int = 4) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(
                JW_URL,
                json={"query": query, "variables": variables},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=40,
            )
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1.0 * attempt)
            continue

        if response.status_code == 429 or response.status_code >= 500:
            last_error = RuntimeError(f"JustWatch HTTP {response.status_code}")
            await asyncio.sleep(1.5 * attempt)
            continue

        try:
            payload = response.json()
        except Exception as exc:
            last_error = RuntimeError(f"JustWatch 返回非 JSON（HTTP {response.status_code}）: {exc}")
            await asyncio.sleep(1.0 * attempt)
            continue

        if payload.get("errors"):
            messages = "; ".join(
                str(item.get("message", item)) if isinstance(item, dict) else str(item)
                for item in payload["errors"]
            )
            raise RuntimeError(f"JustWatch GraphQL 错误: {messages}")

        return payload.get("data") or {}

    raise RuntimeError(f"JustWatch 请求失败: {last_error}")


async def get_jw_countries(client: httpx2.AsyncClient) -> list[str]:
    fallback = [
        "US", "GB", "CA", "AU", "NZ", "IE", "DE", "AT", "CH", "FR", "BE", "NL", "LU",
        "SE", "NO", "DK", "FI", "IS", "ES", "PT", "IT", "MT", "PL", "CZ", "SK", "HU",
        "RO", "BG", "GR", "CY", "HR", "SI", "RS", "BA", "MK", "AL", "ME", "EE", "LV",
        "LT", "UA", "RU", "TR", "IL", "SA", "AE", "QA", "KW", "BH", "OM", "JO", "LB",
        "EG", "MA", "DZ", "TN", "ZA", "NG", "KE", "GH", "IN", "PK", "BD", "LK", "NP",
        "SG", "MY", "TH", "VN", "ID", "PH", "JP", "KR", "HK", "TW", "MO", "BR", "MX",
        "AR", "CL", "CO", "PE", "VE", "UY", "EC", "BO", "PY", "CR", "GT", "HN", "NI",
        "PA", "DO", "SV", "JM", "TT", "BS", "BB", "BM", "KY", "PR",
    ]
    try:
        response = await client.get(JW_LOCALES_URL, timeout=30)
        response.raise_for_status()
        data = response.json()
        countries = [
            item["iso_3166_2"].upper()
            for item in data
            if item.get("iso_3166_2") and item.get("i18n_state", "active") == "active"
        ]
        if countries:
            return list(dict.fromkeys(countries))
    except Exception:
        pass
    return fallback


async def jw_search(client: httpx2.AsyncClient, query: str, country: str,
                    language: str, first: int = 20) -> list[JustWatchTitle]:
    data = await jw_graphql(
        client,
        SEARCH_QUERY,
        {"q": query, "country": country, "language": language, "first": first},
    )
    edges = ((data.get("popularTitles") or {}).get("edges") or [])
    results: list[JustWatchTitle] = []
    for edge in edges:
        node = edge.get("node") or {}
        content = node.get("content") or {}
        imdb_id = (content.get("externalIds") or {}).get("imdbId")
        results.append(
            JustWatchTitle(
                node_id=node.get("id") or "",
                title=content.get("title") or "",
                year=parse_year(content.get("originalReleaseYear")),
                object_type=node.get("objectType") or "",
                imdb_id=imdb_id,
                full_path=content.get("fullPath"),
            )
        )
    return [item for item in results if item.node_id]


async def jw_search_multi(client: httpx2.AsyncClient, query: str,
                          imdb_id: str | None = None) -> list[JustWatchTitle]:
    """先在 US/en 搜索；找不到精确结果时再尝试其他地区/语言。"""
    queries: list[tuple[str, str, str]] = []
    is_cjk = False

    if imdb_id:
        # 用 IMDb 的标题搜索命中率最高
        try:
            imdb = await imdb_lookup(client, imdb_id)
            if imdb and imdb.get("title"):
                for cc, lang in SEARCH_LOCALES[:8]:
                    queries.append((imdb["title"], cc, lang))
        except Exception:
            pass
        for cc, lang in SEARCH_LOCALES:
            queries.append((query, cc, lang))
    else:
        # 中日韩片名优先搜对应的亚洲店面，避免 US 搜索返回一堆无关结果
        is_cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", query))
        locales = (
            [("TW", "zh"), ("HK", "zh"), ("JP", "ja"), ("KR", "ko"),
             ("US", "en"), ("GB", "en")]
            if is_cjk else
            list(SEARCH_LOCALES)
        )
        queries = [(query, cc, lang) for cc, lang in locales]

    seen: dict[str, JustWatchTitle] = {}
    errors: list[str] = []
    exact_title_found = False

    for title_query, country, language in queries:
        if not title_query:
            continue
        try:
            found = await jw_search(client, title_query, country, language)
        except Exception as exc:
            errors.append(f"{country}/{language}: {exc}")
            continue

        for item in found:
            seen.setdefault(item.node_id, item)

        if imdb_id and any(item.imdb_id == imdb_id for item in found):
            break
        if not imdb_id and is_cjk and found:
            break
        if not imdb_id and any(
            norm_text(item.title) == norm_text(title_query) for item in found
        ):
            exact_title_found = True
            break

    if not seen and errors:
        raise RuntimeError(errors[0])

    candidates = list(seen.values())
    if exact_title_found:
        query_key = norm_text(query)
        exact = [item for item in candidates if norm_text(item.title) == query_key]
        if exact:
            return exact
    return candidates


async def imdb_lookup(client: httpx2.AsyncClient, imdb_id: str) -> dict | None:
    url = IMDB_SUGGEST_URL.format(imdb_id=imdb_id.lower())
    response = await client.get(url, timeout=25)
    if response.status_code != 200:
        return None
    data = response.json()
    items = data.get("d") or []
    if not items:
        return None
    item = items[0]
    return {
        "id": item.get("id"),
        "title": item.get("l"),
        "year": parse_year(item.get("y") or item.get("yr")),
        "type": item.get("qid") or item.get("q"),
    }


def choose_title(candidates: list[JustWatchTitle], imdb_id: str | None) -> JustWatchTitle:
    if imdb_id:
        exact = [item for item in candidates if item.imdb_id == imdb_id]
        if exact:
            return exact[0]

    # 去掉重复标题/年份
    unique: list[JustWatchTitle] = []
    seen: set[tuple[str, int | None, str]] = set()
    for item in candidates:
        key = (norm_text(item.title), item.year, item.object_type)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    candidates = unique

    if not candidates:
        raise RuntimeError("没有找到匹配的影片")
    if len(candidates) == 1:
        return candidates[0]

    if imdb_id:
        warn(f"JustWatch 中没有 IMDB {imdb_id} 的精确匹配，请手动确认。")
        if not sys.stdin.isatty():
            raise RuntimeError(
                f"无法自动确定 IMDB {imdb_id} 对应的 JustWatch 条目，"
                "请在交互终端中运行并手动选择，或使用 Apple TV 链接。"
            )

    log("找到多个匹配，请选择：")
    for index, item in enumerate(candidates[:20], start=1):
        log(f"  [{index:>2}] {item.title} ({item.year or '?'})"
            f"  {item.object_type or ''}  {item.imdb_id or ''}")
    if len(candidates) > 20:
        log(f"  ... 以及另外 {len(candidates) - 20} 个结果")

    if not sys.stdin.isatty():
        warn("当前不是交互终端，自动选择第 1 个结果。建议直接输入 IMDB 编号以避免选错。")
        return candidates[0]

    while True:
        try:
            raw = input("输入序号（直接回车 = 1）：").strip()
        except EOFError:
            warn("没有读取到输入，自动选择第 1 个结果。")
            return candidates[0]
        if not raw:
            return candidates[0]
        if raw.isdigit() and 1 <= int(raw) <= min(len(candidates), 20):
            return candidates[int(raw) - 1]
        log("序号无效，请重新输入。")


async def jw_collect_offers(client: httpx2.AsyncClient, node_id: str,
                            countries: list[str], jobs: int = 10
                            ) -> dict[str, list[dict]]:
    semaphore = asyncio.Semaphore(max(1, jobs))
    results: dict[str, list[dict]] = {}
    progress = Progress("JustWatch 地区查询", len(countries))

    async def one(country: str) -> None:
        async with semaphore:
            try:
                data = await jw_graphql(client, OFFERS_QUERY, {"id": node_id, "country": country})
                node = data.get("node") or {}
                results[country] = node.get("offers") or []
            except Exception:
                # 某些国家不在 JustWatch 的 Country 枚举里，直接忽略
                results[country] = []
            finally:
                progress.update(len(results))

    await asyncio.gather(*(one(country) for country in countries))
    progress.close()
    return results


def summarize_offers(offers_by_country: dict[str, list[dict]]) -> tuple[
        dict[str, AppleTarget], dict[str, dict]]:
    """从 JustWatch offers 里提取 Apple TV Store 的 umc 编号，并统计其他流媒体。"""
    targets: dict[str, AppleTarget] = {}
    services: dict[str, dict] = {}
    itunes_urls: list[tuple[str, str]] = []

    for country, offers in offers_by_country.items():
        for offer in offers:
            package = offer.get("package") or {}
            short_name = (package.get("shortName") or "").lower()
            clear_name = package.get("clearName") or short_name or "Unknown"
            url = offer.get("standardWebURL") or ""

            stats = services.setdefault(short_name, {
                "name": clear_name,
                "short_name": short_name,
                "countries": set(),
                "monetization": set(),
                "subtitle_languages": set(),
                "audio_languages": set(),
                "url_examples": [],
            })
            stats["countries"].add(country)
            stats["monetization"].add(offer.get("monetizationType") or "")
            stats["subtitle_languages"].update(offer.get("subtitleLanguages") or [])
            stats["audio_languages"].update(offer.get("audioLanguages") or [])
            if url and len(stats["url_examples"]) < 3:
                stats["url_examples"].append(url)

            if short_name != "itu":
                continue

            if "tv.apple.com" in url:
                match = APPLE_UMC_RE.search(url)
                if match:
                    umc = match.group(0).lower()
                    target = targets.setdefault(umc, AppleTarget(umc=umc))
                    target.countries.add(country)
            elif "itunes.apple.com" in url:
                itunes_urls.append((country, url))

    # 把 itunes.apple.com 链接的解析结果留给调用方处理
    if itunes_urls:
        services.setdefault("_itunes_urls", {"urls": itunes_urls})

    return targets, services


# ---------------------------------------------------------------------------
# Apple TV 抓取
# ---------------------------------------------------------------------------
def _rendition_key(media: Any) -> str:
    language = (getattr(media, "language", None) or "").strip()
    forced = (getattr(media, "forced", None) or "").upper() == "YES"
    characteristics = (getattr(media, "characteristics", None) or "")
    sdh = "public.accessibility" in characteristics
    kind = ("forced" if forced else "") + ("+sdh" if sdh else "")
    stable_id = (getattr(media, "stable_rendition_id", None) or "").strip()
    if stable_id:
        return f"sid:{stable_id}|{language}|{kind}"
    uri = getattr(media, "uri", None) or ""
    uri = re.sub(r"^https?://[^/]+", "", uri)
    return f"uri:{uri}|{language}|{kind}"


def _media_to_rendition(media: Any, country: str, playlist: str) -> Rendition:
    characteristics = (getattr(media, "characteristics", None) or "")
    return Rendition(
        key=_rendition_key(media),
        language=(getattr(media, "language", None) or "").strip(),
        name=(getattr(media, "name", None) or "").strip(),
        forced=(getattr(media, "forced", None) or "").upper() == "YES",
        sdh="public.accessibility" in characteristics,
        countries={country},
        media=media,
        playlist=playlist,
    )


async def probe_apple_movie(atv: AppleTVScraper, itunes: ItunesScraper,
                            country: str, umc: str) -> list[Rendition]:
    url = f"https://tv.apple.com/{country.lower()}/movie/x/{umc}"
    response = await atv.get_data(url)
    renditions: list[Rendition] = []
    playlist_errors: list[Exception] = []

    for media_data in response.media_data:
        for playlist_url in (getattr(media_data, "playlist", None) or []):
            if not playlist_url:
                continue
            try:
                playlist = await itunes.load_playlist(playlist_url)
            except Exception as exc:
                playlist_errors.append(exc)
                continue
            if playlist is None:
                continue
            for media in itunes.find_matching_subtitles(playlist):
                renditions.append(_media_to_rendition(media, country, playlist_url))

    if not renditions and playlist_errors:
        raise playlist_errors[-1]
    return renditions


async def probe_all_storefronts(atv: AppleTVScraper, itunes: ItunesScraper,
                                umc: str, countries: list[str], jobs: int = 8
                                ) -> tuple[dict[str, Rendition], dict[str, str]]:
    semaphore = asyncio.Semaphore(max(1, jobs))
    merged: dict[str, Rendition] = {}
    errors: dict[str, str] = {}
    progress = Progress(f"探测 {umc}", len(countries))
    done = 0
    lock = asyncio.Lock()

    async def one(country: str) -> None:
        nonlocal done
        async with semaphore:
            try:
                found = await retry_async(
                    lambda: probe_apple_movie(atv, itunes, country, umc),
                    attempts=3,
                    label=f"{country} 抓取",
                )
                for rendition in found:
                    if rendition.key in merged:
                        merged[rendition.key].countries.add(country)
                        merged[rendition.key].alternates.append(
                            (rendition.media, rendition.playlist, country))
                    else:
                        rendition.alternates.append(
                            (rendition.media, rendition.playlist, country))
                        merged[rendition.key] = rendition
                if not found:
                    errors[country] = "无字幕轨"
            except Exception as exc:
                if not is_permanent_error(exc):
                    errors[country] = f"{type(exc).__name__}: {exc}"
            finally:
                async with lock:
                    done += 1
                    progress.update(done, f"字幕轨 {len(merged)}")

    await asyncio.gather(*(one(country) for country in countries))
    progress.close()
    return merged, errors


def _download_error_reason(exc: Exception) -> str:
    if isinstance(exc, SubtitlesDownloadError):
        inner = getattr(exc, "original_exc", None)
        if inner is not None:
            return (f"{type(exc).__name__}: "
                    f"{type(inner).__name__}: {inner}")
    return f"{type(exc).__name__}: {exc}"


async def download_renditions(itunes: ItunesScraper, renditions: list[Rendition],
                              jobs: int = 3) -> tuple[list[tuple[Rendition, bytes]], list[tuple[Rendition, str]]]:
    semaphore = asyncio.Semaphore(max(1, jobs))
    successful: list[tuple[Rendition, bytes]] = []
    failed: list[tuple[Rendition, str]] = []
    progress = Progress("下载字幕", len(renditions))

    async def one(rendition: Rendition) -> None:
        async with semaphore:
            candidates = []
            seen = set()
            primary_country = sorted(rendition.countries)[0] if rendition.countries else "?"
            for media, playlist, country in (
                    [(rendition.media, rendition.playlist, primary_country)]
                    + list(rendition.alternates[:6])):
                if media is None:
                    continue
                key = (getattr(media, "absolute_uri", None), playlist)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append((media, playlist, country))
            last_error = None
            for index, (media, playlist, country) in enumerate(candidates, start=1):
                try:
                    data = await retry_async(
                        lambda m=media: itunes.download_subtitles(
                            m, subrip_conversion=True),
                        attempts=3,
                        label=f"{rendition.language or '?'} 下载"
                              + (f"（{country}）" if country else ""),
                    )
                    if not data.content or not data.content.strip():
                        raise ValueError("下载到的字幕内容为空")
                    successful.append((rendition, data.content))
                    detail = (f"{rendition.language or '?'} {len(data.content) // 1024} KB"
                              + (f" [{country}]" if country else ""))
                    break
                except Exception as exc:
                    last_error = exc
                    if index < len(candidates):
                        log(f"  {rendition.language or '?'} 的 {country} 候选失败，"
                            f"尝试其他地区…")
            else:
                reason = _download_error_reason(
                    last_error or RuntimeError("没有可用的下载候选"))
                countries = ",".join(sorted(rendition.countries))
                failed.append((rendition, f"{reason}（地区：{countries or '?'}）"))
                detail = f"{rendition.language or '?'} 失败"
            progress.update(len(successful) + len(failed), detail)

    await asyncio.gather(*(one(rendition) for rendition in renditions))
    progress.close()
    return successful, failed


def assign_tags(variants: list[SubtitleVariant]) -> None:
    """给每条字幕分配语言标签；同一语言同一类型有多个内容时加地区后缀。"""
    counts = Counter((variant.language, variant.kind) for variant in variants)
    used: set[tuple[str, str]] = set()

    for variant in variants:
        base_tag = variant.language or "und"
        if counts[(variant.language, variant.kind)] > 1:
            region = pick_region_for_language(base_tag, variant.countries)
            if "-" in base_tag:
                base_tag = f"{base_tag}.{region}"
            else:
                base_tag = f"{base_tag}-{region}"

        candidate = base_tag
        suffix = 2
        while (candidate, variant.kind) in used:
            candidate = f"{base_tag}.{suffix}"
            suffix += 1
        used.add((candidate, variant.kind))
        variant.tag = candidate


def build_variants(successful: list[tuple[Rendition, bytes]], exact_dedup: bool
                   ) -> tuple[list[SubtitleVariant], int]:
    """按内容去重，并合并同一字幕在不同地区的记录。"""
    groups: dict[tuple[str, str], SubtitleVariant] = {}
    removed = 0

    for rendition, content in sorted(successful, key=lambda item: item[0].key):
        digest = hashlib.sha256(content).hexdigest() if exact_dedup else canonical_srt_hash(content)
        group_key = (digest, rendition.kind)
        existing = groups.get(group_key)

        if existing is None:
            groups[group_key] = SubtitleVariant(
                language=rendition.language,
                name=rendition.name,
                forced=rendition.forced,
                sdh=rendition.sdh,
                countries=set(rendition.countries),
                content=content,
                norm_hash=digest,
            )
        else:
            old_count = len(existing.countries)
            existing.countries.update(rendition.countries)
            # 同名同内容时，优先保留语言代码更具体、覆盖地区更多的版本
            if len(rendition.countries) > old_count:
                existing.content = content
                existing.language = rendition.language
                existing.name = rendition.name
            removed += 1

    variants = list(groups.values())
    variants.sort(key=lambda item: (item.language or "zzz", item.kind, -len(item.countries)))
    assign_tags(variants)
    return variants, removed


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def write_variants(out_dir: Path, base_name: str,
                   variants: list[SubtitleVariant]) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict] = []

    for variant in variants:
        stem = f"{base_name}.{variant.tag}"
        if variant.forced:
            stem += ".forced"
        if variant.sdh:
            stem += ".sdh"
        target = out_dir / f"{stem}.srt"

        if target.exists():
            if target.read_bytes() == variant.content:
                # 完全相同的文件已存在，直接复用
                pass
            else:
                number = 2
                while target.exists():
                    target = out_dir / f"{stem}.{number}.srt"
                    if target.exists() and target.read_bytes() == variant.content:
                        break
                    number += 1

        if not target.exists():
            target.write_bytes(variant.content)

        written.append({
            "file": target.name,
            "language": variant.language,
            "language_tag": variant.tag,
            "language_name": variant.name,
            "forced": variant.forced,
            "sdh": variant.sdh,
            "countries": ordered_countries(variant.countries),
            "country_count": len(variant.countries),
            "bytes": len(variant.content),
            "content_sha256": hashlib.sha256(variant.content).hexdigest(),
        })

    return written


def print_variant_table(variants: list[SubtitleVariant]) -> None:
    log("")
    log(f"{'语言':<12} {'类型':<10} {'地区数':>5}  语言名称")
    log("-" * 78)
    for variant in variants:
        kind = variant.kind
        name = variant.name or ""
        tag = variant.tag or variant.language or "?"
        log(f"{tag:<12} {kind:<10} {len(variant.countries):>5}  {name[:36]}")
    log("-" * 78)
    countries = set().union(*(variant.countries for variant in variants)) if variants else set()
    log(f"共 {len(variants)} 条字幕轨，覆盖 {len(countries)} 个地区"
        "（最终文件还会再按内容去重）。")
    tags = sorted({f"{v.tag}{'.forced' if v.forced else ''}{'.sdh' if v.sdh else ''}"
                   for v in variants if v.tag})
    if tags:
        log("语言标签：" + ", ".join(tags))


def print_services(services: dict[str, dict]) -> None:
    rows = [
        item for key, item in services.items()
        if key and not key.startswith("_") and item.get("countries")
    ]
    if not rows:
        return
    rows.sort(key=lambda item: (-len(item["countries"]), item["name"].lower()))
    log("")
    log("JustWatch 流媒体/商店一览（按国家数排序，前 20）：")
    for item in rows[:20]:
        monetization = "/".join(sorted(value for value in item["monetization"] if value))
        subtitle_langs = sorted(item["subtitle_languages"])
        extra = f"  字幕: {','.join(subtitle_langs[:8])}" if subtitle_langs else ""
        log(f"  {item['name'][:28]:<28} {len(item['countries']):>3} 国  {monetization}{extra}")
    if len(rows) > 20:
        log(f"  ... 其余 {len(rows) - 20} 个服务见 subtitles_report.json")


def write_reports(out_dir: Path, *, title: str, year: int | None, imdb_id: str | None,
                  jw_title: JustWatchTitle | None, umc_targets: dict[str, AppleTarget],
                  services: dict[str, dict], files: list[dict],
                  probed_countries: Iterable[str] = ()) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    subtitle_countries: set[str] = set()
    for item in files:
        subtitle_countries.update(item.get("countries") or [])

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "script_version": SCRIPT_VERSION,
        "title": title,
        "year": year,
        "imdb_id": imdb_id,
        "justwatch_id": jw_title.node_id if jw_title else None,
        "apple_tv_umc_ids": sorted(umc_targets),
        "apple_tv_justwatch_countries": {
            umc: ordered_countries(target.countries)
            for umc, target in umc_targets.items()
        },
        "probed_countries": ordered_countries(probed_countries),
        "subtitle_countries": ordered_countries(subtitle_countries),
        "subtitles": files,
        "streaming_services": {
            key: {
                "name": item["name"],
                "short_name": item["short_name"],
                "countries": ordered_countries(item["countries"]),
                "monetization": sorted(value for value in item["monetization"] if value),
                "subtitle_languages": sorted(item["subtitle_languages"]),
                "audio_languages": sorted(item["audio_languages"]),
                "url_examples": item["url_examples"][:3],
            }
            for key, item in services.items()
            if key and not key.startswith("_")
        },
    }

    (out_dir / "subtitles_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def resolve_direct_apple_url(atv: AppleTVScraper, input_url: str
                                   ) -> tuple[str, int | None, str, str]:
    match = APPLE_TV_URL_RE.search(input_url)
    if not match:
        raise RuntimeError("无法从链接中解析出 Apple TV 影片 ID")
    kind = (match.group("kind") or "movie").lower()
    if kind != "movie":
        raise RuntimeError("目前只支持 Apple TV 电影（movie）链接，剧集/节目暂不支持")
    country = (match.group("cc") or "us").upper()
    umc = match.group("umc").lower()
    response = await atv.get_data(
        f"https://tv.apple.com/{country.lower()}/movie/x/{umc}"
    )
    if not response.media_data:
        raise RuntimeError("Apple TV 没有返回可用的影片信息")
    movie = response.media_data[0]
    year = movie.release_date.year if getattr(movie, "release_date", None) else None
    title = getattr(movie, "name", None) or "Unknown"
    return title, year, umc, country


async def resolve_itunes_url(atv: AppleTVScraper, itunes: ItunesScraper,
                             input_url: str) -> tuple[str, int | None, str]:
    response = await retry_async(
        lambda: itunes.get_data(input_url),
        attempts=3,
        label="iTunes 链接解析",
    )
    for movie in response.media_data:
        umc = getattr(movie, "referrer_id", None)
        if umc:
            year = movie.release_date.year if getattr(movie, "release_date", None) else None
            return getattr(movie, "name", None) or "Unknown", year, str(umc).lower()
    raise RuntimeError("iTunes 链接没有找到对应的 Apple TV 影片 ID")


async def apple_search_umc(atv: AppleTVScraper, title: str,
                           year: int | None, country: str = "US") -> str | None:
    """Apple TV 搜索兜底：只在标题完全匹配（年份 ±1）时才采用，避免搜错片。"""
    storefront_id = AppleTVScraper.storefronts_mapping.get(country.upper())
    if not storefront_id:
        return None
    data = await atv._fetch_api_data(  # noqa: SLF001
        storefront_id=storefront_id,
        endpoint="/search",
        additional_params={"searchTerm": title},
    )
    want_title = norm_text(title)
    matches: list[str] = []

    for shelf in ((data.get("canvas") or {}).get("shelves") or []):
        for item in shelf.get("items") or []:
            if (item.get("type") or "").lower() != "movie":
                continue
            umc = item.get("id") or ""
            if not APPLE_UMC_RE.fullmatch(umc):
                continue
            item_title = item.get("title") or ""
            release_ms = item.get("releaseDate")
            item_year = None
            if isinstance(release_ms, (int, float)):
                item_year = datetime.fromtimestamp(release_ms / 1000, tz=timezone.utc).year
            if norm_text(item_title) != want_title:
                continue
            if year is not None and item_year is not None and abs(item_year - year) > 1:
                continue
            if umc not in matches:
                matches.append(umc)

    return matches[0] if matches else None


async def run(args: argparse.Namespace) -> int:
    started = time.time()

    AppleTVScraper.subtitles_fix_rtl = True
    AppleTVScraper.subtitles_remove_duplicates = True
    Scraper.subtitles_fix_rtl = True
    Scraper.subtitles_remove_duplicates = True
    WebVTTCaptionBlock.subrip_alignment_conversion = True

    if getattr(args, "dsid", None):
        try:
            AppleTVScraper.config = AppleTVScraper.ScraperConfig(dsid=args.dsid)
        except Exception as exc:
            warn(f"DSID 设置失败，将忽略：{exc}")

    client_kwargs: dict[str, Any] = {
        "headers": {"User-Agent": USER_AGENT},
        "timeout": args.timeout,
        "follow_redirects": True,
    }
    if args.proxy:
        client_kwargs["proxy"] = args.proxy

    atv = AppleTVScraper(timeout=args.timeout, proxy=args.proxy)
    itunes = ItunesScraper(timeout=args.timeout, proxy=args.proxy)

    try:
        async with httpx2.AsyncClient(**client_kwargs) as client:
            raw_input = args.query.strip()
            umc_targets: dict[str, AppleTarget] = {}
            services: dict[str, dict] = {}
            jw_title: JustWatchTitle | None = None
            imdb_id: str | None = None
            title = ""
            year: int | None = None
            direct_country: str | None = None

            # ---- 1. 解析输入 -------------------------------------------------
            if APPLE_TV_URL_RE.search(raw_input):
                log("识别到 Apple TV 链接，直接解析…")
                title, year, umc, direct_country = await resolve_direct_apple_url(atv, raw_input)
                umc_targets[umc] = AppleTarget(umc=umc)
                log(f"影片：{title} ({year or '?'})  umc={umc}")

            elif ITUNES_URL_RE.search(raw_input):
                log("识别到 iTunes 链接，正在转换为 Apple TV 条目…")
                title, year, umc = await resolve_itunes_url(atv, itunes, raw_input)
                umc_targets[umc] = AppleTarget(umc=umc)
                log(f"影片：{title} ({year or '?'})  umc={umc}")

            else:
                if IMDB_ID_RE.match(raw_input):
                    imdb_id = raw_input.lower()
                    log(f"IMDB：{imdb_id}")
                    imdb_info = None
                    for attempt in range(3):
                        try:
                            imdb_info = await imdb_lookup(client, imdb_id)
                        except Exception as exc:
                            if attempt == 2:
                                error(f"IMDb 查询失败：{exc}")
                            else:
                                await asyncio.sleep(1.0 * (attempt + 1))
                            continue
                        break
                    if not imdb_info:
                        error(
                            f"IMDb 上找不到 {imdb_id}（或 IMDb 建议接口暂时不可用）。\n"
                            "请确认编号是否正确；也可以直接输入影片名。"
                        )
                        return 2
                    search_title = imdb_info.get("title") or raw_input
                    year = parse_year(imdb_info.get("year"))
                    log(f"IMDB 标题：{search_title} ({year or '?'})")
                else:
                    search_title = raw_input
                    log(f"搜索影片：{search_title}")

                log("在 JustWatch 中查找…")
                candidates = await jw_search_multi(client, search_title, imdb_id)
                if not candidates:
                    error("JustWatch 中没有找到匹配条目。可以尝试直接输入 IMDB 编号，或提供 Apple TV 链接。")
                    return 2

                jw_title = choose_title(candidates, imdb_id)
                title = jw_title.title or search_title
                year = jw_title.year or year
                imdb_id = imdb_id or jw_title.imdb_id

                log(f"已选择：{title} ({year or '?'})  [{jw_title.object_type}]"
                    f"{'  ' + jw_title.imdb_id if jw_title.imdb_id else ''}")

                if jw_title.object_type.upper() not in ("MOVIE", ""):
                    error(
                        f"该条目是 {jw_title.object_type}（剧集/节目），当前脚本只支持电影。\n"
                        "iSubRip 的 Apple TV 剧集抓取功能尚未实现，所以无法按季/集下载。"
                    )
                    return 2

                # ---- 2. JustWatch 全地区 offers -------------------------------
                all_countries = await get_jw_countries(client)
                log(f"共查询 {len(all_countries)} 个 JustWatch 地区的上架情况…")
                offers_by_country = await jw_collect_offers(
                    client, jw_title.node_id, all_countries, jobs=args.probe_jobs
                )
                umc_targets, services = summarize_offers(offers_by_country)

                # itunes.apple.com 链接兜底解析
                itunes_urls = (services.pop("_itunes_urls", {}) or {}).get("urls", [])
                for country, url in itunes_urls:
                    try:
                        _, _, umc = await resolve_itunes_url(atv, itunes, url)
                        target = umc_targets.setdefault(umc, AppleTarget(umc=umc))
                        target.countries.add(country)
                    except Exception as exc:
                        warn(f"{country} 的 iTunes 链接解析失败：{exc}")

                # JustWatch 没有记录 iTunes 条目时，用 Apple TV 搜索兜底（严格匹配片名+年份）
                if not umc_targets and jw_title is not None:
                    try:
                        fallback_umc = await apple_search_umc(
                            atv, title or jw_title.title, year or jw_title.year
                        )
                    except Exception as exc:
                        fallback_umc = None
                        warn(f"Apple TV 搜索兜底失败：{exc}")
                    if fallback_umc:
                        warn(
                            "JustWatch 没有记录该片的 Apple TV Store 条目，"
                            f"但 Apple TV 搜索找到了 {fallback_umc}，继续尝试。"
                        )
                        umc_targets[fallback_umc] = AppleTarget(umc=fallback_umc)

                log(f"Apple TV 影片 ID：{', '.join(sorted(umc_targets)) if umc_targets else '（无）'}")
                for umc, target in umc_targets.items():
                    if target.countries:
                        log(f"  {umc}: JustWatch 记录 {len(target.countries)} 个地区："
                            f"{', '.join(ordered_countries(target.countries)[:12])}"
                            + (" …" if len(target.countries) > 12 else ""))

            # ---- 3. 决定探测哪些国家/地区 -----------------------------------
            if args.services_only:
                report_dir = Path(args.output).expanduser() if args.output else Path.cwd()
                write_reports(
                    report_dir,
                    title=title, year=year, imdb_id=imdb_id, jw_title=jw_title,
                    umc_targets=umc_targets, services=services, files=[],
                    probed_countries=[],
                )
                print_services(services)
                log("")
                log(f"清单文件：{report_dir / 'subtitles_report.json'}")
                return 0

            if not umc_targets:
                error(
                    "没有找到 Apple TV Store（iTunes 购买/租赁）版本。\n"
                    "可能原因：该片只在 Apple TV+ 流媒体上线，或者 JustWatch 未收录该地区的 iTunes 条目。\n"
                    "当前脚本只能下载 Apple TV Store 的字幕；Apple TV+ 原创新片不在支持范围内。\n"
                    "如果只想查看流媒体上架情况，可以加 --services-only。"
                )
                print_services(services)
                return 3

            all_storefronts = list(AppleTVScraper.storefronts_mapping.keys())
            if args.countries:
                wanted = [cc.strip().upper() for cc in args.countries.split(",") if cc.strip()]
                probe_countries = [cc for cc in wanted if cc in AppleTVScraper.storefronts_mapping]
                missing = sorted(set(wanted) - set(probe_countries))
                if missing:
                    warn(f"以下地区不在 Apple TV 商店列表中，将忽略：{', '.join(missing)}")
                if not probe_countries:
                    error("--countries 中没有有效的 Apple TV 地区代码。")
                    return 2
            elif args.justwatch_countries:
                union = set().union(*(target.countries for target in umc_targets.values()))
                probe_countries = [cc for cc in all_storefronts if cc in union]
                if not probe_countries:
                    probe_countries = all_storefronts
            else:
                probe_countries = all_storefronts

            probe_countries = sorted(
                dict.fromkeys(probe_countries),
                key=lambda cc: (COUNTRY_PRIORITY.index(cc) if cc in COUNTRY_PRIORITY else 999, cc),
            )

            log("")
            log(f"开始探测 {len(probe_countries)} 个 Apple TV 地区的字幕轨"
                f"（并发 {args.probe_jobs}）…")

            merged: dict[str, Rendition] = {}
            probe_errors: dict[str, str] = {}

            for umc in sorted(umc_targets):
                if args.justwatch_countries and direct_country is None:
                    countries_for_umc = [
                        cc for cc in probe_countries
                        if cc in umc_targets[umc].countries
                    ] or probe_countries
                else:
                    countries_for_umc = probe_countries

                log(f"\nApple TV 条目 {umc}：探测 {len(countries_for_umc)} 个地区…")
                found, errors = await probe_all_storefronts(
                    atv, itunes, umc, countries_for_umc, jobs=args.probe_jobs
                )
                for key, rendition in found.items():
                    if key in merged:
                        merged[key].countries.update(rendition.countries)
                    else:
                        merged[key] = rendition
                probe_errors.update(errors)

            renditions = list(merged.values())
            if probe_errors:
                log("")
                log(f"有 {len(probe_errors)} 个地区没有拿到字幕轨（通常表示该地区未上架/没有字幕），"
                    f"例如：")
                for cc, reason in list(probe_errors.items())[:8]:
                    log(f"  {cc}: {reason}")
                if len(probe_errors) > 8:
                    log(f"  ... 其余 {len(probe_errors) - 8} 个地区略")

            if not renditions:
                error("没有找到任何字幕轨。该片可能在这些地区都没有 iTunes 字幕，或 Apple 接口有变化。")
                return 3

            # 语言/类型筛选
            if args.lang:
                wanted_langs = [item.strip().lower() for item in args.lang.split(",") if item.strip()]
                filtered = []
                available = sorted({r.language for r in renditions if r.language})

                def language_matches(lang: str, wanted: str) -> bool:
                    lang = (lang or "").lower()
                    base = lang.split("-")[0]
                    if lang == wanted or lang.startswith(wanted + "-") or base == wanted:
                        return True
                    # 中文：zh 同时匹配 cmn（普通话）和 yue（粤语）
                    if wanted in ("zh", "zho", "chi") and base in ("zh", "cmn", "yue"):
                        return True
                    if wanted.startswith("zh-") and base in ("zh", "cmn", "yue"):
                        return lang.endswith(wanted[3:])
                    if wanted in ("no", "nb", "nor") and base in ("no", "nb", "nn"):
                        return True
                    return False

                for rendition in renditions:
                    if any(language_matches(rendition.language, wanted) for wanted in wanted_langs):
                        filtered.append(rendition)
                if not filtered:
                    error(f"--lang 没有匹配到字幕。可用语言：{', '.join(available)}")
                    return 3
                renditions = filtered

            if args.no_forced:
                renditions = [r for r in renditions if not r.forced]
            if args.no_sdh:
                renditions = [r for r in renditions if not r.sdh]

            if not renditions:
                error("筛选后没有剩余字幕。")
                return 3

            countries_covered = set().union(*(r.countries for r in renditions))
            log("")
            log(f"共发现 {len(renditions)} 条唯一字幕轨，覆盖 {len(countries_covered)} 个地区。")

            if args.list:
                variants = [
                    SubtitleVariant(
                        language=r.language, name=r.name, forced=r.forced, sdh=r.sdh,
                        countries=set(r.countries), content=b"", norm_hash="",
                    )
                    for r in renditions
                ]
                variants.sort(key=lambda item: (item.language or "zzz", item.kind,
                                                -len(item.countries)))
                assign_tags(variants)
                print_variant_table(variants)
                print_services(services)
                report_dir = Path(args.output).expanduser() if args.output else Path.cwd()
                write_reports(
                    report_dir,
                    title=title, year=year, imdb_id=imdb_id, jw_title=jw_title,
                    umc_targets=umc_targets, services=services, files=[],
                    probed_countries=probe_countries,
                )
                log("")
                log(f"清单文件：{report_dir / 'subtitles_report.json'}")
                return 0

            # ---- 4. 下载 ----------------------------------------------------
            log("")
            log(f"开始下载（并发 {args.download_jobs}，每条字幕内部还会并行下载分段）…")
            successful, failed = await download_renditions(
                itunes, renditions, jobs=args.download_jobs
            )

            if not successful:
                error("所有字幕都下载失败。")
                for rendition, reason in failed[:10]:
                    error(f"  {rendition.language or '?'}: {reason}")
                return 3

            variants, removed = build_variants(successful, exact_dedup=args.exact_dedup)

            # ---- 5. 输出 ----------------------------------------------------
            if args.output:
                out_dir = Path(args.output).expanduser()
            else:
                folder = f"{slugify_title(title=title, separator=' ')} ({year})" if year else \
                    slugify_title(title=title, separator=" ")
                out_dir = Path.cwd() / sanitize_path_segment(folder.strip() or "AppleTV-Subs")

            base_name = make_base_name(title, year)
            files = write_variants(out_dir, base_name, variants)
            write_reports(
                out_dir,
                title=title, year=year, imdb_id=imdb_id, jw_title=jw_title,
                umc_targets=umc_targets, services=services, files=files,
                probed_countries=probe_countries,
            )

            elapsed = time.time() - started
            log("")
            log("=" * 78)
            log(f"完成：{title} ({year or '?'})")
            log(f"探测地区：{len(probe_countries)}    字幕轨：{len(renditions)}    "
                f"下载成功：{len(successful)}    内容去重：{removed}")
            log(f"输出目录：{out_dir}")
            log(f"文件数：{len(files)}    耗时：{elapsed:.1f} 秒")
            log("")
            for item in files:
                flags = "".join(("[F]" if item["forced"] else "", "[SDH]" if item["sdh"] else ""))
                log(f"  {item['file']}   ({item['country_count']} 地区) {flags}")
            if failed:
                log("")
                log(f"有 {len(failed)} 条字幕下载失败：")
                for rendition, reason in failed[:10]:
                    log(f"  {rendition.language or '?'}: {reason}")
                if len(failed) > 10:
                    log(f"  ... 其余 {len(failed) - 10} 条略")
            print_services(services)
            log("")
            log(f"清单文件：{out_dir / 'subtitles_report.json'}")
            log("=" * 78)
            return 0

    finally:
        try:
            await atv.async_close()
        except Exception:
            pass
        try:
            await itunes.async_close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atv_subs.py",
        description="Apple TV / iTunes 全地区、全语言字幕下载器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python atv_subs.py tt1375666\n"
            "  python atv_subs.py \"Oppenheimer\"\n"
            "  python atv_subs.py tt15398776 --list\n"
            "  python atv_subs.py tt15398776 -o \"E:\\\\Subtitles\\\\Oppenheimer\" --lang en,zh,fr\n"
        ),
    )
    parser.add_argument("query", nargs="?", help="IMDB 编号（tt...）、影片名、Apple TV 链接或 iTunes 链接")
    parser.add_argument("-o", "--output", help="输出目录（默认：当前目录/片名 (年份)/）")
    parser.add_argument("--lang", help="只下载指定语言，逗号分隔，例如 en,zh,pt（支持前缀匹配）")
    parser.add_argument("--countries", help="只探测指定地区，逗号分隔，例如 US,GB,JP")
    parser.add_argument("--list", action="store_true", help="只列出字幕轨，不下载")
    parser.add_argument("--services-only", action="store_true",
                        help="只查询并显示 JustWatch 流媒体上架情况，不下载")
    parser.add_argument("--justwatch-countries", action="store_true",
                        help="只探测 JustWatch 记录的地区（更快，但可能漏掉冷门地区）")
    parser.add_argument("--probe-jobs", type=int, default=8,
                        help="探测 Apple TV 地区的并发数（默认 8）")
    parser.add_argument("--download-jobs", type=int, default=3,
                        help="同时下载的字幕条数（默认 3）")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP 超时秒数（默认 30）")
    parser.add_argument("--proxy", help="HTTP(S) 代理，例如 http://127.0.0.1:7890")
    parser.add_argument("--dsid", help="Apple 账号 DSID（仅下载已购买/已租借内容时需要）")
    parser.add_argument("--exact-dedup", action="store_true",
                        help="只对字节完全相同的字幕去重（默认会忽略 <i> 标签拆分等排版差异）")
    parser.add_argument("--no-forced", action="store_true", help="不下载 Forced 字幕")
    parser.add_argument("--no-sdh", action="store_true", help="不下载 SDH/CC 字幕")
    parser.add_argument("--debug", action="store_true", help="输出 isubrip 调试日志")
    parser.add_argument("--version", action="store_true", help="显示版本")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        try:
            from importlib.metadata import version as pkg_version
            print(f"atv_subs {SCRIPT_VERSION} / isubrip {pkg_version('isubrip')}")
        except Exception:
            print(f"atv_subs {SCRIPT_VERSION}")
        return 0

    if not args.query:
        parser.print_help()
        return 2

    if ISUBRIP_VERSION and ISUBRIP_VERSION < (2, 7, 4):
        print(
            f"[提示] 当前 isubrip 版本为 {'.'.join(map(str, ISUBRIP_VERSION))}，"
            "建议升级到 2.7.4 或更高版本，否则可能漏掉部分 Apple 字幕轨：\n"
            '       python -m pip install -U "isubrip>=2.7.4"',
            flush=True,
        )

    if args.debug:
        logging.getLogger("isubrip").setLevel(logging.DEBUG)
        logging.getLogger("isubrip").addHandler(logging.StreamHandler(sys.stderr))

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        if args.debug:
            import traceback
            traceback.print_exc()
        error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

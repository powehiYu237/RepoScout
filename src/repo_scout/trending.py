"""FR-101：GitHub Trending HTML 采集与解析。

GitHub 没有官方 Trending API，因此解析 HTML。解析器只依赖标准库，
并对结构变化保持宽容：
- 以 <article class="Box-row"> 为记录边界；
- 仓库链接优先取 <h2> 内的两段式 href；
- 描述取第一个 <p>；
- 语言取 itemprop="programmingLanguage"；
- 总星数取 /stargazers 链接文本；
- 时间窗新增星数用正则匹配 "1,234 stars today/this week/this month"。
"""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from typing import Callable

from .http import HttpTransport, TransportError
from .models import TrendingEntry

PERIODS = ("daily", "weekly", "monthly")

_REPO_HREF = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")
_PERIOD_STARS = re.compile(
    r"([\d,]+(?:\.\d+)?k?)\s+stars?\s+(today|this week|this month)", re.IGNORECASE
)
_COUNT = re.compile(r"([\d,]+(?:\.\d+)?)(k?)", re.IGNORECASE)

# GitHub 保留路径，避免把导航链接误判为用户仓库。
_RESERVED_OWNERS = {
    "login",
    "join",
    "sponsors",
    "explore",
    "topics",
    "trending",
    "collections",
    "settings",
    "marketplace",
    "orgs",
    "users",
    "features",
    "enterprise",
    "pricing",
    "notifications",
    "search",
    "apps",
    "site",
    "contact",
}


class TrendingError(RuntimeError):
    """Trending 采集失败基类。"""


class TrendingFetchError(TrendingError):
    """HTTP 层失败。"""


class TrendingParseError(TrendingError):
    """页面结构变化或条目数量异常。"""


def parse_count(raw: str | None) -> int | None:
    """解析 '8,200' / '1.2k' / ' 1,234 ' 这类计数文本。"""
    if not raw:
        return None
    text = raw.replace(",", "").replace(" ", " ").strip()
    match = _COUNT.search(text)
    if match is None:
        return None
    value = float(match.group(1))
    if match.group(2).lower() == "k":
        value *= 1000
    return int(round(value))


def _clean_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.replace(" ", " ")).strip()


class _TrendingHTMLParser(HTMLParser):
    def __init__(self, period: str) -> None:
        super().__init__(convert_charrefs=True)
        self.period = period
        self.entries: list[TrendingEntry] = []
        self._current: dict | None = None
        self._depth = 0
        self._counters = {"p": 0, "lang": 0, "stars": 0, "h2": 0}
        self._buffers = {"article": [], "p": [], "lang": [], "stars": []}
        self._p_done = False

    # -- HTMLParser hooks -------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)

        if tag == "article" and "Box-row" in (attributes.get("class") or "").split():
            if self._current is not None:
                self._finish_article()
            self._current = {"owner": None, "name": None, "href": None, "priority": 0}
            self._depth = 1
            self._reset_buffers()
            return

        if self._current is None:
            return

        if tag == "article":
            self._depth += 1
            return

        if tag == "h2":
            self._counters["h2"] += 1

        if tag == "p":
            if self._counters["p"] == 0 and not self._p_done:
                self._buffers["p"].clear()
            if self._counters["p"] > 0 or not self._p_done:
                self._counters["p"] += 1

        if tag == "span" and attributes.get("itemprop") == "programmingLanguage":
            if self._counters["lang"] == 0:
                self._buffers["lang"].clear()
            self._counters["lang"] += 1

        href = attributes.get("href") or ""
        if tag == "a" and href:
            path = href.split("?", 1)[0].split("#", 1)[0]
            match = _REPO_HREF.match(path)
            if match and match.group(1).lower() not in _RESERVED_OWNERS:
                priority = 2 if self._counters["h2"] > 0 else 1
                if priority > self._current["priority"]:
                    self._current.update(
                        owner=match.group(1),
                        name=match.group(2),
                        href=href,
                        priority=priority,
                    )

            if path.rstrip("/").endswith("/stargazers"):
                self._buffers["stars"].clear()
                self._counters["stars"] += 1

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return

        if tag == "article":
            self._depth -= 1
            if self._depth <= 0:
                self._finish_article()
            return

        if tag == "h2" and self._counters["h2"] > 0:
            self._counters["h2"] -= 1

        if tag == "p" and self._counters["p"] > 0:
            self._counters["p"] -= 1
            if self._counters["p"] == 0 and _clean_text("".join(self._buffers["p"])):
                self._p_done = True

        if tag == "span" and self._counters["lang"] > 0:
            self._counters["lang"] -= 1

        if tag == "a" and self._counters["stars"] > 0:
            self._counters["stars"] -= 1

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        self._buffers["article"].append(data)
        if self._counters["p"] > 0:
            self._buffers["p"].append(data)
        if self._counters["lang"] > 0:
            self._buffers["lang"].append(data)
        if self._counters["stars"] > 0:
            self._buffers["stars"].append(data)

    # -- internal ----------------------------------------------------------
    def _reset_buffers(self) -> None:
        for key in self._buffers:
            self._buffers[key].clear()
        self._counters = {"p": 0, "lang": 0, "stars": 0, "h2": 0}
        self._p_done = False

    def _finish_article(self) -> None:
        current = self._current
        self._current = None

        if current is not None and current["owner"] and current["name"]:
            description = _clean_text("".join(self._buffers["p"])) or None
            language = _clean_text("".join(self._buffers["lang"])) or None
            stars_total = parse_count("".join(self._buffers["stars"]))
            article_text = _clean_text("".join(self._buffers["article"]))
            period_match = _PERIOD_STARS.search(article_text)
            period_stars = parse_count(period_match.group(1)) if period_match else None
            full_name = f"{current['owner']}/{current['name']}"
            self.entries.append(
                TrendingEntry(
                    full_name=full_name,
                    owner=current["owner"],
                    name=current["name"],
                    html_url=f"https://github.com/{full_name}",
                    period=self.period,
                    description=description,
                    language=language,
                    stars_total=stars_total,
                    period_stars=period_stars,
                )
            )

        self._reset_buffers()


def parse_trending_html(html: str, period: str) -> list[TrendingEntry]:
    """解析 Trending HTML；同一窗口内按 full_name 去重（保留首次出现）。"""
    if period not in PERIODS:
        raise ValueError(f"unsupported period: {period}")

    parser = _TrendingHTMLParser(period)
    parser.feed(html)
    parser.close()
    if parser._current is not None:  # 容错：HTML 截断时收尾
        parser._finish_article()

    deduped: dict[str, TrendingEntry] = {}
    for entry in parser.entries:
        deduped.setdefault(entry.full_name.lower(), entry)
    return list(deduped.values())


def fetch_trending(
    transport: HttpTransport,
    period: str,
    *,
    base_url: str,
    user_agent: str,
    retries: int = 3,
    min_items: int = 20,
    timeout: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[TrendingEntry]:
    """抓取并解析某个时间窗的 Trending。

    可重试状态码：429/500/502/503/504。解析条目数低于 min_items 时抛
    TrendingParseError，调用方应保留上次成功数据，不做覆盖。
    """
    if period not in PERIODS:
        raise ValueError(f"unsupported period: {period}")

    url = f"{base_url}?since={period}"
    headers = {"User-Agent": user_agent, "Accept": "text/html"}
    last_error = "unknown error"

    for attempt in range(retries + 1):
        try:
            response = transport.get(url, headers=headers, timeout=timeout)
        except TransportError as exc:
            last_error = f"transport error: {exc}"
            if attempt < retries:
                sleep(0.5 * (2**attempt))
                continue
            raise TrendingFetchError(
                f"failed to fetch {period} trending: {last_error}"
            ) from exc

        if response.status == 200:
            entries = parse_trending_html(response.text, period)
            if len(entries) < min_items:
                raise TrendingParseError(
                    f"parsed {len(entries)} items from {period} trending "
                    f"(expected >= {min_items}); page structure may have changed"
                )
            return entries

        last_error = f"HTTP {response.status}"
        if response.status not in (429, 500, 502, 503, 504):
            break
        if attempt < retries:
            sleep(0.5 * (2**attempt))

    raise TrendingFetchError(f"failed to fetch {period} trending: {last_error}")

"""M4 商业价值评估层（FR-401 ~ FR-406）。

信号来源：

- issues（REST，最近 100 条 open issues）→ 付费意愿；
- README / homepage / `.github/FUNDING.yml` → 商业化空白；
- GitHub Search API → 竞品数量（7 天缓存、每日预算、30 次/分钟限速）；
- topics → 受众支付意愿（附录 B）；
- README/topics/结构 → 差异化缺口与变现路径；
- M2 `ΔStars_7d` 百分位 → 市场验证。

所有子分、输入与证据写入 ``repo_commercial.score_breakdown``，分数可复算。
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Mapping

from .metrics import parse_ts, percentile_map
from .util import iso_z, utc_now

COMMERCIAL_VERSION = "m4-1"

PAID_INTENT_KEYWORDS = (
    "would pay",
    "willing to pay",
    "paid version",
    "paid plan",
    "pricing",
    "enterprise plan",
    "license key",
    "take my money",
    "愿意付费",
    "付费版",
    "求付费",
    "企业版",
    "开个价",
    "买断",
)

STRONG_COMMERCIAL_PATTERNS = (
    r"\bpricing\b",
    r"\bpro plan\b",
    r"\bbook a demo\b",
    r"\bwaitlist\b",
    r"\bpaid plan\b",
    r"\bsubscription\b",
    r"\blicense key\b",
    r"\benterprise plan\b",
)

WEAK_COMMERCIAL_PATTERNS = (
    r"\bupgrade\b",
    r"\benterprise\b",
    r"\bsponsor",
    r"\bfunding\b",
    r"\bhosted\b",
    r"\bcloud\b",
    r"\bsaas\b",
    r"\bvercel\.app\b",
    r"\bnetlify\.app\b",
)

TOPIC_WEIGHTS = {
    "b2b": 100,
    "enterprise": 100,
    "saas": 100,
    "seo": 95,
    "marketing": 95,
    "sales": 95,
    "productivity": 90,
    "workflow": 90,
    "automation": 90,
    "chrome-extension": 85,
    "browser-extension": 85,
    "plugin": 85,
    "analytics": 80,
    "finance": 80,
    "accounting": 80,
    "hr": 80,
    "devtools": 75,
    "developer-tools": 75,
    "api": 75,
    "cli": 75,
    "ai-agent": 70,
    "llm": 70,
    "chatbot": 70,
    "ai": 60,
    "machine-learning": 60,
    "data": 60,
    "boilerplate": 55,
    "starter": 55,
    "template": 55,
    "education": 40,
    "learning": 40,
    "demo": 15,
    "example": 15,
    "tutorial": 10,
    "course": 10,
    "awesome": 0,
    "awesome-list": 0,
    "hacktoberfest": 0,
}

COMPETITION_SCORE = (
    (0, 0, 100),
    (1, 4, 85),
    (5, 10, 65),
    (11, 20, 45),
    (21, math.inf, 20),
)

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "your",
    "you",
    "are",
    "was",
    "were",
    "has",
    "have",
    "can",
    "will",
    "not",
    "but",
    "all",
    "any",
    "our",
    "their",
    "its",
    "into",
    "over",
    "under",
    "between",
    "using",
    "based",
    "built",
}

PAID_VERSION_SCORE = {"none": 90, "possible": 60, "likely": 20}

_PAID_INTENT_PATTERNS = tuple(
    (
        keyword,
        re.compile(
            (
                rf"\b{re.escape(keyword)}\b"
                if keyword.isascii()
                else re.escape(keyword)
            ),
            re.IGNORECASE,
        ),
    )
    for keyword in PAID_INTENT_KEYWORDS
)
_STRONG_COMMERCIAL = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in STRONG_COMMERCIAL_PATTERNS
)
_WEAK_COMMERCIAL = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in WEAK_COMMERCIAL_PATTERNS
)


# -- 数据结构 ---------------------------------------------------------------
@dataclass(frozen=True)
class PaidIntent:
    scanned: int | None
    hit_count: int
    reactions: int
    examples: tuple[dict[str, Any], ...]

    def score(self) -> int:
        if self.scanned is None:
            return 40
        if self.hit_count >= 6:
            return 100
        if self.hit_count >= 3:
            return 85
        if self.hit_count >= 1:
            return 70
        return 40


@dataclass(frozen=True)
class PaidVersion:
    confidence: str
    evidence: tuple[dict[str, str], ...]

    def score(self) -> int:
        return PAID_VERSION_SCORE.get(self.confidence, 60)


@dataclass(frozen=True)
class CommercialResult:
    repo_pk: int
    full_name: str
    stars_total: int | None
    repro_score: float
    analyzed_at: str
    paid_intent: PaidIntent
    paid_intent_score: int
    paid_version: PaidVersion
    competitor_count: int | None
    competitor_query: str | None
    competitor_checked_at: str | None
    audience_score: int
    audience_topic: str | None
    monetization_paths: tuple[str, ...]
    gap_count: int
    differentiation_score: int
    market_alignment: float
    whitespace: float
    competition: float
    biz_score: float
    low_confidence_fields: tuple[str, ...]
    score_breakdown: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_pk": self.repo_pk,
            "full_name": self.full_name,
            "stars_total": self.stars_total,
            "repro_score": self.repro_score,
            "analyzed_at": self.analyzed_at,
            "paid_intent_score": self.paid_intent_score,
            "paid_intent_issues": self.paid_intent.hit_count,
            "paid_intent_reactions": self.paid_intent.reactions,
            "paid_intent_scanned": self.paid_intent.scanned,
            "paid_version": self.paid_version.confidence,
            "competitor_count": self.competitor_count,
            "competitor_query": self.competitor_query,
            "audience_score": self.audience_score,
            "audience_topic": self.audience_topic,
            "monetization_paths": list(self.monetization_paths),
            "differentiation_score": self.differentiation_score,
            "market_alignment": self.market_alignment,
            "whitespace": self.whitespace,
            "competition": self.competition,
            "biz_score": self.biz_score,
            "low_confidence_fields": list(self.low_confidence_fields),
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "repo_pk": self.repo_pk,
            "analyzed_at": self.analyzed_at,
            "commercial_version": COMMERCIAL_VERSION,
            "paid_intent_issues": json.dumps(
                list(self.paid_intent.examples), ensure_ascii=False
            ),
            "paid_intent_scanned": self.paid_intent.scanned,
            "paid_intent_hits": self.paid_intent.hit_count,
            "paid_intent_score": self.paid_intent_score,
            "paid_intent_evidence": json.dumps(
                [
                    {"issue": item["number"], "hits": item["hits"]}
                    for item in self.paid_intent.examples
                ],
                ensure_ascii=False,
            ),
            "paid_version": self.paid_version.confidence,
            "paid_evidence": json.dumps(
                list(self.paid_version.evidence), ensure_ascii=False
            ),
            "competitor_count": self.competitor_count,
            "competitor_query": self.competitor_query,
            "competitor_checked_at": self.competitor_checked_at,
            "audience_score": self.audience_score,
            "audience_topic": self.audience_topic,
            "monetization_paths": json.dumps(
                list(self.monetization_paths), ensure_ascii=False
            ),
            "gap_count": self.gap_count,
            "differentiation_score": self.differentiation_score,
            "market_alignment": self.market_alignment,
            "whitespace": self.whitespace,
            "competition": self.competition,
            "biz_score": self.biz_score,
            "low_confidence_fields": json.dumps(
                list(self.low_confidence_fields), ensure_ascii=False
            ),
            "score_breakdown": json.dumps(self.score_breakdown, ensure_ascii=False),
            "updated_at": self.analyzed_at,
        }


# -- 付费意愿（FR-401） -----------------------------------------------------
def _reaction_count(issue: Mapping[str, Any]) -> int:
    reactions = issue.get("reactions")
    if isinstance(reactions, Mapping):
        total = reactions.get("total_count")
        if isinstance(total, int):
            return total
        return sum(
            int(value)
            for key, value in reactions.items()
            if key != "total_count" and isinstance(value, int)
        )
    return 0


def scan_paid_intent(issues: Iterable[Mapping[str, Any]] | None) -> PaidIntent:
    if issues is None:
        return PaidIntent(scanned=None, hit_count=0, reactions=0, examples=())

    items = list(issues)
    hits = 0
    reactions = 0
    examples: list[dict[str, Any]] = []
    for issue in items:
        title = str(issue.get("title") or "")
        body = str(issue.get("body") or "")
        text = f"{title}\n{body}"
        matched = [
            keyword
            for keyword, pattern in _PAID_INTENT_PATTERNS
            if pattern.search(text)
        ]
        if not matched:
            continue
        hits += 1
        reactions += _reaction_count(issue)
        if len(examples) < 3:
            examples.append(
                {
                    "number": issue.get("number"),
                    "title": title[:200],
                    "url": issue.get("html_url"),
                    "hits": matched,
                }
            )
    return PaidIntent(
        scanned=len(items),
        hit_count=hits,
        reactions=reactions,
        examples=tuple(examples),
    )


# -- 商业化空白（FR-402） ---------------------------------------------------
def _line_hits(
    text: str, patterns: tuple[re.Pattern, ...], source: str
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines()[:3000]:
        for pattern in patterns:
            match = pattern.search(line)
            if match and match.group(0).lower() not in seen:
                seen.add(match.group(0).lower())
                found.append(
                    {
                        "source": source,
                        "keyword": match.group(0)[:60],
                        "line": line.strip()[:200],
                    }
                )
    return found


def detect_paid_version(
    *,
    readme: str | None,
    homepage: str | None,
    funding: str | None,
    topics: Iterable[str],
) -> PaidVersion:
    evidence: list[dict[str, str]] = []
    strong = 0
    weak = 0
    funding_present = bool(funding and funding.strip())

    if readme:
        strong_hits = _line_hits(readme, _STRONG_COMMERCIAL, "README")
        weak_hits = _line_hits(readme, _WEAK_COMMERCIAL, "README")
        strong += len({item["keyword"].lower() for item in strong_hits})
        weak += len({item["keyword"].lower() for item in weak_hits})
        evidence.extend(strong_hits[:5])
        evidence.extend(weak_hits[:5])

    if funding_present:
        weak += 1
        evidence.append(
            {
                "source": "FUNDING",
                "keyword": "funding_file",
                "line": funding.strip().splitlines()[0][:200] if funding.strip() else "",
            }
        )

    if homepage and "github.com" not in homepage:
        weak += 1
        evidence.append(
            {"source": "homepage", "keyword": "product_homepage", "line": homepage[:200]}
        )

    topic_set = {str(topic).lower() for topic in topics}
    if topic_set & {"saas", "enterprise", "b2b"}:
        weak += 1
        evidence.append(
            {
                "source": "topics",
                "keyword": sorted(topic_set & {"saas", "enterprise", "b2b"})[0],
                "line": ", ".join(sorted(topic_set)),
            }
        )

    sponsor_present = any(
        "sponsor" in item["keyword"].lower() for item in evidence
    )
    if strong >= 2:
        confidence = "likely"
    elif strong >= 1 or funding_present or sponsor_present or weak >= 2:
        confidence = "possible"
    else:
        confidence = "none"
    return PaidVersion(confidence=confidence, evidence=tuple(evidence[:10]))


# -- 受众与差异化（FR-404 / FR-405） ----------------------------------------
def audience_from_topics(topics: Iterable[str]) -> tuple[int, str | None]:
    best = 50
    best_topic: str | None = None
    for topic in topics:
        weight = TOPIC_WEIGHTS.get(str(topic).lower())
        if weight is not None and weight > best:
            best = weight
            best_topic = str(topic).lower()
    return best, best_topic


def differentiation_gaps(
    *,
    readme: str | None,
    description: str | None,
    topics: Iterable[str],
    has_docker: bool,
    has_compose: bool,
    paid_version: str,
    homepage: str | None,
) -> tuple[list[str], list[dict[str, str]]]:
    text = " ".join(
        [readme or "", description or "", homepage or "", " ".join(topics)]
    ).lower()
    topic_set = {str(topic).lower() for topic in topics}
    gaps: list[str] = []
    evidence: list[dict[str, str]] = []

    def add(gap: str, reason: str) -> None:
        if gap not in gaps:
            gaps.append(gap)
            evidence.append({"gap": gap, "reason": reason})

    if not re.search(
        r"\bi18n\b|\blocale\b|\blocali[sz]ation\b|\btranslation|\bmultilingual\b|多语言|中文",
        text,
    ):
        add("中文本地化 / 国内合规部署", "README 未发现 i18n / 多语言信号")

    if not re.search(
        r"\bmobile\b|\bandroid\b|\bios\b|react native|\bflutter\b|小程序|移动端",
        text,
    ):
        add("移动端 / 小程序", "README/topics 未发现移动端信号")

    cli_like = "cli" in topic_set or bool(
        re.search(r"\bcommand[- ]line\b|\bterminal\b|\bcli\b", text)
    )
    ui_like = bool(
        topic_set & {"web", "dashboard", "frontend", "desktop", "gui"}
    ) or bool(re.search(r"\bweb\b|\bdashboard\b|\bfrontend\b|\bdesktop\b|\bgui\b", text))
    if cli_like and not ui_like:
        add("GUI / 桌面端", "项目仅提供 CLI")

    self_hostable = bool(has_docker or has_compose) or bool(
        re.search(r"self[- ]host|docker", text)
    )
    if paid_version == "none" and self_hostable:
        add("托管 SaaS", "项目可自托管但没有商业化迹象")
    if paid_version in ("possible", "likely") and not self_hostable:
        add("私有化部署（B2B）", "项目疑似已商业化，但缺少自托管信号")

    if not re.search(r"\bapi\b", text) and "api" not in topic_set:
        add("API 服务化", "README/topics 未发现 API 能力")

    web_like = "web" in topic_set or bool(re.search(r"\bweb\b|dashboard", text))
    extension_like = bool(
        topic_set & {"chrome-extension", "browser-extension", "plugin"}
    ) or bool(re.search(r"\bextension\b", text))
    if web_like and not extension_like:
        add("浏览器插件", "Web 项目未提供浏览器插件")

    return gaps, evidence


# -- 竞品与评分（FR-403 / 7.4） ---------------------------------------------
def competition_query(
    *,
    topics: Iterable[str],
    description: str | None,
    language: str | None,
    audience_topic: str | None,
) -> str | None:
    topic_list = [str(topic).lower() for topic in topics]
    core = audience_topic if audience_topic in topic_list else (
        topic_list[0] if topic_list else None
    )
    if core:
        query = f"topic:{core}"
    else:
        words = [
            word
            for word in re.findall(r"[a-z0-9][a-z0-9-]{2,}", (description or "").lower())
            if word not in STOPWORDS
        ][:3]
        if not words:
            return None
        query = " ".join(words) + " in:name,description"
    if language:
        query += f" language:{language}"
    query += " stars:>100 fork:false -topic:awesome -topic:awesome-list"
    return query


def competition_score(count: int | None) -> float:
    if count is None:
        return 50.0
    for low, high, score in COMPETITION_SCORE:
        if low <= count <= high:
            return float(score)
    return 50.0


def market_alignment_score(percentile_7d: float, paid_intent_score: int) -> float:
    return round(0.5 * percentile_7d + 0.5 * paid_intent_score, 2)


def business_score(
    *,
    market_alignment: float,
    whitespace: float,
    competition: float,
    audience: float,
    differentiation: float,
) -> float:
    return round(
        0.30 * market_alignment
        + 0.25 * whitespace
        + 0.20 * competition
        + 0.15 * audience
        + 0.10 * differentiation,
        2,
    )


def compute_commercial(
    repo: Mapping[str, Any],
    *,
    readme: str | None,
    funding: str | None,
    issues: list[dict] | None,
    competitor_count: int | None,
    competitor_query_text: str | None,
    competitor_checked_at: str | None,
    delta7_percentile: float,
    now: datetime,
) -> CommercialResult:
    topics = _load_topics(repo["topics"])
    paid_intent = scan_paid_intent(issues)
    paid_intent_score = paid_intent.score()
    paid_version = detect_paid_version(
        readme=readme,
        homepage=repo["homepage"],
        funding=funding,
        topics=topics,
    )
    audience_score, audience_topic = audience_from_topics(topics)
    gaps, gap_evidence = differentiation_gaps(
        readme=readme,
        description=repo["description"],
        topics=topics,
        has_docker=bool(repo["has_docker"]),
        has_compose=bool(repo["has_compose"]),
        paid_version=paid_version.confidence,
        homepage=repo["homepage"],
    )
    differentiation = min(100.0, 20.0 + 25.0 * len(gaps))
    whitespace = float(paid_version.score())
    competition = competition_score(competitor_count)
    market = market_alignment_score(delta7_percentile, paid_intent_score)
    biz = business_score(
        market_alignment=market,
        whitespace=whitespace,
        competition=competition,
        audience=float(audience_score),
        differentiation=differentiation,
    )

    low_confidence: list[str] = []
    if paid_intent.scanned is None:
        low_confidence.append("paid_intent")
    if competitor_count is None:
        low_confidence.append("competition")
    if not readme and not repo["homepage"] and not funding:
        low_confidence.append("paid_version")
    if not topics:
        low_confidence.append("audience")

    breakdown = {
        "version": COMMERCIAL_VERSION,
        "weights": {
            "market_alignment": 0.30,
            "whitespace": 0.25,
            "competition": 0.20,
            "audience": 0.15,
            "differentiation": 0.10,
        },
        "subscores": {
            "market_alignment": market,
            "whitespace": whitespace,
            "competition": competition,
            "audience": audience_score,
            "differentiation": differentiation,
        },
        "inputs": {
            "delta7_percentile": round(delta7_percentile, 2),
            "paid_intent_score": paid_intent_score,
            "paid_intent_scanned": paid_intent.scanned,
            "paid_intent_hits": paid_intent.hit_count,
            "paid_version": paid_version.confidence,
            "competitor_count": competitor_count,
            "competitor_query": competitor_query_text,
            "audience_topic": audience_topic,
            "gaps": gaps,
        },
        "evidence": {
            "paid_intent": list(paid_intent.examples),
            "paid_version": list(paid_version.evidence),
            "differentiation": gap_evidence,
        },
        "low_confidence_fields": low_confidence,
    }

    return CommercialResult(
        repo_pk=int(repo["id"]),
        full_name=str(repo["full_name"]),
        stars_total=repo["stars_total"],
        repro_score=float(repo["repro_score"] or 0.0),
        analyzed_at=iso_z(now),
        paid_intent=paid_intent,
        paid_intent_score=paid_intent_score,
        paid_version=paid_version,
        competitor_count=competitor_count,
        competitor_query=competitor_query_text,
        competitor_checked_at=competitor_checked_at,
        audience_score=audience_score,
        audience_topic=audience_topic,
        monetization_paths=tuple(gaps),
        gap_count=len(gaps),
        differentiation_score=int(differentiation),
        market_alignment=market,
        whitespace=whitespace,
        competition=competition,
        biz_score=biz,
        low_confidence_fields=tuple(low_confidence),
        score_breakdown=breakdown,
    )


def _load_topics(raw: Any) -> list[str]:
    try:
        payload = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    return [str(topic).lower() for topic in payload]


# -- 批量编排 ---------------------------------------------------------------
@dataclass
class CommercialSummary:
    analyzed: int = 0
    failed: int = 0
    requests: int = 0
    competition_used: int = 0
    errors: list[str] = field(default_factory=list)
    results: list[CommercialResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "analyzed": self.analyzed,
            "failed": self.failed,
            "requests": self.requests,
            "competition_used": self.competition_used,
            "errors": list(self.errors),
            "results": [item.to_dict() for item in self.results],
        }


def _fetch_or_none(client: Any, method: str, *args, **kwargs):
    """非限流错误降级为 None（issues 关闭、README 404 等）。"""
    from .github import GitHubError, GitHubRateLimitError

    try:
        return getattr(client, method)(*args, **kwargs)
    except GitHubRateLimitError:
        raise
    except GitHubError:
        return None


def run_commercial_analysis(
    db: Any,
    client: Any,
    config: Any,
    *,
    limit: int | None = None,
    force: bool = False,
    competition: bool | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str, float], None] | None = None,
) -> CommercialSummary:
    from .github import GitHubError, GitHubRateLimitError

    now = now or utc_now()
    limit = config.biz_limit if limit is None else limit
    stale_before = iso_z(
        now if force else now - timedelta(hours=config.biz_refresh_hours)
    )
    competition_enabled = config.biz_competition if competition is None else competition
    repos = db.list_repos_for_commercial(limit, stale_before)
    summary = CommercialSummary()
    request_start = getattr(getattr(client, "transport", None), "count", 0)

    if hasattr(db, "prune_repo_commercial"):
        db.prune_repo_commercial()

    percentile = percentile_map(
        {
            int(row["repo_pk"]): float(row["delta_7d"])
            for row in db.list_metric_delta7()
        }
    )
    day = now.date().isoformat()
    used_today = (
        db.count_competitor_checks_today(day)
        if hasattr(db, "count_competitor_checks_today")
        else 0
    )
    budget_left = max(0, config.biz_competition_daily_budget - used_today)
    searches_this_run = 0
    competition_disabled = False
    total = len(repos)
    if progress:
        progress("准备商业分析", 0.0)

    for index, repo in enumerate(repos, start=1):
        full_name = str(repo["full_name"])
        try:
            readme = _fetch_or_none(client, "get_readme", full_name)
            funding = _fetch_or_none(
                client, "get_file_text", full_name, ".github/FUNDING.yml"
            )
            issues = _fetch_or_none(
                client,
                "get_issues",
                full_name,
                per_page=config.biz_issue_limit,
            )
        except GitHubRateLimitError as exc:
            summary.errors.append(f"{full_name}: {exc}")
            summary.failed += 1
            break

        topics = _load_topics(repo["topics"])
        audience_score, audience_topic = audience_from_topics(topics)
        competitor_count = None
        competitor_query_text = None
        competitor_checked_at = None

        if competition_enabled and float(repo["repro_score"] or 0) >= (
            config.biz_competition_min_repro
        ):
            cached_at = repo["cached_competitor_checked_at"]
            cached_count = repo["cached_competitor_count"]
            if (
                cached_at is not None
                and cached_count is not None
                and now - parse_ts(str(cached_at)) <= timedelta(days=7)
            ):
                competitor_count = int(cached_count)
                competitor_query_text = repo["cached_competitor_query"]
                competitor_checked_at = cached_at
            elif budget_left > 0 and not competition_disabled:
                query = competition_query(
                    topics=topics,
                    description=repo["description"],
                    language=repo["language"],
                    audience_topic=audience_topic,
                )
                if query:
                    if searches_this_run > 0 and config.biz_search_delay_seconds > 0:
                        sleep(config.biz_search_delay_seconds)
                    try:
                        payload = client.search_repositories(query, per_page=1)
                        if payload is not None:
                            total = payload.get("total_count")
                            if isinstance(total, int):
                                competitor_count = max(0, total - 1)
                                competitor_checked_at = iso_z(now)
                                competitor_query_text = query
                                summary.competition_used += 1
                    except GitHubRateLimitError:
                        competition_disabled = True
                    except GitHubError as exc:
                        summary.errors.append(f"{full_name} competitor: {exc}")
                    finally:
                        budget_left -= 1
                        searches_this_run += 1

        result = compute_commercial(
            repo,
            readme=readme,
            funding=funding,
            issues=issues,
            competitor_count=competitor_count,
            competitor_query_text=competitor_query_text,
            competitor_checked_at=competitor_checked_at,
            delta7_percentile=percentile.get(int(repo["id"]), 50.0),
            now=now,
        )
        db.upsert_repo_commercial(result.to_row())
        summary.results.append(result)
        summary.analyzed += 1
        if progress:
            progress(f"商业分析 {full_name}", index / max(1, total))

    transport = getattr(client, "transport", None)
    if transport is not None and hasattr(transport, "count"):
        summary.requests = transport.count - request_start
    if progress:
        progress("商业分析完成", 1.0)
    return summary


# -- 输出 -------------------------------------------------------------------
def load_commercial_output(db: Any) -> list[dict[str, Any]]:
    rows = db.list_repo_commercial_joined()
    output: list[dict[str, Any]] = []
    for row in rows:
        try:
            paths = json.loads(row["monetization_paths"] or "[]")
            low = json.loads(row["low_confidence_fields"] or "[]")
            examples = json.loads(row["paid_intent_issues"] or "[]")
        except (TypeError, ValueError):
            paths, low, examples = [], [], []
        output.append(
            {
                "full_name": row["full_name"],
                "stars_total": row["stars_total"],
                "repro_score": row["repro_score"],
                "biz_score": row["biz_score"],
                "market_alignment": row["market_alignment"],
                "whitespace": row["whitespace"],
                "competition": row["competition"],
                "paid_intent_score": row["paid_intent_score"],
                "paid_intent_issues": row["paid_intent_hits"]
                if "paid_intent_hits" in row.keys()
                else len(examples),
                "paid_version": row["paid_version"],
                "competitor_count": row["competitor_count"],
                "competitor_query": row["competitor_query"],
                "audience_score": row["audience_score"],
                "audience_topic": row["audience_topic"],
                "differentiation_score": row["differentiation_score"],
                "monetization_paths": paths,
                "low_confidence_fields": low,
                "analyzed_at": row["analyzed_at"],
            }
        )
    return output


def sort_commercial(
    rows: list[dict[str, Any]], sort: str = "biz"
) -> list[dict[str, Any]]:
    if sort == "biz":
        return sorted(
            rows, key=lambda row: (-(row["biz_score"] or 0.0), row["full_name"])
        )
    if sort == "market":
        return sorted(
            rows,
            key=lambda row: (-(row["market_alignment"] or 0.0), row["full_name"]),
        )
    if sort == "repro":
        return sorted(
            rows, key=lambda row: (-(row["repro_score"] or 0.0), row["full_name"])
        )
    if sort == "stars":
        return sorted(
            rows, key=lambda row: (-(row["stars_total"] or 0), row["full_name"])
        )
    raise ValueError(f"unsupported sort: {sort}")

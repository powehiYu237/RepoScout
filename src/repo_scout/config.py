"""运行时配置。

支持 TOML 配置 + 环境变量覆盖：
- REPO_SCOUT_DB_PATH
- REPO_SCOUT_GITHUB_TOKENS（逗号或换行分隔）
- GITHUB_TOKENS（兼容常见写法）
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_USER_AGENT = "RepoScout/0.1 (+https://github.com/repo-scout; data-collection)"


@dataclass(frozen=True)
class Config:
    db_path: Path = Path("data/repo_scout.sqlite3")
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 20.0
    retries: int = 3
    min_trending_items: int = 8
    enrich_limit: int = 200
    snapshot_limit: int = 500
    snapshot_bucket_hours: int = 6
    github_tokens: tuple[str, ...] = ()
    api_base: str = "https://api.github.com"
    trending_base_url: str = "https://github.com/trending"
    low_watermark: int = 100
    fetch_contributors: bool = False
    fetch_star_history: bool = True
    star_history_limit: int = 200
    star_history_weeks: int = 30
    # M3 可复刻性：用户熟悉的技术栈（小写），用于 FR-305 语言匹配
    tech_stack: tuple[str, ...] = ("python", "typescript", "javascript")
    analysis_limit: int = 100
    analysis_refresh_hours: float = 24.0
    fetch_readme: bool = True
    # M3 深分析：浅克隆 + cloc
    deep_limit: int = 20
    deep_refresh_hours: float = 168.0
    deep_max_size_mb: int = 200
    deep_timeout_seconds: int = 600
    deep_source: str = "auto"
    cloc_path: str = ""
    cloc_auto_download: bool = True
    # M4 商业价值：issues 付费意愿、商业化检测、竞品搜索
    biz_limit: int = 100
    biz_refresh_hours: float = 168.0
    biz_issue_limit: int = 100
    biz_competition: bool = True
    biz_competition_daily_budget: int = 50
    biz_competition_min_repro: float = 60.0
    biz_search_delay_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.snapshot_bucket_hours <= 0 or 24 % self.snapshot_bucket_hours != 0:
            raise ValueError("snapshot_bucket_hours must be a positive divisor of 24")
        if self.star_history_weeks <= 0:
            raise ValueError("star_history_weeks must be positive")
        if self.star_history_limit < 0:
            raise ValueError("star_history_limit must be >= 0")
        if self.analysis_limit < 0:
            raise ValueError("analysis_limit must be >= 0")
        if self.analysis_refresh_hours < 0:
            raise ValueError("analysis_refresh_hours must be >= 0")
        if self.deep_limit < 0:
            raise ValueError("deep_limit must be >= 0")
        if self.deep_refresh_hours < 0:
            raise ValueError("deep_refresh_hours must be >= 0")
        if self.deep_max_size_mb <= 0:
            raise ValueError("deep_max_size_mb must be positive")
        if self.deep_timeout_seconds <= 0:
            raise ValueError("deep_timeout_seconds must be positive")
        if self.deep_source not in ("auto", "clone", "tarball"):
            raise ValueError("deep_source must be auto, clone or tarball")
        if self.biz_limit < 0:
            raise ValueError("biz_limit must be >= 0")
        if self.biz_refresh_hours < 0:
            raise ValueError("biz_refresh_hours must be >= 0")
        if not 1 <= self.biz_issue_limit <= 100:
            raise ValueError("biz_issue_limit must be between 1 and 100")
        if self.biz_competition_daily_budget < 0:
            raise ValueError("biz_competition_daily_budget must be >= 0")
        if self.biz_search_delay_seconds < 0:
            raise ValueError("biz_search_delay_seconds must be >= 0")


def _split_tokens(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.replace("\n", ",").split(",") if part.strip())


def load_config(path: str | Path | None = None) -> Config:
    raw: dict = {}
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"config file not found: {config_path}")
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))

    database = raw.get("database", {})
    http = raw.get("http", {})
    ingest = raw.get("ingest", {})
    github = raw.get("github", {})
    repro = raw.get("repro", {})
    biz = raw.get("biz", {})

    tokens = _split_tokens(
        os.environ.get("REPO_SCOUT_GITHUB_TOKENS")
        or os.environ.get("GITHUB_TOKENS", "")
    )
    if not tokens:
        tokens = tuple(str(token) for token in github.get("tokens", []) if token)

    return Config(
        db_path=Path(
            os.environ.get("REPO_SCOUT_DB_PATH")
            or database.get("path", "data/repo_scout.sqlite3")
        ),
        user_agent=str(http.get("user_agent", DEFAULT_USER_AGENT)),
        timeout=float(http.get("timeout_seconds", 20)),
        retries=int(http.get("retries", 3)),
        min_trending_items=int(http.get("min_trending_items", 8)),
        enrich_limit=int(ingest.get("enrich_limit", 200)),
        snapshot_limit=int(ingest.get("snapshot_limit", 500)),
        snapshot_bucket_hours=int(ingest.get("snapshot_bucket_hours", 6)),
        github_tokens=tokens,
        api_base=str(github.get("api_base", "https://api.github.com")).rstrip("/"),
        trending_base_url=str(
            github.get("trending_base_url", "https://github.com/trending")
        ).rstrip("/"),
        low_watermark=int(github.get("low_watermark", 100)),
        fetch_contributors=bool(github.get("fetch_contributors", False)),
        fetch_star_history=bool(ingest.get("fetch_star_history", True)),
        star_history_limit=int(ingest.get("star_history_limit", 200)),
        star_history_weeks=int(ingest.get("star_history_weeks", 30)),
        tech_stack=tuple(
            str(item).strip().lower()
            for item in repro.get(
                "tech_stack", ["python", "typescript", "javascript"]
            )
            if str(item).strip()
        ),
        analysis_limit=int(repro.get("analysis_limit", 100)),
        analysis_refresh_hours=float(repro.get("refresh_hours", 24)),
        fetch_readme=bool(repro.get("fetch_readme", True)),
        deep_limit=int(repro.get("deep_limit", 20)),
        deep_refresh_hours=float(repro.get("deep_refresh_hours", 168)),
        deep_max_size_mb=int(repro.get("deep_max_size_mb", 200)),
        deep_timeout_seconds=int(repro.get("deep_timeout_seconds", 600)),
        deep_source=str(repro.get("deep_source", "auto")),
        cloc_path=str(repro.get("cloc_path", "")),
        cloc_auto_download=bool(repro.get("cloc_auto_download", True)),
        biz_limit=int(biz.get("analysis_limit", 100)),
        biz_refresh_hours=float(biz.get("refresh_hours", 168)),
        biz_issue_limit=int(biz.get("issue_limit", 100)),
        biz_competition=bool(biz.get("competition", True)),
        biz_competition_daily_budget=int(
            biz.get("competition_daily_budget", 50)
        ),
        biz_competition_min_repro=float(biz.get("competition_min_repro", 60)),
        biz_search_delay_seconds=float(biz.get("search_delay_seconds", 2.0)),
    )

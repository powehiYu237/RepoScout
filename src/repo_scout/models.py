"""领域模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrendingEntry:
    """Trending 页面解析出的一条记录。"""

    full_name: str
    owner: str
    name: str
    html_url: str
    period: str
    description: str | None = None
    language: str | None = None
    stars_total: int | None = None
    period_stars: int | None = None


@dataclass(frozen=True)
class RepoMetadata:
    """GitHub REST API 返回的仓库元数据（已归一化）。"""

    repo_id: int
    full_name: str
    owner: str
    name: str
    html_url: str
    description: str | None
    homepage: str | None
    language: str | None
    license_spdx: str | None
    topics: list[str]
    stars_total: int | None
    forks: int | None
    open_issues: int | None
    subscribers: int | None
    size_kb: int | None
    default_branch: str | None
    archived: bool
    fork: bool
    disabled: bool
    created_at: str | None
    pushed_at: str | None
    contributors: int | None = None

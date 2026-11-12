"""FR-106：仓库分类与黑名单闸门（G1/G2 的自动部分）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_AWESOME_NAME = re.compile(r"^(awesome[-_].*)|(.*[-_]awesome)$", re.IGNORECASE)
_AWESOME_DESC = re.compile(
    r"(curated list|awesome[- ]list|awesome[- ]lists|资源列表|awesome 列表)",
    re.IGNORECASE,
)
_TUTORIAL_NAME = re.compile(
    r"(tutorial|guide|handbook|roadmap|course|bootcamp|learn|教程|指南|学习|入门)",
    re.IGNORECASE,
)
_TEMPLATE_NAME = re.compile(r"(template|boilerplate|starter)", re.IGNORECASE)

_AWESOME_TOPICS = {"awesome", "awesome-list", "awesome-lists"}
_TUTORIAL_TOPICS = {
    "tutorial",
    "tutorials",
    "course",
    "courses",
    "book",
    "books",
    "interview",
    "interview-questions",
    "roadmap",
    "learning",
}
_TEMPLATE_TOPICS = {"template", "templates", "boilerplate", "starter-kit"}


@dataclass(frozen=True)
class Classification:
    category: str
    excluded: bool
    reason: str | None = None


def classify_repo(
    *,
    full_name: str,
    description: str | None = None,
    topics: list[str] | None = None,
    language: str | None = None,
    is_fork: bool = False,
    is_archived: bool = False,
    is_disabled: bool = False,
    md_ratio: float | None = None,
    override: str | None = None,
) -> Classification:
    """按 PRD FR-106 的优先级返回分类。

    优先级：手动覆盖 > 镜像/归档 > 清单 > 教程 > 模板 > 纯文档 > 普通。
    模板不直接排除，只标记为低差异化（与 PRD 一致）。
    """
    if override == "whitelist":
        return Classification(category="normal", excluded=False, reason=None)
    if override == "blacklist":
        return Classification(
            category="manual", excluded=True, reason="manual_blacklist"
        )

    if is_fork:
        return Classification(category="mirror", excluded=True, reason="fork_mirror")
    if is_archived or is_disabled:
        return Classification(category="archived", excluded=True, reason="archived")

    topic_set = {topic.lower() for topic in (topics or []) if topic}
    name = full_name.rsplit("/", 1)[-1]
    description = description or ""
    haystack = f"{name} {description}".lower()

    if (
        topic_set & _AWESOME_TOPICS
        or _AWESOME_NAME.search(name)
        or _AWESOME_DESC.search(description)
    ):
        return Classification(
            category="awesome", excluded=True, reason="awesome_list"
        )

    if (
        topic_set & _TUTORIAL_TOPICS
        or _TUTORIAL_NAME.search(haystack)
    ):
        return Classification(category="tutorial", excluded=True, reason="tutorial")

    if topic_set & _TEMPLATE_TOPICS or _TEMPLATE_NAME.search(name):
        return Classification(
            category="template", excluded=False, reason="low_differentiation"
        )

    if language is None and md_ratio is not None and md_ratio > 0.8:
        return Classification(category="docs", excluded=True, reason="docs_only")

    return Classification(category="normal", excluded=False, reason=None)

"""可选联网测试。

默认跳过；设置 ``REPO_SCOUT_ONLINE=1`` 后运行：

    REPO_SCOUT_ONLINE=1 python -m pytest tests/test_online.py -v

会消耗少量 GitHub 未鉴权配额（60 次/小时）。
"""

from __future__ import annotations

import os

import pytest

from repo_scout.config import load_config
from repo_scout.github import GitHubClient
from repo_scout.http import HttpxTransport
from repo_scout.star_history import fetch_star_history
from repo_scout.trending import fetch_trending

pytestmark = pytest.mark.skipif(
    os.environ.get("REPO_SCOUT_ONLINE") != "1",
    reason="network test; set REPO_SCOUT_ONLINE=1 to enable",
)


def test_real_trending_page_parses() -> None:
    config = load_config(None)
    transport = HttpxTransport(config.user_agent, config.timeout)
    try:
        entries = fetch_trending(
            transport,
            "daily",
            base_url=config.trending_base_url,
            user_agent=config.user_agent,
            retries=1,
            min_items=config.min_trending_items,
            timeout=config.timeout,
        )
    finally:
        transport.close()

    assert len(entries) >= 8
    assert all(entry.full_name.count("/") == 1 for entry in entries)
    assert any(entry.period_stars is not None for entry in entries)


def test_official_star_history_endpoint() -> None:
    config = load_config(None)
    transport = HttpxTransport(config.user_agent, config.timeout)
    try:
        client = GitHubClient(
            transport,
            base_url=config.api_base,
            user_agent=config.user_agent,
            retries=1,
            timeout=config.timeout,
        )
        weeks = fetch_star_history(client, "octocat/Hello-World", weeks=5)
    finally:
        transport.close()

    assert weeks is not None
    assert len(weeks) == 5
    assert all(week.total >= 0 and len(week.days) == 7 for week in weeks)
    assert weeks[0].week_start > weeks[-1].week_start

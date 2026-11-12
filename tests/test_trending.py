from __future__ import annotations

import pytest

from repo_scout.http import HttpResponse, TransportError
from repo_scout.trending import (
    TrendingFetchError,
    TrendingParseError,
    fetch_trending,
    parse_count,
    parse_trending_html,
)
from tests.support import FakeTransport, fixture


def test_parse_daily_fixture() -> None:
    entries = parse_trending_html(fixture("trending_daily.html"), "daily")

    assert [entry.full_name for entry in entries] == [
        "acme/notes-ai",
        "awesome/awesome-ai",
        "lin/tutorial-hub",
        "solo/tiny-tool",
    ]

    first = entries[0]
    assert first.owner == "acme"
    assert first.name == "notes-ai"
    assert first.html_url == "https://github.com/acme/notes-ai"
    assert first.stars_total == 8200
    assert first.period_stars == 180
    assert first.language == "TypeScript"
    assert first.description == (
        "Local-first AI note-taking app with offline sync & encryption."
    )

    awesome = entries[1]
    assert awesome.language is None
    assert awesome.stars_total == 30000
    assert awesome.period_stars == 2500

    tiny = entries[3]
    assert tiny.description is None
    assert tiny.language is None
    assert tiny.stars_total == 600
    assert tiny.period_stars == 45


def test_parse_weekly_and_monthly_windows() -> None:
    weekly = parse_trending_html(fixture("trending_weekly.html"), "weekly")
    assert len(weekly) == 2
    assert weekly[0].full_name == "acme/notes-ai"
    assert weekly[0].period_stars == 1200
    assert weekly[1].full_name == "weekly/weekly-tool"
    assert weekly[1].stars_total == 2300

    monthly = parse_trending_html(fixture("trending_monthly.html"), "monthly")
    assert len(monthly) == 1
    assert monthly[0].full_name == "monthly/monthly-tool"
    assert monthly[0].period_stars == 2000


def test_duplicate_repos_are_deduped_within_a_window() -> None:
    html = fixture("trending_daily.html") * 2
    entries = parse_trending_html(html, "daily")
    assert len(entries) == 4


def test_parse_count_handles_commas_and_k_suffix() -> None:
    assert parse_count("8,200") == 8200
    assert parse_count("1.2k") == 1200
    assert parse_count(" 45 ") == 45
    assert parse_count(None) is None
    assert parse_count("n/a") is None


def test_fetch_retries_server_error_then_succeeds() -> None:
    responses = [
        HttpResponse(status=500, text="", headers={}),
        HttpResponse(status=200, text=fixture("trending_daily.html"), headers={}),
    ]
    transport = FakeTransport(lambda url, headers: responses.pop(0))
    slept: list[float] = []

    entries = fetch_trending(
        transport,
        "daily",
        base_url="https://github.com/trending",
        user_agent="test-agent",
        retries=2,
        min_items=3,
        sleep=slept.append,
    )

    assert len(entries) == 4
    assert len(transport.calls) == 2
    assert slept == [0.5]
    assert transport.calls[0]["url"].endswith("?since=daily")


def test_fetch_retries_transport_errors() -> None:
    attempts = {"count": 0}

    def handler(url, headers):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TransportError("connection reset")
        return HttpResponse(status=200, text=fixture("trending_daily.html"), headers={})

    transport = FakeTransport(handler)
    slept: list[float] = []

    entries = fetch_trending(
        transport,
        "daily",
        base_url="https://github.com/trending",
        user_agent="test-agent",
        retries=2,
        min_items=3,
        sleep=slept.append,
    )

    assert len(entries) == 4
    assert attempts["count"] == 2
    assert slept == [0.5]


def test_fetch_raises_parse_error_when_page_structure_changes() -> None:
    transport = FakeTransport(
        lambda url, headers: HttpResponse(
            status=200, text=fixture("trending_garbled.html"), headers={}
        )
    )

    with pytest.raises(TrendingParseError):
        fetch_trending(
            transport,
            "daily",
            base_url="https://github.com/trending",
            user_agent="test-agent",
            min_items=1,
        )


def test_fetch_does_not_retry_non_retryable_status() -> None:
    transport = FakeTransport(
        lambda url, headers: HttpResponse(status=404, text="not found", headers={})
    )

    with pytest.raises(TrendingFetchError):
        fetch_trending(
            transport,
            "weekly",
            base_url="https://github.com/trending",
            user_agent="test-agent",
            retries=3,
        )

    assert len(transport.calls) == 1

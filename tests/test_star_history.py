from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from repo_scout.github import GitHubClient
from repo_scout.star_history import (
    StarHistoryParseError,
    StarWeek,
    compute_weekly_rows,
    fetch_star_history,
    parse_star_history,
)
from tests.support import FakeTransport, json_response

NEWEST_WEEK = 1794096000  # 2026-11-08 Sunday 00:00 UTC


def payload(count: int, start: int = NEWEST_WEEK, total: int = 10) -> list[dict]:
    return [
        {
            "week": start - index * 7 * 86400,
            "total": total,
            "days": [total, 0, 0, 0, 0, 0, 0],
        }
        for index in range(count)
    ]


def test_parse_star_history() -> None:
    weeks = parse_star_history(
        [{"week": NEWEST_WEEK, "total": 323, "days": [9, 79, 235, 0, 0, 0, 0]}]
    )

    assert len(weeks) == 1
    assert weeks[0].week_start == date(2026, 11, 8)
    assert weeks[0].week_iso == "2026-11-08"
    assert weeks[0].total == 323
    assert weeks[0].days == (9, 79, 235, 0, 0, 0, 0)


def test_parse_sorts_descending_dedupes_and_pads_days() -> None:
    older = {"week": NEWEST_WEEK - 7 * 86400, "total": 5, "days": [1, 2]}
    newer = {"week": NEWEST_WEEK, "total": 7, "days": [7]}

    weeks = parse_star_history([older, newer, newer])

    assert [week.week_start for week in weeks] == [
        date(2026, 11, 8),
        date(2026, 11, 1),
    ]
    assert weeks[0].days == (7, 0, 0, 0, 0, 0, 0)
    assert weeks[1].days == (1, 2, 0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    "payload",
    [
        {"week": NEWEST_WEEK},
        [{"total": 1, "days": []}],
        [{"week": -1, "total": 1, "days": []}],
        [{"week": NEWEST_WEEK, "total": -2, "days": []}],
        [{"week": NEWEST_WEEK, "total": 1, "days": "nope"}],
        [{"week": NEWEST_WEEK, "total": 1, "days": [1, "x"]}],
    ],
)
def test_parse_rejects_malformed_payload(payload) -> None:
    with pytest.raises(StarHistoryParseError):
        parse_star_history(payload)


def test_compute_weekly_rows_derives_cumulative_stars() -> None:
    weeks = [
        StarWeek(date(2026, 11, 8), 323, (0,) * 7),
        StarWeek(date(2026, 11, 1), 22, (0,) * 7),
    ]

    rows = compute_weekly_rows(weeks, current_total=4229)

    assert rows[0]["week_start"] == "2026-11-08"
    assert rows[0]["delta"] == 323
    assert rows[0]["stars_end"] == 4229
    assert rows[1]["stars_end"] == 4229 - 323
    assert rows[0]["source"] == "github_stargazers_history"


def test_compute_weekly_rows_without_current_total() -> None:
    rows = compute_weekly_rows(
        [StarWeek(date(2026, 11, 8), 10, (0,) * 7)], current_total=None
    )
    assert rows[0]["stars_end"] is None


def test_fetch_star_history_paginates_and_sends_api_version() -> None:
    def handler(url: str, headers: dict[str, str]):
        page = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
        if page == 1:
            return json_response(payload(30))
        if page == 2:
            return json_response(payload(15, start=NEWEST_WEEK - 30 * 7 * 86400))
        return json_response([])

    transport = FakeTransport(handler)
    client = GitHubClient(transport)

    weeks = fetch_star_history(client, "acme/notes-ai", weeks=45, per_page=30)

    assert weeks is not None
    assert len(weeks) == 45
    assert len(transport.calls) == 2
    assert weeks[0].week_start == date(2026, 11, 8)
    assert all(
        call["headers"]["X-GitHub-Api-Version"] == "2026-03-10"
        for call in transport.calls
    )


def test_fetch_star_history_returns_none_for_missing_repo() -> None:
    transport = FakeTransport(
        lambda url, headers: json_response({"message": "Not Found"}, status=404)
    )
    client = GitHubClient(transport)

    assert fetch_star_history(client, "missing/repo") is None


def test_fetch_star_history_stops_on_short_page() -> None:
    transport = FakeTransport(lambda url, headers: json_response(payload(5)))
    client = GitHubClient(transport)

    weeks = fetch_star_history(client, "acme/notes-ai", weeks=45, per_page=30)

    assert weeks is not None
    assert len(weeks) == 5
    assert len(transport.calls) == 1


def test_get_star_history_page_validates_pagination() -> None:
    client = GitHubClient(FakeTransport(lambda url, headers: json_response([])))

    with pytest.raises(ValueError):
        client.get_star_history_page("a/b", per_page=31)
    with pytest.raises(ValueError):
        client.get_star_history_page("a/b", page=101)

from __future__ import annotations

import pytest

from repo_scout.github import (
    GitHubAuthError,
    GitHubClient,
    GitHubRateLimitError,
    TokenPool,
    metadata_from_payload,
)
from repo_scout.http import HttpResponse
from tests.support import FakeTransport, json_response, repo_payload


def test_metadata_from_payload_normalizes_fields() -> None:
    meta = metadata_from_payload(repo_payload(topics=["AI", "Notes"]))

    assert meta.repo_id == 101
    assert meta.full_name == "acme/notes-ai"
    assert meta.owner == "acme"
    assert meta.name == "notes-ai"
    assert meta.license_spdx == "MIT"
    assert meta.topics == ["ai", "notes"]
    assert meta.stars_total == 8200
    assert meta.forks == 1100
    assert meta.open_issues == 42
    assert meta.size_kb == 1200
    assert meta.archived is False
    assert meta.fork is False


def test_metadata_without_license() -> None:
    payload = repo_payload(license_spdx=None)
    payload["license"] = None
    assert metadata_from_payload(payload).license_spdx is None


def test_client_rotates_token_on_rate_limit() -> None:
    payload = repo_payload()

    def handler(url: str, headers: dict[str, str]) -> HttpResponse:
        if headers.get("Authorization") == "Bearer t1":
            return json_response(
                {"message": "rate limited"},
                status=403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "2000"},
            )
        return json_response(payload)

    transport = FakeTransport(handler)
    pool = TokenPool(["t1", "t2"], now=lambda: 1000, low_watermark=100)
    client = GitHubClient(transport, tokens=pool, sleep=lambda _: None)

    meta = client.get_repo("acme/notes-ai")

    assert meta is not None
    assert meta.repo_id == 101
    assert [
        call["headers"]["Authorization"] for call in transport.calls
    ] == ["Bearer t1", "Bearer t2"]


def test_client_retries_server_errors() -> None:
    responses = [
        HttpResponse(status=500, text="", headers={}),
        json_response(repo_payload()),
    ]
    transport = FakeTransport(lambda url, headers: responses.pop(0))
    slept: list[float] = []
    client = GitHubClient(transport, retries=2, sleep=slept.append)

    meta = client.get_repo("acme/notes-ai")

    assert meta is not None
    assert meta.repo_id == 101
    assert len(transport.calls) == 2
    assert slept == [0.5]


def test_client_returns_none_for_missing_repo() -> None:
    transport = FakeTransport(
        lambda url, headers: json_response({"message": "Not Found"}, status=404)
    )
    client = GitHubClient(transport)

    assert client.get_repo("missing/repo") is None
    assert len(transport.calls) == 1


def test_client_raises_auth_error_after_retries() -> None:
    transport = FakeTransport(lambda url, headers: json_response({}, status=401))
    client = GitHubClient(transport, retries=1, sleep=lambda _: None)

    with pytest.raises(GitHubAuthError):
        client.get_repo("acme/notes-ai")

    assert len(transport.calls) == 2


def test_client_raises_rate_limit_error_when_all_tokens_cooling() -> None:
    transport = FakeTransport(
        lambda url, headers: json_response(
            {}, status=403, headers={"x-ratelimit-reset": "1"}
        )
    )
    client = GitHubClient(transport, retries=1, sleep=lambda _: None)

    with pytest.raises(GitHubRateLimitError):
        client.get_repo("acme/notes-ai")


def test_unauthenticated_request_when_no_tokens() -> None:
    transport = FakeTransport(lambda url, headers: json_response(repo_payload()))
    client = GitHubClient(transport)

    client.get_repo("acme/notes-ai")

    assert "Authorization" not in transport.calls[0]["headers"]
    assert transport.calls[0]["headers"]["Accept"] == "application/vnd.github+json"


def test_contributors_count_from_link_header() -> None:
    link = (
        '<https://api.github.com/repositories/1/contributors'
        '?per_page=1&anon=false&page=2>; rel="next", '
        '<https://api.github.com/repositories/1/contributors'
        '?per_page=1&anon=false&page=42>; rel="last"'
    )
    transport = FakeTransport(
        lambda url, headers: HttpResponse(status=200, text="[]", headers={"link": link})
    )
    client = GitHubClient(transport)

    assert client.get_contributors_count("acme/notes-ai") == 42


def test_contributors_count_without_link_header() -> None:
    transport = FakeTransport(
        lambda url, headers: HttpResponse(
            status=200,
            text='[{"login": "a"}, {"login": "b"}]',
            headers={},
        )
    )
    client = GitHubClient(transport)

    assert client.get_contributors_count("acme/notes-ai") == 2


def test_token_pool_rotates_at_low_watermark() -> None:
    pool = TokenPool(["a", "b"], low_watermark=100, now=lambda: 0)

    assert pool.acquire() == "a"
    pool.report("a", 200, {"x-ratelimit-remaining": "50"})
    assert pool.acquire() == "b"


def test_token_pool_skips_cooling_tokens_until_reset() -> None:
    now = [0.0]
    pool = TokenPool(["a"], now=lambda: now[0])

    pool.report("a", 403, {"x-ratelimit-reset": "100"})
    assert pool.acquire() is None

    now[0] = 101
    assert pool.acquire() == "a"

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from repo_scout.db import Database
from repo_scout.models import RepoMetadata, TrendingEntry
from repo_scout.util import floor_to_bucket


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "test.sqlite3")
    db.init_schema()
    return db


def entry(**overrides) -> TrendingEntry:
    values = {
        "full_name": "acme/notes-ai",
        "owner": "acme",
        "name": "notes-ai",
        "html_url": "https://github.com/acme/notes-ai",
        "period": "daily",
        "description": "Local-first notes.",
        "language": "TypeScript",
        "stars_total": 100,
        "period_stars": 10,
    }
    values.update(overrides)
    return TrendingEntry(**values)


def metadata(**overrides) -> RepoMetadata:
    values = {
        "repo_id": 101,
        "full_name": "acme/notes-ai",
        "owner": "acme",
        "name": "notes-ai",
        "html_url": "https://github.com/acme/notes-ai",
        "description": "Local-first notes.",
        "homepage": None,
        "language": "TypeScript",
        "license_spdx": "MIT",
        "topics": ["AI", "Notes", "ai"],
        "stars_total": 8200,
        "forks": 1100,
        "open_issues": 42,
        "subscribers": 300,
        "size_kb": 1200,
        "default_branch": "main",
        "archived": False,
        "fork": False,
        "disabled": False,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-11-10T00:00:00Z",
    }
    values.update(overrides)
    return RepoMetadata(**values)


def test_upsert_is_idempotent_and_case_insensitive(tmp_path) -> None:
    db = make_db(tmp_path)

    first = db.upsert_trending_entry(entry(), "2026-11-11T06:00:00Z")
    second = db.upsert_trending_entry(
        entry(stars_total=120), "2026-11-11T12:00:00Z"
    )
    third = db.upsert_trending_entry(
        entry(full_name="acme/notes-ai", stars_total=None),
        "2026-11-11T13:00:00Z",
    )

    assert first.is_new is True
    assert second.is_new is False
    assert third.is_new is False
    assert first.repo_pk == second.repo_pk == third.repo_pk
    assert db.count_repos() == 1
    assert db.get_repo(first.repo_pk)["stars_total"] == 120


def test_snapshot_uses_bucket_and_keeps_latest_value(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = db.upsert_trending_entry(entry(), "2026-11-11T06:00:00Z").repo_pk

    db.take_snapshot(repo_pk, "2026-11-11T06:00:00Z", stars=100)
    db.take_snapshot(repo_pk, "2026-11-11T06:00:00Z", stars=110)
    db.take_snapshot(repo_pk, "2026-11-11T12:00:00Z", stars=130)

    assert db.count_snapshots() == 2
    rows = list(
        db.conn.execute(
            "SELECT captured_at, stars FROM snapshots ORDER BY captured_at"
        )
    )
    assert rows[0]["stars"] == 110
    assert rows[1]["stars"] == 130


def test_trending_hit_is_idempotent_per_bucket(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = db.upsert_trending_entry(entry(), "2026-11-11T06:00:00Z").repo_pk

    db.record_trending_hit(repo_pk, "daily", "2026-11-11T06:00:00Z", 10, 1)
    db.record_trending_hit(repo_pk, "daily", "2026-11-11T06:00:00Z", 12, 2)
    db.record_trending_hit(repo_pk, "weekly", "2026-11-11T06:00:00Z", 100, 1)

    assert db.count_trending_hits() == 2


def test_apply_metadata_and_mark_attempt(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = db.upsert_trending_entry(entry(), "2026-11-11T06:00:00Z").repo_pk

    db.apply_metadata(repo_pk, metadata(), "2026-11-11T07:30:00Z")

    row = db.get_repo(repo_pk)
    assert row["repo_id"] == 101
    assert row["license_spdx"] == "MIT"
    assert json.loads(row["topics"]) == ["ai", "notes"]
    assert row["stars_total"] == 8200
    assert row["metadata_fetched_at"] == "2026-11-11T07:30:00Z"

    db.mark_metadata_attempt(repo_pk, "2026-11-12T07:30:00Z")
    assert (
        db.get_repo(repo_pk)["metadata_fetched_at"] == "2026-11-12T07:30:00Z"
    )


def test_enrichment_selection_skips_excluded_and_fresh_repos(tmp_path) -> None:
    db = make_db(tmp_path)

    fresh = db.upsert_trending_entry(entry(), "2026-11-11T07:30:00Z").repo_pk
    db.apply_metadata(fresh, metadata(), "2026-11-11T07:30:00Z")

    stale = db.upsert_trending_entry(
        entry(full_name="old/stale", owner="old", name="stale"),
        "2026-11-11T07:30:00Z",
    ).repo_pk
    db.apply_metadata(stale, metadata(repo_id=102, full_name="old/stale"), "2026-11-01T00:00:00Z")

    excluded = db.upsert_trending_entry(
        entry(full_name="awesome/awesome-ai"),
        "2026-11-11T07:30:00Z",
    ).repo_pk
    from repo_scout.classify import classify_repo

    db.set_classification(excluded, classify_repo(full_name="awesome/awesome-ai"))

    rows = db.list_repos_for_enrichment(10, "2026-11-10T07:30:00Z")
    names = {row["full_name"] for row in rows}
    assert names == {"old/stale"}


def test_star_weekly_upsert_selection_and_mark(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = db.upsert_trending_entry(entry(), "2026-11-11T07:30:00Z").repo_pk
    db.apply_metadata(repo_pk, metadata(), "2026-11-11T07:30:00Z")

    rows = [
        {
            "week_start": "2026-11-08",
            "delta": 10,
            "stars_end": 8200,
            "source": "github_stargazers_history",
        },
        {
            "week_start": "2026-11-01",
            "delta": 20,
            "stars_end": 8190,
            "source": "github_stargazers_history",
        },
    ]
    db.upsert_star_weekly(repo_pk, rows, "2026-11-11T07:45:00Z")

    assert db.count_star_weekly() == 2
    stored = db.get_star_weekly(repo_pk)
    assert [row["week_start"] for row in stored] == ["2026-11-08", "2026-11-01"]
    assert stored[0]["stars_end"] == 8200

    db.upsert_star_weekly(
        repo_pk,
        [{"week_start": "2026-11-08", "delta": 11, "stars_end": 8201}],
        "2026-11-11T08:00:00Z",
    )
    assert db.count_star_weekly() == 2
    assert db.get_star_weekly(repo_pk)[0]["delta"] == 11

    selected = db.list_repos_for_star_history(10, "2026-11-10T07:45:00Z")
    assert [row["full_name"] for row in selected] == ["acme/notes-ai"]

    db.mark_star_history_fetched(repo_pk, "2026-11-11T07:45:00Z")
    assert db.list_repos_for_star_history(10, "2026-11-10T07:45:00Z") == []


def test_log_run_and_status(tmp_path) -> None:
    db = make_db(tmp_path)

    db.log_run(
        run_at="2026-11-11T07:31:00Z",
        run_type="ingest",
        status="ok",
        requests=7,
        success=4,
        items=7,
        details={"fetched": 7},
    )

    run = db.latest_run()
    assert run is not None
    assert run["status"] == "ok"
    assert json.loads(run["details"])["fetched"] == 7

    status = db.status()
    assert status["repos"] == 0
    assert status["last_run"]["requests"] == 7


def test_floor_to_bucket() -> None:
    dt = datetime(2026, 11, 11, 7, 45, 30, tzinfo=timezone.utc)
    assert floor_to_bucket(dt, 6) == datetime(2026, 11, 11, 6, 0, tzinfo=timezone.utc)
    assert floor_to_bucket(
        datetime(2026, 11, 11, 0, 10, tzinfo=timezone.utc), 6
    ) == datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError):
        floor_to_bucket(dt, 5)

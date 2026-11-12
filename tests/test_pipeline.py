from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from repo_scout.config import Config
from repo_scout.db import Database
from repo_scout.http import HttpResponse
from repo_scout.pipeline import IngestPipeline
from tests.support import FakeTransport, fixture, json_response, repo_payload

FIXTURES = {
    "daily": fixture("trending_daily.html"),
    "weekly": fixture("trending_weekly.html"),
    "monthly": fixture("trending_monthly.html"),
}

METADATA = {
    "acme/notes-ai": repo_payload(
        "acme/notes-ai", 101, topics=["ai", "notes", "productivity"]
    ),
    "solo/tiny-tool": repo_payload(
        "solo/tiny-tool",
        102,
        stars=600,
        language=None,
        license_spdx=None,
        description=None,
        topics=[],
    ),
    "weekly/weekly-tool": repo_payload(
        "weekly/weekly-tool", 103, stars=2300, language="Go"
    ),
    "monthly/monthly-tool": repo_payload(
        "monthly/monthly-tool", 104, stars=5400, language="JavaScript"
    ),
}

NOW = datetime(2026, 11, 11, 7, 45, tzinfo=timezone.utc)


def make_handler(html_by_period=None, metadata=None):
    html_by_period = html_by_period or FIXTURES
    metadata = metadata or METADATA

    def handler(url: str, headers: dict[str, str]) -> HttpResponse:
        if "github.com/trending" in url:
            period = parse_qs(urlparse(url).query).get("since", ["daily"])[0]
            html = html_by_period.get(period)
            if html is None:
                return HttpResponse(status=404, text="not found", headers={})
            return HttpResponse(status=200, text=html, headers={})

        prefix = "https://api.github.com/repos/"
        if url.startswith(prefix):
            full_name = url[len(prefix) :].split("?")[0]
            payload = metadata.get(full_name)
            if payload is not None:
                return json_response(payload)
            return json_response({"message": "Not Found"}, status=404)

        return json_response({}, status=404)

    return handler


def make_pipeline(tmp_path, transport: FakeTransport, **config_overrides):
    db = Database(tmp_path / "db.sqlite3")
    db.init_schema()
    values = {
        "db_path": tmp_path / "db.sqlite3",
        "min_trending_items": 1,
        "enrich_limit": 50,
        "snapshot_limit": 50,
        "fetch_star_history": False,
    }
    values.update(config_overrides)
    config = Config(**values)
    pipeline = IngestPipeline(
        db,
        transport,
        config,
        now_fn=lambda: NOW,
        sleep=lambda _: None,
    )
    return db, pipeline


def history_payload(count: int = 30, total: int = 10) -> list[dict]:
    newest_week = 1794096000  # 2026-11-08, Sunday UTC
    return [
        {
            "week": newest_week - index * 7 * 86400,
            "total": total,
            "days": [total, 0, 0, 0, 0, 0, 0],
        }
        for index in range(count)
    ]


def test_full_ingest_run(tmp_path) -> None:
    transport = FakeTransport(make_handler())
    db, pipeline = make_pipeline(tmp_path, transport)

    summary = pipeline.run()

    assert summary.status == "ok"
    assert summary.periods == {"daily": 4, "weekly": 2, "monthly": 1}
    assert summary.fetched == 7
    assert summary.new_repos == 6
    assert summary.enriched == 4
    assert summary.excluded_total == 2
    assert summary.snapshots == 4
    assert summary.metrics_scored == 4
    assert summary.metrics_risky == 0
    assert summary.requests == 7
    assert summary.errors == []

    notes = db.get_repo_by_full_name("acme/notes-ai")
    assert notes["repo_id"] == 101
    assert notes["license_spdx"] == "MIT"
    assert json.loads(notes["topics"]) == ["ai", "notes", "productivity"]
    assert notes["stars_total"] == 8200
    assert notes["category"] == "normal"

    awesome = db.get_repo_by_full_name("awesome/awesome-ai")
    assert awesome["category"] == "awesome"
    assert awesome["excluded"] == 1

    tutorial = db.get_repo_by_full_name("lin/tutorial-hub")
    assert tutorial["category"] == "tutorial"
    assert tutorial["excluded"] == 1

    assert db.count_repos() == 6
    assert db.count_trending_hits() == 7
    assert db.count_snapshots() == 4
    assert db.count_repo_metrics() == 4

    run = db.latest_run()
    assert run is not None
    assert run["status"] == "ok"
    assert json.loads(run["details"])["fetched"] == 7


def test_second_run_is_idempotent(tmp_path) -> None:
    transport = FakeTransport(make_handler())
    db, pipeline = make_pipeline(tmp_path, transport)

    pipeline.run()
    second = pipeline.run()

    assert second.status == "ok"
    assert second.new_repos == 0
    assert second.updated_repos == 7
    assert second.enriched == 0
    assert db.count_repos() == 6
    assert db.count_trending_hits() == 7
    assert db.count_snapshots() == 4
    assert db.count_repo_metrics() == 4


def test_parse_failure_marks_partial_and_keeps_existing_data(tmp_path) -> None:
    transport = FakeTransport(make_handler())
    db, pipeline = make_pipeline(tmp_path, transport)

    assert pipeline.run().status == "ok"

    transport.handler = make_handler(
        {**FIXTURES, "daily": fixture("trending_garbled.html")}
    )
    summary = pipeline.run()

    assert summary.status == "partial"
    assert summary.periods == {"weekly": 2, "monthly": 1}
    assert any(error.startswith("daily:") for error in summary.errors)
    assert db.count_repos() == 6  # 既有数据未被清空


def test_all_periods_failing_marks_failed(tmp_path) -> None:
    transport = FakeTransport(
        make_handler({period: fixture("trending_garbled.html") for period in FIXTURES})
    )
    db, pipeline = make_pipeline(tmp_path, transport)

    summary = pipeline.run()

    assert summary.status == "failed"
    assert summary.fetched == 0
    assert len(summary.errors) == 3
    assert db.latest_run()["status"] == "failed"


def test_no_enrich_option_only_collects_trending(tmp_path) -> None:
    transport = FakeTransport(make_handler())
    db, pipeline = make_pipeline(tmp_path, transport)

    summary = pipeline.run(enrich=False)

    assert summary.status == "ok"
    assert summary.enriched == 0
    assert summary.requests == 3
    row = db.get_repo_by_full_name("acme/notes-ai")
    assert row["repo_id"] is None
    assert row["license_spdx"] is None


def test_add_repo(tmp_path) -> None:
    transport = FakeTransport(make_handler())
    db, pipeline = make_pipeline(tmp_path, transport)

    assert pipeline.add_repo("acme/notes-ai") is True
    assert db.count_repos() == 1
    assert db.count_snapshots() == 1
    assert db.get_repo_by_full_name("acme/notes-ai")["repo_id"] == 101
    assert db.latest_run()["run_type"] == "add"

    assert pipeline.add_repo("missing/repo") is False
    assert db.latest_run()["status"] == "failed"


def test_star_history_backfill_and_idempotency(tmp_path) -> None:
    def handler(url: str, headers: dict[str, str]) -> HttpResponse:
        if "github.com/trending" in url:
            period = parse_qs(urlparse(url).query).get("since", ["daily"])[0]
            return HttpResponse(status=200, text=FIXTURES[period], headers={})
        if "/stargazers/history" in url:
            page = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
            return json_response(history_payload(30) if page == 1 else [])
        if url.startswith("https://api.github.com/repos/"):
            full_name = url[len("https://api.github.com/repos/") :].split("?")[0]
            payload = METADATA.get(full_name)
            if payload is not None:
                return json_response(payload)
            return json_response({"message": "Not Found"}, status=404)
        return json_response({}, status=404)

    transport = FakeTransport(handler)
    db, pipeline = make_pipeline(
        tmp_path,
        transport,
        fetch_star_history=True,
        star_history_limit=10,
        star_history_weeks=30,
    )

    summary = pipeline.run()

    assert summary.status == "ok"
    assert summary.star_history_repos == 4
    assert summary.star_history_weeks == 120
    assert db.count_star_weekly() == 120

    notes = db.get_repo_by_full_name("acme/notes-ai")
    rows = db.get_star_weekly(notes["id"])
    assert len(rows) == 30
    assert rows[0]["stars_end"] == notes["stars_total"]
    assert rows[1]["stars_end"] == notes["stars_total"] - 10
    assert rows[0]["delta"] == 10
    assert notes["star_history_fetched_at"] == "2026-11-11T07:45:00Z"

    second = pipeline.run()

    assert second.status == "ok"
    assert second.star_history_repos == 0
    assert db.count_star_weekly() == 120


def test_star_history_rate_limit_marks_partial(tmp_path) -> None:
    def handler(url: str, headers: dict[str, str]) -> HttpResponse:
        if "github.com/trending" in url:
            period = parse_qs(urlparse(url).query).get("since", ["daily"])[0]
            return HttpResponse(status=200, text=FIXTURES[period], headers={})
        if "/stargazers/history" in url:
            return json_response({"message": "rate limited"}, status=403)
        if url.startswith("https://api.github.com/repos/"):
            full_name = url[len("https://api.github.com/repos/") :].split("?")[0]
            return json_response(METADATA.get(full_name) or {"message": "Not Found"}, status=200 if full_name in METADATA else 404)
        return json_response({}, status=404)

    transport = FakeTransport(handler)
    db, pipeline = make_pipeline(
        tmp_path, transport, fetch_star_history=True, star_history_limit=10
    )

    summary = pipeline.run()

    assert summary.status == "partial"
    assert any(error.startswith("star_history:") for error in summary.errors)
    assert db.count_star_weekly() == 0

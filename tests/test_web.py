from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from repo_scout.db import Database
from repo_scout.models import RepoMetadata, TrendingEntry
from repo_scout.util import iso_z
from repo_scout.web import RepoScoutServer

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)


def seed_repo(
    db: Database,
    *,
    full_name: str,
    repo_id: int,
    stars: int,
    loc: int,
    repro: float,
    biz: float,
    license_spdx: str | None,
    license_risk: str,
    paid_version: str = "none",
    delta7: int = 800,
    trend: float = 70.0,
) -> int:
    entry = TrendingEntry(
        full_name=full_name,
        owner=full_name.split("/")[0],
        name=full_name.split("/")[1],
        html_url=f"https://github.com/{full_name}",
        period="daily",
        stars_total=stars,
    )
    repo_pk = db.upsert_trending_entry(entry, iso_z(NOW - timedelta(hours=1))).repo_pk
    meta = RepoMetadata(
        repo_id=repo_id,
        full_name=full_name,
        owner=full_name.split("/")[0],
        name=full_name.split("/")[1],
        html_url=f"https://github.com/{full_name}",
        description=f"{full_name} description",
        homepage=None,
        language="Python",
        license_spdx=license_spdx,
        topics=["saas", "seo"],
        stars_total=stars,
        forks=10,
        open_issues=5,
        subscribers=2,
        size_kb=100,
        default_branch="main",
        archived=False,
        fork=False,
        disabled=False,
        created_at="2026-01-01T00:00:00Z",
        pushed_at="2026-11-10T00:00:00Z",
    )
    db.apply_metadata(repo_pk, meta, iso_z(NOW - timedelta(hours=1)))
    db.upsert_repo_metrics(
        [
            {
                "repo_pk": repo_pk,
            "computed_at": iso_z(NOW - timedelta(hours=1)),
            "stars_total": stars,
            "delta_24h": 100,
            "delta_7d": delta7,
            "delta_30d": 2400,
            "velocity_7d": delta7 / 7,
            "velocity_prev": 100.0,
            "acceleration": 0.1,
            "acceleration_available": 1,
            "anomaly": 0,
            "burst": 0,
            "anomaly_available": 1,
            "trend_score": trend,
            "stars_per_contributor": 1000.0,
            "suspicious_stars": 0,
            "history_confidence": "high",
            "sources": "{}",
            "updated_at": iso_z(NOW - timedelta(hours=1)),
            }
        ]
    )
    db.upsert_repo_analysis(
        {
            "repo_pk": repo_pk,
            "analyzed_at": iso_z(NOW - timedelta(hours=2)),
            "analysis_version": "m3-light-1",
            "loc": loc,
            "loc_method": "cloc",
            "loc_confidence": "high",
            "dep_count": 5,
            "dev_dep_count": 2,
            "dep_manifest": "pyproject.toml",
            "dep_confidence": "high",
            "ext_services": "[]",
            "ext_evidence": "[]",
            "has_docker": 1,
            "has_compose": 0,
            "compose_services": None,
            "has_monorepo": 0,
            "ci_workflows": 1,
            "src_files": 5,
            "top_dirs": 3,
            "tree_truncated": 0,
            "license_risk": license_risk,
            "s_loc": 85,
            "s_dep": 100,
            "s_ext": 100,
            "s_lang": 100,
            "s_license": 100,
            "s_struct": 100,
            "repro_score": repro,
            "complexity_score": 10,
            "complexity_grade": "A",
            "replication_weeks_min": 1.0,
            "replication_weeks_max": 1.5,
            "low_confidence_fields": "[]",
            "score_breakdown": "{}",
            "updated_at": iso_z(NOW - timedelta(hours=2)),
        }
    )
    db.upsert_repo_commercial(
        {
            "repo_pk": repo_pk,
            "analyzed_at": iso_z(NOW - timedelta(hours=3)),
            "commercial_version": "m4-1",
            "paid_intent_issues": "[]",
            "paid_intent_scanned": 10,
            "paid_intent_hits": 2,
            "paid_intent_score": 70,
            "paid_intent_evidence": "[]",
            "paid_version": paid_version,
            "paid_evidence": "[]",
            "competitor_count": 6,
            "competitor_query": "topic:saas",
            "competitor_checked_at": iso_z(NOW - timedelta(hours=3)),
            "audience_score": 100,
            "audience_topic": "saas",
            "monetization_paths": json.dumps(["托管 SaaS"], ensure_ascii=False),
            "gap_count": 2,
            "differentiation_score": 70,
            "market_alignment": 75.0,
            "whitespace": 60.0,
            "competition": 65.0,
            "biz_score": biz,
            "low_confidence_fields": "[]",
            "score_breakdown": "{}",
            "updated_at": iso_z(NOW - timedelta(hours=3)),
        }
    )
    db.upsert_star_weekly(
        repo_pk,
        [
            {"week_start": "2026-11-01", "delta": 10, "stars_end": stars - 20},
            {"week_start": "2026-11-08", "delta": 20, "stars_end": stars},
        ],
        iso_z(NOW - timedelta(hours=1)),
    )
    for index, offset in enumerate((3, 2, 1)):
        db.take_snapshot(
            repo_pk,
            iso_z(NOW - timedelta(days=offset)),
            stars - (2 - index) * 5,
        )
    return repo_pk


@pytest.fixture()
def server(tmp_path):
    db_path = tmp_path / "web.sqlite3"
    db = Database(db_path)
    db.init_schema()
    visible = seed_repo(
        db,
        full_name="acme/rocket",
        repo_id=101,
        stars=10000,
        loc=8000,
        repro=90,
        biz=70,
        license_spdx="MIT",
        license_risk="green",
    )
    seed_repo(
        db,
        full_name="acme/gpl",
        repo_id=102,
        stars=20000,
        loc=8000,
        repro=70,
        biz=80,
        license_spdx="GPL-3.0",
        license_risk="red",
    )
    seed_repo(
        db,
        full_name="acme/giant",
        repo_id=103,
        stars=150000,
        loc=100000,
        repro=60,
        biz=60,
        license_spdx="MIT",
        license_risk="green",
    )
    seed_repo(
        db,
        full_name="acme/tiny",
        repo_id=104,
        stars=100,
        loc=8000,
        repro=60,
        biz=60,
        license_spdx="MIT",
        license_risk="green",
    )
    db.log_run(
        run_at=iso_z(NOW - timedelta(hours=1)),
        run_type="ingest",
        status="ok",
        requests=76,
        items=39,
    )
    db.close()

    httpd = RepoScoutServer(("127.0.0.1", 0), db_path)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}", visible
    httpd.jobs.close()
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def request(base: str, path: str, method: str = "GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def test_list_default_gates_and_toggles(server) -> None:
    base, _ = server
    status, body, _ = request(base, "/api/repos")
    payload = json.loads(body)
    assert status == 200
    assert payload["total"] == 1
    assert payload["items"][0]["full_name"] == "acme/rocket"
    assert payload["meta"]["gates"]["g3"]["count"] == 1
    assert payload["meta"]["gates"]["g5"]["count"] == 1
    assert payload["meta"]["gates"]["g6"]["count"] == 1
    assert payload["meta"]["candidate_pool"] == 4
    assert payload["meta"]["snapshot_at"] is not None

    status, body, _ = request(
        base, "/api/repos?include_gpl=1&include_giant=1&min_stars=0"
    )
    payload = json.loads(body)
    assert status == 200
    assert payload["total"] == 4


def test_detail_watch_feedback(server) -> None:
    base, repo_pk = server
    status, body, _ = request(base, f"/api/repos/{repo_pk}")
    payload = json.loads(body)
    assert status == 200
    assert payload["repo"]["full_name"] == "acme/rocket"
    assert len(payload["star_weeks"]) == 2
    assert len(payload["daily"]) == 2
    assert "breakdown" in payload["analysis"]
    assert "breakdown" in payload["commercial"]
    assert payload["repo"]["watch"]["watched"] is False

    status, body, _ = request(
        base,
        "/api/watch",
        method="POST",
        body={
            "repo_pk": repo_pk,
            "action": "add",
            "status": "investigating",
            "note": "看一下",
        },
    )
    assert status == 200
    assert json.loads(body)["watch"]["watched"] is True

    status, body, _ = request(base, f"/api/repos/{repo_pk}")
    assert json.loads(body)["repo"]["watch"]["status"] == "investigating"

    status, body, _ = request(
        base,
        "/api/feedback",
        method="POST",
        body={"repo_pk": repo_pk, "verdict": "useful"},
    )
    assert status == 200

    status, body, _ = request(
        base, "/api/watch", method="POST", body={"repo_pk": repo_pk, "action": "remove"}
    )
    assert status == 200
    assert json.loads(body)["watch"]["watched"] is False


def test_export_daily_and_static(server) -> None:
    base, _ = server
    status, body, headers = request(base, "/api/export.csv")
    assert status == 200
    assert body.startswith("﻿".encode("utf-8"))
    assert "acme/rocket" in body.decode("utf-8")
    assert headers["Content-Type"].startswith("text/csv")

    status, body, _ = request(base, "/api/daily")
    payload = json.loads(body)
    assert status == 200
    assert payload["items"][0]["full_name"] == "acme/rocket"

    status, body, headers = request(base, "/")
    assert status == 200
    assert "RepoScout" in body.decode("utf-8")
    assert headers["Content-Type"].startswith("text/html")

    status, body, _ = request(base, "/does-not-exist")
    assert status == 404


def test_invalid_post(server) -> None:
    base, repo_pk = server
    status, _, _ = request(
        base,
        "/api/watch",
        method="POST",
        body={"repo_pk": repo_pk, "status": "bad"},
    )
    assert status == 400


def test_pipeline_and_job_run(server) -> None:
    base, _ = server
    status, body, _ = request(base, "/api/pipeline")
    payload = json.loads(body)
    assert status == 200
    assert len(payload["stages"]) == 5
    assert payload["config"]["token_configured"] is False
    assert payload["stages"][0]["key"] == "ingest"

    status, body, _ = request(
        base, "/api/jobs", method="POST", body={"action": "metrics"}
    )
    assert status == 202
    job = json.loads(body)["job"]
    assert job["status"] == "queued"

    deadline = time.time() + 8
    final = None
    while time.time() < deadline:
        status, body, _ = request(base, f"/api/jobs/{job['id']}")
        assert status == 200
        final = json.loads(body)
        if final["status"] in {"success", "partial", "failed"}:
            break
        time.sleep(0.05)

    assert final is not None
    assert final["status"] == "success"
    assert final["progress"] == 1.0
    assert final["result"]["scored"] >= 1

    status, body, _ = request(base, "/api/jobs")
    jobs = json.loads(body)
    assert status == 200
    assert jobs["jobs"][0]["id"] == job["id"]

    status, body, _ = request(base, "/api/jobs/99999")
    assert status == 404
    status, _, _ = request(
        base, "/api/jobs", method="POST", body={"action": "unknown"}
    )
    assert status == 400


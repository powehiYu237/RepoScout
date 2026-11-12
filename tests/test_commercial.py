from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from repo_scout.commercial import (
    audience_from_topics,
    business_score,
    competition_query,
    competition_score,
    detect_paid_version,
    differentiation_gaps,
    load_commercial_output,
    run_commercial_analysis,
    scan_paid_intent,
    sort_commercial,
)
from repo_scout.config import Config
from repo_scout.db import Database
from repo_scout.github import GitHubClient
from repo_scout.http import CountingTransport
from repo_scout.models import RepoMetadata, TrendingEntry
from repo_scout.util import iso_z
from tests.support import FakeTransport, json_response

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
README_TEXT = """# Rocket

Pricing: Pro plan $9. Book a demo. Join the waitlist.
Hosted SaaS. Sponsors welcome.
"""
ISSUES = [
    {
        "number": 1,
        "title": "Would pay for this",
        "body": "I need a paid plan please",
        "html_url": "https://github.com/acme/rocket/issues/1",
        "reactions": {"total_count": 5},
    },
    {
        "number": 2,
        "title": "愿意付费",
        "body": "",
        "html_url": "https://github.com/acme/rocket/issues/2",
        "reactions": {"total_count": 2},
    },
    {
        "number": 3,
        "title": "pricing?",
        "body": "take my money",
        "html_url": "https://github.com/acme/rocket/issues/3",
        "reactions": {"total_count": 1},
    },
    {
        "number": 4,
        "title": "bug",
        "body": "crash",
        "html_url": "https://github.com/acme/rocket/issues/4",
        "reactions": {"total_count": 0},
    },
]


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "commercial.sqlite3")
    db.init_schema()
    return db


def seed_repo(db: Database) -> int:
    full_name = "acme/rocket"
    entry = TrendingEntry(
        full_name=full_name,
        owner="acme",
        name="rocket",
        html_url=f"https://github.com/{full_name}",
        period="daily",
        stars_total=10000,
    )
    repo_pk = db.upsert_trending_entry(entry, iso_z(NOW - timedelta(hours=1))).repo_pk
    meta = RepoMetadata(
        repo_id=101,
        full_name=full_name,
        owner="acme",
        name="rocket",
        html_url=f"https://github.com/{full_name}",
        description="CLI for SEO automation",
        homepage="https://rocket.example.com",
        language="Python",
        license_spdx="MIT",
        topics=["saas", "chrome-extension", "cli", "seo"],
        stars_total=10000,
        forks=100,
        open_issues=10,
        subscribers=10,
        size_kb=100,
        default_branch="main",
        archived=False,
        fork=False,
        disabled=False,
        created_at="2026-01-01T00:00:00Z",
        pushed_at="2026-11-10T00:00:00Z",
    )
    db.apply_metadata(repo_pk, meta, iso_z(NOW - timedelta(hours=1)))
    return repo_pk


def seed_analysis(db: Database, repo_pk: int, *, repro: float = 80.0) -> None:
    db.upsert_repo_analysis(
        {
            "repo_pk": repo_pk,
            "analyzed_at": iso_z(NOW - timedelta(hours=2)),
            "analysis_version": "m3-light-1",
            "loc": 8000,
            "loc_method": "api_estimate",
            "loc_confidence": "medium",
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
            "license_risk": "green",
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
            "score_breakdown": json.dumps({"hard_language_unmatched": False}),
            "updated_at": iso_z(NOW - timedelta(hours=2)),
        }
    )


def make_config(tmp_path, **overrides) -> Config:
    values = {
        "db_path": tmp_path / "commercial.sqlite3",
        "biz_limit": 100,
        "biz_refresh_hours": 168,
        "biz_issue_limit": 100,
        "biz_competition": True,
        "biz_competition_daily_budget": 50,
        "biz_competition_min_repro": 60,
        "biz_search_delay_seconds": 0,
    }
    values.update(overrides)
    return Config(**values)


def make_handler(search_calls: list[str] | None = None):
    search_calls = search_calls if search_calls is not None else []

    def handler(url: str, headers: dict[str, str]):
        if url.endswith("/readme"):
            return json_response(
                {
                    "encoding": "base64",
                    "content": base64.b64encode(README_TEXT.encode()).decode(),
                }
            )
        if "/contents/.github/FUNDING.yml" in url:
            return json_response(
                {
                    "encoding": "base64",
                    "content": base64.b64encode(b"github: sponsor\n").decode(),
                }
            )
        if "/issues?" in url:
            return json_response(ISSUES)
        if "/search/repositories" in url:
            search_calls.append(url)
            return json_response({"total_count": 7, "items": []})
        return json_response({"message": "Not Found"}, status=404)

    return handler


def test_scan_paid_intent() -> None:
    result = scan_paid_intent(ISSUES)
    assert result.scanned == 4
    assert result.hit_count == 3
    assert result.reactions == 8
    assert len(result.examples) == 3
    assert result.score() == 85
    assert scan_paid_intent(None).score() == 40


def test_detect_paid_version_confidence() -> None:
    assert detect_paid_version(
        readme="A command line tool", homepage=None, funding=None, topics=[]
    ).confidence == "none"
    assert detect_paid_version(
        readme="A command line tool",
        homepage="https://demo.example.com",
        funding=None,
        topics=[],
    ).confidence == "none"
    assert detect_paid_version(
        readme="Sponsor us on GitHub", homepage=None, funding=None, topics=[]
    ).confidence == "possible"
    assert detect_paid_version(
        readme="", homepage=None, funding="github: sponsor", topics=[]
    ).confidence == "possible"
    likely = detect_paid_version(
        readme="Pricing\nPro plan $9", homepage=None, funding=None, topics=[]
    )
    assert likely.confidence == "likely"
    assert likely.score() == 20


def test_audience_and_differentiation() -> None:
    assert audience_from_topics(["b2b", "ai"]) == (100, "b2b")
    assert audience_from_topics([]) == (50, None)
    assert audience_from_topics(["unknown"]) == (50, None)

    gaps, _ = differentiation_gaps(
        readme="A CLI tool",
        description="command line utility",
        topics=["cli", "seo"],
        has_docker=False,
        has_compose=False,
        paid_version="none",
        homepage=None,
    )
    assert "GUI / 桌面端" in gaps
    assert "中文本地化 / 国内合规部署" in gaps
    assert "移动端 / 小程序" in gaps
    assert "API 服务化" in gaps

    gaps, _ = differentiation_gaps(
        readme="Hosted product",
        description="",
        topics=[],
        has_docker=False,
        has_compose=False,
        paid_version="possible",
        homepage="https://x.example.com",
    )
    assert "私有化部署（B2B）" in gaps


def test_competition_query_and_score() -> None:
    query = competition_query(
        topics=["saas", "b2b"],
        description="SEO automation",
        language="Python",
        audience_topic="saas",
    )
    assert query == (
        "topic:saas language:Python stars:>100 fork:false "
        "-topic:awesome -topic:awesome-list"
    )
    keyword_query = competition_query(
        topics=[], description="seo automation tool", language=None, audience_topic=None
    )
    assert keyword_query.startswith("seo automation tool in:name,description")
    assert competition_score(0) == 100.0
    assert competition_score(6) == 65.0
    assert competition_score(25) == 20.0
    assert competition_score(None) == 50.0


def test_business_score_weights() -> None:
    assert business_score(
        market_alignment=100,
        whitespace=100,
        competition=100,
        audience=100,
        differentiation=100,
    ) == 100.0
    assert business_score(
        market_alignment=50,
        whitespace=50,
        competition=50,
        audience=50,
        differentiation=50,
    ) == 50.0


def test_run_commercial_analysis_end_to_end(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = seed_repo(db)
    seed_analysis(db, repo_pk)
    search_calls: list[str] = []
    transport = CountingTransport(FakeTransport(make_handler(search_calls)))
    client = GitHubClient(transport, retries=0)

    summary = run_commercial_analysis(
        db,
        client,
        make_config(tmp_path),
        force=True,
        now=NOW,
        sleep=lambda _: None,
    )

    assert summary.analyzed == 1
    assert summary.failed == 0
    assert summary.requests == 4
    assert summary.competition_used == 1
    assert len(search_calls) == 1

    stored = db.get_repo_commercial(repo_pk)
    assert stored is not None
    assert stored["paid_intent_hits"] == 3
    assert stored["paid_intent_score"] == 85
    assert stored["paid_version"] == "likely"
    assert stored["competitor_count"] == 6
    assert stored["audience_score"] == 100
    assert stored["audience_topic"] == "saas"
    assert stored["market_alignment"] == pytest.approx(67.5)
    assert stored["biz_score"] == pytest.approx(63.25)
    assert json.loads(stored["monetization_paths"])

    output = load_commercial_output(db)
    assert len(output) == 1
    assert output[0]["full_name"] == "acme/rocket"
    assert output[0]["paid_intent_issues"] == 3
    assert sort_commercial(output, "biz")[0]["biz_score"] == pytest.approx(63.25)

    again = run_commercial_analysis(
        db,
        client,
        make_config(tmp_path),
        force=False,
        now=NOW,
        sleep=lambda _: None,
    )
    assert again.analyzed == 0


def test_run_commercial_analysis_respects_search_budget(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = seed_repo(db)
    seed_analysis(db, repo_pk)
    search_calls: list[str] = []
    transport = CountingTransport(FakeTransport(make_handler(search_calls)))
    client = GitHubClient(transport, retries=0)

    summary = run_commercial_analysis(
        db,
        client,
        make_config(tmp_path, biz_competition_daily_budget=0),
        force=True,
        now=NOW,
        sleep=lambda _: None,
    )

    assert summary.analyzed == 1
    assert summary.requests == 3
    assert search_calls == []
    stored = db.get_repo_commercial(repo_pk)
    assert stored["competitor_count"] is None
    assert json.loads(stored["low_confidence_fields"]) == ["competition"]
    assert stored["competition"] == 50.0

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from repo_scout.analysis import (
    analyze_repo,
    complexity_grade,
    complexity_score,
    compose_service_count,
    dependency_info,
    dependency_score,
    external_service_score,
    find_external_services,
    language_match_score,
    license_score,
    load_analysis_results,
    loc_from_languages,
    loc_score,
    replication_weeks,
    repro_score,
    run_analysis,
    structure_from_tree,
    structure_score,
)
from repo_scout.db import Database
from repo_scout.github import GitHubClient
from repo_scout.http import CountingTransport
from repo_scout.models import RepoMetadata, TrendingEntry
from repo_scout.util import iso_z
from tests.support import FakeTransport, json_response

NOW = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "analysis.sqlite3")
    db.init_schema()
    return db


def seed_repo(
    db: Database,
    full_name: str = "acme/rocket",
    *,
    repo_id: int = 101,
    stars: int = 10000,
    language: str | None = "TypeScript",
    license_spdx: str | None = "MIT",
    default_branch: str = "main",
) -> int:
    entry = TrendingEntry(
        full_name=full_name,
        owner=full_name.split("/")[0],
        name=full_name.split("/")[1],
        html_url=f"https://github.com/{full_name}",
        period="daily",
        stars_total=stars,
    )
    repo_pk = db.upsert_trending_entry(
        entry, iso_z(NOW - timedelta(hours=1))
    ).repo_pk
    meta = RepoMetadata(
        repo_id=repo_id,
        full_name=full_name,
        owner=full_name.split("/")[0],
        name=full_name.split("/")[1],
        html_url=f"https://github.com/{full_name}",
        description=None,
        homepage=None,
        language=language,
        license_spdx=license_spdx,
        topics=[],
        stars_total=stars,
        forks=0,
        open_issues=0,
        subscribers=0,
        size_kb=100,
        default_branch=default_branch,
        archived=False,
        fork=False,
        disabled=False,
        created_at=None,
        pushed_at=None,
    )
    db.apply_metadata(repo_pk, meta, iso_z(NOW - timedelta(hours=1)))
    return repo_pk


# -- 依赖解析 ---------------------------------------------------------------
def test_dependency_info_package_json_and_priority() -> None:
    manifests = {
        "package.json": json.dumps(
            {
                "dependencies": {"react": "19", "openai": "4"},
                "devDependencies": {"vitest": "1"},
                "workspaces": ["packages/*"],
            }
        ),
        "pyproject.toml": '[project]\ndependencies = ["httpx"]\n',
    }
    info = dependency_info(manifests)
    assert info.count == 2
    assert info.dev_count == 1
    assert info.manifest == "package.json"
    assert info.confidence == "high"
    assert info.has_workspaces is True


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        (
            "pyproject.toml",
            '[project]\ndependencies = ["httpx>=0.27", "rich"]\n'
            '[project.optional-dependencies]\ndev = ["pytest>=8", "ruff"]\n',
            (2, 2),
        ),
        (
            "pyproject.toml",
            '[tool.poetry.dependencies]\npython = "^3.11"\nrequests = "^2"\n'
            '[tool.poetry.group.dev.dependencies]\npytest = "^8"\n',
            (1, 1),
        ),
        (
            "requirements.txt",
            "# comment\n-r base.txt\nhttpx>=0.27\nrich\n--index-url https://x\norjson\n",
            (3, 0),
        ),
        (
            "go.mod",
            "module x\nrequire (\n  github.com/a/b v1.0\n  github.com/c/d v2.0\n)\n"
            "require github.com/e/f v3.0\n",
            (3, 0),
        ),
        (
            "Cargo.toml",
            '[dependencies]\nserde = "1"\n[dev-dependencies]\ntempfile = "3"\n'
            '[build-dependencies]\ncc = "1"\n',
            (2, 1),
        ),
        (
            "composer.json",
            json.dumps(
                {
                    "require": {"php": ">=8.2", "laravel/framework": "11"},
                    "require-dev": {"phpunit/phpunit": "11"},
                }
            ),
            (1, 1),
        ),
        (
            "Gemfile",
            "source 'https://rubygems.org'\ngem 'rails', '~> 7'\ngem 'puma'\n",
            (2, 0),
        ),
        (
            "pom.xml",
            "<project><dependencies>"
            "<dependency><artifactId>a</artifactId></dependency>"
            "<dependency><artifactId>b</artifactId></dependency>"
            "</dependencies></project>",
            (2, 0),
        ),
    ],
)
def test_manifest_dependency_counts(name: str, text: str, expected) -> None:
    info = dependency_info({name: text})
    assert (info.count, info.dev_count) == expected


def test_dependency_info_missing_or_invalid() -> None:
    assert dependency_info({}).count is None
    info = dependency_info({"package.json": "{invalid"})
    assert info.count is None
    assert info.confidence == "low"


# -- 工程结构 ---------------------------------------------------------------
TREE = {
    "truncated": False,
    "tree": [
        {"path": "src", "type": "tree"},
        {"path": "src/main.ts", "type": "blob"},
        {"path": "src/app.ts", "type": "blob"},
        {"path": ".github", "type": "tree"},
        {"path": ".github/workflows", "type": "tree"},
        {"path": ".github/workflows/ci.yml", "type": "blob"},
        {"path": "packages", "type": "tree"},
        {"path": "package.json", "type": "blob"},
        {"path": "README.md", "type": "blob"},
        {"path": "Dockerfile", "type": "blob"},
        {"path": "docker-compose.yml", "type": "blob"},
    ],
}


def test_structure_from_tree() -> None:
    structure = structure_from_tree(TREE)
    assert structure is not None
    assert structure.src_files == 2
    assert structure.top_dirs == 3
    assert structure.ci_workflows == 1
    assert structure.has_monorepo is True
    assert structure.has_docker is True
    assert structure.has_compose is True


def test_structure_score_rules() -> None:
    structure = structure_from_tree(TREE)
    assert structure_score(structure, compose_services=2) == 70.0
    assert structure_score(structure, compose_services=5) == 60.0
    assert structure_score(None, None) == 60.0


def test_compose_service_count() -> None:
    text = "services:\n  web:\n    image: nginx\n  db:\n    image: postgres\n"
    assert compose_service_count(text) == 2
    assert compose_service_count("version: '3'\n") is None


# -- 外部服务 ---------------------------------------------------------------
def test_external_services_keyword_match() -> None:
    readme = (
        "We use OpenAI for inference.\n"
        "Deployed on Supabase.\n"
        "The laws of physics still apply.\n"
    )
    manifests = {"package.json": '{"dependencies": {"openai": "4"}}'}
    external = find_external_services(readme, manifests)

    assert set(external.services) == {"OpenAI", "Supabase"}
    assert external.known is True
    assert any(item["service"] == "OpenAI" for item in external.evidence)


def test_external_services_unknown_when_nothing_fetched() -> None:
    external = find_external_services(None, {})
    assert external.known is False
    assert external.services == ()


# -- 评分 -------------------------------------------------------------------
def test_loc_estimate_and_score() -> None:
    assert loc_from_languages({"TypeScript": 350_000, "JavaScript": 35_000}) == 11_000
    assert loc_from_languages({}) is None
    assert [loc_score(value) for value in (1000, 7000, 15000, 30000, 70000, 200000, None)] == [
        100.0,
        85.0,
        75.0,
        55.0,
        25.0,
        5.0,
        60.0,
    ]


def test_dependency_and_external_scores() -> None:
    assert [dependency_score(value) for value in (20, 30, 80, 200, None)] == [
        100.0,
        75.0,
        50.0,
        20.0,
        60.0,
    ]
    assert [
        external_service_score(value) for value in (0, 1, 2, 3, 4, 5, None)
    ] == [100.0, 80.0, 80.0, 55.0, 55.0, 25.0, 60.0]


def test_language_match() -> None:
    assert language_match_score("TypeScript", ["typescript"]) == (100.0, False, True)
    assert language_match_score("TypeScript", ["javascript"]) == (80.0, False, True)
    assert language_match_score("Python", ["typescript"]) == (55.0, False, True)
    assert language_match_score("Rust", ["typescript"]) == (20.0, True, True)
    assert language_match_score(None, ["typescript"]) == (50.0, False, False)


def test_license_risk_table() -> None:
    assert license_score("MIT") == (100.0, "green")
    assert license_score("Apache-2.0") == (100.0, "green")
    assert license_score("MPL-2.0") == (55.0, "yellow")
    assert license_score("LGPL-3.0-only") == (55.0, "yellow")
    assert license_score("AGPL-3.0") == (0.0, "red")
    assert license_score("GPL-3.0-or-later") == (0.0, "red")
    assert license_score(None) == (20.0, "gray")
    assert license_score("Custom") == (50.0, "unknown")


def test_repro_score_and_hard_language_cap() -> None:
    score = repro_score(
        s_loc=75,
        s_dep=100,
        s_ext=80,
        s_lang=100,
        s_license=100,
        s_struct=70,
        hard_language_unmatched=False,
    )
    assert score == pytest.approx(88.75)

    capped = repro_score(
        s_loc=100,
        s_dep=100,
        s_ext=100,
        s_lang=20,
        s_license=100,
        s_struct=100,
        hard_language_unmatched=True,
    )
    assert capped == 40.0


def test_complexity_grade_and_replication_weeks() -> None:
    assert complexity_score(s_loc=75, s_dep=100, s_ext=80, s_struct=70) == 17.0
    assert [complexity_grade(value) for value in (10, 30, 45, 60, 75, 90)] == [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
    ]
    assert replication_weeks(
        loc=11_000,
        external_services=2,
        hard_language_unmatched=False,
        has_monorepo=True,
    ) == (2.5, 4.0)
    assert replication_weeks(
        loc=None,
        external_services=0,
        hard_language_unmatched=False,
        has_monorepo=False,
    ) == (None, None)


# -- 端到端浅分析 -----------------------------------------------------------
README_TEXT = "We use OpenAI for inference.\nDeployed on Supabase.\n"
PACKAGE_JSON = json.dumps(
    {
        "dependencies": {"react": "19", "openai": "4"},
        "devDependencies": {"vitest": "1"},
        "workspaces": ["packages/*"],
    }
)
COMPOSE_TEXT = "services:\n  web:\n    image: nginx\n  db:\n    image: postgres\n"


def make_handler():
    def handler(url: str, headers: dict[str, str]):
        if url.endswith("/languages"):
            return json_response({"TypeScript": 350_000, "JavaScript": 35_000})
        if "/git/trees/" in url:
            return json_response(TREE)
        if url.endswith("/readme"):
            return json_response(
                {
                    "encoding": "base64",
                    "content": base64.b64encode(README_TEXT.encode()).decode(),
                }
            )
        if "/contents/package.json" in url:
            return json_response(
                {
                    "encoding": "base64",
                    "content": base64.b64encode(PACKAGE_JSON.encode()).decode(),
                }
            )
        if "/contents/docker-compose.yml" in url:
            return json_response(
                {
                    "encoding": "base64",
                    "content": base64.b64encode(COMPOSE_TEXT.encode()).decode(),
                }
            )
        return json_response({"message": "Not Found"}, status=404)

    return handler


def test_analyze_repo_end_to_end() -> None:
    handler = make_handler()
    transport = CountingTransport(FakeTransport(handler))
    client = GitHubClient(transport, retries=0)
    repo = {
        "id": 1,
        "full_name": "acme/rocket",
        "default_branch": "main",
        "language": "TypeScript",
        "license_spdx": "MIT",
        "stars_total": 10000,
    }

    result = analyze_repo(client, repo, tech_stack=["typescript"], now=NOW)

    assert result.loc == 11_000
    assert result.dep_count == 2
    assert result.dev_dep_count == 1
    assert result.ext_services == ("OpenAI", "Supabase")
    assert result.has_monorepo is True
    assert result.compose_services == 2
    assert result.s_lang == 100.0
    assert result.repro_score == pytest.approx(88.75)
    assert result.complexity_score == 17.0
    assert result.complexity_grade == "A"
    assert result.replication_weeks_min == 2.5
    assert result.replication_weeks_max == 4.0
    assert result.low_confidence_fields == ()
    assert transport.count == 5


def test_run_analysis_persists_and_caches(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = seed_repo(db)
    transport = CountingTransport(FakeTransport(make_handler()))
    client = GitHubClient(transport, retries=0)

    summary = run_analysis(
        db,
        client,
        limit=10,
        force=True,
        tech_stack=["typescript"],
        now=NOW,
    )

    assert summary.analyzed == 1
    assert summary.failed == 0
    assert summary.requests == 5
    stored = db.get_repo_analysis(repo_pk)
    assert stored is not None
    assert stored["repro_score"] == pytest.approx(88.75)
    assert json.loads(stored["ext_services"]) == ["OpenAI", "Supabase"]
    assert db.count_repo_analysis() == 1
    assert db.status()["analysis"] == 1

    again = run_analysis(db, client, limit=10, force=False, now=NOW)
    assert again.analyzed == 0
    assert again.skipped == 1
    assert again.requests == 0

    loaded = load_analysis_results(db)
    assert len(loaded) == 1
    assert loaded[0].full_name == "acme/rocket"
    assert loaded[0].ext_services == ("OpenAI", "Supabase")
    assert loaded[0].repro_score == pytest.approx(88.75)


def test_run_analysis_rate_limit_stops(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_repo(db)

    def handler(url: str, headers: dict[str, str]):
        return json_response({"message": "rate limited"}, status=403)

    client = GitHubClient(CountingTransport(FakeTransport(handler)), retries=0)
    summary = run_analysis(db, client, limit=10, force=True, now=NOW)

    assert summary.analyzed == 0
    assert summary.failed == 1
    assert summary.errors
    assert db.count_repo_analysis() == 0

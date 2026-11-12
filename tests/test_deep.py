from __future__ import annotations

import json
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from repo_scout.config import Config
from repo_scout.db import Database
from repo_scout.deep import (
    DeepAnalysisError,
    _extract_tarball,
    cloc_code_lines,
    count_lines_fallback,
    deep_update_values,
    parse_cloc_json,
    run_cloc,
    run_deep_analysis,
)
from repo_scout.models import RepoMetadata, TrendingEntry
from repo_scout.util import iso_z

NOW = datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc)


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "deep.sqlite3")
    db.init_schema()
    return db


def seed_repo(
    db: Database,
    *,
    full_name: str = "acme/rocket",
    size_kb: int = 1000,
    stars: int = 10000,
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
        repo_id=hash(full_name) % 1_000_000,
        full_name=full_name,
        owner=full_name.split("/")[0],
        name=full_name.split("/")[1],
        html_url=f"https://github.com/{full_name}",
        description=None,
        homepage=None,
        language="Python",
        license_spdx="MIT",
        topics=[],
        stars_total=stars,
        forks=0,
        open_issues=0,
        subscribers=0,
        size_kb=size_kb,
        default_branch="main",
        archived=False,
        fork=False,
        disabled=False,
        created_at=None,
        pushed_at=None,
    )
    db.apply_metadata(repo_pk, meta, iso_z(NOW - timedelta(hours=1)))
    return repo_pk


def seed_analysis(db: Database, repo_pk: int) -> None:
    db.upsert_repo_analysis(
        {
            "repo_pk": repo_pk,
            "analyzed_at": iso_z(NOW - timedelta(hours=2)),
            "analysis_version": "m3-light-1",
            "loc": 11000,
            "loc_method": "api_estimate",
            "loc_confidence": "medium",
            "dep_count": 2,
            "dev_dep_count": 1,
            "dep_manifest": "package.json",
            "dep_confidence": "high",
            "ext_services": "[]",
            "ext_evidence": "[]",
            "has_docker": 0,
            "has_compose": 0,
            "compose_services": None,
            "has_monorepo": 0,
            "ci_workflows": 1,
            "src_files": 2,
            "top_dirs": 3,
            "tree_truncated": 0,
            "license_risk": "green",
            "s_loc": 75,
            "s_dep": 100,
            "s_ext": 100,
            "s_lang": 100,
            "s_license": 100,
            "s_struct": 70,
            "repro_score": 88.75,
            "complexity_score": 17,
            "complexity_grade": "A",
            "replication_weeks_min": 2.5,
            "replication_weeks_max": 4.0,
            "low_confidence_fields": "[]",
            "score_breakdown": json.dumps({"hard_language_unmatched": False}),
            "updated_at": iso_z(NOW - timedelta(hours=2)),
        }
    )


def write_fake_cloc(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "import json\n"
        "print(json.dumps({'SUM': {'code': 123}, "
        "'Python': {'code': 100}, 'Shell': {'code': 23}}))\n",
        encoding="utf-8",
    )
    return path


def fake_clone(_repo, destination: Path, _timeout: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "main.py").write_text("print('hi')\n", encoding="utf-8")


def make_config(tmp_path, *, deep_source: str = "clone") -> Config:
    return Config(
        db_path=tmp_path / "deep.sqlite3",
        deep_limit=10,
        deep_refresh_hours=168,
        deep_max_size_mb=200,
        deep_timeout_seconds=30,
        deep_source=deep_source,
        cloc_auto_download=False,
    )


def test_parse_cloc_json() -> None:
    text = json.dumps(
        {
            "header": {"cloc_url": "x"},
            "Python": {"nFiles": 2, "code": 100},
            "Shell": {"nFiles": 1, "code": 20},
            "SUM": {"blank": 5, "comment": 3, "code": 120},
        }
    )
    assert parse_cloc_json(text) == (120, {"Python": 100, "Shell": 20})
    assert parse_cloc_json("not json") is None


def test_cloc_code_lines_excludes_docs_and_config() -> None:
    languages = {"Python": 100, "Markdown": 50, "JSON": 20, "YAML": 30}
    assert cloc_code_lines(languages) == 100
    assert cloc_code_lines({"Markdown": 50}) == 50


def test_run_cloc_timeout_kills_process(tmp_path) -> None:
    with pytest.raises(DeepAnalysisError, match="timed out"):
        run_cloc(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            tmp_path,
            timeout=1,
        )


def test_count_lines_fallback(tmp_path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.py").write_text(
        "# comment\nx = 1\n\ny = 2\n", encoding="utf-8"
    )
    (root / "b.ts").write_text("// comment\nconst x = 1;\n", encoding="utf-8")
    (root / "ignored.md").write_text("hello\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "c.js").write_text("skip();\n", encoding="utf-8")

    total, languages = count_lines_fallback(root)

    assert total == 3
    assert languages == {"Python": 2, "TypeScript": 1}


def test_extract_tarball_strips_top_directory(tmp_path) -> None:
    archive = tmp_path / "repo.tar.gz"
    payload = tmp_path / "payload"
    (payload / "repo-main").mkdir(parents=True)
    (payload / "repo-main" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload / "repo-main", arcname="repo-main")

    destination = tmp_path / "checkout"
    _extract_tarball(archive, tmp_path / "extract", destination)

    assert (destination / "main.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_deep_update_values_recomputes_scores() -> None:
    repo = {
        "low_confidence_fields": "[]",
        "score_breakdown": json.dumps({"hard_language_unmatched": False}),
        "ext_services": "[]",
        "has_monorepo": 0,
        "s_dep": 100,
        "s_ext": 100,
        "s_lang": 100,
        "s_license": 100,
        "s_struct": 70,
    }
    values = deep_update_values(
        repo,
        loc=123,
        method="cloc",
        languages={"Python": 123},
        duration_ms=50,
        now=NOW,
    )

    assert values["loc"] == 123
    assert values["loc_method"] == "cloc"
    assert values["loc_confidence"] == "high"
    assert values["s_loc"] == 100.0
    assert values["repro_score"] == 97.0
    assert values["complexity_score"] == 3.0
    assert values["complexity_grade"] == "A"
    assert values["replication_weeks_min"] == 0.5
    assert values["replication_weeks_max"] == 1.0
    assert values["deep_error"] is None


def test_deep_update_values_hard_language_cap() -> None:
    repo = {
        "low_confidence_fields": '["dep"]',
        "score_breakdown": json.dumps({"hard_language_unmatched": True}),
        "ext_services": "[]",
        "has_monorepo": 0,
        "s_dep": 60,
        "s_ext": 100,
        "s_lang": 20,
        "s_license": 100,
        "s_struct": 100,
    }
    values = deep_update_values(
        repo,
        loc=60000,
        method="cloc",
        languages={"Rust": 60000},
        duration_ms=10,
        now=NOW,
    )
    assert values["repro_score"] == 40.0
    assert values["complexity_grade"].endswith("*")


def test_run_deep_analysis_updates_db(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = seed_repo(db)
    seed_analysis(db, repo_pk)
    fake_cloc = write_fake_cloc(tmp_path / "tools" / "fake_cloc.py")

    summary = run_deep_analysis(
        db,
        make_config(tmp_path),
        force=True,
        now=NOW,
        cloc_command=[sys.executable, str(fake_cloc)],
        clone_fn=fake_clone,
    )

    assert summary.analyzed == 1
    assert summary.failed == 0
    assert summary.skipped_large == 0
    stored = db.get_repo_analysis(repo_pk)
    assert stored["loc"] == 123
    assert stored["loc_method"] == "cloc"
    assert stored["loc_confidence"] == "high"
    assert stored["repro_score"] == 97.0
    assert stored["complexity_grade"] == "A"
    assert stored["deep_error"] is None
    assert stored["deep_analyzed_at"] == iso_z(NOW)
    assert json.loads(stored["deep_languages"]) == {"Python": 100, "Shell": 23}
    assert db.count_deep_analysis() == 1
    assert db.status()["deep_analysis"] == 1


def test_run_deep_analysis_skips_large_without_clone(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = seed_repo(db, size_kb=300 * 1024)
    seed_analysis(db, repo_pk)
    called = False

    def clone(_repo, _destination, _timeout):
        nonlocal called
        called = True

    summary = run_deep_analysis(
        db,
        make_config(tmp_path),
        force=True,
        now=NOW,
        cloc_command=[sys.executable],
        clone_fn=clone,
    )

    assert summary.skipped_large == 1
    assert summary.analyzed == 0
    assert called is False
    stored = db.get_repo_analysis(repo_pk)
    assert stored["deep_error"] == "too_large"
    assert stored["deep_analyzed_at"] == iso_z(NOW)


def test_run_deep_analysis_clone_failure_marks_error(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = seed_repo(db)
    seed_analysis(db, repo_pk)

    def clone(_repo, _destination, _timeout):
        raise DeepAnalysisError("clone failed")

    summary = run_deep_analysis(
        db,
        make_config(tmp_path),
        force=True,
        now=NOW,
        cloc_command=[sys.executable],
        clone_fn=clone,
    )

    assert summary.failed == 1
    assert summary.analyzed == 0
    stored = db.get_repo_analysis(repo_pk)
    assert stored["loc"] == 11000
    assert stored["deep_error"] == "clone failed"
    assert stored["deep_analyzed_at"] is None


def test_run_deep_analysis_falls_back_to_tarball(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = seed_repo(db)
    seed_analysis(db, repo_pk)
    fake_cloc = write_fake_cloc(tmp_path / "tools" / "fake_cloc.py")

    def clone(_repo, _destination, _timeout):
        raise DeepAnalysisError("clone failed")

    def tarball(_repo, destination: Path, _timeout, _max_bytes):
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "main.py").write_text("print('hi')\n", encoding="utf-8")

    summary = run_deep_analysis(
        db,
        make_config(tmp_path, deep_source="auto"),
        force=True,
        now=NOW,
        cloc_command=[sys.executable, str(fake_cloc)],
        clone_fn=clone,
        tarball_fn=tarball,
    )

    assert summary.analyzed == 1
    assert summary.failed == 0
    stored = db.get_repo_analysis(repo_pk)
    assert stored["loc_method"] == "cloc"
    assert stored["loc"] == 123


def test_run_deep_analysis_cache_and_fallback(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = seed_repo(db)
    seed_analysis(db, repo_pk)

    # cloc 不可用且禁止自动下载 → 使用内置行数统计。
    summary = run_deep_analysis(
        db,
        make_config(tmp_path),
        force=True,
        now=NOW,
        cloc_command=None,
        clone_fn=fake_clone,
    )
    assert summary.analyzed == 1
    stored = db.get_repo_analysis(repo_pk)
    assert stored["loc_method"] == "line_count"
    assert stored["loc"] == 1
    assert stored["loc_confidence"] == "medium"

    again = run_deep_analysis(
        db,
        make_config(tmp_path),
        force=False,
        now=NOW,
        cloc_command=None,
        clone_fn=fake_clone,
    )
    assert again.analyzed == 0

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from repo_scout.classify import Classification
from repo_scout.db import Database
from repo_scout.metrics import (
    CumulativePoint,
    RepoMetrics,
    assign_trend_scores,
    compute_acceleration,
    compute_and_store_metrics,
    compute_delta_24h,
    compute_metrics,
    compute_window_delta,
    detect_anomaly,
    daily_delta_series,
    percentile_map,
    summarize_metrics,
)
from repo_scout.models import TrendingEntry
from repo_scout.util import iso_z

NOW = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "metrics.sqlite3")
    db.init_schema()
    return db


def add_repo(db: Database, full_name: str = "acme/rocket", stars: int = 10000) -> int:
    entry = TrendingEntry(
        full_name=full_name,
        owner=full_name.split("/")[0],
        name=full_name.split("/")[1],
        html_url=f"https://github.com/{full_name}",
        period="daily",
        stars_total=stars,
    )
    return db.upsert_trending_entry(entry, iso_z(NOW - timedelta(hours=1))).repo_pk


def add_weekly_series(
    db: Database,
    repo_pk: int,
    *,
    total: int = 10000,
    per_week: int = 70,
    weeks: int = 30,
    fetched_at: datetime = NOW - timedelta(hours=1),
) -> None:
    """构造“每周稳定新增 per_week”的 30 周官方历史。"""
    end_week = date(2026, 11, 8)
    rows = []
    for index in range(weeks):
        week_start = end_week - timedelta(weeks=index)
        rows.append(
            {
                "week_start": week_start.isoformat(),
                "delta": per_week,
                "stars_end": max(0, total - per_week * index),
                "source": "github_stargazers_history",
            }
        )
    db.upsert_star_weekly(repo_pk, rows, iso_z(fetched_at))


def metric(pk: int, **overrides) -> RepoMetrics:
    values = {
        "repo_pk": pk,
        "full_name": f"acme/repo-{pk}",
        "stars_total": 1000,
        "delta_24h": 0,
        "delta_7d": 0,
        "delta_30d": 0,
        "velocity_7d": None,
        "velocity_prev": None,
        "acceleration": None,
        "acceleration_available": False,
        "anomaly": False,
        "burst": False,
        "anomaly_available": False,
    }
    values.update(overrides)
    return RepoMetrics(**values)


# -- 历史窗口 ---------------------------------------------------------------
def test_window_deltas_from_star_history() -> None:
    rows = []
    for index in range(30):
        week_start = date(2026, 11, 8) - timedelta(weeks=index)
        rows.append(
            {
                "week_start": week_start.isoformat(),
                "delta": 70,
                "stars_end": 10000 - 70 * index,
                "fetched_at": iso_z(NOW - timedelta(hours=1)),
            }
        )
    # 查询按 week_start 升序，这里手动反转模拟 DB 返回顺序。
    ordered = list(reversed(rows))

    delta_7d, source_7d = compute_window_delta(
        days=7, weeks=ordered, snapshots=[], stars_total=10000, now=NOW
    )
    delta_30d, source_30d = compute_window_delta(
        days=30, weeks=ordered, snapshots=[], stars_total=10000, now=NOW
    )

    assert (delta_7d, source_7d) == (100, "star_history")
    assert (delta_30d, source_30d) == (330, "star_history")


def test_window_delta_requires_coverage() -> None:
    # 只有 1 周历史，无法覆盖 7 天/30 天窗口。
    rows = [
        {
            "week_start": "2026-11-08",
            "delta": 10,
            "stars_end": 100,
            "fetched_at": iso_z(NOW - timedelta(hours=1)),
        }
    ]
    assert compute_window_delta(
        days=7, weeks=rows, snapshots=[], stars_total=100, now=NOW
    ) == (None, None)


def test_delta_24h_trending_preferred_and_snapshot_fallback() -> None:
    snapshots = [
        CumulativePoint(NOW - timedelta(hours=24), 9000),
        CumulativePoint(NOW - timedelta(hours=6), 9500),
        CumulativePoint(NOW, 10000),
    ]

    assert compute_delta_24h(
        stars_total=10000,
        snapshots=snapshots,
        daily_hit=(NOW - timedelta(hours=2), 25),
        now=NOW,
    ) == (25, "trending_daily")

    assert compute_delta_24h(
        stars_total=10000,
        snapshots=snapshots,
        daily_hit=None,
        now=NOW,
    ) == (1000, "snapshot")

    # 只有 18 小时快照：不外推，返回缺失。
    assert compute_delta_24h(
        stars_total=10000,
        snapshots=[
            CumulativePoint(NOW - timedelta(hours=18), 9000),
            CumulativePoint(NOW, 10000),
        ],
        daily_hit=None,
        now=NOW,
    ) == (None, None)


def test_velocity_and_acceleration() -> None:
    snapshots = [
        CumulativePoint(NOW - timedelta(days=14), 700),
        CumulativePoint(NOW - timedelta(days=7), 800),
        CumulativePoint(NOW, 1000),
    ]
    velocity_7d, velocity_prev, acceleration, available = compute_acceleration(
        weeks=[], snapshots=snapshots, stars_total=1000, now=NOW, delta_7d=200
    )

    assert velocity_7d == pytest.approx(28.5714)
    assert velocity_prev == pytest.approx(14.2857)
    assert acceleration == pytest.approx(1.0)
    assert available is True


def test_acceleration_missing_when_previous_growth_too_small() -> None:
    snapshots = [
        CumulativePoint(NOW - timedelta(days=14), 799),
        CumulativePoint(NOW - timedelta(days=7), 800),
        CumulativePoint(NOW, 1000),
    ]
    _, velocity_prev, acceleration, available = compute_acceleration(
        weeks=[], snapshots=snapshots, stars_total=1000, now=NOW, delta_7d=200
    )

    assert velocity_prev == pytest.approx(1 / 7, abs=1e-4)
    assert acceleration is None
    assert available is False


# -- 异常检测 ---------------------------------------------------------------
def test_anomaly_3sigma_and_burst() -> None:
    snapshots = [
        CumulativePoint(
            datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc) + timedelta(days=index),
            1000 + 10 * index,
        )
        for index in range(20)
    ]
    snapshots.append(
        CumulativePoint(datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc), 1500)
    )

    available, anomaly, burst = detect_anomaly(daily_delta_series(snapshots, NOW))

    assert available is True
    assert anomaly is True
    assert burst is True


def test_anomaly_needs_seven_days() -> None:
    snapshots = [
        CumulativePoint(NOW - timedelta(days=index), 1000 - index)
        for index in range(4, -1, -1)
    ]
    assert detect_anomaly(daily_delta_series(snapshots, NOW)) == (False, False, False)


# -- TrendScore -------------------------------------------------------------
def test_percentile_map() -> None:
    result = percentile_map({1: 10, 2: 20, 3: 30})
    assert result[1] == pytest.approx(16.6667, abs=1e-3)
    assert result[2] == pytest.approx(50.0)
    assert result[3] == pytest.approx(83.3333, abs=1e-3)
    assert percentile_map({7: 1})[7] == 50.0


def test_trend_score_weights_and_missing_values() -> None:
    items = [
        metric(1, delta_24h=100, delta_7d=100, delta_30d=100, acceleration=1.0),
        metric(2, delta_24h=0, delta_7d=0, delta_30d=0, acceleration=0.0),
        metric(3, delta_24h=None, delta_7d=None, delta_30d=None),
    ]

    scored = {item.repo_pk: item for item in assign_trend_scores(items)}

    assert scored[1].trend_score == pytest.approx(76.25)
    assert scored[2].trend_score == pytest.approx(26.25)
    assert scored[3].trend_score == pytest.approx(47.5)


def test_trend_score_penalizes_anomaly_and_burst() -> None:
    items = [
        metric(1, delta_24h=100, delta_7d=100, delta_30d=100, acceleration=1.0),
        metric(2, delta_24h=0, delta_7d=0, delta_30d=0, acceleration=0.0),
        metric(
            3,
            delta_24h=100,
            delta_7d=100,
            delta_30d=100,
            acceleration=1.0,
            anomaly=True,
        ),
        metric(
            4,
            delta_24h=100,
            delta_7d=100,
            delta_30d=100,
            acceleration=1.0,
            burst=True,
        ),
    ]

    scored = {item.repo_pk: item for item in assign_trend_scores(items)}

    baseline = scored[1].trend_score
    assert scored[3].trend_score == pytest.approx(round(baseline * 0.6, 2))
    assert scored[4].trend_score == pytest.approx(round(baseline * 0.6, 2))


# -- 编排、持久化与输出 -----------------------------------------------------
def test_compute_and_store_metrics(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = add_repo(db)
    add_weekly_series(db, repo_pk)

    items = compute_and_store_metrics(db, now=NOW)

    assert len(items) == 1
    item = items[0]
    assert item.delta_7d == 100
    assert item.delta_30d == 330
    assert item.velocity_7d == pytest.approx(14.2857)
    assert item.acceleration == pytest.approx(0.4286)
    assert item.acceleration_available is True
    assert item.anomaly_available is False
    assert item.history_confidence == "high"
    assert item.sources["delta_7d"] == "star_history"
    assert item.trend_score == 50.0

    stored = db.get_repo_metrics(repo_pk)
    assert stored is not None
    assert stored["delta_7d"] == 100
    assert json.loads(stored["sources"])["delta_30d"] == "star_history"

    status = db.status()
    assert status["metrics"] == 1
    assert status["metrics_computed_at"] == iso_z(NOW)


def test_excluded_repos_are_removed_from_metrics(tmp_path) -> None:
    db = make_db(tmp_path)
    repo_pk = add_repo(db)
    add_weekly_series(db, repo_pk)
    compute_and_store_metrics(db, now=NOW)
    assert db.count_repo_metrics() == 1

    db.set_classification(repo_pk, Classification("tutorial", True, "tutorial"))
    compute_and_store_metrics(db, now=NOW)

    assert db.count_repo_metrics() == 0


def test_snapshot_ordering_uses_trend_score(tmp_path) -> None:
    db = make_db(tmp_path)
    fast = add_repo(db, "acme/fast", stars=1000)
    slow = add_repo(db, "acme/slow", stars=100000)
    add_weekly_series(db, fast, total=1000, per_week=100)
    add_weekly_series(db, slow, total=100000, per_week=1)
    compute_and_store_metrics(db, now=NOW)

    selected = db.list_repos_for_snapshot(10)

    assert [row["full_name"] for row in selected[:2]] == ["acme/fast", "acme/slow"]


def test_metrics_skip_repos_without_stars(tmp_path) -> None:
    db = make_db(tmp_path)
    entry = TrendingEntry(
        full_name="acme/unknown",
        owner="acme",
        name="unknown",
        html_url="https://github.com/acme/unknown",
        period="daily",
        stars_total=None,
    )
    db.upsert_trending_entry(entry, iso_z(NOW))
    assert compute_metrics(db, now=NOW) == []


def test_summarize_metrics_sorting_and_top() -> None:
    items = [
        metric(1, full_name="a/one", delta_7d=10, velocity_7d=1.0, stars_total=100),
        metric(2, full_name="b/two", delta_7d=50, velocity_7d=5.0, stars_total=10),
    ]
    scored = assign_trend_scores(items)

    summary = summarize_metrics(scored, top=1, sort="delta7")

    assert summary["repos"] == 2
    assert summary["top"][0]["full_name"] == "b/two"
    assert summary["coverage"]["delta_7d"] == 2
    with pytest.raises(ValueError):
        summarize_metrics(scored, sort="unknown")

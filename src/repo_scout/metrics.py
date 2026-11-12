"""M2 增长趋势计算层（FR-201 ~ FR-206）。

数据来源与优先级：

- ``trending_hits``：daily 榜单的 “stars today”，优先作为 ``ΔStars_24h``；
- ``snapshots``：6 小时快照，提供 ``Δ24h`` 兜底与异常波动检测的日增量序列；
- ``star_weekly``：官方周级星标历史，回填 ``Δ7d`` / ``Δ30d`` 与加速率。

所有排序指标都使用**候选池内百分位**（FR-203），避免绝对星数霸榜。
计算过程不访问网络，可随时用 ``python -m repo_scout metrics`` 重算。
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping

from .util import iso_z, utc_now

# 与 FR-201 对齐：上周增速低于 20 星/周时加速率不可用。
MIN_PREV_WEEKLY_GROWTH = 20.0
# stars/contributor 超过该阈值标记“单人爆款/刷星嫌疑”。
SUSPICIOUS_STARS_PER_CONTRIBUTOR = 10_000
# 异常/单日爆发对 TrendScore 的惩罚系数。
ANOMALY_SCORE_MULTIPLIER = 0.6
# 异常检测使用最近多少天的日增量。
ANOMALY_WINDOW_DAYS = 30
ANOMALY_MIN_DAYS = 7
# 单日爆发：近 7 天增长中单日占比阈值。
BURST_SHARE = 0.7


def parse_ts(value: str) -> datetime:
    """解析数据库中的 ISO 时间（统一按 UTC 处理）。"""
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class CumulativePoint:
    """某一时刻的累计星数。"""

    at: datetime
    stars: float


@dataclass(frozen=True)
class RepoMetrics:
    repo_pk: int
    full_name: str
    stars_total: int
    delta_24h: int | None
    delta_7d: int | None
    delta_30d: int | None
    velocity_7d: float | None
    velocity_prev: float | None
    acceleration: float | None
    acceleration_available: bool
    anomaly: bool
    burst: bool
    anomaly_available: bool
    trend_score: float | None = None
    stars_per_contributor: float | None = None
    suspicious_stars: bool = False
    history_confidence: str = "low"
    sources: dict[str, str | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_pk": self.repo_pk,
            "full_name": self.full_name,
            "stars_total": self.stars_total,
            "delta_24h": self.delta_24h,
            "delta_7d": self.delta_7d,
            "delta_30d": self.delta_30d,
            "velocity_7d": self.velocity_7d,
            "velocity_prev": self.velocity_prev,
            "acceleration": self.acceleration,
            "acceleration_available": self.acceleration_available,
            "anomaly": self.anomaly,
            "burst": self.burst,
            "anomaly_available": self.anomaly_available,
            "trend_score": self.trend_score,
            "stars_per_contributor": self.stars_per_contributor,
            "suspicious_stars": self.suspicious_stars,
            "history_confidence": self.history_confidence,
            "sources": dict(self.sources),
        }


# -- 累计星数序列 -----------------------------------------------------------
def normalize_points(points: Iterable[CumulativePoint]) -> list[CumulativePoint]:
    """按时间排序并按时间戳去重（后出现的值优先）。"""
    by_time: dict[datetime, float] = {}
    for point in points:
        by_time[point.at] = point.stars
    return [
        CumulativePoint(at=at, stars=stars)
        for at, stars in sorted(by_time.items())
    ]


def interpolate_value(
    points: list[CumulativePoint], target: datetime
) -> float | None:
    """在累计星数序列上取 ``target`` 时刻的值（线性插值）。

    目标时刻超出序列范围时返回 ``None``——M2 不做外推，避免用 18 小时
    数据伪造 24 小时增量。
    """
    if not points or target < points[0].at or target > points[-1].at:
        return None
    if target == points[-1].at:
        return points[-1].stars

    for left, right in zip(points, points[1:]):
        if left.at <= target <= right.at:
            span = (right.at - left.at).total_seconds()
            if span <= 0:
                return right.stars
            ratio = (target - left.at).total_seconds() / span
            return left.stars + (right.stars - left.stars) * ratio
    return None


def snapshot_points(rows: Iterable[sqlite3.Row]) -> list[CumulativePoint]:
    return normalize_points(
        CumulativePoint(at=parse_ts(row["captured_at"]), stars=float(row["stars"]))
        for row in rows
    )


def star_history_points(
    weeks: Iterable[sqlite3.Row], stars_total: int, now: datetime
) -> list[CumulativePoint]:
    """把周级历史转成“时间 → 累计星数”序列。

    ``stars_end`` 是周末累计值；当前周是部分数据，其 ``stars_end`` 等于抓取时
    的总星数，因此时间点取 ``fetched_at``（不晚于该周结束）。再补一个 ``now``
    锚点，保证最新总星数参与插值。
    """
    points = [CumulativePoint(at=now, stars=float(stars_total))]
    for row in weeks:
        if row["stars_end"] is None:
            continue
        week_start = date.fromisoformat(str(row["week_start"]))
        week_end = datetime.combine(
            week_start + timedelta(days=7), time.min, tzinfo=timezone.utc
        )
        fetched_at = parse_ts(row["fetched_at"])
        at = min(week_end, fetched_at)
        if at > now:
            at = now
        points.append(
            CumulativePoint(at=at, stars=float(row["stars_end"]))
        )
    return normalize_points(points)


def _history_value_at(
    weeks: list[sqlite3.Row],
    snapshots: list[CumulativePoint],
    stars_total: int,
    target: datetime,
    now: datetime,
) -> tuple[float | None, str | None]:
    """优先用官方周级历史，其次用快照，取 ``target`` 时刻的累计星数。"""
    value = interpolate_value(star_history_points(weeks, stars_total, now), target)
    if value is not None:
        return value, "star_history"
    value = interpolate_value(snapshots, target)
    if value is not None:
        return value, "snapshot"
    return None, None


# -- 基础指标（FR-201） -----------------------------------------------------
def compute_delta_24h(
    *,
    stars_total: int,
    snapshots: list[CumulativePoint],
    daily_hit: tuple[datetime, int] | None,
    now: datetime,
) -> tuple[int | None, str | None]:
    """Δ24h：优先 Trending daily 的 “stars today”，缺失时用快照。"""
    if daily_hit is not None:
        captured_at, period_stars = daily_hit
        if now - captured_at <= timedelta(hours=24):
            return int(period_stars), "trending_daily"

    value = interpolate_value(snapshots, now - timedelta(hours=24))
    if value is None:
        return None, None
    return int(round(stars_total - value)), "snapshot"


def compute_window_delta(
    *,
    days: int,
    weeks: list[sqlite3.Row],
    snapshots: list[CumulativePoint],
    stars_total: int,
    now: datetime,
) -> tuple[int | None, str | None]:
    target = now - timedelta(days=days)
    value, source = _history_value_at(weeks, snapshots, stars_total, target, now)
    if value is None:
        return None, None
    return int(round(stars_total - value)), source


def compute_acceleration(
    *,
    weeks: list[sqlite3.Row],
    snapshots: list[CumulativePoint],
    stars_total: int,
    now: datetime,
    delta_7d: int | None,
) -> tuple[float | None, float | None, float | None, bool]:
    """返回 (Velocity_7d, Velocity_prev, Acceleration, 是否可用)。

    上周新增 < 20 星（``Velocity_prev * 7 < 20``）时加速率置空，
    避免用极小分母放大噪音（FR-201）。
    """
    velocity_7d = None if delta_7d is None else round(delta_7d / 7, 4)

    value_7d, _ = _history_value_at(
        weeks, snapshots, stars_total, now - timedelta(days=7), now
    )
    value_14d, _ = _history_value_at(
        weeks, snapshots, stars_total, now - timedelta(days=14), now
    )
    velocity_prev = None
    if value_7d is not None and value_14d is not None:
        velocity_prev = round((value_7d - value_14d) / 7, 4)

    acceleration = None
    available = False
    if (
        velocity_7d is not None
        and velocity_prev is not None
        and velocity_prev * 7 >= MIN_PREV_WEEKLY_GROWTH
    ):
        acceleration = round((velocity_7d - velocity_prev) / max(velocity_prev, 5.0), 4)
        available = True
    return velocity_7d, velocity_prev, acceleration, available


# -- 异常波动（FR-202） -----------------------------------------------------
def daily_delta_series(
    snapshots: list[CumulativePoint], now: datetime
) -> list[tuple[date, int]]:
    """按 UTC 日取当天最后一个快照，生成相邻日期的日增量。"""
    by_day: dict[date, float] = {}
    cutoff = now - timedelta(days=ANOMALY_WINDOW_DAYS)
    for point in snapshots:
        if point.at < cutoff:
            continue
        by_day[point.at.date()] = point.stars  # points 已按时间升序，后值覆盖
    days = sorted(by_day)
    return [
        (days[index], int(round(by_day[days[index]] - by_day[days[index - 1]])))
        for index in range(1, len(days))
    ]


def detect_anomaly(
    series: list[tuple[date, int]]
) -> tuple[bool, bool, bool]:
    """返回 (数据是否充足, 是否 3σ 异常, 是否单日爆发)。"""
    deltas = [delta for _, delta in series]
    if len(deltas) < ANOMALY_MIN_DAYS:
        return False, False, False

    # 异常检测忽略负值（取消星标），避免把下降计入噪声基线。
    stats_values = [delta for delta in deltas if delta >= 0]
    anomaly = False
    if len(stats_values) >= ANOMALY_MIN_DAYS:
        mean = sum(stats_values) / len(stats_values)
        variance = sum((value - mean) ** 2 for value in stats_values) / len(stats_values)
        sigma = math.sqrt(variance)
        latest = deltas[-1]
        if latest > 0:
            anomaly = latest > mean + 3 * sigma if sigma > 0 else latest > mean

    last_7 = deltas[-7:]
    total = sum(last_7)
    burst = total > 0 and max(last_7) / total >= BURST_SHARE
    return True, anomaly, burst


# -- TrendScore（FR-203） ---------------------------------------------------
def percentile_map(values: Mapping[int, float]) -> dict[int, float]:
    """候选池内百分位（0–100，平均秩，缺失值不参与分布）。"""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    total = len(ordered)
    result: dict[int, float] = {}
    index = 0
    while index < total:
        end = index
        while end + 1 < total and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        percentile = 100.0 * (index + (end - index + 1) / 2) / total
        for position in range(index, end + 1):
            result[ordered[position][0]] = percentile
        index = end + 1
    return result


def assign_trend_scores(items: list[RepoMetrics]) -> list[RepoMetrics]:
    """PC 百分位加权；缺失指标按 50 分位，异常项 ×0.6。"""
    p7 = percentile_map(
        {item.repo_pk: float(item.delta_7d) for item in items if item.delta_7d is not None}
    )
    p24 = percentile_map(
        {
            item.repo_pk: float(item.delta_24h)
            for item in items
            if item.delta_24h is not None
        }
    )
    p30 = percentile_map(
        {
            item.repo_pk: float(item.delta_30d)
            for item in items
            if item.delta_30d is not None
        }
    )
    # 加速率缺失按 0 参与分布（FR-203），避免冷启动项目被过度惩罚。
    pacc = percentile_map(
        {
            item.repo_pk: float(item.acceleration or 0.0)
            for item in items
        }
    )

    scored: list[RepoMetrics] = []
    for item in items:
        score = (
            0.40 * p7.get(item.repo_pk, 50.0)
            + 0.25 * p24.get(item.repo_pk, 50.0)
            + 0.20 * p30.get(item.repo_pk, 50.0)
            + 0.15 * pacc.get(item.repo_pk, 50.0)
        )
        if item.anomaly or item.burst:
            score *= ANOMALY_SCORE_MULTIPLIER
        scored.append(replace(item, trend_score=round(score, 2)))
    return scored


# -- 单仓库计算 -------------------------------------------------------------
def compute_repo_metrics(
    repo: sqlite3.Row,
    *,
    snapshots: list[CumulativePoint],
    weeks: list[sqlite3.Row],
    daily_hit: tuple[datetime, int] | None,
    now: datetime,
) -> RepoMetrics:
    stars_total = int(repo["stars_total"])
    delta_24h, source_24h = compute_delta_24h(
        stars_total=stars_total,
        snapshots=snapshots,
        daily_hit=daily_hit,
        now=now,
    )
    delta_7d, source_7d = compute_window_delta(
        days=7,
        weeks=weeks,
        snapshots=snapshots,
        stars_total=stars_total,
        now=now,
    )
    delta_30d, source_30d = compute_window_delta(
        days=30,
        weeks=weeks,
        snapshots=snapshots,
        stars_total=stars_total,
        now=now,
    )
    velocity_7d, velocity_prev, acceleration, accel_available = compute_acceleration(
        weeks=weeks,
        snapshots=snapshots,
        stars_total=stars_total,
        now=now,
        delta_7d=delta_7d,
    )

    anomaly_available, anomaly, burst = detect_anomaly(
        daily_delta_series(snapshots, now)
    )

    contributors = repo["contributors"]
    stars_per_contributor = None
    suspicious = False
    if contributors is not None and int(contributors) > 0:
        stars_per_contributor = round(stars_total / int(contributors), 1)
        suspicious = stars_per_contributor > SUSPICIOUS_STARS_PER_CONTRIBUTOR

    if source_7d == "star_history" and source_30d == "star_history":
        confidence = "high"
    elif delta_7d is not None:
        confidence = "medium"
    else:
        confidence = "low"

    return RepoMetrics(
        repo_pk=int(repo["id"]),
        full_name=str(repo["full_name"]),
        stars_total=stars_total,
        delta_24h=delta_24h,
        delta_7d=delta_7d,
        delta_30d=delta_30d,
        velocity_7d=velocity_7d,
        velocity_prev=velocity_prev,
        acceleration=acceleration,
        acceleration_available=accel_available,
        anomaly=anomaly,
        burst=burst,
        anomaly_available=anomaly_available,
        stars_per_contributor=stars_per_contributor,
        suspicious_stars=suspicious,
        history_confidence=confidence,
        sources={
            "delta_24h": source_24h,
            "delta_7d": source_7d,
            "delta_30d": source_30d,
        },
    )


# -- 全量编排与持久化 -------------------------------------------------------
def _group_rows(rows: Iterable[sqlite3.Row], key: str = "repo_pk") -> dict[int, list[sqlite3.Row]]:
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(int(row[key]), []).append(row)
    return grouped


def compute_metrics(db: Any, *, now: datetime | None = None) -> list[RepoMetrics]:
    """计算全体有效仓库的 M2 指标（不写库）。"""
    now = now or utc_now()
    repos = db.list_metric_repos()
    snapshot_rows = _group_rows(db.list_metric_snapshots(since=iso_z(now - timedelta(days=32))))
    week_rows = _group_rows(db.list_metric_star_weekly())
    daily_rows = db.list_latest_daily_hits(since=iso_z(now - timedelta(hours=24)))

    daily_hits: dict[int, tuple[datetime, int]] = {}
    for row in daily_rows:
        if row["period_stars"] is None:
            continue
        daily_hits[int(row["repo_pk"])] = (
            parse_ts(row["captured_at"]),
            int(row["period_stars"]),
        )

    items: list[RepoMetrics] = []
    for repo in repos:
        repo_pk = int(repo["id"])
        items.append(
            compute_repo_metrics(
                repo,
                snapshots=snapshot_points(snapshot_rows.get(repo_pk, [])),
                weeks=week_rows.get(repo_pk, []),
                daily_hit=daily_hits.get(repo_pk),
                now=now,
            )
        )
    return assign_trend_scores(items)


def store_metrics(db: Any, items: list[RepoMetrics], computed_at: datetime) -> None:
    rows = [
        {
            "repo_pk": item.repo_pk,
            "computed_at": iso_z(computed_at),
            "stars_total": item.stars_total,
            "delta_24h": item.delta_24h,
            "delta_7d": item.delta_7d,
            "delta_30d": item.delta_30d,
            "velocity_7d": item.velocity_7d,
            "velocity_prev": item.velocity_prev,
            "acceleration": item.acceleration,
            "acceleration_available": int(item.acceleration_available),
            "anomaly": int(item.anomaly),
            "burst": int(item.burst),
            "anomaly_available": int(item.anomaly_available),
            "trend_score": item.trend_score,
            "stars_per_contributor": item.stars_per_contributor,
            "suspicious_stars": int(item.suspicious_stars),
            "history_confidence": item.history_confidence,
            "sources": json.dumps(item.sources, ensure_ascii=False),
            "updated_at": iso_z(computed_at),
        }
        for item in items
    ]
    db.upsert_repo_metrics(rows)


def compute_and_store_metrics(
    db: Any, *, now: datetime | None = None, write: bool = True
) -> list[RepoMetrics]:
    computed_at = now or utc_now()
    items = compute_metrics(db, now=computed_at)
    if write:
        store_metrics(db, items, computed_at)
    return items


# -- 输出 -------------------------------------------------------------------
SORT_KEYS = {
    "trend": lambda item: (item.trend_score is None, -(item.trend_score or 0.0), item.full_name),
    "delta7": lambda item: (item.delta_7d is None, -(item.delta_7d or 0), item.full_name),
    "velocity": lambda item: (
        item.velocity_7d is None,
        -(item.velocity_7d or 0.0),
        item.full_name,
    ),
    "stars": lambda item: (-item.stars_total, item.full_name),
}


def sort_metrics(items: list[RepoMetrics], sort: str = "trend") -> list[RepoMetrics]:
    key = SORT_KEYS.get(sort)
    if key is None:
        raise ValueError(f"unsupported sort: {sort}")
    return sorted(items, key=key)


def summarize_metrics(
    items: list[RepoMetrics], *, top: int = 10, sort: str = "trend"
) -> dict[str, Any]:
    ordered = sort_metrics(items, sort)
    selected = ordered if top <= 0 else ordered[:top]
    return {
        "repos": len(items),
        "coverage": {
            "delta_24h": sum(1 for item in items if item.delta_24h is not None),
            "delta_7d": sum(1 for item in items if item.delta_7d is not None),
            "delta_30d": sum(1 for item in items if item.delta_30d is not None),
            "acceleration": sum(1 for item in items if item.acceleration_available),
            "anomaly": sum(1 for item in items if item.anomaly_available),
        },
        "risky": sum(1 for item in items if item.anomaly or item.burst),
        "sort": sort,
        "top": [item.to_dict() for item in selected],
    }

"""采集流水线编排（M1）。

一次 run 的顺序：
1. 抓取 daily/weekly/monthly 三个 Trending 页面并解析；
2. 按 full_name 去重写入 repos，记录 trending_hits；
3. 对未补全/过期仓库调用 GitHub API 补全元数据；
4. 应用分类黑名单（awesome/tutorial/镜像/归档等）；
5. 对未排除仓库写入 6 小时快照；
6. 写入 ingest_runs 运行记录。

任何单个数据源失败都不会清空既有数据；运行状态区分 ok / partial / failed。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Sequence

from .classify import classify_repo
from .config import Config
from .db import Database
from .github import GitHubClient, GitHubError, GitHubRateLimitError, TokenPool
from .http import CountingTransport, HttpTransport
from .metrics import compute_and_store_metrics
from .models import TrendingEntry
from .star_history import StarHistoryError, compute_weekly_rows, fetch_star_history
from .trending import PERIODS, TrendingError, fetch_trending
from .util import floor_to_bucket, hours_ago, iso_z, utc_now

__all__ = ["IngestPipeline", "IngestSummary"]

logger = logging.getLogger(__name__)


@dataclass
class IngestSummary:
    status: str = "ok"
    periods: dict[str, int] = field(default_factory=dict)
    fetched: int = 0
    new_repos: int = 0
    updated_repos: int = 0
    enriched: int = 0
    excluded_total: int = 0
    snapshots: int = 0
    star_history_repos: int = 0
    star_history_weeks: int = 0
    metrics_scored: int = 0
    metrics_risky: int = 0
    requests: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "periods": dict(self.periods),
            "fetched": self.fetched,
            "new_repos": self.new_repos,
            "updated_repos": self.updated_repos,
            "enriched": self.enriched,
            "excluded_total": self.excluded_total,
            "snapshots": self.snapshots,
            "star_history_repos": self.star_history_repos,
            "star_history_weeks": self.star_history_weeks,
            "metrics_scored": self.metrics_scored,
            "metrics_risky": self.metrics_risky,
            "requests": self.requests,
            "errors": list(self.errors),
        }


class IngestPipeline:
    def __init__(
        self,
        db: Database,
        transport: HttpTransport,
        config: Config,
        *,
        now_fn: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.db = db
        self.config = config
        self.transport = CountingTransport(transport)
        self.tokens = TokenPool(
            config.github_tokens, low_watermark=config.low_watermark
        )
        self.github = GitHubClient(
            self.transport,
            tokens=self.tokens,
            base_url=config.api_base,
            user_agent=config.user_agent,
            retries=config.retries,
            timeout=config.timeout,
            sleep=sleep,
        )
        self.now_fn = now_fn
        self.sleep = sleep

    # -- main entry --------------------------------------------------------
    def run(
        self,
        periods: Sequence[str] | None = None,
        *,
        enrich: bool = True,
        snapshot: bool = True,
        progress: Callable[[str, float], None] | None = None,
    ) -> IngestSummary:
        def report(label: str, value: float) -> None:
            if progress:
                progress(label, max(0.0, min(1.0, value)))

        selected_periods = list(periods or PERIODS)
        unknown = [period for period in selected_periods if period not in PERIODS]
        if unknown:
            raise ValueError(f"unsupported periods: {unknown}")

        started = self.now_fn()
        started_iso = iso_z(started)
        bucket_iso = iso_z(
            floor_to_bucket(started, self.config.snapshot_bucket_hours)
        )
        request_start = self.transport.count
        summary = IngestSummary()
        report("开始抓取 Trending", 0.02)

        for index, period in enumerate(selected_periods, start=1):
            try:
                entries = fetch_trending(
                    self.transport,
                    period,
                    base_url=self.config.trending_base_url,
                    user_agent=self.config.user_agent,
                    retries=self.config.retries,
                    min_items=self.config.min_trending_items,
                    timeout=self.config.timeout,
                    sleep=self.sleep,
                )
            except TrendingError as exc:
                summary.errors.append(f"{period}: {exc}")
                report(f"解析 {period} 失败", 0.08 + 0.25 * index / len(selected_periods))
                continue

            summary.periods[period] = len(entries)
            summary.fetched += len(entries)

            for rank, entry in enumerate(entries, start=1):
                result = self.db.upsert_trending_entry(entry, started_iso)
                if result.is_new:
                    summary.new_repos += 1
                else:
                    summary.updated_repos += 1
                self.db.record_trending_hit(
                    result.repo_pk,
                    period,
                    bucket_iso,
                    entry.period_stars,
                    rank,
                )
                self._classify_repo(result.repo_pk)
            report(f"解析 {period}", 0.08 + 0.25 * index / len(selected_periods))

        if enrich:
            report("补全元数据", 0.4)
            self._enrich(summary, started, started_iso)
            report("元数据补全完成", 0.6)

        if self.config.fetch_star_history:
            report("回填星标历史", 0.65)
            self._fetch_star_history(summary, started, started_iso)
            report("星标历史完成", 0.78)

        if snapshot:
            report("写入快照", 0.82)
            for row in self.db.list_repos_for_snapshot(self.config.snapshot_limit):
                self.db.take_snapshot(
                    int(row["id"]),
                    bucket_iso,
                    int(row["stars_total"]),
                    row["forks"],
                    row["open_issues"],
                )
                summary.snapshots += 1
            report("快照完成", 0.9)

        # M2：基于最新快照与星标历史计算增长指标；纯本地计算，不消耗配额。
        report("计算 M2 指标", 0.92)
        try:
            metric_items = compute_and_store_metrics(self.db, now=started)
            summary.metrics_scored = len(metric_items)
            summary.metrics_risky = sum(
                1 for item in metric_items if item.anomaly or item.burst
            )
        except Exception as exc:  # 指标失败不应影响已完成的采集
            logger.exception("metrics computation failed")
            summary.errors.append(f"metrics: {exc}")
        report("M2 指标完成", 0.98)

        summary.excluded_total = self.db.count_excluded()
        summary.requests = self.transport.count - request_start

        failed_periods = sum(1 for period in selected_periods if period not in summary.periods)
        if failed_periods == len(selected_periods) and summary.fetched == 0:
            summary.status = "failed"
        elif summary.errors:
            summary.status = "partial"
        else:
            summary.status = "ok"

        finished = self.now_fn()
        self.db.log_run(
            run_at=started_iso,
            run_type="ingest",
            source="github",
            requests=summary.requests,
            success=summary.enriched,
            items=summary.fetched,
            duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
            status=summary.status,
            error="; ".join(summary.errors) if summary.errors else None,
            details=summary.as_dict(),
        )
        report("采集完成", 1.0)
        return summary

    def add_repo(self, full_name: str) -> bool:
        """FR-108：手动添加一个仓库并立即补全、分类、快照。"""
        started = self.now_fn()
        now_iso = iso_z(started)
        bucket_iso = iso_z(
            floor_to_bucket(started, self.config.snapshot_bucket_hours)
        )

        try:
            meta = self.github.get_repo(full_name)
        except GitHubError as exc:
            self.db.log_run(
                run_at=now_iso,
                run_type="add",
                source="manual",
                status="failed",
                error=str(exc),
            )
            raise

        if meta is None:
            self.db.log_run(
                run_at=now_iso,
                run_type="add",
                source="manual",
                status="failed",
                error=f"repo not found: {full_name}",
            )
            return False

        entry = TrendingEntry(
            full_name=meta.full_name,
            owner=meta.owner,
            name=meta.name,
            html_url=meta.html_url,
            period="manual",
            description=meta.description,
            language=meta.language,
            stars_total=meta.stars_total,
        )
        result = self.db.upsert_trending_entry(entry, now_iso)
        self.db.apply_metadata(result.repo_pk, meta, now_iso)
        self._classify_repo(result.repo_pk)
        if meta.stars_total is not None:
            self.db.take_snapshot(
                result.repo_pk,
                bucket_iso,
                meta.stars_total,
                meta.forks,
                meta.open_issues,
            )

        star_weeks = 0
        if self.config.fetch_star_history:
            try:
                weeks = fetch_star_history(
                    self.github,
                    meta.full_name,
                    weeks=self.config.star_history_weeks,
                )
                if weeks:
                    self.db.upsert_star_weekly(
                        result.repo_pk,
                        compute_weekly_rows(weeks, meta.stars_total),
                        now_iso,
                    )
                    self.db.mark_star_history_fetched(result.repo_pk, now_iso)
                    star_weeks = len(weeks)
            except GitHubRateLimitError as exc:
                logger.warning("star history rate limited for %s: %s", meta.full_name, exc)
            except (GitHubError, StarHistoryError) as exc:
                logger.warning("star history failed for %s: %s", meta.full_name, exc)

        try:
            compute_and_store_metrics(self.db, now=started)
        except Exception:  # 指标失败不应影响手动添加结果
            logger.exception("metrics computation failed")

        self.db.log_run(
            run_at=now_iso,
            run_type="add",
            source="manual",
            status="ok",
            requests=1 + (1 if star_weeks else 0),
            success=1,
            items=1,
            details={"full_name": meta.full_name, "star_weeks": star_weeks},
        )
        return True

    # -- internals ---------------------------------------------------------
    def _classify_repo(self, repo_pk: int) -> None:
        row = self.db.get_repo(repo_pk)
        if row is None:
            return
        try:
            topics = list(json.loads(row["topics"] or "[]"))
        except (TypeError, ValueError):
            topics = []
        classification = classify_repo(
            full_name=row["full_name"],
            description=row["description"],
            topics=topics,
            language=row["language"],
            is_fork=bool(row["is_fork"]),
            is_archived=bool(row["is_archived"]),
            is_disabled=bool(row["is_disabled"]),
            override=row["manual_override"],
        )
        self.db.set_classification(repo_pk, classification)

    def _enrich(
        self, summary: IngestSummary, started: datetime, started_iso: str
    ) -> None:
        stale_before = iso_z(hours_ago(started, 24))
        rows = self.db.list_repos_for_enrichment(
            self.config.enrich_limit, stale_before
        )

        for row in rows:
            try:
                meta = self.github.get_repo(row["full_name"])
            except GitHubRateLimitError as exc:
                summary.errors.append(f"enrich: {exc}")
                break
            except GitHubError as exc:
                summary.errors.append(f"enrich {row['full_name']}: {exc}")
                continue

            if meta is None:
                # 404：记录一次尝试，避免下一轮立刻重复请求。
                self.db.mark_metadata_attempt(int(row["id"]), started_iso)
                continue

            if self.config.fetch_contributors and meta.contributors is None:
                try:
                    count = self.github.get_contributors_count(row["full_name"])
                except GitHubError:
                    count = None
                if count is not None:
                    meta = replace(meta, contributors=count)

            self.db.apply_metadata(int(row["id"]), meta, started_iso)
            self._classify_repo(int(row["id"]))
            summary.enriched += 1

    def _fetch_star_history(
        self, summary: IngestSummary, started: datetime, started_iso: str
    ) -> None:
        """FR-103：为已补全元数据的仓库回填最近 N 周星标历史。"""
        stale_before = iso_z(hours_ago(started, 24))
        rows = self.db.list_repos_for_star_history(
            self.config.star_history_limit, stale_before
        )

        for row in rows:
            repo_pk = int(row["id"])
            try:
                weeks = fetch_star_history(
                    self.github,
                    row["full_name"],
                    weeks=self.config.star_history_weeks,
                )
            except GitHubRateLimitError as exc:
                summary.errors.append(f"star_history: {exc}")
                break
            except (GitHubError, StarHistoryError) as exc:
                summary.errors.append(
                    f"star_history {row['full_name']}: {exc}"
                )
                continue

            if not weeks:
                # 404 或空响应：记录一次尝试，本轮不再重复请求。
                self.db.mark_star_history_fetched(repo_pk, started_iso)
                continue

            weekly_rows = compute_weekly_rows(weeks, row["stars_total"])
            self.db.upsert_star_weekly(repo_pk, weekly_rows, started_iso)
            self.db.mark_star_history_fetched(repo_pk, started_iso)
            summary.star_history_repos += 1
            summary.star_history_weeks += len(weekly_rows)

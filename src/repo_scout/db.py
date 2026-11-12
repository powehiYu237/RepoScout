"""SQLite 持久层。

M1 使用 `repos.id` 作为内部代理主键（Trending 阶段还没有 GitHub repo_id），
`repos.repo_id` 在元数据补全后写入并保持唯一。其余表通过 `repo_pk` 引用。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .classify import Classification
from .models import RepoMetadata, TrendingEntry

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS repos (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id             INTEGER UNIQUE,
  full_name           TEXT NOT NULL,
  full_name_key       TEXT NOT NULL UNIQUE,
  owner               TEXT NOT NULL,
  name                TEXT NOT NULL,
  description         TEXT,
  html_url            TEXT NOT NULL,
  homepage            TEXT,
  language            TEXT,
  license_spdx        TEXT,
  topics              TEXT NOT NULL DEFAULT '[]',
  stars_total         INTEGER,
  forks               INTEGER,
  open_issues         INTEGER,
  contributors        INTEGER,
  size_kb             INTEGER,
  default_branch      TEXT,
  is_fork             INTEGER NOT NULL DEFAULT 0,
  is_archived         INTEGER NOT NULL DEFAULT 0,
  is_disabled         INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT,
  pushed_at           TEXT,
  first_seen_at       TEXT NOT NULL,
  last_seen_at        TEXT NOT NULL,
  metadata_fetched_at TEXT,
  star_history_fetched_at TEXT,
  category            TEXT NOT NULL DEFAULT 'normal',
  excluded            INTEGER NOT NULL DEFAULT 0,
  exclude_reason      TEXT,
  manual_override     TEXT,
  updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repos_stars ON repos(stars_total DESC);
CREATE INDEX IF NOT EXISTS idx_repos_category ON repos(category);
CREATE INDEX IF NOT EXISTS idx_repos_enrich ON repos(metadata_fetched_at);

CREATE TABLE IF NOT EXISTS trending_hits (
  repo_pk      INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  period       TEXT NOT NULL,
  captured_at  TEXT NOT NULL,
  period_stars INTEGER,
  rank         INTEGER,
  PRIMARY KEY (repo_pk, period, captured_at)
);

CREATE TABLE IF NOT EXISTS snapshots (
  repo_pk     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  captured_at TEXT NOT NULL,
  stars       INTEGER NOT NULL,
  forks       INTEGER,
  open_issues INTEGER,
  PRIMARY KEY (repo_pk, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(captured_at);

CREATE TABLE IF NOT EXISTS star_weekly (
  repo_pk     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  week_start  TEXT NOT NULL,
  delta       INTEGER NOT NULL,
  stars_end   INTEGER,
  source      TEXT NOT NULL,
  fetched_at  TEXT NOT NULL,
  PRIMARY KEY (repo_pk, week_start)
);
CREATE INDEX IF NOT EXISTS idx_star_weekly_week ON star_weekly(week_start);

CREATE TABLE IF NOT EXISTS repo_metrics (
  repo_pk                INTEGER PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,
  computed_at            TEXT NOT NULL,
  stars_total            INTEGER,
  delta_24h              INTEGER,
  delta_7d               INTEGER,
  delta_30d              INTEGER,
  velocity_7d            REAL,
  velocity_prev          REAL,
  acceleration           REAL,
  acceleration_available INTEGER NOT NULL DEFAULT 0,
  anomaly                INTEGER NOT NULL DEFAULT 0,
  burst                  INTEGER NOT NULL DEFAULT 0,
  anomaly_available      INTEGER NOT NULL DEFAULT 0,
  trend_score            REAL,
  stars_per_contributor  REAL,
  suspicious_stars       INTEGER NOT NULL DEFAULT 0,
  history_confidence     TEXT NOT NULL DEFAULT 'low',
  sources                TEXT NOT NULL DEFAULT '{}',
  updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repo_metrics_score ON repo_metrics(trend_score DESC);

CREATE TABLE IF NOT EXISTS repo_analysis (
  repo_pk                INTEGER PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,
  analyzed_at            TEXT NOT NULL,
  analysis_version       TEXT NOT NULL,
  languages              TEXT,
  loc                    INTEGER,
  loc_method             TEXT,
  loc_confidence         TEXT,
  dep_count              INTEGER,
  dev_dep_count          INTEGER,
  dep_manifest           TEXT,
  dep_confidence         TEXT,
  ext_services           TEXT,
  ext_evidence           TEXT,
  has_docker             INTEGER,
  has_compose            INTEGER,
  compose_services       INTEGER,
  has_monorepo           INTEGER,
  ci_workflows           INTEGER,
  src_files              INTEGER,
  top_dirs               INTEGER,
  tree_truncated         INTEGER NOT NULL DEFAULT 0,
  license_risk           TEXT,
  s_loc                  REAL,
  s_dep                  REAL,
  s_ext                  REAL,
  s_lang                 REAL,
  s_license              REAL,
  s_struct               REAL,
  repro_score            REAL,
  complexity_score       REAL,
  complexity_grade       TEXT,
  replication_weeks_min  REAL,
  replication_weeks_max  REAL,
  low_confidence_fields  TEXT,
  score_breakdown        TEXT NOT NULL DEFAULT '{}',
  deep_analyzed_at       TEXT,
  deep_method            TEXT,
  deep_languages         TEXT,
  deep_duration_ms       INTEGER,
  deep_error             TEXT,
  updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repo_analysis_score ON repo_analysis(repro_score DESC);

CREATE TABLE IF NOT EXISTS repo_commercial (
  repo_pk                INTEGER PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,
  analyzed_at            TEXT NOT NULL,
  commercial_version     TEXT NOT NULL,
  paid_intent_issues     TEXT,
  paid_intent_scanned    INTEGER NOT NULL DEFAULT 0,
  paid_intent_hits       INTEGER NOT NULL DEFAULT 0,
  paid_intent_score      INTEGER,
  paid_intent_evidence   TEXT,
  paid_version           TEXT,
  paid_evidence          TEXT,
  competitor_count       INTEGER,
  competitor_query       TEXT,
  competitor_checked_at  TEXT,
  audience_score         INTEGER,
  audience_topic         TEXT,
  monetization_paths     TEXT,
  gap_count              INTEGER,
  differentiation_score  INTEGER,
  market_alignment       REAL,
  whitespace             REAL,
  competition            REAL,
  biz_score              REAL,
  low_confidence_fields  TEXT,
  score_breakdown        TEXT NOT NULL DEFAULT '{}',
  updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repo_commercial_score ON repo_commercial(biz_score DESC);

CREATE TABLE IF NOT EXISTS watchlist (
  repo_pk    INTEGER PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,
  added_at   TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'watching',
  note       TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_pk    INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  verdict    TEXT NOT NULL,
  reason     TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at      TEXT NOT NULL,
  run_type    TEXT NOT NULL,
  source      TEXT,
  requests    INTEGER NOT NULL DEFAULT 0,
  success     INTEGER NOT NULL DEFAULT 0,
  items       INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL,
  error       TEXT,
  details     TEXT
);

CREATE TABLE IF NOT EXISTS api_tokens (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  label        TEXT NOT NULL,
  token_enc    BLOB NOT NULL,
  remaining    INTEGER,
  reset_at     TEXT,
  status       TEXT NOT NULL DEFAULT 'active',
  last_used_at TEXT
);
"""


@dataclass(frozen=True)
class UpsertResult:
    repo_pk: int
    is_new: bool


class Database:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self._ensure_column("repos", "star_history_fetched_at", "TEXT")
        for column, declaration in (
            ("deep_analyzed_at", "TEXT"),
            ("deep_method", "TEXT"),
            ("deep_languages", "TEXT"),
            ("deep_duration_ms", "INTEGER"),
            ("deep_error", "TEXT"),
        ):
            self._ensure_column("repo_analysis", column, declaration)
        self._ensure_column(
            "repo_commercial", "paid_intent_hits", "INTEGER NOT NULL DEFAULT 0"
        )
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        """轻量迁移：为已存在的数据库补齐新增列。"""
        columns = {
            row["name"]
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def close(self) -> None:
        self.conn.close()

    # -- repos -------------------------------------------------------------
    def upsert_trending_entry(self, entry: TrendingEntry, now_iso: str) -> UpsertResult:
        key = entry.full_name.lower()
        existing = self.conn.execute(
            "SELECT id FROM repos WHERE full_name_key = ?", (key,)
        ).fetchone()

        with self.conn:
            if existing is not None:
                self.conn.execute(
                    """
                    UPDATE repos SET
                      full_name = ?, owner = ?, name = ?, html_url = ?,
                      description = COALESCE(?, description),
                      language = COALESCE(?, language),
                      stars_total = COALESCE(?, stars_total),
                      last_seen_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        entry.full_name,
                        entry.owner,
                        entry.name,
                        entry.html_url,
                        entry.description,
                        entry.language,
                        entry.stars_total,
                        now_iso,
                        now_iso,
                        int(existing["id"]),
                    ),
                )
                return UpsertResult(repo_pk=int(existing["id"]), is_new=False)

            cursor = self.conn.execute(
                """
                INSERT INTO repos (
                  full_name, full_name_key, owner, name, description, html_url,
                  language, stars_total, first_seen_at, last_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.full_name,
                    key,
                    entry.owner,
                    entry.name,
                    entry.description,
                    entry.html_url,
                    entry.language,
                    entry.stars_total,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
            return UpsertResult(repo_pk=int(cursor.lastrowid), is_new=True)

    def get_repo(self, repo_pk: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM repos WHERE id = ?", (repo_pk,)
        ).fetchone()

    def get_repo_by_full_name(self, full_name: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM repos WHERE full_name_key = ?", (full_name.lower(),)
        ).fetchone()

    def apply_metadata(
        self, repo_pk: int, meta: RepoMetadata, fetched_at: str
    ) -> None:
        topics = json.dumps(
            sorted({topic.lower() for topic in meta.topics if topic}),
            ensure_ascii=False,
        )
        conflict = self.conn.execute(
            "SELECT id FROM repos WHERE repo_id = ? AND id != ?",
            (meta.repo_id, repo_pk),
        ).fetchone()
        include_repo_id = conflict is None

        assignments = [
            "description = COALESCE(?, description)",
            "homepage = COALESCE(?, homepage)",
            "language = COALESCE(?, language)",
            "license_spdx = COALESCE(?, license_spdx)",
            "topics = ?",
            "stars_total = COALESCE(?, stars_total)",
            "forks = COALESCE(?, forks)",
            "open_issues = COALESCE(?, open_issues)",
            "contributors = COALESCE(?, contributors)",
            "size_kb = COALESCE(?, size_kb)",
            "default_branch = COALESCE(?, default_branch)",
            "is_fork = ?",
            "is_archived = ?",
            "is_disabled = ?",
            "created_at = COALESCE(?, created_at)",
            "pushed_at = COALESCE(?, pushed_at)",
            "metadata_fetched_at = ?",
            "updated_at = ?",
        ]
        values: list = [
            meta.description,
            meta.homepage,
            meta.language,
            meta.license_spdx,
            topics,
            meta.stars_total,
            meta.forks,
            meta.open_issues,
            meta.contributors,
            meta.size_kb,
            meta.default_branch,
            int(meta.fork),
            int(meta.archived),
            int(meta.disabled),
            meta.created_at,
            meta.pushed_at,
            fetched_at,
            fetched_at,
        ]
        if include_repo_id:
            assignments.insert(0, "repo_id = ?")
            values.insert(0, meta.repo_id)
        values.append(repo_pk)

        with self.conn:
            self.conn.execute(
                f"UPDATE repos SET {', '.join(assignments)} WHERE id = ?", values
            )

    def mark_metadata_attempt(self, repo_pk: int, fetched_at: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE repos SET metadata_fetched_at = ? WHERE id = ?",
                (fetched_at, repo_pk),
            )

    def set_classification(self, repo_pk: int, classification: Classification) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE repos SET category = ?, excluded = ?, exclude_reason = ?
                WHERE id = ?
                """,
                (
                    classification.category,
                    int(classification.excluded),
                    classification.reason,
                    repo_pk,
                ),
            )

    def list_repos_for_enrichment(
        self, limit: int, stale_before: str
    ) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM repos
                WHERE excluded = 0
                  AND (metadata_fetched_at IS NULL OR metadata_fetched_at < ?)
                ORDER BY (repo_id IS NULL) DESC, stars_total DESC
                LIMIT ?
                """,
                (stale_before, limit),
            )
        )

    def list_repos_for_snapshot(self, limit: int) -> list[sqlite3.Row]:
        """快照候选：优先按 M2 TrendScore，尚未评分时退回总星数排序。"""
        return list(
            self.conn.execute(
                """
                SELECT r.* FROM repos r
                LEFT JOIN repo_metrics m ON m.repo_pk = r.id
                WHERE r.excluded = 0 AND r.stars_total IS NOT NULL
                ORDER BY (m.trend_score IS NULL) ASC,
                         m.trend_score DESC,
                         r.stars_total DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    # -- observations ------------------------------------------------------
    def record_trending_hit(
        self,
        repo_pk: int,
        period: str,
        captured_at: str,
        period_stars: int | None,
        rank: int | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO trending_hits (repo_pk, period, captured_at, period_stars, rank)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(repo_pk, period, captured_at) DO UPDATE SET
                  period_stars = excluded.period_stars,
                  rank = excluded.rank
                """,
                (repo_pk, period, captured_at, period_stars, rank),
            )

    def take_snapshot(
        self,
        repo_pk: int,
        captured_at: str,
        stars: int,
        forks: int | None = None,
        open_issues: int | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO snapshots (repo_pk, captured_at, stars, forks, open_issues)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(repo_pk, captured_at) DO UPDATE SET
                  stars = excluded.stars,
                  forks = excluded.forks,
                  open_issues = excluded.open_issues
                """,
                (repo_pk, captured_at, stars, forks, open_issues),
            )

    # -- star history (FR-103) --------------------------------------------
    def upsert_star_weekly(
        self, repo_pk: int, rows: list[dict], fetched_at: str
    ) -> None:
        if not rows:
            return
        values = [
            (
                repo_pk,
                row["week_start"],
                int(row["delta"]),
                row.get("stars_end"),
                row.get("source", "github_stargazers_history"),
                fetched_at,
            )
            for row in rows
        ]
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO star_weekly (
                  repo_pk, week_start, delta, stars_end, source, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_pk, week_start) DO UPDATE SET
                  delta = excluded.delta,
                  stars_end = excluded.stars_end,
                  source = excluded.source,
                  fetched_at = excluded.fetched_at
                """,
                values,
            )

    def mark_star_history_fetched(self, repo_pk: int, fetched_at: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE repos SET star_history_fetched_at = ? WHERE id = ?",
                (fetched_at, repo_pk),
            )

    def list_repos_for_star_history(
        self, limit: int, stale_before: str
    ) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM repos
                WHERE excluded = 0
                  AND repo_id IS NOT NULL
                  AND stars_total IS NOT NULL
                  AND (
                    star_history_fetched_at IS NULL
                    OR star_history_fetched_at < ?
                  )
                ORDER BY stars_total DESC
                LIMIT ?
                """,
                (stale_before, limit),
            )
        )

    def get_star_weekly(self, repo_pk: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM star_weekly
                WHERE repo_pk = ?
                ORDER BY week_start DESC
                """,
                (repo_pk,),
            )
        )

    def count_star_weekly(self) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) FROM star_weekly").fetchone()[0]
        )

    # -- metrics (M2) ------------------------------------------------------
    def list_metric_repos(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM repos
                WHERE excluded = 0 AND stars_total IS NOT NULL
                ORDER BY id
                """
            )
        )

    def list_metric_snapshots(self, since: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT repo_pk, captured_at, stars FROM snapshots
                WHERE captured_at >= ?
                ORDER BY repo_pk, captured_at
                """,
                (since,),
            )
        )

    def list_metric_star_weekly(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT repo_pk, week_start, delta, stars_end, fetched_at
                FROM star_weekly
                WHERE stars_end IS NOT NULL
                ORDER BY repo_pk, week_start
                """
            )
        )

    def list_latest_daily_hits(self, since: str) -> list[sqlite3.Row]:
        """近 24 小时的 daily 榜单值；按时间升序，调用方取最后一条即可。"""
        return list(
            self.conn.execute(
                """
                SELECT repo_pk, captured_at, period_stars
                FROM trending_hits
                WHERE period = 'daily' AND captured_at >= ?
                ORDER BY captured_at ASC
                """,
                (since,),
            )
        )

    def upsert_repo_metrics(self, rows: list[dict]) -> None:
        if not rows:
            with self.conn:
                self.conn.execute(
                    """
                    DELETE FROM repo_metrics
                    WHERE repo_pk IN (SELECT id FROM repos WHERE excluded = 1)
                    """
                )
            return
        values = [
            (
                row["repo_pk"],
                row["computed_at"],
                row["stars_total"],
                row["delta_24h"],
                row["delta_7d"],
                row["delta_30d"],
                row["velocity_7d"],
                row["velocity_prev"],
                row["acceleration"],
                row["acceleration_available"],
                row["anomaly"],
                row["burst"],
                row["anomaly_available"],
                row["trend_score"],
                row["stars_per_contributor"],
                row["suspicious_stars"],
                row["history_confidence"],
                row["sources"],
                row["updated_at"],
            )
            for row in rows
        ]
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO repo_metrics (
                  repo_pk, computed_at, stars_total, delta_24h, delta_7d, delta_30d,
                  velocity_7d, velocity_prev, acceleration, acceleration_available,
                  anomaly, burst, anomaly_available, trend_score,
                  stars_per_contributor, suspicious_stars, history_confidence,
                  sources, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_pk) DO UPDATE SET
                  computed_at = excluded.computed_at,
                  stars_total = excluded.stars_total,
                  delta_24h = excluded.delta_24h,
                  delta_7d = excluded.delta_7d,
                  delta_30d = excluded.delta_30d,
                  velocity_7d = excluded.velocity_7d,
                  velocity_prev = excluded.velocity_prev,
                  acceleration = excluded.acceleration,
                  acceleration_available = excluded.acceleration_available,
                  anomaly = excluded.anomaly,
                  burst = excluded.burst,
                  anomaly_available = excluded.anomaly_available,
                  trend_score = excluded.trend_score,
                  stars_per_contributor = excluded.stars_per_contributor,
                  suspicious_stars = excluded.suspicious_stars,
                  history_confidence = excluded.history_confidence,
                  sources = excluded.sources,
                  updated_at = excluded.updated_at
                """,
                values,
            )
            # 已排除的仓库不再保留评分，避免被误消费。
            self.conn.execute(
                """
                DELETE FROM repo_metrics
                WHERE repo_pk IN (SELECT id FROM repos WHERE excluded = 1)
                """
            )

    def get_repo_metrics(self, repo_pk: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM repo_metrics WHERE repo_pk = ?", (repo_pk,)
        ).fetchone()

    def count_repo_metrics(self) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) FROM repo_metrics").fetchone()[0]
        )

    def latest_metrics_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(computed_at) AS computed_at FROM repo_metrics"
        ).fetchone()
        return row["computed_at"] if row is not None else None

    # -- analysis (M3) -----------------------------------------------------
    def list_repos_for_analysis(
        self, limit: int, stale_before: str
    ) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT r.* FROM repos r
                LEFT JOIN repo_analysis a ON a.repo_pk = r.id
                WHERE r.excluded = 0
                  AND r.repo_id IS NOT NULL
                  AND (a.analyzed_at IS NULL OR a.analyzed_at < ?)
                ORDER BY (a.analyzed_at IS NOT NULL) ASC,
                         r.stars_total DESC
                LIMIT ?
                """,
                (stale_before, limit),
            )
        )

    def upsert_repo_analysis(self, row: dict) -> None:
        columns = (
            "repo_pk",
            "analyzed_at",
            "analysis_version",
            "languages",
            "loc",
            "loc_method",
            "loc_confidence",
            "dep_count",
            "dev_dep_count",
            "dep_manifest",
            "dep_confidence",
            "ext_services",
            "ext_evidence",
            "has_docker",
            "has_compose",
            "compose_services",
            "has_monorepo",
            "ci_workflows",
            "src_files",
            "top_dirs",
            "tree_truncated",
            "license_risk",
            "s_loc",
            "s_dep",
            "s_ext",
            "s_lang",
            "s_license",
            "s_struct",
            "repro_score",
            "complexity_score",
            "complexity_grade",
            "replication_weeks_min",
            "replication_weeks_max",
            "low_confidence_fields",
            "score_breakdown",
            "updated_at",
        )
        values = [row.get(column) for column in columns]
        assignments = ", ".join(
            f"{column} = excluded.{column}" for column in columns[1:]
        )
        placeholders = ", ".join("?" for _ in columns)
        with self.conn:
            self.conn.execute(
                f"""
                INSERT INTO repo_analysis ({', '.join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(repo_pk) DO UPDATE SET {assignments}
                """,
                values,
            )
            # 已排除的仓库不再保留分析结果。
            self.conn.execute(
                """
                DELETE FROM repo_analysis
                WHERE repo_pk IN (SELECT id FROM repos WHERE excluded = 1)
                """
            )

    def get_repo_analysis(self, repo_pk: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM repo_analysis WHERE repo_pk = ?", (repo_pk,)
        ).fetchone()

    def prune_repo_analysis(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                DELETE FROM repo_analysis
                WHERE repo_pk IN (SELECT id FROM repos WHERE excluded = 1)
                """
            )

    def count_fresh_analysis(self, stale_before: str) -> int:
        """读取缓存仍有效的分析结果数量，用于 analyze 的 skipped 统计。"""
        return int(
            self.conn.execute(
                """
                SELECT COUNT(*) FROM repos r
                JOIN repo_analysis a ON a.repo_pk = r.id
                WHERE r.excluded = 0 AND r.repo_id IS NOT NULL
                  AND a.analyzed_at >= ?
                """,
                (stale_before,),
            ).fetchone()[0]
        )

    def count_repo_analysis(self) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) FROM repo_analysis").fetchone()[0]
        )

    def latest_analysis_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(analyzed_at) AS analyzed_at FROM repo_analysis"
        ).fetchone()
        return row["analyzed_at"] if row is not None else None

    def list_repo_analysis_joined(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT r.full_name, r.stars_total,
                       r.language AS repo_language, r.license_spdx,
                       a.*
                FROM repo_analysis a
                JOIN repos r ON r.id = a.repo_pk
                ORDER BY a.repro_score DESC
                """
            )
        )

    def list_repos_for_deep_analysis(
        self, limit: int, stale_before: str
    ) -> list[sqlite3.Row]:
        """按浅分析 ReproScore 选 Top N，过滤最近已深分析的仓库。"""
        return list(
            self.conn.execute(
                """
                SELECT r.id, r.full_name, r.html_url, r.default_branch,
                       r.size_kb, r.stars_total,
                       a.s_dep, a.s_ext, a.s_lang, a.s_license, a.s_struct,
                       a.ext_services, a.has_monorepo, a.low_confidence_fields,
                       a.score_breakdown, a.dep_count, a.loc AS shallow_loc,
                       a.loc_method, a.repro_score AS shallow_repro,
                       a.complexity_grade AS shallow_grade,
                       a.deep_analyzed_at
                FROM repo_analysis a
                JOIN repos r ON r.id = a.repo_pk
                WHERE r.excluded = 0 AND r.repo_id IS NOT NULL
                  AND (a.deep_analyzed_at IS NULL OR a.deep_analyzed_at < ?)
                ORDER BY a.repro_score DESC, r.stars_total DESC
                LIMIT ?
                """,
                (stale_before, limit),
            )
        )

    def update_deep_analysis(self, repo_pk: int, values: dict) -> None:
        allowed = {
            "loc",
            "loc_method",
            "loc_confidence",
            "s_loc",
            "repro_score",
            "complexity_score",
            "complexity_grade",
            "replication_weeks_min",
            "replication_weeks_max",
            "score_breakdown",
            "deep_analyzed_at",
            "deep_method",
            "deep_languages",
            "deep_duration_ms",
            "deep_error",
            "updated_at",
        }
        columns = [key for key in values if key in allowed]
        if not columns:
            return
        assignments = ", ".join(f"{column} = ?" for column in columns)
        params = [values[column] for column in columns]
        params.append(repo_pk)
        with self.conn:
            self.conn.execute(
                f"UPDATE repo_analysis SET {assignments} WHERE repo_pk = ?",
                params,
            )

    def count_deep_analysis(self) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM repo_analysis WHERE deep_analyzed_at IS NOT NULL"
            ).fetchone()[0]
        )

    def latest_deep_analysis_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(deep_analyzed_at) AS analyzed_at FROM repo_analysis"
        ).fetchone()
        return row["analyzed_at"] if row is not None else None

    # -- commercial (M4) ---------------------------------------------------
    def list_repos_for_commercial(
        self, limit: int, stale_before: str
    ) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT r.id, r.full_name, r.description, r.homepage,
                       r.language, r.topics, r.stars_total,
                       a.repro_score, a.has_docker, a.has_compose, a.has_monorepo,
                       c.analyzed_at AS commercial_analyzed_at,
                       c.competitor_count AS cached_competitor_count,
                       c.competitor_query AS cached_competitor_query,
                       c.competitor_checked_at AS cached_competitor_checked_at
                FROM repos r
                JOIN repo_analysis a ON a.repo_pk = r.id
                LEFT JOIN repo_commercial c ON c.repo_pk = r.id
                WHERE r.excluded = 0 AND r.repo_id IS NOT NULL
                  AND (c.analyzed_at IS NULL OR c.analyzed_at < ?)
                ORDER BY a.repro_score DESC, r.stars_total DESC
                LIMIT ?
                """,
                (stale_before, limit),
            )
        )

    def upsert_repo_commercial(self, row: dict) -> None:
        columns = (
            "repo_pk",
            "analyzed_at",
            "commercial_version",
            "paid_intent_issues",
            "paid_intent_scanned",
            "paid_intent_hits",
            "paid_intent_score",
            "paid_intent_evidence",
            "paid_version",
            "paid_evidence",
            "competitor_count",
            "competitor_query",
            "competitor_checked_at",
            "audience_score",
            "audience_topic",
            "monetization_paths",
            "gap_count",
            "differentiation_score",
            "market_alignment",
            "whitespace",
            "competition",
            "biz_score",
            "low_confidence_fields",
            "score_breakdown",
            "updated_at",
        )
        values = [row.get(column) for column in columns]
        assignments = ", ".join(
            f"{column} = excluded.{column}" for column in columns[1:]
        )
        placeholders = ", ".join("?" for _ in columns)
        with self.conn:
            self.conn.execute(
                f"""
                INSERT INTO repo_commercial ({', '.join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(repo_pk) DO UPDATE SET {assignments}
                """,
                values,
            )
            self.conn.execute(
                """
                DELETE FROM repo_commercial
                WHERE repo_pk IN (SELECT id FROM repos WHERE excluded = 1)
                """
            )

    def get_repo_commercial(self, repo_pk: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM repo_commercial WHERE repo_pk = ?", (repo_pk,)
        ).fetchone()

    def prune_repo_commercial(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                DELETE FROM repo_commercial
                WHERE repo_pk IN (SELECT id FROM repos WHERE excluded = 1)
                """
            )

    def count_repo_commercial(self) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) FROM repo_commercial").fetchone()[0]
        )

    def latest_commercial_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(analyzed_at) AS analyzed_at FROM repo_commercial"
        ).fetchone()
        return row["analyzed_at"] if row is not None else None

    def list_metric_delta7(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT repo_pk, delta_7d FROM repo_metrics WHERE delta_7d IS NOT NULL"
            )
        )

    def count_competitor_checks_today(self, day: str) -> int:
        return int(
            self.conn.execute(
                """
                SELECT COUNT(*) FROM repo_commercial
                WHERE competitor_checked_at LIKE ?
                """,
                (f"{day}%",),
            ).fetchone()[0]
        )

    def list_repo_commercial_joined(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT r.full_name, r.stars_total,
                       a.repro_score,
                       c.*
                FROM repo_commercial c
                JOIN repos r ON r.id = c.repo_pk
                LEFT JOIN repo_analysis a ON a.repo_pk = c.repo_pk
                ORDER BY c.biz_score DESC
                """
            )
        )

    # -- M5 read models / watchlist ---------------------------------------
    _SCORED_SELECT = """
        SELECT r.*,
               m.delta_24h, m.delta_7d, m.delta_30d,
               m.velocity_7d, m.velocity_prev, m.acceleration,
               m.acceleration_available, m.anomaly, m.burst,
               m.anomaly_available, m.trend_score,
               m.stars_per_contributor, m.suspicious_stars,
               m.history_confidence, m.computed_at AS metrics_at,
               a.loc, a.loc_method, a.loc_confidence,
               a.dep_count, a.dev_dep_count, a.dep_manifest,
               a.ext_services, a.has_docker, a.has_compose, a.has_monorepo,
               a.ci_workflows, a.src_files, a.top_dirs, a.license_risk,
               a.s_loc, a.s_dep, a.s_ext, a.s_lang, a.s_license, a.s_struct,
               a.repro_score, a.complexity_score, a.complexity_grade,
               a.replication_weeks_min, a.replication_weeks_max,
               a.low_confidence_fields AS analysis_low_confidence,
               a.score_breakdown AS analysis_breakdown,
               a.deep_analyzed_at, a.analyzed_at AS analysis_at,
               c.paid_intent_score, c.paid_intent_hits, c.paid_intent_issues,
               c.paid_version, c.paid_evidence, c.competitor_count,
               c.competitor_query, c.audience_score, c.audience_topic,
               c.monetization_paths, c.gap_count, c.differentiation_score,
               c.market_alignment, c.whitespace, c.competition, c.biz_score,
               c.low_confidence_fields AS commercial_low_confidence,
               c.score_breakdown AS commercial_breakdown,
               c.analyzed_at AS commercial_at,
               w.added_at AS watched_at, w.status AS watch_status,
               w.note AS watch_note
        FROM repos r
        LEFT JOIN repo_metrics m ON m.repo_pk = r.id
        LEFT JOIN repo_analysis a ON a.repo_pk = r.id
        LEFT JOIN repo_commercial c ON c.repo_pk = r.id
        LEFT JOIN watchlist w ON w.repo_pk = r.id
    """

    def list_scored_repos(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(self._SCORED_SELECT + " ORDER BY r.id"))

    def get_scored_repo(self, repo_pk: int) -> sqlite3.Row | None:
        return self.conn.execute(
            self._SCORED_SELECT + " WHERE r.id = ?", (repo_pk,)
        ).fetchone()

    def list_repo_snapshots(self, repo_pk: int, since: str | None = None):
        if since is None:
            return list(
                self.conn.execute(
                    """
                    SELECT captured_at, stars, forks, open_issues
                    FROM snapshots WHERE repo_pk = ?
                    ORDER BY captured_at
                    """,
                    (repo_pk,),
                )
            )
        return list(
            self.conn.execute(
                """
                SELECT captured_at, stars, forks, open_issues
                FROM snapshots WHERE repo_pk = ? AND captured_at >= ?
                ORDER BY captured_at
                """,
                (repo_pk, since),
            )
        )

    def latest_snapshot_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(captured_at) AS captured_at FROM snapshots"
        ).fetchone()
        return row["captured_at"] if row is not None else None

    def get_watch(self, repo_pk: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM watchlist WHERE repo_pk = ?", (repo_pk,)
        ).fetchone()

    def upsert_watch(
        self, repo_pk: int, *, added_at: str, status: str, note: str | None
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO watchlist (repo_pk, added_at, status, note, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(repo_pk) DO UPDATE SET
                  status = excluded.status,
                  note = excluded.note,
                  updated_at = excluded.updated_at
                """,
                (repo_pk, added_at, status, note, added_at),
            )

    def remove_watch(self, repo_pk: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM watchlist WHERE repo_pk = ?", (repo_pk,))

    def add_feedback(
        self, repo_pk: int, verdict: str, reason: str | None, created_at: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO feedback (repo_pk, verdict, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (repo_pk, verdict, reason, created_at),
            )

    # -- run log / status --------------------------------------------------
    def log_run(
        self,
        *,
        run_at: str,
        run_type: str,
        status: str,
        requests: int = 0,
        success: int = 0,
        items: int = 0,
        duration_ms: int = 0,
        source: str | None = None,
        error: str | None = None,
        details: dict | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO ingest_runs (
                  run_at, run_type, source, requests, success, items,
                  duration_ms, status, error, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_at,
                    run_type,
                    source,
                    requests,
                    success,
                    items,
                    duration_ms,
                    status,
                    error,
                    json.dumps(details, ensure_ascii=False) if details else None,
                ),
            )

    def latest_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def latest_run_for_type(self, run_type: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM ingest_runs WHERE run_type = ? ORDER BY id DESC LIMIT 1",
            (run_type,),
        ).fetchone()

    def count_repos(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0])

    def count_excluded(self) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM repos WHERE excluded = 1"
            ).fetchone()[0]
        )

    def count_snapshots(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])

    def count_trending_hits(self) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) FROM trending_hits").fetchone()[0]
        )

    def status(self) -> dict:
        run = self.latest_run()
        return {
            "repos": self.count_repos(),
            "excluded": self.count_excluded(),
            "snapshots": self.count_snapshots(),
            "trending_hits": self.count_trending_hits(),
            "star_weeks": self.count_star_weekly(),
            "metrics": self.count_repo_metrics(),
            "metrics_computed_at": self.latest_metrics_at(),
            "analysis": self.count_repo_analysis(),
            "analysis_at": self.latest_analysis_at(),
            "deep_analysis": self.count_deep_analysis(),
            "deep_analysis_at": self.latest_deep_analysis_at(),
            "commercial": self.count_repo_commercial(),
            "commercial_at": self.latest_commercial_at(),
            "last_run": dict(run) if run is not None else None,
        }

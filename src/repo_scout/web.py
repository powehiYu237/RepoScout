"""M5 Web 应用：本地只读数据 API + 单页应用。

不引入 Web 框架：``ThreadingHTTPServer`` + SQLite 读取层，静态资源来自
``repo_scout/web/``。服务只监听本机，所有数据来自本地数据库。
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .config import Config
from .db import Database
from .jobs import ACTION_LABELS, JobManager
from .scoring import (
    DEFAULT_MIN_STARS,
    GATE_INFO,
    PRESETS,
    apply_filters,
    detail_payload,
    gate_counts,
    paginate,
    score_rows,
    sort_items,
)
from .util import iso_z, utc_now

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/styles.css": "styles.css",
    "/favicon.svg": "favicon.svg",
}
WATCH_STATUSES = {"watching", "investigating", "planned", "abandoned"}


def _now() -> datetime:
    return utc_now()


def _query(params: Mapping[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = params.get(key)
    return values[0] if values else default


def _bool_query(params: Mapping[str, list[str]], key: str) -> bool:
    value = _query(params, key, "")
    return str(value).lower() in {"1", "true", "yes", "on"}


def _int_query(params: Mapping[str, list[str]], key: str, default: int | None) -> int | None:
    value = _query(params, key)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _preset_for(params: Mapping[str, list[str]]) -> dict[str, Any]:
    key = _query(params, "preset", "balanced") or "balanced"
    return PRESETS.get(key, PRESETS["balanced"])


def _score_context(db: Database, params: Mapping[str, list[str]]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    preset = _preset_for(params)
    min_stars = _int_query(params, "min_stars", DEFAULT_MIN_STARS) or 0
    rows = db.list_scored_repos()
    items = score_rows(
        rows,
        now=_now(),
        alpha=float(preset["alpha"]),
        min_stars=min_stars,
    )
    meta = build_meta(db, items, preset, min_stars)
    return items, preset, meta


def build_meta(
    db: Database,
    items: list[dict[str, Any]],
    preset: Mapping[str, Any],
    min_stars: int,
) -> dict[str, Any]:
    counts = gate_counts(items, min_stars)
    snapshot_at = db.latest_snapshot_at()
    stale = True
    snapshot_age_hours = None
    if snapshot_at:
        try:
            reference = datetime.fromisoformat(str(snapshot_at).replace("Z", "+00:00"))
            snapshot_age_hours = round(
                (_now() - reference).total_seconds() / 3600, 1
            )
            stale = snapshot_age_hours > 12
        except ValueError:
            stale = True
    languages: dict[str, int] = {}
    licenses: dict[str, int] = {}
    paid_versions: dict[str, int] = {}
    grades: dict[str, int] = {}
    for item in items:
        language = item["language"] or "未标注"
        languages[language] = languages.get(language, 0) + 1
        risk = item["license"]["risk"]
        licenses[risk] = licenses.get(risk, 0) + 1
        paid = item["biz"]["paid_version"]
        paid_versions[paid] = paid_versions.get(paid, 0) + 1
        grade = item["repro"]["grade"].rstrip("*") or "—"
        grades[grade] = grades.get(grade, 0) + 1
    last_run = db.latest_run()
    return {
        "scoring_version": items[0]["scoring_version"] if items else "m5-v1.0.0",
        "preset": dict(preset),
        "presets": list(PRESETS.values()),
        "min_stars": min_stars,
        "candidate_pool": counts["candidate_pool"],
        "visible_default": counts["visible_default"],
        "gates": {
            key: {**GATE_INFO[key], "count": counts.get(key, 0)}
            for key in GATE_INFO
        },
        "snapshot_at": snapshot_at,
        "snapshot_age_hours": snapshot_age_hours,
        "stale": stale,
        "last_run": (
            {
                "run_at": last_run["run_at"],
                "status": last_run["status"],
                "error": last_run["error"],
                "items": last_run["items"],
                "requests": last_run["requests"],
            }
            if last_run is not None
            else None
        ),
        "metrics_at": db.latest_metrics_at() if hasattr(db, "latest_metrics_at") else None,
        "analysis_at": db.latest_analysis_at() if hasattr(db, "latest_analysis_at") else None,
        "deep_analysis_at": db.latest_deep_analysis_at()
        if hasattr(db, "latest_deep_analysis_at")
        else None,
        "commercial_at": db.latest_commercial_at()
        if hasattr(db, "latest_commercial_at")
        else None,
        "facets": {
            "languages": dict(sorted(languages.items(), key=lambda pair: (-pair[1], pair[0]))),
            "licenses": licenses,
            "paid_versions": paid_versions,
            "grades": dict(sorted(grades.items())),
        },
        "watched": sum(1 for item in items if item["watch"]["watched"]),
        "competitors_known": sum(
            1 for item in items if item["biz"]["competitor_count"] is not None
        ),
    }


def _list_payload(db: Database, params: Mapping[str, list[str]]) -> dict[str, Any]:
    items, preset, meta = _score_context(db, params)
    filtered = apply_filters(items, params, preset=preset)
    sort = _query(params, "sort", preset.get("sort", "total")) or "total"
    order = _query(params, "order", "desc") or "desc"
    filtered = sort_items(filtered, sort, order)
    page = _int_query(params, "page", 1) or 1
    page_size = _int_query(params, "page_size", 50) or 50
    page_items, total, size = paginate(filtered, page, page_size)
    meta["filtered_total"] = total
    meta["sort"] = sort
    meta["order"] = order
    meta["page"] = page
    meta["page_size"] = size
    return {"meta": meta, "items": page_items, "total": total}


def _daily_payload(db: Database) -> dict[str, Any]:
    params = {"preset": ["balanced"], "min_stars": [str(DEFAULT_MIN_STARS)]}
    items, preset, meta = _score_context(db, params)
    filtered = [item for item in items if item["visible_by_default"]]
    ranked = sort_items(filtered, "total", "desc")[:5]
    meta["filtered_total"] = len(filtered)
    return {"meta": meta, "items": ranked}


def _age_hours(value: str | None) -> float | None:
    if not value:
        return None
    try:
        reference = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((_now() - reference).total_seconds() / 3600, 1)


def _pipeline_stage_status(
    *,
    count: int,
    updated_at: str | None,
    threshold_hours: float,
    action: str,
    active: Mapping[str, Any] | None,
    last_status: str | None = None,
) -> str:
    if active and active.get("action") in (action, "full"):
        return "running"
    if last_status in ("failed", "partial"):
        return "error" if last_status == "failed" else "warn"
    if count <= 0:
        return "empty"
    age = _age_hours(updated_at)
    if age is None or age > threshold_hours:
        return "stale"
    return "ok"


def _pipeline_payload(db: Database, server: Any) -> dict[str, Any]:
    status = db.status()
    last_run = status.get("last_run") or {}
    ingest_run = db.latest_run_for_type("ingest")
    ingest_at = ingest_run["run_at"] if ingest_run is not None else None
    ingest_status = ingest_run["status"] if ingest_run is not None else None
    active = server.jobs.active_job()
    jobs = server.jobs.list_jobs(12)
    config = server.config

    stages = [
        {
            "key": "ingest",
            "label": "采集",
            "action": "ingest",
            "count": status.get("repos", 0),
            "updated_at": ingest_at,
            "detail": (
                f"{status.get('repos', 0)} 个仓库 · "
                f"{status.get('trending_hits', 0)} 次榜单命中 · "
                f"{status.get('star_weeks', 0)} 周星标历史"
            ),
            "run_label": "重新采集" if status.get("repos") else "开始采集",
            "threshold_hours": 12,
            "last_status": ingest_status,
        },
        {
            "key": "metrics",
            "label": "M2 指标",
            "action": "metrics",
            "count": status.get("metrics", 0),
            "updated_at": status.get("metrics_computed_at"),
            "detail": f"{status.get('metrics', 0)} 个仓库已计算增长指标",
            "run_label": "重算指标" if status.get("metrics") else "计算指标",
            "threshold_hours": 24,
            "last_status": None,
        },
        {
            "key": "analyze",
            "label": "M3 浅分析",
            "action": "analyze",
            "count": status.get("analysis", 0),
            "updated_at": status.get("analysis_at"),
            "detail": f"{status.get('analysis', 0)} 个仓库已完成 LoC/依赖/结构分析",
            "run_label": "重新浅分析" if status.get("analysis") else "运行浅分析",
            "threshold_hours": 24,
            "last_status": None,
        },
        {
            "key": "deep",
            "label": "M3 深分析",
            "action": "deep-analyze",
            "count": status.get("deep_analysis", 0),
            "updated_at": status.get("deep_analysis_at"),
            "detail": f"{status.get('deep_analysis', 0)} 个仓库已完成 cloc 深分析",
            "run_label": "重新深分析" if status.get("deep_analysis") else "运行深分析",
            "threshold_hours": 168,
            "last_status": None,
        },
        {
            "key": "biz",
            "label": "M4 商业分析",
            "action": "biz-analyze",
            "count": status.get("commercial", 0),
            "updated_at": status.get("commercial_at"),
            "detail": f"{status.get('commercial', 0)} 个仓库已完成商业信号分析",
            "run_label": "重新商业分析" if status.get("commercial") else "运行商业分析",
            "threshold_hours": 168,
            "last_status": None,
        },
    ]
    for stage in stages:
        stage["age_hours"] = _age_hours(stage["updated_at"])
        stage["status"] = _pipeline_stage_status(
            count=int(stage["count"] or 0),
            updated_at=stage["updated_at"],
            threshold_hours=stage["threshold_hours"],
            action=stage["action"],
            active=active,
            last_status=stage.get("last_status"),
        )
        stage.pop("last_status", None)

    warnings: list[str] = []
    if not config.github_tokens:
        warnings.append("未配置 GitHub Token：M1/M3/M4 的 API 请求会受 60 次/小时匿名配额限制。")
    if status.get("repos", 0) == 0:
        warnings.append("数据库为空：先运行「采集」；如果 github.com 网页不可达，请先配置代理。")
    if status.get("repos", 0) and not status.get("metrics", 0):
        warnings.append("已有仓库但 M2 指标为空：运行「M2 指标」。")
    if status.get("analysis", 0) and not status.get("commercial", 0):
        warnings.append("已有浅分析但 M4 商业分析为空：运行「M4 商业分析」。")
    if ingest_run is not None and ingest_run["status"] == "failed":
        warnings.append(
            f"最近一次采集失败：{ingest_run['error'] or '未知原因'}"
        )

    return {
        "stages": stages,
        "active": active,
        "jobs": jobs,
        "actions": ACTION_LABELS,
        "last_run": last_run,
        "config": {
            "analysis_limit": config.analysis_limit,
            "deep_limit": config.deep_limit,
            "biz_limit": config.biz_limit,
            "deep_max_size_mb": config.deep_max_size_mb,
            "deep_timeout_seconds": config.deep_timeout_seconds,
            "competition_min_repro": config.biz_competition_min_repro,
            "competition_daily_budget": config.biz_competition_daily_budget,
            "token_configured": bool(config.github_tokens),
            "db_path": str(config.db_path),
        },
        "warnings": warnings,
    }


def _repo_detail(db: Database, repo_pk: int, params: Mapping[str, list[str]]) -> dict[str, Any] | None:
    row = db.get_scored_repo(repo_pk)
    if row is None:
        return None
    preset = _preset_for(params)
    min_stars = _int_query(params, "min_stars", DEFAULT_MIN_STARS) or 0
    now = _now()
    item = score_rows(
        [row], now=now, alpha=float(preset["alpha"]), min_stars=min_stars
    )[0]
    payload = detail_payload(db, item, row, now=now)
    payload["meta"] = build_meta(db, [item], preset, min_stars)
    return payload


def _export_rows(db: Database, params: Mapping[str, list[str]]) -> tuple[list[dict[str, Any]], str, str]:
    items, preset, _ = _score_context(db, params)
    filtered = apply_filters(items, params, preset=preset)
    sort = _query(params, "sort", preset.get("sort", "total")) or "total"
    order = _query(params, "order", "desc") or "desc"
    filtered = sort_items(filtered, sort, order)
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "full_name",
            "url",
            "description",
            "language",
            "stars",
            "delta_24h",
            "delta_7d",
            "delta_30d",
            "velocity_7d",
            "acceleration",
            "repro_score",
            "complexity_grade",
            "biz_score",
            "total_score",
            "license",
            "license_risk",
            "paid_version",
            "competitor_count",
            "audience_score",
            "monetization_paths",
            "risks",
            "reason",
        ]
    )
    for item in filtered:
        writer.writerow(
            [
                item["full_name"],
                item["url"],
                item["description"] or "",
                item["language"] or "",
                item["stars"],
                item["growth"]["d24"] or "",
                item["growth"]["d7"] or "",
                item["growth"]["d30"] or "",
                item["growth"]["velocity"] or "",
                item["growth"]["acceleration"] or "",
                item["scores"]["repro"],
                item["repro"]["grade"],
                item["scores"]["biz"],
                item["scores"]["total"],
                item["license"]["spdx"] or "",
                item["license"]["risk"],
                item["biz"]["paid_version"],
                item["biz"]["competitor_count"]
                if item["biz"]["competitor_count"] is not None
                else "",
                item["biz"]["audience"],
                " / ".join(item["biz"]["monetization_paths"]),
                " / ".join(risk["label"] for risk in item["risks"]),
                item["reason"],
            ]
        )
    filename = f"repo-scout-{datetime.now(timezone.utc):%Y%m%d}.csv"
    return filtered, buffer.getvalue(), filename


class RepoScoutServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        db_path: str | Path,
        config: Config | None = None,
    ):
        self.db_path = str(db_path)
        self.config = config or Config(db_path=Path(db_path))
        self.jobs = JobManager(self.db_path, self.config)
        super().__init__(address, RepoScoutHandler)


class RepoScoutHandler(BaseHTTPRequestHandler):
    server_version = "RepoScout/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    # -- helpers -----------------------------------------------------------
    def _db(self) -> Database:
        return Database(self.server.db_path)  # type: ignore[attr-defined]

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; img-src 'self' data:; connect-src 'self'",
        )
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status=status)

    # -- HTTP --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        db = self._db()
        try:
            if path == "/api/repos":
                self._json(_list_payload(db, params))
                return
            if path == "/api/daily":
                self._json(_daily_payload(db))
                return
            if path == "/api/pipeline":
                self._json(_pipeline_payload(db, self.server))
                return
            if path == "/api/jobs":
                self._json(
                    {
                        "active": self.server.jobs.active_job(),
                        "jobs": self.server.jobs.list_jobs(20),
                        "actions": ACTION_LABELS,
                    }
                )
                return
            if path.startswith("/api/jobs/"):
                try:
                    job_id = int(path.rsplit("/", 1)[1])
                except ValueError:
                    self._error(400, "invalid job id")
                    return
                job = self.server.jobs.get(job_id)
                if job is None:
                    self._error(404, "job not found")
                    return
                self._json(job)
                return
            if path.startswith("/api/repos/"):
                try:
                    repo_pk = int(path.rsplit("/", 1)[1])
                except ValueError:
                    self._error(400, "invalid repo id")
                    return
                payload = _repo_detail(db, repo_pk, params)
                if payload is None:
                    self._error(404, "repo not found")
                    return
                self._json(payload)
                return
            if path == "/api/export.csv":
                _, content, filename = _export_rows(db, params)
                self._send(
                    200,
                    content.encode("utf-8"),
                    "text/csv; charset=utf-8",
                    extra_headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    },
                )
                return
            if path in STATIC_FILES:
                self._serve_static(STATIC_FILES[path])
                return
            self._error(404, "not found")
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._error(500, f"server error: {exc}")
        finally:
            db.close()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self._error(413, "payload too large")
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._error(400, "invalid JSON")
            return
        if not isinstance(body, dict):
            self._error(400, "JSON body must be an object")
            return
        db = self._db()
        try:
            if parsed.path == "/api/watch":
                self._handle_watch(db, body)
                return
            if parsed.path == "/api/feedback":
                self._handle_feedback(db, body)
                return
            if parsed.path == "/api/jobs":
                self._handle_job_submit(body)
                return
            self._error(404, "not found")
        except Exception as exc:  # pragma: no cover
            self._error(500, f"server error: {exc}")
        finally:
            db.close()

    def _handle_watch(self, db: Database, body: Mapping[str, Any]) -> None:
        try:
            repo_pk = int(body.get("repo_pk"))
        except (TypeError, ValueError):
            self._error(400, "repo_pk is required")
            return
        action = str(body.get("action") or "add")
        if action == "remove":
            db.remove_watch(repo_pk)
            self._json({"ok": True, "watch": {"watched": False}})
            return
        status = str(body.get("status") or "watching")
        if status not in WATCH_STATUSES:
            self._error(400, "invalid watch status")
            return
        note = body.get("note")
        note = str(note)[:500] if note is not None else None
        db.upsert_watch(
            repo_pk,
            added_at=iso_z(_now()),
            status=status,
            note=note,
        )
        watch = db.get_watch(repo_pk)
        self._json(
            {
                "ok": True,
                "watch": {
                    "watched": True,
                    "status": watch["status"] if watch else status,
                    "note": watch["note"] if watch else note,
                    "added_at": watch["added_at"] if watch else None,
                },
            }
        )

    def _handle_feedback(self, db: Database, body: Mapping[str, Any]) -> None:
        try:
            repo_pk = int(body.get("repo_pk"))
        except (TypeError, ValueError):
            self._error(400, "repo_pk is required")
            return
        verdict = str(body.get("verdict") or "")
        if verdict not in {"useful", "not_useful"}:
            self._error(400, "verdict must be useful or not_useful")
            return
        reason = body.get("reason")
        db.add_feedback(
            repo_pk,
            verdict,
            str(reason)[:500] if reason is not None else None,
            iso_z(_now()),
        )
        self._json({"ok": True})

    def _handle_job_submit(self, body: Mapping[str, Any]) -> None:
        action = str(body.get("action") or "")
        options = body.get("options") or {}
        if not isinstance(options, Mapping):
            self._error(400, "options must be an object")
            return
        try:
            job = self.server.jobs.submit(action, options)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._json({"ok": True, "job": job}, status=202)

    def _serve_static(self, name: str) -> None:
        path = WEB_DIR / name
        if not path.is_file():
            self._error(404, "static asset missing")
            return
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
        ):
            content_type += "; charset=utf-8"
        body = path.read_bytes()
        self._send(200, body, content_type)


def serve(
    db_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    config: Config | None = None,
) -> int:
    db = Database(db_path)
    db.init_schema()
    db.close()
    server = RepoScoutServer((host, port), db_path, config=config)
    url = f"http://{host}:{port}/"
    print(f"RepoScout Web 已启动：{url}")
    print("按 Ctrl+C 停止。")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.jobs.close()
        server.server_close()
    return 0

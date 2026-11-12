"""M5 Web 控制台的本地任务队列。

抓取、指标、浅/深分析、商业分析都可能在数秒到数分钟内完成，因此放在
单线程后台队列中执行；HTTP 请求只负责提交任务和轮询状态。任务状态保存在
进程内（重启后丢失），业务结果仍写入 SQLite。
"""

from __future__ import annotations

import copy
import logging
import queue
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .analysis import run_analysis
from .commercial import run_commercial_analysis
from .config import Config
from .db import Database
from .deep import run_deep_analysis
from .github import GitHubClient, TokenPool
from .http import CountingTransport, HttpxTransport
from .metrics import compute_and_store_metrics
from .pipeline import IngestPipeline
from .util import iso_z, utc_now

logger = logging.getLogger(__name__)

ACTION_LABELS = {
    "ingest": "采集",
    "metrics": "M2 指标",
    "analyze": "M3 浅分析",
    "deep-analyze": "M3 深分析",
    "biz-analyze": "M4 商业分析",
    "full": "全流程",
    "add": "手动添加",
}
ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"success", "partial", "failed"}


class JobManager:
    def __init__(self, db_path: str | Path, config: Config) -> None:
        self.db_path = str(db_path)
        self.config = config
        self._jobs: dict[int, dict[str, Any]] = {}
        self._order: list[int] = []
        self._lock = threading.Lock()
        self._counter = 0
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._worker, name="repo-scout-jobs", daemon=True
        )
        self._thread.start()

    # -- public API --------------------------------------------------------
    def submit(self, action: str, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if action not in ACTION_LABELS:
            raise ValueError(f"unsupported action: {action}")
        if action == "add":
            repo = str((options or {}).get("repo") or "").strip()
            if not repo:
                raise ValueError("add action requires options.repo")
        with self._lock:
            self._counter += 1
            job_id = self._counter
            job = {
                "id": job_id,
                "action": action,
                "label": ACTION_LABELS[action],
                "status": "queued",
                "stage": "排队中",
                "progress": 0.0,
                "created_at": iso_z(utc_now()),
                "started_at": None,
                "finished_at": None,
                "logs": [],
                "result": None,
                "error": None,
                "options": dict(options or {}),
            }
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._queue.put(job_id)
            return copy.deepcopy(job)

    def get(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            ids = self._order[-limit:]
            return [copy.deepcopy(self._jobs[job_id]) for job_id in reversed(ids)]

    def active_job(self) -> dict[str, Any] | None:
        with self._lock:
            for job_id in self._order:
                job = self._jobs[job_id]
                if job["status"] in ACTIVE_STATUSES:
                    return copy.deepcopy(job)
        return None

    def close(self) -> None:
        self._queue.put(None)

    # -- worker ------------------------------------------------------------
    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                break
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    continue
                job["status"] = "running"
                job["started_at"] = iso_z(utc_now())
                job["stage"] = "启动任务"
                action = job["action"]
                options = dict(job["options"])
            try:
                status, result = self._run(job_id, action, options)
            except Exception as exc:  # noqa: BLE001 - job boundary
                logger.exception("job %s failed", job_id)
                status = "failed"
                result = None
                with self._lock:
                    self._jobs[job_id]["error"] = f"{exc}\n{traceback.format_exc()[-1500:]}"
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = status
                job["progress"] = 1.0
                job["finished_at"] = iso_z(utc_now())
                if status == "success":
                    job["stage"] = "完成"
                elif status == "partial":
                    job["stage"] = "部分完成"
                else:
                    job["stage"] = "失败"
                job["result"] = result

    # -- job internals -----------------------------------------------------
    def _progress(self, job_id: int, label: str, value: float) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["stage"] = label
            job["progress"] = max(0.0, min(1.0, float(value)))
            logs = job["logs"]
            if not logs or logs[-1]["message"] != label:
                logs.append({"at": iso_z(utc_now()), "message": label})
                if len(logs) > 200:
                    del logs[: len(logs) - 200]

    def _run(
        self, job_id: int, action: str, options: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any] | None]:
        db = Database(self.db_path)
        db.init_schema()
        try:
            progress = lambda label, value: self._progress(job_id, label, value)
            if action == "ingest":
                return self._run_ingest(db, options, progress)
            if action == "add":
                return self._run_add(db, options, progress)
            if action == "metrics":
                return self._run_metrics(db, progress)
            if action == "analyze":
                return self._run_analyze(db, options, progress)
            if action == "deep-analyze":
                return self._run_deep(db, options, progress)
            if action == "biz-analyze":
                return self._run_biz(db, options, progress)
            if action == "full":
                return self._run_full(db, options, progress)
            raise ValueError(f"unsupported action: {action}")
        finally:
            db.close()

    def _run_ingest(
        self, db: Database, options: Mapping[str, Any], progress
    ) -> tuple[str, dict[str, Any]]:
        transport = HttpxTransport(self.config.user_agent, self.config.timeout)
        try:
            run_config = replace(
                self.config,
                fetch_star_history=bool(options.get("star_history", True)),
            )
            pipeline = IngestPipeline(db, transport, run_config)
            summary = pipeline.run(
                periods=options.get("periods") or None,
                enrich=bool(options.get("enrich", True)),
                snapshot=bool(options.get("snapshot", True)),
                progress=progress,
            )
            return self._status(summary.status), summary.as_dict()
        finally:
            transport.close()

    def _run_add(
        self, db: Database, options: Mapping[str, Any], progress
    ) -> tuple[str, dict[str, Any]]:
        transport = HttpxTransport(self.config.user_agent, self.config.timeout)
        try:
            pipeline = IngestPipeline(db, transport, self.config)
            full_name = str(options.get("repo") or "").strip()
            repo = _normalize_repo(full_name)
            progress("添加仓库", 0.1)
            added = pipeline.add_repo(repo)
            progress("添加完成", 1.0)
            return ("success" if added else "failed"), {
                "added": added,
                "full_name": repo,
            }
        finally:
            transport.close()

    def _run_metrics(self, db: Database, progress) -> tuple[str, dict[str, Any]]:
        progress("计算 M2 指标", 0.05)
        items = compute_and_store_metrics(db, now=utc_now())
        result = {
            "scored": len(items),
            "delta_24h": sum(1 for item in items if item.delta_24h is not None),
            "delta_7d": sum(1 for item in items if item.delta_7d is not None),
            "delta_30d": sum(1 for item in items if item.delta_30d is not None),
            "acceleration": sum(1 for item in items if item.acceleration_available),
            "anomaly": sum(1 for item in items if item.anomaly_available),
        }
        progress("M2 指标完成", 1.0)
        return "success", result

    def _run_analyze(
        self, db: Database, options: Mapping[str, Any], progress
    ) -> tuple[str, dict[str, Any]]:
        transport = HttpxTransport(self.config.user_agent, self.config.timeout)
        try:
            client = self._client(transport)
            limit = _int_option(options.get("limit"), self.config.analysis_limit)
            summary = run_analysis(
                db,
                client,
                limit=limit,
                refresh_hours=self.config.analysis_refresh_hours,
                force=bool(options.get("force")),
                tech_stack=self.config.tech_stack,
                fetch_readme=self.config.fetch_readme,
                progress=progress,
            )
            payload = summary.as_dict(top=5)
            payload["results_total"] = len(summary.results)
            return self._analysis_status(summary), payload
        finally:
            transport.close()

    def _run_deep(
        self, db: Database, options: Mapping[str, Any], progress
    ) -> tuple[str, dict[str, Any]]:
        transport = HttpxTransport(self.config.user_agent, self.config.timeout)
        try:
            client = self._client(transport)
            limit = _int_option(options.get("limit"), self.config.deep_limit)
            summary = run_deep_analysis(
                db,
                self.config,
                limit=limit,
                force=bool(options.get("force")),
                client=client,
                progress=progress,
            )
            payload = summary.as_dict()
            payload["results_total"] = len(summary.results)
            payload["results"] = payload["results"][:5]
            return self._deep_status(summary), payload
        finally:
            transport.close()

    def _run_biz(
        self, db: Database, options: Mapping[str, Any], progress
    ) -> tuple[str, dict[str, Any]]:
        transport = HttpxTransport(self.config.user_agent, self.config.timeout)
        try:
            client = self._client(transport)
            limit = _int_option(options.get("limit"), self.config.biz_limit)
            summary = run_commercial_analysis(
                db,
                client,
                self.config,
                limit=limit,
                force=bool(options.get("force")),
                competition=bool(options.get("competition", True)),
                progress=progress,
            )
            payload = summary.as_dict()
            payload["results_total"] = len(summary.results)
            payload["results"] = payload["results"][:5]
            return self._commercial_status(summary), payload
        finally:
            transport.close()

    def _run_full(
        self, db: Database, options: Mapping[str, Any], progress
    ) -> tuple[str, dict[str, Any]]:
        results: dict[str, Any] = {}
        statuses: list[str] = []
        errors: list[str] = []
        transport = HttpxTransport(self.config.user_agent, self.config.timeout)
        try:
            client = self._client(transport)
            run_config = self.config

            def stage(base: float, span: float):
                return lambda label, value: progress(
                    label, base + span * max(0.0, min(1.0, value))
                )

            # 1. 采集
            pipeline = IngestPipeline(db, transport, run_config)
            ingest_summary = pipeline.run(
                periods=options.get("periods") or None,
                progress=stage(0.0, 0.35),
            )
            results["ingest"] = ingest_summary.as_dict()
            statuses.append(self._status(ingest_summary.status))
            errors.extend(ingest_summary.errors)

            # 2. M2
            progress("计算 M2 指标", 0.38)
            metric_items = compute_and_store_metrics(db, now=utc_now())
            results["metrics"] = {"scored": len(metric_items)}
            statuses.append("success")

            # 3. M3 浅分析
            analyze_summary = run_analysis(
                db,
                client,
                limit=_int_option(
                    options.get("analyze_limit"), self.config.analysis_limit
                ),
                refresh_hours=self.config.analysis_refresh_hours,
                force=bool(options.get("force")),
                tech_stack=self.config.tech_stack,
                fetch_readme=self.config.fetch_readme,
                progress=stage(0.4, 0.25),
            )
            results["analyze"] = analyze_summary.as_dict(top=5)
            statuses.append(self._analysis_status(analyze_summary))
            errors.extend(analyze_summary.errors)

            # 4. 可选深分析
            if bool(options.get("deep")):
                deep_summary = run_deep_analysis(
                    db,
                    self.config,
                    limit=_int_option(options.get("deep_limit"), self.config.deep_limit),
                    force=bool(options.get("force")),
                    client=client,
                    progress=stage(0.65, 0.15),
                )
                results["deep"] = deep_summary.as_dict()
                results["deep"]["results"] = results["deep"]["results"][:5]
                statuses.append(self._deep_status(deep_summary))
                errors.extend(deep_summary.errors)

            # 5. M4 商业分析
            biz_summary = run_commercial_analysis(
                db,
                client,
                self.config,
                limit=_int_option(options.get("biz_limit"), self.config.biz_limit),
                force=bool(options.get("force")),
                competition=bool(options.get("competition", True)),
                progress=stage(0.82, 0.18),
            )
            results["biz"] = biz_summary.as_dict()
            results["biz"]["results"] = results["biz"]["results"][:5]
            statuses.append(self._commercial_status(biz_summary))
            errors.extend(biz_summary.errors)

            results["errors"] = errors[:30]
            progress("全流程完成", 1.0)
            if statuses and all(status == "success" for status in statuses):
                final = "success"
            elif all(status == "failed" for status in statuses):
                final = "failed"
            else:
                final = "partial"
            return final, results
        finally:
            transport.close()

    # -- helpers -----------------------------------------------------------
    def _client(self, transport: HttpxTransport) -> GitHubClient:
        counting = CountingTransport(transport)
        pool = TokenPool(
            self.config.github_tokens, low_watermark=self.config.low_watermark
        )
        return GitHubClient(
            counting,
            tokens=pool,
            base_url=self.config.api_base,
            user_agent=self.config.user_agent,
            retries=self.config.retries,
            timeout=self.config.timeout,
        )

    @staticmethod
    def _status(status: str) -> str:
        if status == "ok":
            return "success"
        return status if status in TERMINAL_STATUSES else "partial"

    @staticmethod
    def _analysis_status(summary: Any) -> str:
        if summary.failed and not summary.analyzed:
            return "failed"
        if summary.failed or summary.errors:
            return "partial"
        return "success"

    @staticmethod
    def _deep_status(summary: Any) -> str:
        if summary.failed and not summary.analyzed:
            return "failed"
        if summary.failed:
            return "partial"
        return "success"

    @staticmethod
    def _commercial_status(summary: Any) -> str:
        if summary.failed and not summary.analyzed:
            return "failed"
        if summary.failed or summary.errors:
            return "partial"
        return "success"


def _int_option(value: Any, default: int) -> int:
    try:
        number = int(value)
        return max(0, number)
    except (TypeError, ValueError):
        return int(default)


def _normalize_repo(value: str) -> str:
    from urllib.parse import urlparse

    text = value.strip().rstrip("/")
    if text.startswith(("http://", "https://")):
        parts = [part for part in urlparse(text).path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        raise ValueError(f"not a GitHub repo URL: {value}")
    if text.count("/") == 1 and all(text.split("/")):
        return text
    raise ValueError(f"expected owner/repo or GitHub URL, got: {value}")

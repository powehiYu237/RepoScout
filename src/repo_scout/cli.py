"""命令行入口。

用法：
  python -m repo_scout init-db [--config config.toml]
  python -m repo_scout ingest  [--period daily --period weekly] [--no-enrich] [--no-snapshot]
  python -m repo_scout add owner/repo
  python -m repo_scout status
  python -m repo_scout metrics [--top 10] [--sort trend|delta7|velocity|stars]
  python -m repo_scout analyze [--limit N] [--force] [--top 10] [--sort repro|stars|grade]
  python -m repo_scout deep-analyze [--limit N] [--force] [--top 10]
  python -m repo_scout biz-analyze [--limit N] [--force] [--top 10] [--no-competition]
  python -m repo_scout serve [--host 127.0.0.1] [--port 8765] [--open]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from .analysis import load_analysis_results, run_analysis, sort_analyses
from .commercial import load_commercial_output, run_commercial_analysis, sort_commercial
from .config import Config, load_config
from .db import Database
from .deep import cloc_version, resolve_cloc, run_deep_analysis
from .web import serve as serve_web
from .github import GitHubClient, GitHubError, GitHubRateLimitError, TokenPool
from .http import CountingTransport, HttpxTransport
from .metrics import compute_and_store_metrics, summarize_metrics
from .pipeline import IngestPipeline
from .star_history import StarHistoryError, fetch_star_history
from .trending import PERIODS


def normalize_repo_arg(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.startswith(("http://", "https://")):
        parts = [part for part in urlparse(value).path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        raise ValueError(f"not a GitHub repo URL: {value}")
    if value.count("/") == 1 and all(value.split("/")):
        return value
    raise ValueError(f"expected owner/repo or GitHub URL, got: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-scout")
    parser.add_argument("--config", type=Path, default=None, help="TOML 配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="创建/升级数据库")

    ingest = sub.add_parser("ingest", help="运行一次数据采集")
    ingest.add_argument(
        "--period",
        dest="periods",
        action="append",
        choices=PERIODS,
        help="只采集指定时间窗，可重复指定",
    )
    ingest.add_argument("--no-enrich", action="store_true", help="跳过 GitHub 元数据补全")
    ingest.add_argument("--no-snapshot", action="store_true", help="跳过快照写入")
    ingest.add_argument(
        "--no-star-history", action="store_true", help="跳过星标历史回填（FR-103）"
    )

    add = sub.add_parser("add", help="手动添加一个仓库")
    add.add_argument("repo", help="owner/repo 或仓库 URL")
    add.add_argument(
        "--no-star-history", action="store_true", help="跳过星标历史回填（FR-103）"
    )

    history = sub.add_parser(
        "star-history", help="查询单个仓库的星标历史（联网，GitHub 官方 API）"
    )
    history.add_argument("repo", help="owner/repo 或仓库 URL")
    history.add_argument(
        "--weeks", type=int, default=30, help="回溯周数（默认 30，最大 3000）"
    )

    sub.add_parser("status", help="查看数据库状态与最近一次运行")

    metrics = sub.add_parser(
        "metrics", help="计算 M2 增长指标与 TrendScore（离线，不消耗配额）"
    )
    metrics.add_argument(
        "--top", type=int, default=10, help="输出前 N 条；0 表示全部（默认 10）"
    )
    metrics.add_argument(
        "--sort",
        choices=("trend", "delta7", "velocity", "stars"),
        default="trend",
        help="排序方式（默认 trend）",
    )
    metrics.add_argument(
        "--no-write", action="store_true", help="只输出结果，不写入 repo_metrics"
    )

    analyze = sub.add_parser(
        "analyze", help="M3 浅分析：LoC/依赖/外部服务/结构/License（联网）"
    )
    analyze.add_argument(
        "--limit", type=int, default=None, help="本次最多分析多少个仓库"
    )
    analyze.add_argument(
        "--force", action="store_true", help="忽略 refresh_hours 缓存，强制重算"
    )
    analyze.add_argument("--top", type=int, default=10, help="输出前 N 条（默认 10）")
    analyze.add_argument(
        "--sort",
        choices=("repro", "stars", "grade"),
        default="repro",
        help="排序方式（默认 repro）",
    )

    deep = sub.add_parser(
        "deep-analyze", help="M3 深分析：浅克隆 + cloc（联网，需已有浅分析）"
    )
    deep.add_argument(
        "--limit", type=int, default=None, help="本次最多分析多少个仓库"
    )
    deep.add_argument(
        "--force", action="store_true", help="忽略 deep_refresh_hours 缓存"
    )
    deep.add_argument("--top", type=int, default=10, help="输出前 N 条（默认 10）")
    deep.add_argument(
        "--sort",
        choices=("repro", "stars", "grade"),
        default="repro",
        help="排序方式（默认 repro）",
    )
    deep.add_argument(
        "--keep", action="store_true", help="保留克隆目录（仅调试，默认立即删除）"
    )

    biz = sub.add_parser(
        "biz-analyze", help="M4 商业价值分析：付费意愿/商业化/竞品/受众/差异化（联网）"
    )
    biz.add_argument(
        "--limit", type=int, default=None, help="本次最多分析多少个仓库"
    )
    biz.add_argument(
        "--force", action="store_true", help="忽略 biz refresh_hours 缓存"
    )
    biz.add_argument("--top", type=int, default=10, help="输出前 N 条（默认 10）")
    biz.add_argument(
        "--sort",
        choices=("biz", "market", "repro", "stars"),
        default="biz",
        help="排序方式（默认 biz）",
    )
    biz.add_argument(
        "--no-competition",
        action="store_true",
        help="跳过 GitHub Search API 竞品统计",
    )

    serve = sub.add_parser("serve", help="启动 M5 Web 应用（列表/详情/筛选/导出）")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    serve.add_argument("--port", type=int, default=8765, help="端口（默认 8765）")
    serve.add_argument("--open", action="store_true", help="启动后打开浏览器")
    return parser


def _warn_if_no_tokens(config: Config) -> None:
    if config.github_tokens:
        return
    print(
        "warning: 未配置 GitHub Token，未鉴权配额仅 60 次/小时；"
        "建议设置 REPO_SCOUT_GITHUB_TOKENS 或调低 enrich_limit / star_history_limit（见 README）。",
        file=sys.stderr,
    )


def _run_star_history(config: Config, args: argparse.Namespace) -> int:
    _warn_if_no_tokens(config)
    if not 1 <= args.weeks <= 3000:
        print(json.dumps({"error": "weeks must be between 1 and 3000"}, ensure_ascii=False))
        return 1

    try:
        full_name = normalize_repo_arg(args.repo)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    transport = HttpxTransport(config.user_agent, config.timeout)
    try:
        pool = TokenPool(
            config.github_tokens, low_watermark=config.low_watermark
        )
        client = GitHubClient(
            transport,
            tokens=pool,
            base_url=config.api_base,
            user_agent=config.user_agent,
            retries=config.retries,
            timeout=config.timeout,
        )
        try:
            weeks = fetch_star_history(client, full_name, weeks=args.weeks)
        except GitHubRateLimitError as exc:
            payload = {"full_name": full_name, "error": str(exc)}
            if not config.github_tokens:
                payload["hint"] = (
                    "未配置 Token 时使用共享出口 IP 的匿名配额，容易触发限流；"
                    "请设置 REPO_SCOUT_GITHUB_TOKENS 后重试。"
                )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2
        except (GitHubError, StarHistoryError) as exc:
            print(
                json.dumps(
                    {"full_name": full_name, "error": str(exc)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        if weeks is None:
            print(
                json.dumps(
                    {"full_name": full_name, "error": "repository not found"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        data = [
            {"week": week.week_iso, "total": week.total, "days": list(week.days)}
            for week in weeks
        ]
        print(
            json.dumps(
                {"full_name": full_name, "weeks": len(data), "data": data},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        transport.close()


def _run_analysis(config: Config, args: argparse.Namespace, db: Database) -> int:
    """M3：执行浅分析并输出结果。"""
    transport = HttpxTransport(config.user_agent, config.timeout)
    try:
        counting = CountingTransport(transport)
        pool = TokenPool(config.github_tokens, low_watermark=config.low_watermark)
        client = GitHubClient(
            counting,
            tokens=pool,
            base_url=config.api_base,
            user_agent=config.user_agent,
            retries=config.retries,
            timeout=config.timeout,
        )
        summary = run_analysis(
            db,
            client,
            limit=args.limit if args.limit is not None else config.analysis_limit,
            refresh_hours=config.analysis_refresh_hours,
            force=args.force,
            tech_stack=config.tech_stack,
            fetch_readme=config.fetch_readme,
        )
        # 展示时包含缓存命中的历史结果，而不是只显示本轮新分析的仓库。
        if hasattr(db, "list_repo_analysis_joined"):
            summary.results = load_analysis_results(db)
        print(
            json.dumps(
                summary.as_dict(top=args.top, sort=args.sort),
                ensure_ascii=False,
                indent=2,
            )
        )
        if summary.failed and not summary.analyzed:
            return 1
        return 2 if summary.errors else 0
    finally:
        transport.close()


def _run_deep_analysis(config: Config, args: argparse.Namespace, db: Database) -> int:
    """M3：浅克隆 + cloc，更新 LoC 与复刻分。"""
    transport = HttpxTransport(config.user_agent, config.timeout)
    try:
        counting = CountingTransport(transport)
        pool = TokenPool(config.github_tokens, low_watermark=config.low_watermark)
        client = GitHubClient(
            counting,
            tokens=pool,
            base_url=config.api_base,
            user_agent=config.user_agent,
            retries=config.retries,
            timeout=config.timeout,
        )
        cloc_command = resolve_cloc(config, client)
        summary = run_deep_analysis(
            db,
            config,
            limit=args.limit,
            force=args.force,
            client=client,
            cloc_command=cloc_command,
            keep_clones=args.keep,
        )
        payload = summary.as_dict()
        payload["cloc"] = cloc_version(cloc_command) if cloc_command else None
        results = load_analysis_results(db)
        ordered = sort_analyses(results, args.sort)
        payload["top"] = [
            item.to_dict()
            for item in (ordered if args.top <= 0 else ordered[: args.top])
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if summary.failed and not summary.analyzed:
            return 1
        return 2 if summary.errors else 0
    finally:
        transport.close()


def _run_commercial(config: Config, args: argparse.Namespace, db: Database) -> int:
    """M4：商业价值信号分析与 BizScore。"""
    transport = HttpxTransport(config.user_agent, config.timeout)
    try:
        counting = CountingTransport(transport)
        pool = TokenPool(config.github_tokens, low_watermark=config.low_watermark)
        client = GitHubClient(
            counting,
            tokens=pool,
            base_url=config.api_base,
            user_agent=config.user_agent,
            retries=config.retries,
            timeout=config.timeout,
        )
        summary = run_commercial_analysis(
            db,
            client,
            config,
            limit=args.limit,
            force=args.force,
            competition=not args.no_competition,
        )
        payload = summary.as_dict()
        ordered = sort_commercial(load_commercial_output(db), args.sort)
        payload["top"] = ordered if args.top <= 0 else ordered[: args.top]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if summary.failed and not summary.analyzed:
            return 1
        return 2 if summary.errors else 0
    finally:
        transport.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)

    if args.command == "star-history":
        return _run_star_history(config, args)

    if args.command in {"ingest", "add", "analyze", "biz-analyze"}:
        _warn_if_no_tokens(config)

    db = Database(config.db_path)
    db.init_schema()

    try:
        if args.command == "init-db":
            print(json.dumps({"db": str(config.db_path), **db.status()}, ensure_ascii=False, indent=2))
            return 0

        if args.command == "status":
            print(json.dumps(db.status(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "metrics":
            items = compute_and_store_metrics(db, write=not args.no_write)
            print(
                json.dumps(
                    summarize_metrics(items, top=args.top, sort=args.sort),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "analyze":
            return _run_analysis(config, args, db)

        if args.command == "deep-analyze":
            return _run_deep_analysis(config, args, db)

        if args.command == "biz-analyze":
            return _run_commercial(config, args, db)

        if args.command == "serve":
            db.close()
            return serve_web(
                config.db_path,
                host=args.host,
                port=args.port,
                open_browser=args.open,
                config=config,
            )

        transport = HttpxTransport(config.user_agent, config.timeout)
        try:
            if getattr(args, "no_star_history", False):
                config = replace(config, fetch_star_history=False)
            pipeline = IngestPipeline(db, transport, config)

            if args.command == "add":
                full_name = normalize_repo_arg(args.repo)
                added = pipeline.add_repo(full_name)
                print(
                    json.dumps(
                        {"added": added, "full_name": full_name},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0 if added else 1

            summary = pipeline.run(
                periods=args.periods,
                enrich=not args.no_enrich,
                snapshot=not args.no_snapshot,
            )
            print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))
            if summary.status == "ok":
                return 0
            if summary.status == "partial":
                return 2
            return 1
        finally:
            transport.close()
    finally:
        db.close()

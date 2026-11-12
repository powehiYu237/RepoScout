"""M3 深分析（FR-301 / FR-308）：浅克隆 + cloc。

流程：

1. 从浅分析结果中按 ReproScore 取 Top N（默认 20，周级缓存）；
2. ``git clone --depth 1`` 到独立临时目录（超过体积上限直接跳过）；
3. 运行 ``cloc --json`` 统计代码行数（cloc 缺失时自动从官方仓库下载 Perl
   脚本，仍不可用则退化为内置行数统计）；
4. 用 cloc 结果覆盖 LoC 估算值，重算 ReproScore / 复杂度 / 复刻周期；
5. **无论成功失败，立即删除克隆目录**，不长期保留源码。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

from .analysis import (
    SOURCE_EXTENSIONS,
    complexity_grade,
    complexity_score,
    loc_score,
    replication_weeks,
    repro_score,
)
from .config import Config
from .util import iso_z, utc_now

CLOC_REPO = "AlDanial/cloc"
SOURCE_EXCLUDES = "node_modules,.git,vendor,dist,build,target,.venv,venv,third_party,Pods,.next,.nuxt,coverage"
EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
    "third_party",
    "Pods",
    ".next",
    ".nuxt",
    "coverage",
}
COMMENT_PREFIXES = ("#", "//", "/*", "*", "*/", "--", "<!--", '"""', "'''")
LANGUAGE_BY_EXTENSION = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".swift": "Swift",
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".h": "C/C++ Header",
    ".hpp": "C/C++ Header",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".scala": "Scala",
    ".sh": "Shell",
    ".lua": "Lua",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

# cloc 默认会把文档/配置也算入 code；复刻体量只看真正的代码语言。
NON_CODE_LANGUAGES = {
    "Markdown",
    "JSON",
    "YAML",
    "TOML",
    "INI",
    "CSV",
    "Text",
    "reStructuredText",
    "AsciiDoc",
    "License",
    "XML",
    "SVG",
}


def cloc_code_lines(languages: Mapping[str, int]) -> int:
    """从 cloc 分语言统计中取“代码”行数（排除文档/配置）。"""
    code = sum(
        int(lines)
        for language, lines in languages.items()
        if language not in NON_CODE_LANGUAGES
    )
    if code > 0:
        return code
    # 极端情况下（纯文档仓库）退回 cloc 原始总和，避免 LoC 凭空变成 0。
    return sum(int(lines) for lines in languages.values())


class DeepAnalysisError(RuntimeError):
    """深分析失败（克隆、cloc 或解析）。"""


def _kill_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def _run_command(
    command: list[str],
    *,
    timeout: int,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    """运行外部命令；超时时杀掉整个进程树，避免 Windows 子进程残留。"""
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise DeepAnalysisError(f"command timed out after {timeout}s") from exc
    return process.returncode, stdout or "", stderr or ""


# -- cloc -------------------------------------------------------------------
def parse_cloc_json(text: str) -> tuple[int, dict[str, int]] | None:
    """解析 cloc --json 输出，返回 (总代码行, 各语言行数)。"""
    start = text.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(text[start:])
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    languages: dict[str, int] = {}
    for name, value in payload.items():
        if name in ("header", "SUM"):
            continue
        if isinstance(value, dict) and isinstance(value.get("code"), int):
            languages[str(name)] = int(value["code"])

    summary = payload.get("SUM")
    total = summary.get("code") if isinstance(summary, Mapping) else None
    if not isinstance(total, int):
        total = sum(languages.values())
    return total, languages


def _command_from_path(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    if path.suffix.lower() in (".pl", ".pm"):
        perl = shutil.which("perl")
        return [perl, str(path)] if perl else None
    return [str(path)]


def resolve_cloc(
    config: Config, client: Any | None = None
) -> list[str] | None:
    """定位 cloc：配置路径 → PATH → 本地缓存 → GitHub 下载。"""
    if config.cloc_path:
        command = _command_from_path(Path(config.cloc_path))
        if command:
            return command

    found = shutil.which("cloc")
    if found:
        return [found]

    cache = Path(config.db_path).parent / "tools" / "cloc.pl"
    command = _command_from_path(cache)
    if command:
        return command

    if config.cloc_auto_download and client is not None:
        try:
            text = client.get_file_text(CLOC_REPO, "cloc")
        except Exception:
            text = None
        if text:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding="utf-8")
            return _command_from_path(cache)
    return None


def cloc_version(command: list[str]) -> str | None:
    try:
        _, stdout, _ = _run_command(list(command) + ["--version"], timeout=15)
    except (DeepAnalysisError, OSError):
        return None
    output = stdout.strip()
    if not output or output.startswith("{"):
        return None
    return output.splitlines()[0][:80]


def run_cloc(
    command: list[str], path: Path, *, timeout: int = 300
) -> tuple[int, dict[str, int]]:
    cmd = list(command) + [
        "--json",
        "--quiet",
        f"--exclude-dir={SOURCE_EXCLUDES}",
        "--timeout",
        "60",
        str(path),
    ]
    try:
        returncode, stdout, stderr = _run_command(cmd, timeout=timeout)
    except DeepAnalysisError:
        raise
    except OSError as exc:
        raise DeepAnalysisError(f"failed to run cloc: {exc}") from exc

    parsed = parse_cloc_json(stdout)
    if parsed is None:
        detail = (stderr or stdout or "").strip().splitlines()[:1]
        raise DeepAnalysisError(
            "cloc did not return valid JSON"
            + (f": {detail[0][:200]}" if detail else "")
        )
    return parsed


# -- 内置行数统计（cloc 不可用时的降级） ------------------------------------
def count_lines_fallback(root: Path) -> tuple[int, dict[str, int]]:
    """按源文件扩展名统计非空、非纯注释行；置信度低于 cloc。"""
    languages: dict[str, int] = {}
    total = 0
    for base, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRS]
        for name in files:
            suffix = Path(name).suffix.lower()
            if suffix not in SOURCE_EXTENSIONS:
                continue
            path = Path(base) / name
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue
            text = raw.decode("utf-8", errors="ignore")
            count = 0
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(COMMENT_PREFIXES):
                    continue
                count += 1
            total += count
            language = LANGUAGE_BY_EXTENSION.get(suffix, suffix.lstrip("."))
            languages[language] = languages.get(language, 0) + count
    return total, languages


# -- 克隆与清理 -------------------------------------------------------------
def _clone_repository(
    repo: Mapping[str, Any], destination: Path, timeout: int
) -> None:
    url = str(repo["html_url"] or f"https://github.com/{repo['full_name']}")
    if not url.endswith(".git"):
        url += ".git"
    command = [
        "git",
        "-c",
        "advice.detachedHead=false",
        "-c",
        "http.lowSpeedLimit=1000",
        "-c",
        "http.lowSpeedTime=30",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        url,
        str(destination),
    ]
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_LFS_SKIP_SMUDGE", "1")
    try:
        returncode, stdout, stderr = _run_command(
            command, timeout=timeout, env=env
        )
    except DeepAnalysisError:
        raise
    except OSError as exc:
        raise DeepAnalysisError(f"failed to run git: {exc}") from exc

    if returncode != 0:
        detail = (stderr or stdout or "").strip().splitlines()
        message = detail[-1][:300] if detail else f"exit code {returncode}"
        raise DeepAnalysisError(f"git clone failed: {message}")


def _remove_tree(path: Path) -> None:
    def _handle(func, target, _exc_info):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onexc=_handle)
    except TypeError:  # Python < 3.12
        shutil.rmtree(path, onerror=lambda f, p, e: _handle(f, p, e))
    except OSError:
        pass


def _extract_tarball(archive: Path, extract_dir: Path, destination: Path) -> None:
    """安全解压 GitHub tarball，并把唯一顶层目录移动到 destination。"""
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            try:
                bundle.extractall(extract_dir, filter="data")
            except TypeError:  # Python < 3.12
                bundle.extractall(extract_dir)
    except (OSError, tarfile.TarError) as exc:
        raise DeepAnalysisError(f"failed to extract tarball: {exc}") from exc

    entries = list(extract_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        shutil.move(str(entries[0]), str(destination))
    else:
        shutil.move(str(extract_dir), str(destination))


def _download_tarball(
    repo: Mapping[str, Any],
    destination: Path,
    timeout: int,
    max_bytes: int,
) -> None:
    """codeload tarball 兜底：github.com git 端口不稳定时使用。"""
    full_name = str(repo["full_name"])
    branch = str(repo["default_branch"] or "main")
    url = f"https://codeload.github.com/{full_name}/tar.gz/refs/heads/{branch}"
    workdir = destination.parent / "tarball"
    workdir.mkdir(parents=True, exist_ok=True)
    archive = workdir / "repo.tar.gz"
    total = 0
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise DeepAnalysisError(
                        f"tarball download failed: HTTP {response.status_code}"
                    )
                with archive.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise DeepAnalysisError(
                                f"tarball exceeds size limit ({max_bytes} bytes)"
                            )
                        handle.write(chunk)
    except httpx.HTTPError as exc:
        raise DeepAnalysisError(f"tarball download failed: {exc}") from exc

    _extract_tarball(archive, workdir / "extract", destination)


# -- 结果重算 ---------------------------------------------------------------
def deep_update_values(
    repo: Mapping[str, Any],
    *,
    loc: int,
    method: str,
    languages: dict[str, int],
    duration_ms: int,
    now: datetime,
) -> dict[str, Any]:
    """用 cloc/行数统计结果覆盖 LoC，并重算复刻相关分数。"""
    low = set(json.loads(repo["low_confidence_fields"] or "[]"))
    low.discard("loc")
    breakdown = json.loads(repo["score_breakdown"] or "{}")
    if not isinstance(breakdown, dict):
        breakdown = {}
    hard = bool(breakdown.get("hard_language_unmatched"))
    services = json.loads(repo["ext_services"] or "[]")
    has_monorepo = bool(repo["has_monorepo"])

    s_loc = loc_score(loc)
    s_dep = float(repo["s_dep"]) if repo["s_dep"] is not None else 60.0
    s_ext = float(repo["s_ext"]) if repo["s_ext"] is not None else 60.0
    s_lang = float(repo["s_lang"]) if repo["s_lang"] is not None else 50.0
    s_license = (
        float(repo["s_license"]) if repo["s_license"] is not None else 50.0
    )
    s_struct = float(repo["s_struct"]) if repo["s_struct"] is not None else 60.0

    score = repro_score(
        s_loc=s_loc,
        s_dep=s_dep,
        s_ext=s_ext,
        s_lang=s_lang,
        s_license=s_license,
        s_struct=s_struct,
        hard_language_unmatched=hard,
    )
    complexity = complexity_score(
        s_loc=s_loc, s_dep=s_dep, s_ext=s_ext, s_struct=s_struct
    )
    grade = complexity_grade(complexity)
    if "dep" in low:
        grade += "*"

    rep_low, rep_high = replication_weeks(
        loc=loc,
        external_services=len(services),
        hard_language_unmatched=hard,
        has_monorepo=has_monorepo,
    )

    now_iso = iso_z(now)
    breakdown = {
        **breakdown,
        "inputs": {
            **(
                breakdown.get("inputs")
                if isinstance(breakdown.get("inputs"), dict)
                else {}
            ),
            "loc": loc,
            "loc_method": method,
            "cloc_total": sum(languages.values()),
            "deep_languages": languages,
        },
        "subscores": {
            **(
                breakdown.get("subscores")
                if isinstance(breakdown.get("subscores"), dict)
                else {}
            ),
            "loc": s_loc,
        },
        "low_confidence_fields": sorted(low),
        "deep_analyzed_at": now_iso,
    }

    return {
        "loc": loc,
        "loc_method": method,
        "loc_confidence": "high" if method == "cloc" else "medium",
        "s_loc": s_loc,
        "repro_score": score,
        "complexity_score": complexity,
        "complexity_grade": grade,
        "replication_weeks_min": rep_low,
        "replication_weeks_max": rep_high,
        "score_breakdown": json.dumps(breakdown, ensure_ascii=False),
        "deep_analyzed_at": now_iso,
        "deep_method": method,
        "deep_languages": json.dumps(languages, ensure_ascii=False, sort_keys=True),
        "deep_duration_ms": duration_ms,
        "deep_error": None,
        "updated_at": now_iso,
    }


# -- 批量编排 ---------------------------------------------------------------
@dataclass
class DeepAnalysisSummary:
    analyzed: int = 0
    skipped_large: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "analyzed": self.analyzed,
            "skipped_large": self.skipped_large,
            "failed": self.failed,
            "errors": list(self.errors),
            "results": list(self.results),
        }


def run_deep_analysis(
    db: Any,
    config: Config,
    *,
    limit: int | None = None,
    force: bool = False,
    now: datetime | None = None,
    client: Any | None = None,
    cloc_command: list[str] | None = None,
    clone_fn: Callable[[Mapping[str, Any], Path, int], None] | None = None,
    tarball_fn: Callable[[Mapping[str, Any], Path, int, int], None] | None = None,
    keep_clones: bool = False,
    progress: Callable[[str, float], None] | None = None,
) -> DeepAnalysisSummary:
    """对浅分析 Top N 执行深分析；失败不覆盖已有结果。"""
    now = now or utc_now()
    limit = config.deep_limit if limit is None else limit
    stale_before = iso_z(
        now if force else now - timedelta(hours=config.deep_refresh_hours)
    )
    repos = db.list_repos_for_deep_analysis(limit, stale_before)
    summary = DeepAnalysisSummary()
    clone = clone_fn or _clone_repository
    tarball = tarball_fn or _download_tarball
    max_size_kb = config.deep_max_size_mb * 1024
    max_bytes = config.deep_max_size_mb * 1024 * 1024

    if cloc_command is None:
        cloc_command = resolve_cloc(config, client)

    total = len(repos)
    if progress:
        progress("准备深分析", 0.0)

    for index, repo in enumerate(repos, start=1):
        full_name = str(repo["full_name"])
        size_kb = repo["size_kb"]
        if size_kb is not None and int(size_kb) > max_size_kb:
            db.update_deep_analysis(
                int(repo["id"]),
                {
                    "deep_analyzed_at": iso_z(now),
                    "deep_error": "too_large",
                    "updated_at": iso_z(now),
                },
            )
            summary.skipped_large += 1
            if progress:
                progress(f"跳过大型仓库 {full_name}", index / max(1, total))
            continue

        workdir = Path(tempfile.mkdtemp(prefix="repo-scout-deep-"))
        clone_dir = workdir / "repo"
        started = now
        try:
            if config.deep_source == "tarball":
                tarball(
                    repo,
                    clone_dir,
                    config.deep_timeout_seconds,
                    max_bytes,
                )
            elif config.deep_source == "clone":
                clone(repo, clone_dir, config.deep_timeout_seconds)
            else:
                try:
                    clone(repo, clone_dir, config.deep_timeout_seconds)
                except DeepAnalysisError as clone_exc:
                    _remove_tree(clone_dir)
                    try:
                        tarball(
                            repo,
                            clone_dir,
                            config.deep_timeout_seconds,
                            max_bytes,
                        )
                    except DeepAnalysisError as tarball_exc:
                        raise DeepAnalysisError(
                            f"clone failed ({clone_exc}); "
                            f"tarball failed ({tarball_exc})"
                        ) from tarball_exc
            started = utc_now()
            if cloc_command:
                _, languages = run_cloc(
                    cloc_command,
                    clone_dir,
                    timeout=config.deep_timeout_seconds,
                )
                loc = cloc_code_lines(languages)
                method = "cloc"
            else:
                loc, languages = count_lines_fallback(clone_dir)
                method = "line_count"
            duration_ms = int((utc_now() - started).total_seconds() * 1000)
            values = deep_update_values(
                repo,
                loc=loc,
                method=method,
                languages=languages,
                duration_ms=duration_ms,
                now=now,
            )
            db.update_deep_analysis(int(repo["id"]), values)
            summary.analyzed += 1
            summary.results.append(
                {
                    "full_name": full_name,
                    "loc": loc,
                    "loc_method": method,
                    "loc_confidence": values["loc_confidence"],
                    "languages": languages,
                    "repro_before": repo["shallow_repro"],
                    "repro_after": values["repro_score"],
                    "grade_before": repo["shallow_grade"],
                    "grade_after": values["complexity_grade"],
                    "duration_ms": duration_ms,
                }
            )
        except (DeepAnalysisError, OSError) as exc:
            summary.failed += 1
            summary.errors.append(f"{full_name}: {exc}")
            # 已有成功的深分析结果时，不用一次瞬时失败覆盖它。
            if repo["deep_analyzed_at"] is None:
                db.update_deep_analysis(
                    int(repo["id"]),
                    {"deep_error": str(exc)[:500], "updated_at": iso_z(now)},
                )
        finally:
            if keep_clones:
                summary.results.append(
                    {"full_name": full_name, "clone_dir": str(clone_dir)}
                )
            else:
                _remove_tree(workdir)
            if progress:
                progress(f"深分析 {full_name}", index / max(1, total))

    if progress:
        progress("深分析完成", 1.0)
    return summary

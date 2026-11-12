"""M3 可复刻性评估层（FR-301 ~ FR-308）。

本版本实现**浅分析**：使用 GitHub REST API 的 languages / git trees / contents
接口，配合仓库元数据，估算代码体量、依赖、外部服务、工程结构、语言匹配与
License 风险，并计算 ReproScore、复杂度等级与复刻周期。

深分析（浅克隆 + cloc）保留为后续能力；所有子项都记录输入、证据与置信度，
缺失字段按 PRD 的中性分值降级，不会伪造成 0。
"""

from __future__ import annotations

import json
import math
import re
import tomllib
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Mapping
from xml.etree import ElementTree

from .util import iso_z, utc_now

ANALYSIS_VERSION = "m3-light-1"

MANIFEST_PRIORITY = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "composer.json",
    "Gemfile",
)

SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".swift",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".scala",
    ".sh",
    ".lua",
    ".dart",
    ".ex",
    ".exs",
    ".vue",
    ".svelte",
}

GREEN_LICENSES = {
    "MIT",
    "APACHE-2.0",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "ISC",
    "0BSD",
    "UNLICENSE",
    "CC0-1.0",
}
YELLOW_LICENSE_PREFIXES = ("MPL-", "LGPL-")
RED_LICENSE_PREFIXES = ("AGPL-", "GPL-")

FRIENDLY_LANGUAGES = {"python", "javascript", "typescript", "go"}
HARD_LANGUAGES = {
    "rust",
    "cpp",
    "c",
    "scala",
    "haskell",
    "zig",
    "cuda",
    "assembly",
}

LANGUAGE_ALIASES = {
    "ts": "typescript",
    "typescript": "typescript",
    "js": "javascript",
    "javascript": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "golang": "go",
    "go": "go",
    "py": "python",
    "python": "python",
    "c++": "cpp",
    "cpp": "cpp",
    "cplusplus": "cpp",
    "c#": "csharp",
    "csharp": "csharp",
    "next.js": "nextjs",
    "nextjs": "nextjs",
    "react": "react",
    "reactjs": "react",
    "vue": "vue",
    "nuxt": "nuxt",
}
ADJACENT_SKILLS = {
    "typescript": {"javascript"},
    "javascript": {"typescript"},
    "react": {"nextjs"},
    "nextjs": {"react"},
}

# FR-303：关键词 → 规范服务名。使用词边界，避免 "laws" 命中 aws 之类误报。
SERVICE_PATTERNS: dict[str, tuple[str, ...]] = {
    "OpenAI": (r"\bopenai\b", r"\bopen-ai\b"),
    "Anthropic": (r"\banthropic\b",),
    "Claude": (r"\bclaude\b",),
    "Gemini": (r"\bgemini\b",),
    "AWS": (r"\baws\b", r"\bamazon web services\b"),
    "S3": (r"\bs3\b",),
    "GCP": (r"\bgcp\b", r"\bgoogle cloud\b"),
    "Azure": (r"\bazure\b",),
    "Supabase": (r"\bsupabase\b",),
    "Firebase": (r"\bfirebase\b",),
    "PostgreSQL": (r"\bpostgres(?:ql)?\b",),
    "MySQL": (r"\bmysql\b",),
    "Redis": (r"\bredis\b",),
    "MongoDB": (r"\bmongo(?:db)?\b",),
    "Elasticsearch": (r"\belasticsearch\b",),
    "Pinecone": (r"\bpinecone\b",),
    "Qdrant": (r"\bqdrant\b",),
    "Stripe": (r"\bstripe\b",),
    "Auth0": (r"\bauth0\b",),
    "Clerk": (r"\bclerk\b",),
    "Sentry": (r"\bsentry\b",),
    "Vercel": (r"\bvercel\b",),
    "Cloudflare": (r"\bcloudflare\b",),
}

_COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)
_DOCKER_FILES = ("Dockerfile", "dockerfile")
_MONOREPO_FILES = ("pnpm-workspace.yaml", "pnpm-workspace.yml", "lerna.json", "nx.json")


# -- 数据结构 ---------------------------------------------------------------
@dataclass(frozen=True)
class DependencyInfo:
    count: int | None
    dev_count: int | None
    manifest: str | None
    confidence: str
    has_workspaces: bool = False


@dataclass(frozen=True)
class StructureInfo:
    src_files: int
    top_dirs: int
    ci_workflows: int
    has_docker: bool
    has_compose: bool
    compose_services: int | None
    has_monorepo: bool
    tree_truncated: bool


@dataclass(frozen=True)
class ExternalServices:
    services: tuple[str, ...]
    evidence: tuple[dict[str, str], ...]
    known: bool


@dataclass(frozen=True)
class RepoAnalysis:
    repo_pk: int
    full_name: str
    stars_total: int | None
    language: str | None
    license_spdx: str | None
    analyzed_at: str
    languages: dict[str, int]
    loc: int | None
    loc_method: str | None
    loc_confidence: str
    dep_count: int | None
    dev_dep_count: int | None
    dep_manifest: str | None
    dep_confidence: str
    ext_services: tuple[str, ...]
    ext_evidence: tuple[dict[str, str], ...]
    has_docker: bool | None
    has_compose: bool | None
    compose_services: int | None
    has_monorepo: bool | None
    ci_workflows: int | None
    src_files: int | None
    top_dirs: int | None
    tree_truncated: bool
    license_risk: str
    s_loc: float
    s_dep: float
    s_ext: float
    s_lang: float
    s_license: float
    s_struct: float
    repro_score: float
    complexity_score: float
    complexity_grade: str
    replication_weeks_min: float | None
    replication_weeks_max: float | None
    low_confidence_fields: tuple[str, ...]
    score_breakdown: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_breakdown: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "repo_pk": self.repo_pk,
            "full_name": self.full_name,
            "stars_total": self.stars_total,
            "language": self.language,
            "license": self.license_spdx,
            "license_risk": self.license_risk,
            "analyzed_at": self.analyzed_at,
            "loc": self.loc,
            "loc_method": self.loc_method,
            "loc_confidence": self.loc_confidence,
            "dep_count": self.dep_count,
            "dev_dep_count": self.dev_dep_count,
            "dep_manifest": self.dep_manifest,
            "dep_confidence": self.dep_confidence,
            "ext_services": list(self.ext_services),
            "has_docker": self.has_docker,
            "has_compose": self.has_compose,
            "compose_services": self.compose_services,
            "has_monorepo": self.has_monorepo,
            "ci_workflows": self.ci_workflows,
            "src_files": self.src_files,
            "top_dirs": self.top_dirs,
            "repro_score": self.repro_score,
            "complexity_score": self.complexity_score,
            "complexity_grade": self.complexity_grade,
            "replication_weeks": [self.replication_weeks_min, self.replication_weeks_max],
            "low_confidence_fields": list(self.low_confidence_fields),
        }
        if include_breakdown:
            payload["score_breakdown"] = self.score_breakdown
        return payload

    def to_row(self) -> dict[str, Any]:
        return {
            "repo_pk": self.repo_pk,
            "analyzed_at": self.analyzed_at,
            "analysis_version": ANALYSIS_VERSION,
            "languages": json.dumps(self.languages, ensure_ascii=False, sort_keys=True),
            "loc": self.loc,
            "loc_method": self.loc_method,
            "loc_confidence": self.loc_confidence,
            "dep_count": self.dep_count,
            "dev_dep_count": self.dev_dep_count,
            "dep_manifest": self.dep_manifest,
            "dep_confidence": self.dep_confidence,
            "ext_services": json.dumps(list(self.ext_services), ensure_ascii=False),
            "ext_evidence": json.dumps(list(self.ext_evidence), ensure_ascii=False),
            "has_docker": _int_or_none(self.has_docker),
            "has_compose": _int_or_none(self.has_compose),
            "compose_services": self.compose_services,
            "has_monorepo": _int_or_none(self.has_monorepo),
            "ci_workflows": self.ci_workflows,
            "src_files": self.src_files,
            "top_dirs": self.top_dirs,
            "tree_truncated": int(self.tree_truncated),
            "license_risk": self.license_risk,
            "s_loc": self.s_loc,
            "s_dep": self.s_dep,
            "s_ext": self.s_ext,
            "s_lang": self.s_lang,
            "s_license": self.s_license,
            "s_struct": self.s_struct,
            "repro_score": self.repro_score,
            "complexity_score": self.complexity_score,
            "complexity_grade": self.complexity_grade,
            "replication_weeks_min": self.replication_weeks_min,
            "replication_weeks_max": self.replication_weeks_max,
            "low_confidence_fields": json.dumps(
                list(self.low_confidence_fields), ensure_ascii=False
            ),
            "score_breakdown": json.dumps(self.score_breakdown, ensure_ascii=False),
            "updated_at": self.analyzed_at,
        }


def _int_or_none(value: bool | None) -> int | None:
    return None if value is None else int(value)


# -- 解析：清单与依赖（FR-302） --------------------------------------------
def _as_mapping(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _manifest_counts(name: str, text: str) -> tuple[int | None, int | None, bool]:
    """返回 (运行时依赖数, 开发依赖数, 是否声明 workspaces)。"""
    try:
        if name == "package.json":
            payload = json.loads(text)
            deps = _as_mapping(payload.get("dependencies"))
            dev = _as_mapping(payload.get("devDependencies"))
            return len(deps), len(dev), bool(payload.get("workspaces"))

        if name == "pyproject.toml":
            payload = tomllib.loads(text)
            project = _as_mapping(payload.get("project"))
            deps = project.get("dependencies") or []
            optional = _as_mapping(project.get("optional-dependencies"))
            dev = sum(len(items or []) for items in optional.values())
            poetry = _as_mapping(_as_mapping(payload.get("tool")).get("poetry"))
            if poetry:
                runtime = [
                    key
                    for key in _as_mapping(poetry.get("dependencies"))
                    if key.lower() != "python"
                ]
                dev_poetry: list[str] = []
                for key in ("dev-dependencies",):
                    dev_poetry.extend(_as_mapping(poetry.get(key)))
                for group in _as_mapping(poetry.get("group")).values():
                    dev_poetry.extend(_as_mapping(_as_mapping(group).get("dependencies")))
                return len(deps) + len(runtime), dev + len(dev_poetry), False
            return len(deps), dev, False

        if name == "requirements.txt":
            count = 0
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-r", "-c", "-e", "--")):
                    continue
                count += 1
            return count, 0, False

        if name == "go.mod":
            count = 0
            in_block = False
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("require ("):
                    in_block = True
                    continue
                if in_block and stripped == ")":
                    in_block = False
                    continue
                if in_block and stripped and not stripped.startswith("//"):
                    count += 1
                elif stripped.startswith("require ") and not stripped.startswith("require ("):
                    count += 1
            return count, 0, False

        if name == "Cargo.toml":
            payload = tomllib.loads(text)
            deps = _as_mapping(payload.get("dependencies"))
            dev = _as_mapping(payload.get("dev-dependencies"))
            build = _as_mapping(payload.get("build-dependencies"))
            return len(deps) + len(build), len(dev), bool(payload.get("workspace"))

        if name == "composer.json":
            payload = json.loads(text)
            deps = _as_mapping(payload.get("require"))
            dev = _as_mapping(payload.get("require-dev"))
            runtime = [
                key
                for key in deps
                if key.lower() != "php" and not key.lower().startswith("ext-")
            ]
            return len(runtime), len(dev), False

        if name == "pom.xml":
            root = ElementTree.fromstring(text)
            count = sum(1 for _ in root.iter() if _.tag.endswith("dependency"))
            return count, 0, False

        if name == "Gemfile":
            count = sum(
                1
                for line in text.splitlines()
                if re.match(r"^\s*gem\s+['\"]", line)
            )
            return count, 0, False
    except (ValueError, TypeError, tomllib.TOMLDecodeError, ElementTree.ParseError):
        return None, None, False
    return None, None, False


def dependency_info(manifests: Mapping[str, str]) -> DependencyInfo:
    for name in MANIFEST_PRIORITY:
        text = manifests.get(name)
        if text is None:
            continue
        count, dev_count, workspaces = _manifest_counts(name, text)
        if count is None:
            return DependencyInfo(None, None, name, "low", workspaces)
        return DependencyInfo(count, dev_count or 0, name, "high", workspaces)
    return DependencyInfo(None, None, None, "low", False)


# -- 解析：工程结构（FR-304） ----------------------------------------------
def structure_from_tree(payload: Mapping | None) -> StructureInfo | None:
    if payload is None:
        return None
    entries = payload.get("tree")
    if not isinstance(entries, list):
        return None

    top_dirs: set[str] = set()
    src_files = 0
    workflows = 0
    root_files: set[str] = set()
    monorepo_dirs = False

    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        path = str(entry.get("path") or "")
        kind = entry.get("type")
        if not path:
            continue
        if "/" not in path:
            if kind == "blob":
                root_files.add(path)
            elif kind == "tree":
                top_dirs.add(path)
                if path in ("packages", "apps"):
                    monorepo_dirs = True
            continue

        first = path.split("/", 1)[0]
        top_dirs.add(first)
        if first in ("packages", "apps"):
            monorepo_dirs = True
        if kind == "blob":
            if path.startswith("src/") and _is_source_file(path):
                src_files += 1
            if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
                workflows += 1

    has_monorepo = monorepo_dirs or any(name in root_files for name in _MONOREPO_FILES)
    has_docker = any(name in root_files for name in _DOCKER_FILES)
    has_compose = any(name in root_files for name in _COMPOSE_FILES)
    return StructureInfo(
        src_files=src_files,
        top_dirs=len(top_dirs),
        ci_workflows=workflows,
        has_docker=has_docker,
        has_compose=has_compose,
        compose_services=None,
        has_monorepo=has_monorepo,
        tree_truncated=bool(payload.get("truncated", False)),
    )


def _is_source_file(path: str) -> bool:
    dot = path.rfind(".")
    return dot >= 0 and path[dot:].lower() in SOURCE_EXTENSIONS


def compose_service_count(text: str) -> int | None:
    in_services = False
    count = 0
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if not in_services:
            continue
        if re.match(r"^\S", line):
            break
        if re.match(r"^  [A-Za-z0-9_.-]+:\s*$", line):
            count += 1
    return count if in_services else None


def find_compose_path(payload: Mapping | None) -> str | None:
    if payload is None:
        return None
    for entry in payload.get("tree") or []:
        if not isinstance(entry, Mapping):
            continue
        path = str(entry.get("path") or "")
        if path in _COMPOSE_FILES:
            return path
    return None


# -- 外部服务（FR-303） -----------------------------------------------------
def find_external_services(
    readme: str | None, manifests: Mapping[str, str]
) -> ExternalServices:
    sources: list[tuple[str, str]] = []
    if readme:
        sources.append(("README", readme))
    for name, text in manifests.items():
        sources.append((name, text))

    known = bool(sources)
    found: list[str] = []
    evidence: list[dict[str, str]] = []
    seen: set[str] = set()

    for service, patterns in SERVICE_PATTERNS.items():
        matched = False
        for source_name, text in sources:
            for line in text.splitlines()[:2000]:
                for pattern in patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        if service not in seen:
                            found.append(service)
                            seen.add(service)
                            evidence.append(
                                {
                                    "service": service,
                                    "source": source_name,
                                    "line": line.strip()[:200],
                                }
                            )
                        matched = True
                        break
                if matched:
                    break
            if matched:
                break
    return ExternalServices(tuple(found), tuple(evidence[:20]), known)


# -- 评分（7.2 / 7.6 / 7.7） ------------------------------------------------
def loc_from_languages(languages: Mapping[str, int]) -> int | None:
    total = sum(int(size) for size in languages.values() if isinstance(size, int))
    if total <= 0:
        return None
    return int(round(total / 35))


def loc_score(loc: int | None) -> float:
    if loc is None:
        return 60.0
    if loc < 5_000:
        return 100.0
    if loc < 10_000:
        return 85.0
    if loc < 20_000:
        return 75.0
    if loc < 50_000:
        return 55.0
    if loc < 100_000:
        return 25.0
    return 5.0


def dependency_score(count: int | None) -> float:
    if count is None:
        return 60.0
    if count <= 20:
        return 100.0
    if count <= 50:
        return 75.0
    if count <= 100:
        return 50.0
    return 20.0


def external_service_score(count: int | None) -> float:
    if count is None:
        return 60.0
    if count == 0:
        return 100.0
    if count <= 2:
        return 80.0
    if count <= 4:
        return 55.0
    return 25.0


def normalize_skill(value: str) -> str:
    text = value.strip().lower().replace(" ", "")
    return LANGUAGE_ALIASES.get(text, text)


def language_match_score(
    language: str | None, tech_stack: Iterable[str]
) -> tuple[float, bool, bool]:
    """返回 (分数, 是否高门槛语言未匹配, 语言是否已知)。"""
    if not language:
        return 50.0, False, False
    lang = normalize_skill(language)
    skills = {normalize_skill(item) for item in tech_stack if item}
    if lang in skills:
        return 100.0, False, True
    if ADJACENT_SKILLS.get(lang, set()) & skills:
        return 80.0, False, True
    if lang in FRIENDLY_LANGUAGES:
        return 55.0, False, True
    if lang in HARD_LANGUAGES:
        return 20.0, True, True
    return 50.0, False, True


def license_score(spdx: str | None) -> tuple[float, str]:
    """返回 (分数, 风险色块)。"""
    if not spdx:
        return 20.0, "gray"
    normalized = spdx.strip().upper()
    if normalized in GREEN_LICENSES:
        return 100.0, "green"
    if normalized.startswith(YELLOW_LICENSE_PREFIXES):
        return 55.0, "yellow"
    if normalized.startswith(RED_LICENSE_PREFIXES):
        return 0.0, "red"
    return 50.0, "unknown"


def structure_score(
    structure: StructureInfo | None, compose_services: int | None
) -> float:
    if structure is None:
        return 60.0
    score = 100.0
    if structure.has_monorepo:
        score -= 30
    if structure.ci_workflows >= 5:
        score -= 15
    if compose_services is not None and compose_services >= 5:
        score -= 10
    if structure.top_dirs > 20:
        score -= 10
    return max(0.0, score)


def repro_score(
    *,
    s_loc: float,
    s_dep: float,
    s_ext: float,
    s_lang: float,
    s_license: float,
    s_struct: float,
    hard_language_unmatched: bool,
) -> float:
    score = (
        0.25 * s_loc
        + 0.15 * s_dep
        + 0.10 * s_ext
        + 0.20 * s_lang
        + 0.20 * s_license
        + 0.10 * s_struct
    )
    if hard_language_unmatched:
        score = min(score, 40.0)
    return round(score, 2)


def complexity_score(
    *, s_loc: float, s_dep: float, s_ext: float, s_struct: float
) -> float:
    return round(
        100.0 - (0.40 * s_loc + 0.30 * s_dep + 0.20 * s_ext + 0.10 * s_struct), 2
    )


def complexity_grade(score: float) -> str:
    if score <= 20:
        return "A"
    if score <= 35:
        return "B"
    if score <= 50:
        return "C"
    if score <= 65:
        return "D"
    if score <= 80:
        return "E"
    return "F"


def replication_weeks(
    *,
    loc: int | None,
    external_services: int,
    hard_language_unmatched: bool,
    has_monorepo: bool,
) -> tuple[float | None, float | None]:
    if loc is None:
        return None, None
    if loc < 5_000:
        base = 0.5
    elif loc < 10_000:
        base = 1.0
    elif loc < 20_000:
        base = 1.5
    elif loc < 50_000:
        base = 3.0
    else:
        base = 6.0

    value = base + min(external_services, 4) * 0.5
    if hard_language_unmatched:
        value *= 1.5
    if has_monorepo:
        value += 0.5

    low = math.ceil(value * 0.8 * 2) / 2
    high = math.ceil(value * 1.2 * 2) / 2
    return low, high


# -- 单仓库编排 -------------------------------------------------------------
def _dominant_language(
    languages: Mapping[str, int], fallback: str | None
) -> str | None:
    if languages:
        return max(languages.items(), key=lambda item: item[1])[0]
    return fallback


def analyze_repo(
    client: Any,
    repo: Mapping[str, Any],
    *,
    tech_stack: Iterable[str] = (),
    fetch_readme: bool = True,
    now: datetime | None = None,
) -> RepoAnalysis:
    """对一个仓库执行浅分析（4–8 次 REST 请求）。"""
    now = now or utc_now()
    full_name = str(repo["full_name"])
    branch = repo["default_branch"] or "main"

    languages = client.get_languages(full_name) or {}
    tree_payload = client.get_tree(full_name, str(branch))
    readme = client.get_readme(full_name) if fetch_readme else None

    manifests: dict[str, str] = {}
    root_files: set[str] = set()
    if tree_payload is not None:
        for entry in tree_payload.get("tree") or []:
            if not isinstance(entry, Mapping):
                continue
            path = str(entry.get("path") or "")
            if "/" not in path and entry.get("type") == "blob":
                root_files.add(path)
        for name in MANIFEST_PRIORITY:
            if name in root_files:
                text = client.get_file_text(full_name, name)
                if text is not None:
                    manifests[name] = text

    structure = structure_from_tree(tree_payload)
    compose_services = None
    if structure is not None and structure.has_compose:
        compose_path = find_compose_path(tree_payload)
        if compose_path:
            compose_text = client.get_file_text(full_name, compose_path)
            if compose_text is not None:
                compose_services = compose_service_count(compose_text)

    deps = dependency_info(manifests)
    if structure is not None and deps.has_workspaces:
        structure = replace(structure, has_monorepo=True)

    external = find_external_services(readme, manifests)
    loc = loc_from_languages(languages)
    language = _dominant_language(languages, repo["language"])

    low_confidence: list[str] = []
    if loc is None:
        low_confidence.append("loc")
    if deps.count is None:
        low_confidence.append("dep")
    if not external.known:
        low_confidence.append("ext_services")
    if structure is None:
        low_confidence.append("structure")
    if not language:
        low_confidence.append("language")

    s_loc = loc_score(loc)
    s_dep = dependency_score(deps.count)
    s_ext = external_service_score(len(external.services) if external.known else None)
    s_lang, hard_unmatched, _ = language_match_score(language, tech_stack)
    s_license, license_risk = license_score(repo["license_spdx"])
    s_struct = structure_score(structure, compose_services)

    score = repro_score(
        s_loc=s_loc,
        s_dep=s_dep,
        s_ext=s_ext,
        s_lang=s_lang,
        s_license=s_license,
        s_struct=s_struct,
        hard_language_unmatched=hard_unmatched,
    )
    complexity = complexity_score(
        s_loc=s_loc, s_dep=s_dep, s_ext=s_ext, s_struct=s_struct
    )
    grade = complexity_grade(complexity)
    if "loc" in low_confidence or "dep" in low_confidence:
        grade += "*"

    rep_low, rep_high = replication_weeks(
        loc=loc,
        external_services=len(external.services),
        hard_language_unmatched=hard_unmatched,
        has_monorepo=bool(structure and structure.has_monorepo),
    )

    breakdown = {
        "version": ANALYSIS_VERSION,
        "weights": {
            "loc": 0.25,
            "dep": 0.15,
            "ext": 0.10,
            "lang": 0.20,
            "license": 0.20,
            "struct": 0.10,
        },
        "subscores": {
            "loc": s_loc,
            "dep": s_dep,
            "ext": s_ext,
            "lang": s_lang,
            "license": s_license,
            "struct": s_struct,
        },
        "inputs": {
            "loc": loc,
            "dep_count": deps.count,
            "dep_manifest": deps.manifest,
            "ext_services": list(external.services),
            "language": language,
            "license_spdx": repo["license_spdx"],
            "monorepo": bool(structure and structure.has_monorepo),
            "ci_workflows": structure.ci_workflows if structure else None,
        },
        "hard_language_unmatched": hard_unmatched,
        "low_confidence_fields": low_confidence,
    }

    return RepoAnalysis(
        repo_pk=int(repo["id"]),
        full_name=full_name,
        stars_total=repo["stars_total"],
        language=language,
        license_spdx=repo["license_spdx"],
        analyzed_at=iso_z(now),
        languages=dict(languages),
        loc=loc,
        loc_method="api_estimate" if loc is not None else None,
        loc_confidence="medium" if loc is not None else "low",
        dep_count=deps.count,
        dev_dep_count=deps.dev_count,
        dep_manifest=deps.manifest,
        dep_confidence=deps.confidence,
        ext_services=external.services,
        ext_evidence=external.evidence,
        has_docker=structure.has_docker if structure else None,
        has_compose=structure.has_compose if structure else None,
        compose_services=compose_services,
        has_monorepo=structure.has_monorepo if structure else None,
        ci_workflows=structure.ci_workflows if structure else None,
        src_files=structure.src_files if structure else None,
        top_dirs=structure.top_dirs if structure else None,
        tree_truncated=bool(structure.tree_truncated) if structure else False,
        license_risk=license_risk,
        s_loc=s_loc,
        s_dep=s_dep,
        s_ext=s_ext,
        s_lang=s_lang,
        s_license=s_license,
        s_struct=s_struct,
        repro_score=score,
        complexity_score=complexity,
        complexity_grade=grade,
        replication_weeks_min=rep_low,
        replication_weeks_max=rep_high,
        low_confidence_fields=tuple(low_confidence),
        score_breakdown=breakdown,
    )


# -- 批量分析 ---------------------------------------------------------------
@dataclass
class AnalysisSummary:
    analyzed: int = 0
    skipped: int = 0
    failed: int = 0
    requests: int = 0
    errors: list[str] = field(default_factory=list)
    results: list[RepoAnalysis] = field(default_factory=list)

    def as_dict(self, *, top: int = 10, sort: str = "repro") -> dict[str, Any]:
        ordered = sort_analyses(self.results, sort)
        selected = ordered if top <= 0 else ordered[:top]
        return {
            "analyzed": self.analyzed,
            "skipped": self.skipped,
            "failed": self.failed,
            "requests": self.requests,
            "errors": list(self.errors),
            "top": [item.to_dict() for item in selected],
        }


def sort_analyses(
    results: list[RepoAnalysis], sort: str = "repro"
) -> list[RepoAnalysis]:
    if sort == "repro":
        return sorted(results, key=lambda item: (-item.repro_score, item.full_name))
    if sort == "stars":
        return sorted(
            results,
            key=lambda item: (-(item.stars_total or 0), item.full_name),
        )
    if sort == "grade":
        return sorted(
            results,
            key=lambda item: (item.complexity_score, item.full_name),
        )
    raise ValueError(f"unsupported sort: {sort}")


def load_analysis_results(db: Any) -> list[RepoAnalysis]:
    """从数据库重建已落库的分析结果，供 CLI 展示缓存命中时的 Top N。"""
    rows = db.list_repo_analysis_joined()
    results: list[RepoAnalysis] = []
    for row in rows:
        try:
            languages = json.loads(row["languages"] or "{}")
            services = json.loads(row["ext_services"] or "[]")
            evidence = json.loads(row["ext_evidence"] or "[]")
            low_confidence = json.loads(row["low_confidence_fields"] or "[]")
            breakdown = json.loads(row["score_breakdown"] or "{}")
        except (TypeError, ValueError):
            continue
        results.append(
            RepoAnalysis(
                repo_pk=int(row["repo_pk"]),
                full_name=str(row["full_name"]),
                stars_total=row["stars_total"],
                language=_dominant_language(languages, row["repo_language"]),
                license_spdx=row["license_spdx"],
                analyzed_at=str(row["analyzed_at"]),
                languages=languages,
                loc=row["loc"],
                loc_method=row["loc_method"],
                loc_confidence=row["loc_confidence"] or "low",
                dep_count=row["dep_count"],
                dev_dep_count=row["dev_dep_count"],
                dep_manifest=row["dep_manifest"],
                dep_confidence=row["dep_confidence"] or "low",
                ext_services=tuple(services),
                ext_evidence=tuple(evidence),
                has_docker=None if row["has_docker"] is None else bool(row["has_docker"]),
                has_compose=None if row["has_compose"] is None else bool(row["has_compose"]),
                compose_services=row["compose_services"],
                has_monorepo=(
                    None if row["has_monorepo"] is None else bool(row["has_monorepo"])
                ),
                ci_workflows=row["ci_workflows"],
                src_files=row["src_files"],
                top_dirs=row["top_dirs"],
                tree_truncated=bool(row["tree_truncated"]),
                license_risk=row["license_risk"] or "unknown",
                s_loc=row["s_loc"] or 0.0,
                s_dep=row["s_dep"] or 0.0,
                s_ext=row["s_ext"] or 0.0,
                s_lang=row["s_lang"] or 0.0,
                s_license=row["s_license"] or 0.0,
                s_struct=row["s_struct"] or 0.0,
                repro_score=row["repro_score"] or 0.0,
                complexity_score=row["complexity_score"] or 0.0,
                complexity_grade=row["complexity_grade"] or "",
                replication_weeks_min=row["replication_weeks_min"],
                replication_weeks_max=row["replication_weeks_max"],
                low_confidence_fields=tuple(low_confidence),
                score_breakdown=breakdown,
            )
        )
    return results


def run_analysis(
    db: Any,
    client: Any,
    *,
    limit: int = 100,
    refresh_hours: float = 24,
    force: bool = False,
    tech_stack: Iterable[str] = (),
    fetch_readme: bool = True,
    now: datetime | None = None,
    progress: Callable[[str, float], None] | None = None,
) -> AnalysisSummary:
    """批量执行浅分析并落库。

    单个仓库失败只记录错误，不覆盖其上一次成功结果；遇到限流立即停止。
    """
    from .github import GitHubError, GitHubRateLimitError

    now = now or utc_now()
    stale_before = iso_z(
        now if force else now - timedelta(hours=refresh_hours)
    )
    if hasattr(db, "prune_repo_analysis"):
        db.prune_repo_analysis()
    repos = db.list_repos_for_analysis(limit, stale_before)
    summary = AnalysisSummary()
    if hasattr(db, "count_fresh_analysis"):
        summary.skipped = db.count_fresh_analysis(stale_before)
    request_start = getattr(getattr(client, "transport", None), "count", 0)
    total = len(repos)
    if progress:
        progress("准备浅分析", 0.0)

    for index, repo in enumerate(repos, start=1):
        try:
            result = analyze_repo(
                client,
                repo,
                tech_stack=tech_stack,
                fetch_readme=fetch_readme,
                now=now,
            )
        except GitHubRateLimitError as exc:
            summary.errors.append(f"{repo['full_name']}: {exc}")
            summary.failed += 1
            break
        except GitHubError as exc:
            summary.errors.append(f"{repo['full_name']}: {exc}")
            summary.failed += 1
            if progress:
                progress(f"浅分析 {repo['full_name']}", index / max(1, total))
            continue

        db.upsert_repo_analysis(result.to_row())
        summary.results.append(result)
        summary.analyzed += 1
        if progress:
            progress(f"浅分析 {repo['full_name']}", index / max(1, total))

    transport = getattr(client, "transport", None)
    if transport is not None and hasattr(transport, "count"):
        summary.requests = transport.count - request_start
    if progress:
        progress("浅分析完成", 1.0)
    return summary

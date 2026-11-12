"""FR-102 / FR-104：GitHub REST 客户端与 Token 池。

- 元数据补全（仓库字段、License、topics、贡献者数）
- Token 轮询：剩余配额低于水位或遇到 403/429 时切换下一个 Token
- 对 5xx 做指数退避重试；404 视为仓库不存在，返回 None
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping
from urllib.parse import quote

from .http import HttpTransport, TransportError
from .models import RepoMetadata


class GitHubError(RuntimeError):
    """GitHub 客户端错误基类。"""


class GitHubAuthError(GitHubError):
    """401：Token 无效或权限不足。"""


class GitHubRateLimitError(GitHubError):
    """403/429：配额耗尽且无可用 Token。"""


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _error_message(text: str) -> str | None:
    """从 GitHub 错误 JSON 中提取 message，用于更清晰的异常信息。"""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, dict):
        message = payload.get("message")
        if message:
            return str(message)[:200]
    return None


def _decode_content(payload: object) -> str | None:
    """解码 Contents API 的 base64 文本；非文件或无法解码时返回 None。"""
    if not isinstance(payload, dict):
        return None
    encoded = payload.get("content")
    if not isinstance(encoded, str) or payload.get("encoding") != "base64":
        return None
    try:
        raw = base64.b64decode(encoded)
    except (ValueError, TypeError):
        return None
    return raw.decode("utf-8", errors="replace")


@dataclass
class TokenState:
    token: str
    remaining: int | None = None
    reset_at: float | None = None
    cooling_until: float | None = None


class TokenPool:
    """简单的 Token 轮询器。

    策略：优先使用当前 Token；当剩余配额低于 low_watermark，或收到
    403/429，或剩余配额为 0 时，切换到下一个 Token。冷却中的 Token
    在冷却到期前不会被选中。
    """

    def __init__(
        self,
        tokens: Iterable[str] = (),
        *,
        now: Callable[[], float] = time.time,
        low_watermark: int = 100,
    ) -> None:
        self._states = [TokenState(token=token) for token in tokens if token]
        self._index = 0
        self._now = now
        self._low_watermark = low_watermark

    def __len__(self) -> int:
        return len(self._states)

    def acquire(self) -> str | None:
        count = len(self._states)
        if count == 0:
            return None
        for _ in range(count):
            state = self._states[self._index]
            if state.cooling_until is None or state.cooling_until <= self._now():
                return state.token
            self._advance()
        return None

    def report(
        self, token: str | None, status: int, headers: Mapping[str, str]
    ) -> None:
        if token is None:
            return
        state = next((s for s in self._states if s.token == token), None)
        if state is None:
            return

        remaining_raw = _header(headers, "x-ratelimit-remaining")
        reset_raw = _header(headers, "x-ratelimit-reset")
        if remaining_raw is not None:
            try:
                state.remaining = int(remaining_raw)
            except ValueError:
                pass
        if reset_raw is not None:
            try:
                state.reset_at = float(reset_raw)
            except ValueError:
                pass

        if status in (401, 403, 429) or (
            state.remaining is not None and state.remaining <= 0
        ):
            reset = state.reset_at
            state.cooling_until = (
                reset if reset is not None and reset > self._now() else self._now() + 60
            )
            self._advance()
            return

        if state.remaining is not None and state.remaining <= self._low_watermark:
            self._advance()

    def _advance(self) -> None:
        if self._states:
            self._index = (self._index + 1) % len(self._states)


def metadata_from_payload(payload: Mapping) -> RepoMetadata:
    """把 REST 仓库 JSON 转成 RepoMetadata。字段缺失时尽量降级而非崩溃。"""

    full_name = payload.get("full_name") or ""
    owner_payload = payload.get("owner") or {}
    owner = owner_payload.get("login") or (
        full_name.split("/", 1)[0] if "/" in full_name else ""
    )
    name = payload.get("name") or (
        full_name.split("/", 1)[1] if "/" in full_name else ""
    )

    license_payload = payload.get("license") or {}
    license_spdx = (
        license_payload.get("spdx_id") if isinstance(license_payload, Mapping) else None
    )
    if license_spdx in (None, "", "NOASSERTION"):
        license_spdx = None

    topics = payload.get("topics") or []

    return RepoMetadata(
        repo_id=int(payload["id"]),
        full_name=full_name,
        owner=owner,
        name=name,
        html_url=payload.get("html_url")
        or (f"https://github.com/{full_name}" if full_name else ""),
        description=payload.get("description"),
        homepage=payload.get("homepage"),
        language=payload.get("language"),
        license_spdx=license_spdx,
        topics=[str(topic).lower() for topic in topics if topic],
        stars_total=payload.get("stargazers_count"),
        forks=payload.get("forks_count"),
        open_issues=payload.get("open_issues_count"),
        subscribers=payload.get("subscribers_count") or payload.get("watchers_count"),
        size_kb=payload.get("size"),
        default_branch=payload.get("default_branch"),
        archived=bool(payload.get("archived", False)),
        fork=bool(payload.get("fork", False)),
        disabled=bool(payload.get("disabled", False)),
        created_at=payload.get("created_at"),
        pushed_at=payload.get("pushed_at"),
    )


class GitHubClient:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        tokens: TokenPool | Iterable[str] | None = None,
        base_url: str = "https://api.github.com",
        user_agent: str = "RepoScout/0.1",
        retries: int = 3,
        timeout: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if isinstance(tokens, TokenPool):
            self.tokens = tokens
        else:
            self.tokens = TokenPool(tokens or ())
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.retries = retries
        self.timeout = timeout
        self.sleep = sleep

    # -- public API --------------------------------------------------------
    def get_repo(self, full_name: str) -> RepoMetadata | None:
        response = self._request(f"/repos/{full_name}")
        if response is None:
            return None
        try:
            payload = json.loads(response.text)
            return metadata_from_payload(payload)
        except (ValueError, KeyError, TypeError) as exc:
            raise GitHubError(f"invalid repo payload for {full_name}: {exc}") from exc

    def get_star_history_page(
        self, full_name: str, *, page: int = 1, per_page: int = 30
    ) -> list[dict] | None:
        """FR-103：官方星标历史（周级聚合）。

        GET /repos/{owner}/{repo}/stargazers/history
        - per_page 最大 30，page 最大 100
        - 返回按新→旧排列的周桶，当前周为部分数据
        - 公开仓库无需 Token；未鉴权配额 60 次/小时
        """
        if not 1 <= page <= 100:
            raise ValueError("page must be between 1 and 100")
        if not 1 <= per_page <= 30:
            raise ValueError("per_page must be between 1 and 30")

        response = self._request(
            f"/repos/{full_name}/stargazers/history?per_page={per_page}&page={page}",
            api_version="2026-03-10",
        )
        if response is None:
            return None
        try:
            payload = json.loads(response.text)
        except ValueError as exc:
            raise GitHubError(
                f"invalid star history payload for {full_name}: {exc}"
            ) from exc
        if not isinstance(payload, list):
            raise GitHubError(
                f"invalid star history payload for {full_name}: expected a list"
            )
        return payload

    def get_contributors_count(self, full_name: str) -> int | None:
        response = self._request(
            f"/repos/{full_name}/contributors?per_page=1&anon=false"
        )
        if response is None:
            return None

        link = _header(response.headers, "link") or ""
        match = re.search(r'[?&]page=(\d+)>;\s*rel="last"', link)
        if match:
            return int(match.group(1))

        try:
            payload = json.loads(response.text)
        except ValueError:
            return None
        if isinstance(payload, list):
            return len(payload)
        return None

    # -- M3 light analysis -------------------------------------------------
    def get_languages(self, full_name: str) -> dict[str, int] | None:
        """FR-301：各语言的字节数；仓库不存在返回 None。"""
        response = self._request(f"/repos/{full_name}/languages")
        if response is None:
            return None
        try:
            payload = json.loads(response.text)
        except ValueError as exc:
            raise GitHubError(f"invalid languages payload for {full_name}") from exc
        if not isinstance(payload, dict):
            raise GitHubError(f"invalid languages payload for {full_name}: expected object")
        languages: dict[str, int] = {}
        for language, size in payload.items():
            try:
                languages[str(language)] = int(size)
            except (TypeError, ValueError):
                continue
        return languages

    def get_tree(self, full_name: str, ref: str) -> dict | None:
        """FR-304：Git Trees API（recursive=1），返回原始 payload。"""
        response = self._request(
            f"/repos/{full_name}/git/trees/{quote(ref, safe='')}?recursive=1"
        )
        if response is None:
            return None
        try:
            payload = json.loads(response.text)
        except ValueError as exc:
            raise GitHubError(f"invalid tree payload for {full_name}") from exc
        if not isinstance(payload, dict):
            raise GitHubError(f"invalid tree payload for {full_name}: expected object")
        return payload

    def get_readme(self, full_name: str) -> str | None:
        """读取 README（Contents API，base64），最多保留 100KB。"""
        response = self._request(f"/repos/{full_name}/readme")
        if response is None:
            return None
        try:
            payload = json.loads(response.text)
        except ValueError as exc:
            raise GitHubError(f"invalid readme payload for {full_name}") from exc
        text = _decode_content(payload)
        return text[:100_000] if text is not None else None

    def get_file_text(self, full_name: str, path: str) -> str | None:
        """读取仓库中的单个文本文件（Contents API）。"""
        response = self._request(
            f"/repos/{full_name}/contents/{quote(path, safe='/')}"
        )
        if response is None:
            return None
        try:
            payload = json.loads(response.text)
        except ValueError as exc:
            raise GitHubError(f"invalid content payload for {full_name}/{path}") from exc
        if isinstance(payload, list):
            return None
        return _decode_content(payload)

    # -- M4 commercial signals ---------------------------------------------
    def get_issues(
        self,
        full_name: str,
        *,
        state: str = "open",
        per_page: int = 100,
        sort: str = "created",
        direction: str = "desc",
    ) -> list[dict] | None:
        """FR-401：最近 issues（排除 PR）；仓库不存在返回 None。"""
        per_page = max(1, min(int(per_page), 100))
        response = self._request(
            f"/repos/{full_name}/issues"
            f"?state={quote(state, safe='')}&per_page={per_page}"
            f"&sort={quote(sort, safe='')}&direction={quote(direction, safe='')}"
        )
        if response is None:
            return None
        try:
            payload = json.loads(response.text)
        except ValueError as exc:
            raise GitHubError(f"invalid issues payload for {full_name}") from exc
        if not isinstance(payload, list):
            raise GitHubError(f"invalid issues payload for {full_name}: expected a list")
        return [
            item
            for item in payload
            if isinstance(item, dict) and "pull_request" not in item
        ]

    def search_repositories(self, query: str, *, per_page: int = 1) -> dict | None:
        """FR-403：GitHub Search API（独立限速 30 次/分钟）。"""
        per_page = max(1, min(int(per_page), 100))
        response = self._request(
            f"/search/repositories?q={quote(query, safe='')}&per_page={per_page}"
        )
        if response is None:
            return None
        try:
            payload = json.loads(response.text)
        except ValueError as exc:
            raise GitHubError(f"invalid search payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise GitHubError("invalid search payload: expected an object")
        return payload

    # -- internals ---------------------------------------------------------
    def _request(
        self,
        path: str,
        *,
        api_version: str = "2022-11-28",
        accept: str = "application/vnd.github+json",
    ):
        url = (
            path
            if path.startswith(("http://", "https://"))
            else f"{self.base_url}{path}"
        )
        last_error = "request failed"

        for attempt in range(self.retries + 1):
            token = self.tokens.acquire()
            headers = {
                "Accept": accept,
                "X-GitHub-Api-Version": api_version,
                "User-Agent": self.user_agent,
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"

            try:
                response = self.transport.get(url, headers=headers, timeout=self.timeout)
            except TransportError as exc:
                last_error = f"transport error: {exc}"
                if attempt < self.retries:
                    self.sleep(0.5 * (2**attempt))
                    continue
                raise GitHubError(last_error) from exc

            self.tokens.report(token, response.status, response.headers)

            if response.status in (200, 201):
                return response
            if response.status == 404:
                return None

            if response.status == 401:
                message = _error_message(response.text)
                last_error = "unauthorized (401)" + (
                    f": {message}" if message else ""
                )
                if attempt < self.retries:
                    continue
                raise GitHubAuthError(last_error)

            if response.status in (403, 429):
                message = _error_message(response.text)
                last_error = f"rate limited ({response.status})" + (
                    f": {message}" if message else ""
                )
                if attempt < self.retries:
                    continue
                raise GitHubRateLimitError(last_error)

            if response.status >= 500:
                message = _error_message(response.text)
                last_error = f"server error ({response.status})" + (
                    f": {message}" if message else ""
                )
                if attempt < self.retries:
                    self.sleep(0.5 * (2**attempt))
                    continue
                raise GitHubError(last_error)

            raise GitHubError(f"unexpected HTTP {response.status} for {url}")

        raise GitHubError(last_error)

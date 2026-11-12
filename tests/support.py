"""测试辅助：离线 FakeTransport 与 payload 构造器。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from repo_scout.http import HttpResponse

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def json_response(
    payload, status: int = 200, headers: dict[str, str] | None = None
) -> HttpResponse:
    return HttpResponse(
        status=status,
        text=json.dumps(payload),
        headers=headers or {},
    )


@dataclass
class FakeTransport:
    handler: Callable[[str, dict[str, str]], HttpResponse]
    calls: list[dict] = field(default_factory=list)

    def get(self, url, *, headers=None, timeout=None) -> HttpResponse:
        header_map = dict(headers or {})
        self.calls.append({"url": url, "headers": header_map})
        return self.handler(url, header_map)


def repo_payload(
    full_name: str = "acme/notes-ai",
    repo_id: int = 101,
    *,
    stars: int = 8200,
    forks: int = 1100,
    open_issues: int = 42,
    subscribers: int = 300,
    language: str | None = "TypeScript",
    license_spdx: str | None = "MIT",
    topics: list[str] | None = None,
    description: str | None = "Local-first AI note-taking app.",
    homepage: str | None = None,
    size_kb: int = 1200,
    archived: bool = False,
    fork: bool = False,
    disabled: bool = False,
) -> dict:
    owner, name = full_name.split("/", 1)
    return {
        "id": repo_id,
        "full_name": full_name,
        "name": name,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{full_name}",
        "description": description,
        "homepage": homepage,
        "language": language,
        "license": {"spdx_id": license_spdx, "name": license_spdx}
        if license_spdx
        else None,
        "topics": topics or [],
        "stargazers_count": stars,
        "forks_count": forks,
        "open_issues_count": open_issues,
        "subscribers_count": subscribers,
        "watchers_count": stars,
        "size": size_kb,
        "default_branch": "main",
        "archived": archived,
        "fork": fork,
        "disabled": disabled,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-11-10T00:00:00Z",
    }

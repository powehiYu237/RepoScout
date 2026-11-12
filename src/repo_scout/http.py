"""HTTP 传输层抽象。

生产实现基于 httpx；测试通过注入 FakeTransport 完全离线。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class TransportError(RuntimeError):
    """网络层错误（超时、连接失败等）。"""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    text: str
    headers: Mapping[str, str]
    url: str = ""


class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse: ...


class HttpxTransport:
    """基于 httpx 的同步 HTTP 客户端（连接复用）。"""

    def __init__(self, user_agent: str, timeout: float = 20.0) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确提示
            raise RuntimeError("httpx is required: pip install httpx") from exc

        self._httpx = httpx
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        try:
            response = self._client.get(url, headers=headers, timeout=timeout)
        except self._httpx.HTTPError as exc:
            raise TransportError(str(exc)) from exc
        return HttpResponse(
            status=response.status_code,
            text=response.text,
            headers={key.lower(): value for key, value in response.headers.items()},
            url=str(response.url),
        )

    def close(self) -> None:  # pragma: no cover - 资源释放
        self._client.close()

    def __enter__(self) -> "HttpxTransport":
        return self

    def __exit__(self, *exc_info: object) -> None:  # pragma: no cover
        self.close()


class CountingTransport:
    """统计请求次数，供 ingest_runs 记录配额使用情况。"""

    def __init__(self, inner: HttpTransport) -> None:
        self._inner = inner
        self.count = 0

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        self.count += 1
        return self._inner.get(url, headers=headers, timeout=timeout)

"""FR-103：GitHub 官方星标历史（周级聚合）。

调查结论（2026-11-11 实测）：

- 官方端点：``GET /repos/{owner}/{repo}/stargazers/history``
- OpenAPI 规格：per_page 默认/最大 30，page 最大 100，响应按新→旧排列；
  ``week`` 为周起始 Unix 时间戳，``total`` 为该周新增星数，
  ``days`` 为从周日开始的 7 天计数；无星的周也会返回 0。
- 公开仓库无需 Token；未鉴权受核心配额限制（60 次/小时），
  配置 PAT 后为 5000 次/小时。
- 当前周是部分数据；跨周/日边界不保证严格 UTC 对齐。
- 旧的 ``/repos/{owner}/{repo}/stargazers`` 列表接口已限制为仓库管理员，
  未鉴权返回 401，因此不再作为星标历史数据源。

本模块只负责解析、分页与"周增量 → 累计星数"换算，HTTP 调用复用
``GitHubClient``（含 Token 池与重试）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .github import GitHubClient

MAX_WEEKS_PER_PAGE = 30
MAX_PAGES = 100
MAX_WEEKS = MAX_WEEKS_PER_PAGE * MAX_PAGES

SOURCE = "github_stargazers_history"


class StarHistoryError(RuntimeError):
    """星标历史获取或解析失败。"""


class StarHistoryParseError(StarHistoryError):
    """响应结构不符合预期。"""


@dataclass(frozen=True)
class StarWeek:
    week_start: date
    total: int
    days: tuple[int, ...]

    @property
    def week_iso(self) -> str:
        return self.week_start.isoformat()


def _as_non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise StarHistoryParseError(f"{field} must be an integer, got bool")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise StarHistoryParseError(f"{field} must be an integer: {value!r}") from exc
    if number < 0:
        raise StarHistoryParseError(f"{field} must be >= 0: {number}")
    return number


def parse_star_history(payload: object) -> list[StarWeek]:
    """把 API 的 JSON 数组解析为 StarWeek 列表（按周倒序、去重）。"""
    if not isinstance(payload, list):
        raise StarHistoryParseError("star history payload must be a list")

    parsed: list[StarWeek] = []
    seen: set[date] = set()

    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise StarHistoryParseError(f"week item #{index} must be an object")
        if "week" not in item or "total" not in item:
            raise StarHistoryParseError(
                f"week item #{index} is missing required fields"
            )

        timestamp = _as_non_negative_int(item["week"], "week")
        total = _as_non_negative_int(item["total"], "total")

        days_raw = item.get("days") or []
        if not isinstance(days_raw, (list, tuple)):
            raise StarHistoryParseError(f"days of week #{index} must be a list")
        days = tuple(_as_non_negative_int(day, "days") for day in list(days_raw)[:7])
        days = days + (0,) * (7 - len(days))

        try:
            week_start = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError) as exc:
            raise StarHistoryParseError(f"invalid week timestamp: {timestamp}") from exc

        if week_start in seen:
            continue
        seen.add(week_start)
        parsed.append(StarWeek(week_start=week_start, total=total, days=days))

    parsed.sort(key=lambda week: week.week_start, reverse=True)
    return parsed


def fetch_star_history(
    client: GitHubClient,
    full_name: str,
    *,
    weeks: int = 30,
    per_page: int = MAX_WEEKS_PER_PAGE,
) -> list[StarWeek] | None:
    """分页拉取最近的 ``weeks`` 周星标历史。

    - 仓库不存在返回 ``None``；
    - 页面为空或不足一页时提前停止；
    - 网络/配额错误向上抛出 GitHub 客户端异常。
    """
    if weeks <= 0:
        raise ValueError("weeks must be positive")
    if not 1 <= per_page <= MAX_WEEKS_PER_PAGE:
        raise ValueError(f"per_page must be between 1 and {MAX_WEEKS_PER_PAGE}")

    weeks = min(weeks, MAX_WEEKS)
    pages_needed = min((weeks + per_page - 1) // per_page, MAX_PAGES)

    collected: list[StarWeek] = []
    seen: set[date] = set()

    for page in range(1, pages_needed + 1):
        payload = client.get_star_history_page(
            full_name, page=page, per_page=per_page
        )
        if payload is None:
            return None if not collected else collected

        batch = parse_star_history(payload)
        if not batch:
            break

        for week in batch:
            if week.week_start not in seen:
                seen.add(week.week_start)
                collected.append(week)

        if len(batch) < per_page or len(collected) >= weeks:
            break

    collected.sort(key=lambda week: week.week_start, reverse=True)
    return collected[:weeks]


def compute_weekly_rows(
    weeks: list[StarWeek],
    current_total: int | None,
    *,
    source: str = SOURCE,
) -> list[dict]:
    """把"每周新增"换算为每周结束时的累计星数。

    最新一周的 ``stars_end`` 取当前总星数；更早一周减去更新周的新增星。
    当前总星数缺失时 ``stars_end`` 为 None。

    注：GitHub 只统计"当前仍存在的星"（取消星标的用户不在历史中），
    因此按新增量倒推是近似值；本周为部分数据。
    """
    rows: list[dict] = []
    newer_sum = 0
    for week in weeks:
        stars_end = None
        if current_total is not None:
            stars_end = max(0, int(current_total) - newer_sum)
        rows.append(
            {
                "week_start": week.week_iso,
                "delta": week.total,
                "stars_end": stars_end,
                "source": source,
            }
        )
        newer_sum += week.total
    return rows

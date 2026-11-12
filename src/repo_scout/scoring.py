"""M5 综合评分、硬闸门与列表查询（FR-501 ~ FR-505）。

本模块只做纯计算与筛选，不依赖 HTTP/UI：

- G1–G7 硬闸门；
- TotalScore = α·ReproScore + (1−α)·BizScore；
- 风险标签、规则化一句话结论、窗口期参考值；
- 列表过滤、排序、分页与详情数据组装。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from .metrics import parse_ts
from .util import utc_now

SCORING_VERSION = "m5-v1.0.0"
DEFAULT_MIN_STARS = 500
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

PRESETS: dict[str, dict[str, Any]] = {
    "balanced": {
        "key": "balanced",
        "label": "平衡",
        "alpha": 0.5,
        "sort": "total",
        "hint": "复刻与商业各占一半",
    },
    "conservative": {
        "key": "conservative",
        "label": "稳",
        "alpha": 0.7,
        "sort": "total",
        "hint": "更看重可复刻性",
    },
    "profit": {
        "key": "profit",
        "label": "赚",
        "alpha": 0.3,
        "sort": "total",
        "hint": "更看重商业价值",
    },
    "small": {
        "key": "small",
        "label": "小项目优先",
        "alpha": 0.6,
        "sort": "total",
        "max_stars": 20_000,
        "max_loc": 20_000,
        "hint": "≤20k 星且 ≤20k 行",
    },
    "trend": {
        "key": "trend",
        "label": "热点",
        "alpha": 0.5,
        "sort": "trend",
        "hint": "按 TrendScore 排序",
    },
    "velocity": {
        "key": "velocity",
        "label": "增速",
        "alpha": 0.5,
        "sort": "velocity",
        "hint": "按 7 日日均增速排序",
    },
    "repro": {
        "key": "repro",
        "label": "复刻优先",
        "alpha": 0.5,
        "sort": "repro",
        "hint": "按 ReproScore 排序",
    },
    "biz": {
        "key": "biz",
        "label": "商业优先",
        "alpha": 0.5,
        "sort": "biz",
        "hint": "按 BizScore 排序",
    },
}

LICENSE_LABELS = {
    "green": "可商用",
    "yellow": "修改需开源",
    "red": "传染性开源",
    "gray": "无许可",
    "unknown": "需人工复核",
}

GATE_INFO = {
    "g1": {"label": "镜像/归档", "action": "排除"},
    "g2": {"label": "清单/教程/文档", "action": "排除"},
    "g3": {"label": "GPL/AGPL", "action": "默认隐藏"},
    "g4": {"label": "无 License", "action": "显示并标注"},
    "g5": {"label": "巨石项目", "action": "默认隐藏"},
    "g6": {"label": "星数过低", "action": "默认隐藏"},
    "g7": {"label": "刷星嫌疑", "action": "显示风险标"},
}

SORT_KEYS = {
    "total": lambda item: item["scores"]["total"],
    "trend": lambda item: item["growth"]["trend_score"],
    "velocity": lambda item: item["growth"]["velocity"],
    "delta7": lambda item: item["growth"]["d7"],
    "delta30": lambda item: item["growth"]["d30"],
    "delta24": lambda item: item["growth"]["d24"],
    "acceleration": lambda item: item["growth"]["acceleration"],
    "repro": lambda item: item["scores"]["repro"],
    "biz": lambda item: item["scores"]["biz"],
    "stars": lambda item: item["stars"],
    "competitors": lambda item: item["biz"]["competitor_count"],
}


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _unwrap(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _float(value: Any, default: float | None = None) -> float | None:
    value = _unwrap(value)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int | None = None) -> int | None:
    value = _unwrap(value)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def license_view(spdx: str | None, risk: str | None) -> dict[str, Any]:
    level = risk or ("gray" if not spdx else "unknown")
    return {
        "spdx": spdx,
        "risk": level,
        "label": LICENSE_LABELS.get(level, "需人工复核"),
    }


def _low_confidence(row: Mapping[str, Any]) -> list[str]:
    fields: list[str] = []
    for key in ("analysis_low_confidence", "commercial_low_confidence"):
        try:
            payload = json.loads(row[key] or "[]")
        except (TypeError, ValueError):
            payload = []
        if isinstance(payload, list):
            fields.extend(str(item) for item in payload)
    return sorted(set(fields))


def window_estimate(item: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    first_seen = item.get("first_seen_at")
    if not first_seen:
        return {"days": None, "basis": ["缺少首次发现时间"], "closed": False}
    try:
        first = parse_ts(str(first_seen))
    except ValueError:
        return {"days": None, "basis": ["首次发现时间格式异常"], "closed": False}
    days = (now - first).total_seconds() / 86400
    remaining = max(0.0, min(14.0, 14.0 - days))
    basis = [f"首次发现 {days:.1f} 天前"]
    closed = False
    if item.get("paid_version") == "likely":
        remaining = 0.0
        closed = True
        basis.append("疑似已商业化")
    acceleration = item.get("acceleration")
    if acceleration is not None and float(acceleration) < 0:
        remaining *= 0.6
        basis.append("加速率转负 ×0.6")
    return {
        "days": round(remaining, 1),
        "basis": basis,
        "closed": closed,
        "note": "剩余窗口为启发式参考值，不构成精确预测",
    }


def build_reason(item: Mapping[str, Any], score: Mapping[str, Any]) -> str:
    parts: list[str] = []
    d7 = item["growth"]["d7"]
    d24 = item["growth"]["d24"]
    if d7 is not None:
        parts.append(f"7 日 {d7:+,}")
    elif d24 is not None:
        parts.append(f"24h {d24:+,}")
    parts.append(f"复刻 {item['scores']['repro']:.0f} {item['repro']['grade']}")
    parts.append(f"商业 {item['scores']['biz']:.0f}")
    if item["biz"]["paid_version"] == "likely":
        parts.append("疑似已商业化")
    elif item["biz"]["competitor_count"] == 0:
        parts.append("暂未发现竞品")
    elif item["biz"]["competitor_count"] is not None and item["biz"]["competitor_count"] <= 4:
        parts.append(f"竞品 {item['biz']['competitor_count']}")
    if item["gates"]["g3"]:
        parts.append("GPL 风险")
    if item["gates"]["g7"]:
        parts.append("增长异常")
    return "，".join(parts) + "。"


def score_repo(
    row: Mapping[str, Any],
    *,
    now: datetime,
    alpha: float = 0.5,
    min_stars: int = DEFAULT_MIN_STARS,
) -> dict[str, Any]:
    if not isinstance(row, dict):
        row = dict(row)
    repo_id = _int(row["id"]) or 0
    category = str(row["category"] or "normal")
    excluded = bool(row["excluded"])
    is_fork = bool(row["is_fork"])
    is_archived = bool(row["is_archived"])
    is_disabled = bool(row["is_disabled"])
    stars = _int(row["stars_total"], 0) or 0
    license_spdx = row["license_spdx"]
    license_risk = row["license_risk"] or (
        "gray" if not license_spdx else "unknown"
    )
    loc = _int(row["loc"])
    repro = _float(row["repro_score"], 60.0) or 60.0
    biz = _float(row["biz_score"], 60.0) or 60.0
    total = round(alpha * repro + (1 - alpha) * biz, 2)

    g1 = bool(is_fork or is_archived or is_disabled or category in ("mirror", "archived"))
    g2 = (not g1) and (excluded or category in ("awesome", "tutorial", "docs"))
    g3 = license_risk == "red"
    g4 = not license_spdx
    g5 = (loc is not None and loc >= 50_000) or (
        loc is None and stars >= 100_000
    )
    g6 = stars < min_stars
    suspicious = bool(
        row["anomaly"]
        or row["burst"]
        or row["suspicious_stars"]
        or (
            row["stars_per_contributor"] is not None
            and float(row["stars_per_contributor"]) > 10_000
        )
    )
    gates = {
        "g1": g1,
        "g2": g2,
        "g3": g3,
        "g4": g4,
        "g5": g5,
        "g6": g6,
        "g7": suspicious,
    }
    visible = not (g1 or g2 or g3 or g5 or g6)

    risks: list[dict[str, str]] = []
    if g3:
        risks.append({"key": "gpl", "label": "GPL/AGPL", "severity": "high"})
    if g4:
        risks.append({"key": "no_license", "label": "无 License", "severity": "medium"})
    if g5:
        risks.append({"key": "giant", "label": "巨石", "severity": "medium"})
    if suspicious:
        risks.append({"key": "suspicious", "label": "刷星嫌疑", "severity": "high"})
    paid_version = row["paid_version"] or "none"
    if paid_version == "likely":
        risks.append({"key": "commercialized", "label": "已商业化", "severity": "medium"})
    elif paid_version == "possible":
        risks.append({"key": "possible_commercial", "label": "疑似商业化", "severity": "low"})
    low = _low_confidence(row)
    if low:
        risks.append({"key": "low_confidence", "label": "数据不足", "severity": "low"})

    topics = _json(row["topics"], [])
    if not isinstance(topics, list):
        topics = []
    ext_services = _json(row["ext_services"], [])
    if not isinstance(ext_services, list):
        ext_services = []
    monetization_paths = _json(row["monetization_paths"], [])
    if not isinstance(monetization_paths, list):
        monetization_paths = []
    analysis_subscores = {
        "loc": _float(row["s_loc"]),
        "dep": _float(row["s_dep"]),
        "ext": _float(row["s_ext"]),
        "lang": _float(row["s_lang"]),
        "license": _float(row["s_license"]),
        "struct": _float(row["s_struct"]),
    }
    commercial_subscores = {
        "market": _float(row["market_alignment"]),
        "whitespace": _float(row["whitespace"]),
        "competition": _float(row["competition"]),
        "audience": _float(row["audience_score"]),
        "differentiation": _float(row["differentiation_score"]),
    }
    grade = str(row["complexity_grade"] or "—")
    replication = [
        _float(row["replication_weeks_min"]),
        _float(row["replication_weeks_max"]),
    ]

    item: dict[str, Any] = {
        "id": repo_id,
        "full_name": str(row["full_name"]),
        "owner": str(row["owner"]),
        "name": str(row["name"]),
        "description": row["description"],
        "url": str(row["html_url"]),
        "homepage": row["homepage"],
        "language": row["language"],
        "topics": [str(topic) for topic in topics],
        "stars": stars,
        "forks": _int(row["forks"]),
        "issues": _int(row["open_issues"]),
        "contributors": _int(row["contributors"]),
        "created_at": row["created_at"],
        "pushed_at": row["pushed_at"],
        "first_seen_at": row["first_seen_at"],
        "category": category,
        "license": license_view(license_spdx, license_risk),
        "scores": {
            "total": total,
            "repro": round(repro, 2),
            "biz": round(biz, 2),
            "trend": _float(row["trend_score"]),
            "alpha": alpha,
        },
        "growth": {
            "d24": _int(row["delta_24h"]),
            "d7": _int(row["delta_7d"]),
            "d30": _int(row["delta_30d"]),
            "velocity": _float(row["velocity_7d"]),
            "velocity_prev": _float(row["velocity_prev"]),
            "acceleration": _float(row["acceleration"]),
            "acceleration_available": bool(row["acceleration_available"]),
            "trend_score": _float(row["trend_score"]),
            "history_confidence": row["history_confidence"] or "low",
            "metrics_at": row["metrics_at"],
        },
        "repro": {
            "loc": loc,
            "loc_method": row["loc_method"],
            "loc_confidence": row["loc_confidence"],
            "dep_count": _int(row["dep_count"]),
            "dep_manifest": row["dep_manifest"],
            "ext_services": [str(service) for service in ext_services],
            "monorepo": bool(row["has_monorepo"]),
            "ci_workflows": _int(row["ci_workflows"]),
            "src_files": _int(row["src_files"]),
            "top_dirs": _int(row["top_dirs"]),
            "complexity": _float(row["complexity_score"]),
            "grade": grade,
            "replication_weeks": replication,
            "deep": row["deep_analyzed_at"] is not None,
            "subscores": analysis_subscores,
            "low_confidence": _json(row["analysis_low_confidence"], []),
        },
        "biz": {
            "paid_intent": _int(row["paid_intent_score"], 40),
            "paid_intent_hits": _int(row["paid_intent_hits"], 0),
            "paid_version": paid_version,
            "competitor_count": _int(row["competitor_count"]),
            "competitor_query": row["competitor_query"],
            "audience": _int(row["audience_score"], 50),
            "audience_topic": row["audience_topic"],
            "market": commercial_subscores["market"],
            "whitespace": commercial_subscores["whitespace"],
            "competition": commercial_subscores["competition"],
            "differentiation": commercial_subscores["differentiation"],
            "monetization_paths": [str(path) for path in monetization_paths],
            "low_confidence": _json(row["commercial_low_confidence"], []),
        },
        "gates": gates,
        "visible_by_default": visible,
        "risks": risks,
        "data_quality": {
            "low_confidence": low,
            "metrics": row["history_confidence"] or "low",
            "analysis": row["loc_confidence"] or "low",
            "commercial": "low" if _json(row["commercial_low_confidence"], []) else "high",
        },
        "watch": (
            {
                "watched": True,
                "status": row["watch_status"],
                "note": row["watch_note"],
                "added_at": row["watched_at"],
            }
            if row["watched_at"]
            else {"watched": False, "status": None, "note": None, "added_at": None}
        ),
        "window": window_estimate(row, now),
        "scoring_version": SCORING_VERSION,
    }
    item["reason"] = build_reason(item, item["scores"])
    return item


def score_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    alpha: float = 0.5,
    min_stars: int = DEFAULT_MIN_STARS,
) -> list[dict[str, Any]]:
    now = now or utc_now()
    return [
        score_repo(row, now=now, alpha=alpha, min_stars=min_stars)
        for row in rows
    ]


def gate_counts(items: Iterable[Mapping[str, Any]], min_stars: int = DEFAULT_MIN_STARS) -> dict[str, int]:
    counts = {key: 0 for key in GATE_INFO}
    for item in items:
        for key, hit in item["gates"].items():
            if hit:
                counts[key] += 1
    counts["g6"] = sum(1 for item in items if item["gates"]["g6"])
    counts["visible_default"] = sum(
        1 for item in items if item["visible_by_default"]
    )
    counts["candidate_pool"] = sum(
        1 for item in items if not item["gates"]["g1"] and not item["gates"]["g2"]
    )
    return counts


def apply_filters(
    items: list[dict[str, Any]],
    params: Mapping[str, Any],
    *,
    preset: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    preset = preset or PRESETS["balanced"]
    include_gpl = _bool(params.get("include_gpl"))
    include_giant = _bool(params.get("include_giant"))
    min_stars = _int(params.get("min_stars"), DEFAULT_MIN_STARS) or 0
    query = str(_unwrap(params.get("q")) or "").strip().lower()
    language = str(_unwrap(params.get("language")) or "").strip().lower()
    license_risk = str(_unwrap(params.get("license")) or "").strip().lower()
    paid_version = str(_unwrap(params.get("paid_version")) or "").strip().lower()
    complexity = str(_unwrap(params.get("complexity")) or "").strip().upper()
    risk = str(_unwrap(params.get("risk")) or "").strip().lower()
    if license_risk == "red" or risk == "gpl":
        # 用户明确按 GPL 筛选时，自动打开 GPL 开关，否则永远筛不到结果。
        include_gpl = True
    min_repro = _float(params.get("min_repro"))
    min_biz = _float(params.get("min_biz"))
    min_delta7 = _int(params.get("min_delta7"))
    max_competitors = _int(params.get("max_competitors"))
    only_watched = _bool(params.get("watched"))
    max_stars = _int(preset.get("max_stars")) if preset.get("max_stars") else None
    max_loc = _int(preset.get("max_loc")) if preset.get("max_loc") else None

    result: list[dict[str, Any]] = []
    for item in items:
        gates = item["gates"]
        if gates["g1"] or gates["g2"]:
            continue
        if gates["g3"] and not include_gpl:
            continue
        if gates["g5"] and not include_giant:
            continue
        if item["stars"] < min_stars:
            continue
        if max_stars is not None and item["stars"] > max_stars:
            continue
        if max_loc is not None:
            loc = item["repro"]["loc"]
            if loc is None or loc > max_loc:
                continue
        if only_watched and not item["watch"]["watched"]:
            continue
        if query:
            haystack = " ".join(
                [
                    item["full_name"],
                    item["description"] or "",
                    " ".join(item["topics"]),
                ]
            ).lower()
            if query not in haystack:
                continue
        if language and (item["language"] or "").lower() != language:
            continue
        if license_risk and item["license"]["risk"] != license_risk:
            continue
        if paid_version and item["biz"]["paid_version"] != paid_version:
            continue
        if complexity and item["repro"]["grade"].rstrip("*").upper() != complexity:
            continue
        if min_repro is not None and item["scores"]["repro"] < min_repro:
            continue
        if min_biz is not None and item["scores"]["biz"] < min_biz:
            continue
        if min_delta7 is not None and (
            item["growth"]["d7"] is None or item["growth"]["d7"] < min_delta7
        ):
            continue
        if max_competitors is not None and (
            item["biz"]["competitor_count"] is None
            or item["biz"]["competitor_count"] > max_competitors
        ):
            continue
        if risk == "suspicious" and not item["gates"]["g7"]:
            continue
        if risk == "commercialized" and item["biz"]["paid_version"] == "none":
            continue
        if risk == "gpl" and not item["gates"]["g3"]:
            continue
        if risk == "low_confidence" and not item["data_quality"]["low_confidence"]:
            continue
        result.append(item)
    return result


def sort_items(
    items: list[dict[str, Any]], sort: str = "total", order: str = "desc"
) -> list[dict[str, Any]]:
    key = SORT_KEYS.get(sort)
    if key is None:
        raise ValueError(f"unsupported sort: {sort}")
    reverse = order != "asc"

    def sort_key(item: dict[str, Any]):
        value = key(item)
        if value is None:
            return (1, 0.0, item["full_name"])
        return (0, -value if reverse else value, item["full_name"])

    return sorted(items, key=sort_key)


def _bool(value: Any) -> bool:
    value = _unwrap(value)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def paginate(
    items: list[dict[str, Any]], page: int = 1, page_size: int = DEFAULT_PAGE_SIZE
) -> tuple[list[dict[str, Any]], int, int]:
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    total = len(items)
    start = (page - 1) * page_size
    return items[start : start + page_size], total, page_size


def detail_payload(
    db: Any,
    item: dict[str, Any],
    row: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    repo_pk = item["id"]
    weeks = list(reversed(db.get_star_weekly(repo_pk)))
    star_weeks = [
        {
            "week_start": week["week_start"],
            "delta": week["delta"],
            "stars_end": week["stars_end"],
            "source": week["source"],
        }
        for week in weeks
    ]
    snapshots = [
        snapshot
        for snapshot in db.list_repo_snapshots(repo_pk)
        if parse_ts(str(snapshot["captured_at"])) >= now - timedelta(days=30)
    ]
    by_day: dict[str, int] = {}
    for snapshot in snapshots:
        day = str(snapshot["captured_at"])[:10]
        by_day[day] = int(snapshot["stars"])
    days = sorted(by_day)
    daily = [
        {
            "date": days[index],
            "delta": by_day[days[index]] - by_day[days[index - 1]],
            "stars": by_day[days[index]],
        }
        for index in range(1, len(days))
    ]
    analysis_breakdown = _json(row["analysis_breakdown"], {})
    commercial_breakdown = _json(row["commercial_breakdown"], {})
    paid_issues = _json(row["paid_intent_issues"], [])
    paid_evidence = _json(row["paid_evidence"], [])
    return {
        "repo": item,
        "star_weeks": star_weeks,
        "daily": daily,
        "snapshots": [
            {
                "captured_at": snapshot["captured_at"],
                "stars": snapshot["stars"],
            }
            for snapshot in snapshots
        ],
        "analysis": {
            "breakdown": analysis_breakdown,
            "low_confidence": _json(row["analysis_low_confidence"], []),
            "deep_analyzed_at": row["deep_analyzed_at"],
            "analyzed_at": row["analysis_at"],
        },
        "commercial": {
            "breakdown": commercial_breakdown,
            "paid_issues": paid_issues,
            "paid_evidence": paid_evidence,
            "low_confidence": _json(row["commercial_low_confidence"], []),
            "analyzed_at": row["commercial_at"],
        },
        "window": item["window"],
        "gates": {key: GATE_INFO[key] for key in GATE_INFO},
    }

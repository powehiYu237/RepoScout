from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from repo_scout.scoring import (
    PRESETS,
    apply_filters,
    gate_counts,
    score_repo,
    score_rows,
    sort_items,
    window_estimate,
)

NOW = datetime(2026, 11, 12, 7, 0, tzinfo=timezone.utc)


def row(**overrides):
    values = {
        "id": 1,
        "full_name": "acme/rocket",
        "owner": "acme",
        "name": "rocket",
        "description": "CLI for SEO automation",
        "html_url": "https://github.com/acme/rocket",
        "homepage": None,
        "language": "Python",
        "topics": json.dumps(["saas", "seo"]),
        "stars_total": 10000,
        "forks": 100,
        "open_issues": 10,
        "contributors": 10,
        "created_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-11-10T00:00:00Z",
        "first_seen_at": "2026-11-08T00:00:00Z",
        "category": "normal",
        "excluded": 0,
        "is_fork": 0,
        "is_archived": 0,
        "is_disabled": 0,
        "license_spdx": "MIT",
        "license_risk": "green",
        "delta_24h": 120,
        "delta_7d": 800,
        "delta_30d": 2400,
        "velocity_7d": 114.28,
        "velocity_prev": 100.0,
        "acceleration": 0.14,
        "acceleration_available": 1,
        "anomaly": 0,
        "burst": 0,
        "anomaly_available": 1,
        "trend_score": 78.0,
        "stars_per_contributor": 1000.0,
        "suspicious_stars": 0,
        "history_confidence": "high",
        "metrics_at": "2026-11-12T06:00:00Z",
        "loc": 8000,
        "loc_method": "cloc",
        "loc_confidence": "high",
        "dep_count": 5,
        "dev_dep_count": 2,
        "dep_manifest": "pyproject.toml",
        "ext_services": json.dumps(["OpenAI"]),
        "has_docker": 1,
        "has_compose": 0,
        "has_monorepo": 0,
        "ci_workflows": 1,
        "src_files": 5,
        "top_dirs": 3,
        "s_loc": 85,
        "s_dep": 100,
        "s_ext": 80,
        "s_lang": 100,
        "s_license": 100,
        "s_struct": 100,
        "repro_score": 80.0,
        "complexity_score": 10,
        "complexity_grade": "A",
        "replication_weeks_min": 1.0,
        "replication_weeks_max": 1.5,
        "analysis_low_confidence": "[]",
        "analysis_breakdown": "{}",
        "deep_analyzed_at": "2026-11-12T05:00:00Z",
        "analysis_at": "2026-11-12T04:00:00Z",
        "paid_intent_score": 70,
        "paid_intent_hits": 2,
        "paid_intent_issues": "[]",
        "paid_version": "possible",
        "paid_evidence": "[]",
        "competitor_count": 6,
        "competitor_query": "topic:saas",
        "audience_score": 100,
        "audience_topic": "saas",
        "monetization_paths": json.dumps(["托管 SaaS"]),
        "gap_count": 2,
        "differentiation_score": 70,
        "market_alignment": 75.0,
        "whitespace": 60.0,
        "competition": 65.0,
        "biz_score": 60.0,
        "commercial_low_confidence": "[]",
        "commercial_breakdown": "{}",
        "commercial_at": "2026-11-12T05:30:00Z",
        "watched_at": None,
        "watch_status": None,
        "watch_note": None,
    }
    values.update(overrides)
    return values


def test_total_score_alpha_and_license() -> None:
    item = score_repo(row(), now=NOW, alpha=0.7)
    assert item["scores"]["total"] == 74.0
    assert item["license"] == {
        "spdx": "MIT",
        "risk": "green",
        "label": "可商用",
    }
    assert item["visible_by_default"] is True


def test_gates_and_visibility() -> None:
    gpl = score_repo(
        row(license_spdx="GPL-3.0", license_risk="red", repro_score=60, biz_score=90),
        now=NOW,
    )
    assert gpl["gates"]["g3"] is True
    assert gpl["visible_by_default"] is False

    no_license = score_repo(
        row(license_spdx=None, license_risk="gray"), now=NOW
    )
    assert no_license["gates"]["g4"] is True
    assert no_license["visible_by_default"] is True

    giant = score_repo(row(loc=60_000), now=NOW)
    assert giant["gates"]["g5"] is True
    assert giant["visible_by_default"] is False

    low = score_repo(row(stars_total=100), now=NOW)
    assert low["gates"]["g6"] is True
    assert low["visible_by_default"] is False


def test_risk_tags() -> None:
    item = score_repo(
        row(
            license_spdx=None,
            license_risk="gray",
            anomaly=1,
            paid_version="likely",
        ),
        now=NOW,
    )
    labels = {risk["label"] for risk in item["risks"]}
    assert "刷星嫌疑" in labels
    assert "已商业化" in labels
    assert "无 License" in labels


def test_window_estimate() -> None:
    result = window_estimate(
        {
            "first_seen_at": (NOW - timedelta(days=4)).isoformat().replace("+00:00", "Z"),
            "paid_version": "none",
            "acceleration": -0.2,
        },
        NOW,
    )
    assert result["days"] == 6.0
    assert any("加速率转负" in line for line in result["basis"])


def test_apply_filters_and_sort() -> None:
    items = score_rows(
        [
            row(id=1, full_name="a/one", repro_score=90, biz_score=70, stars_total=10000),
            row(
                id=2,
                full_name="b/two",
                license_spdx="GPL-3.0",
                license_risk="red",
                repro_score=70,
                biz_score=80,
                stars_total=20000,
            ),
            row(
                id=3,
                full_name="c/three",
                loc=60000,
                repro_score=60,
                biz_score=60,
                stars_total=150000,
            ),
        ],
        now=NOW,
    )

    default_visible = apply_filters(items, {})
    assert [item["full_name"] for item in default_visible] == ["a/one"]

    everything = apply_filters(
        items, {"include_gpl": ["1"], "include_giant": ["1"], "min_stars": ["0"]}
    )
    assert {item["full_name"] for item in everything} == {"a/one", "b/two", "c/three"}

    sorted_items = sort_items(everything, "biz", "desc")
    assert sorted_items[0]["full_name"] == "b/two"


def test_small_preset_filter() -> None:
    items = score_rows(
        [
            row(id=1, full_name="a/small", loc=8000, stars_total=10000),
            row(id=2, full_name="b/large", loc=30000, stars_total=10000),
        ],
        now=NOW,
    )
    small = PRESETS["small"]
    filtered = apply_filters(items, {"preset": ["small"]}, preset=small)
    assert [item["full_name"] for item in filtered] == ["a/small"]


def test_gate_counts_and_reason() -> None:
    items = score_rows(
        [
            row(id=1, full_name="a/one"),
            row(
                id=2,
                full_name="b/two",
                license_spdx="GPL-3.0",
                license_risk="red",
            ),
            row(id=3, full_name="c/three", stars_total=100),
            row(id=4, full_name="d/four", loc=60000),
        ],
        now=NOW,
    )
    counts = gate_counts(items)
    assert counts["g3"] == 1
    assert counts["g5"] == 1
    assert counts["g6"] == 1
    assert counts["visible_default"] == 1
    assert items[0]["reason"].startswith("7 日 +800")

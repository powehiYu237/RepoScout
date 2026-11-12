from __future__ import annotations

from repo_scout.classify import classify_repo


def test_fork_is_excluded_as_mirror() -> None:
    result = classify_repo(full_name="someone/cool-tool", is_fork=True)
    assert result.category == "mirror"
    assert result.excluded is True
    assert result.reason == "fork_mirror"


def test_archived_and_disabled_are_excluded() -> None:
    assert classify_repo(full_name="a/b", is_archived=True).excluded is True
    assert classify_repo(full_name="a/b", is_disabled=True).category == "archived"


def test_awesome_list_by_name() -> None:
    result = classify_repo(full_name="awesome/awesome-ai")
    assert result.category == "awesome"
    assert result.excluded is True


def test_awesome_list_by_topic_or_description() -> None:
    by_topic = classify_repo(full_name="someone/tools", topics=["awesome-list"])
    assert by_topic.category == "awesome"

    by_description = classify_repo(
        full_name="someone/tools",
        description="A curated list of useful developer tools.",
    )
    assert by_description.category == "awesome"


def test_tutorial_by_topic_and_chinese_description() -> None:
    by_topic = classify_repo(full_name="someone/learn-x", topics=["tutorial"])
    assert by_topic.category == "tutorial"
    assert by_topic.excluded is True

    by_description = classify_repo(
        full_name="someone/build-bot",
        description="从零开始的新手教程，带你构建聊天机器人。",
    )
    assert by_description.category == "tutorial"


def test_template_is_flagged_but_not_excluded() -> None:
    result = classify_repo(full_name="someone/saas-template")
    assert result.category == "template"
    assert result.excluded is False
    assert result.reason == "low_differentiation"


def test_docs_only_repo_requires_md_ratio() -> None:
    result = classify_repo(
        full_name="someone/knowledge-base", language=None, md_ratio=0.95
    )
    assert result.category == "docs"
    assert result.excluded is True

    unknown_ratio = classify_repo(full_name="someone/knowledge-base", language=None)
    assert unknown_ratio.category == "normal"


def test_manual_override_has_highest_priority() -> None:
    whitelisted = classify_repo(
        full_name="awesome/awesome-ai", is_fork=True, override="whitelist"
    )
    assert whitelisted.category == "normal"
    assert whitelisted.excluded is False

    blacklisted = classify_repo(full_name="acme/normal", override="blacklist")
    assert blacklisted.category == "manual"
    assert blacklisted.excluded is True


def test_normal_repo_is_kept() -> None:
    result = classify_repo(
        full_name="acme/notes-ai",
        description="Local-first note taking app.",
        topics=["productivity", "notes"],
        language="TypeScript",
    )
    assert result.category == "normal"
    assert result.excluded is False
    assert result.reason is None

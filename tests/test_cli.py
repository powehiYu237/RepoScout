from __future__ import annotations

import pytest

from repo_scout.cli import normalize_repo_arg


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("acme/notes-ai", "acme/notes-ai"),
        ("acme/notes-ai/", "acme/notes-ai"),
        ("https://github.com/acme/notes-ai", "acme/notes-ai"),
        ("https://github.com/acme/notes-ai/", "acme/notes-ai"),
        ("https://github.com/acme/notes-ai?tab=readme", "acme/notes-ai"),
    ],
)
def test_normalize_repo_arg(value: str, expected: str) -> None:
    assert normalize_repo_arg(value) == expected


@pytest.mark.parametrize(
    "value",
    ["not-a-repo", "https://github.com/acme", "https://github.com/", ""],
)
def test_normalize_repo_arg_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_repo_arg(value)

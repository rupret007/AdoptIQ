"""Shared helpers for quote-agnostic Python source-shape regression pins."""

from __future__ import annotations

import re
from pathlib import Path


def normalize_py_quotes(text: str) -> str:
    """Normalize double quotes to single quotes for source substring checks."""

    return text.replace('"', "'")


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def index_in_source(source: str, fragment: str) -> int:
    """Return the index of *fragment* in *source*, ignoring quote-style drift."""

    idx = source.find(fragment)
    if idx >= 0:
        return idx
    normalized_source = normalize_py_quotes(source)
    normalized_fragment = normalize_py_quotes(fragment)
    idx = normalized_source.find(normalized_fragment)
    if idx >= 0:
        return idx
    collapsed_source = _collapse_whitespace(normalized_source)
    collapsed_fragment = _collapse_whitespace(normalized_fragment)
    return collapsed_source.find(collapsed_fragment)


def assert_not_in_source(source: str, fragment: str, *, label: str = "source") -> None:
    """Assert *fragment* does not appear in *source*, ignoring quote-style drift."""

    if fragment not in source and normalize_py_quotes(fragment) not in normalize_py_quotes(source):
        return
    raise AssertionError(f"{label}: forbidden fragment {fragment!r} is present")


def columns_list_present(source: str, columns: list[str], *, label: str = "source") -> None:
    """Assert a ``columns=[...]`` block lists *columns* in order (quote/whitespace agnostic)."""

    parts = [rf'["\']{re.escape(col)}["\']' for col in columns]
    pattern = r"columns\s*=\s*\[\s*" + r"\s*,\s*".join(parts) + r"\s*,?\s*\]"
    assert_regex_in_source(source, pattern, label=label)


def assert_in_source(source: str, fragment: str, *, label: str = "source") -> None:
    """Assert *fragment* appears in *source*, ignoring quote-style drift."""

    # Round 148: migrator accidentally passed pre-evaluated ``in`` / ``or`` booleans.
    if isinstance(fragment, bool):
        if fragment:
            return
        raise AssertionError(f"{label}: condition evaluated to False")
    if fragment in source:
        return
    if normalize_py_quotes(fragment) in normalize_py_quotes(source):
        return
    if _collapse_whitespace(fragment) in _collapse_whitespace(source):
        return
    if _collapse_whitespace(normalize_py_quotes(fragment)) in _collapse_whitespace(
        normalize_py_quotes(source)
    ):
        return
    raise AssertionError(f"{label}: missing fragment {fragment!r}")


def assert_any_in_source(source: str, *fragments: str, label: str = "source") -> None:
    """Assert at least one *fragment* appears in *source* (quote-agnostic)."""

    for fragment in fragments:
        if fragment in source:
            return
        if normalize_py_quotes(fragment) in normalize_py_quotes(source):
            return
        if _collapse_whitespace(normalize_py_quotes(fragment)) in _collapse_whitespace(
            normalize_py_quotes(source)
        ):
            return
    raise AssertionError(f"{label}: none of {fragments!r} found in source")


def read_repo_file(relative_path: str, *, project_root: Path | None = None) -> str:
    """Read a repository file relative to the project root."""

    root = project_root or Path(__file__).resolve().parent.parent
    return (root / relative_path).read_text(encoding="utf-8")


def count_in_source(source: str, fragment: str) -> int:
    """Count occurrences of *fragment* in *source*, ignoring quote-style drift."""

    direct = source.count(fragment)
    if direct:
        return direct
    normalized_source = normalize_py_quotes(source)
    normalized_fragment = normalize_py_quotes(fragment)
    return normalized_source.count(normalized_fragment)


def assert_regex_in_source(source: str, pattern: str | re.Pattern[str], *, label: str = "source") -> None:
    """Assert *pattern* matches *source*, trying quote-normalized variants."""

    compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
    if compiled.search(source):
        return
    normalized = normalize_py_quotes(source)
    if compiled.search(normalized):
        return
    raise AssertionError(f"{label}: pattern {pattern!r} did not match")

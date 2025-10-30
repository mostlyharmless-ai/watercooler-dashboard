"""Tests for thread parser."""

import pytest
from pathlib import Path
from watercooler_dashboard.thread_parser import ThreadParser


def test_thread_parser_initialization():
    """Test ThreadParser initialization."""
    parser = ThreadParser()
    assert parser.threads_base is not None
    assert isinstance(parser.threads_base, Path)


def test_resolve_threads_base_with_explicit_path(tmp_path):
    """Test resolving threads base with explicit path."""
    parser = ThreadParser(threads_base=str(tmp_path))
    assert parser.threads_base == tmp_path


def test_get_all_threads_empty():
    """Test getting threads when no threads exist."""
    parser = ThreadParser(threads_base="/nonexistent/path")
    threads = parser.get_all_threads()
    assert threads == []


def test_grouping_across_repos(tmp_path):
    """Parser should group threads per repository."""

    repo_a = tmp_path / "alpha-threads"
    repo_b = tmp_path / "beta-threads"
    repo_a.mkdir()
    repo_b.mkdir()

    (repo_a / "thread-one.md").write_text(
        """# thread-one
Status: OPEN
Ball: Codex
Created: 2025-10-30T00:00:00Z

---
Entry: Someone 2025-10-30T00:00:00Z
Role: implementer
Type: Note
Title: Initial note

Body
"""
    )

    (repo_b / "thread-two.md").write_text(
        """# thread-two
Status: IN_REVIEW
Ball: Claude
Created: 2025-10-30T01:00:00Z

---
Entry: Someone 2025-10-30T01:00:00Z
Role: planner
Type: Note
Title: Planning

Body
"""
    )

    parser = ThreadParser(threads_base=str(tmp_path))
    grouped = parser.get_threads_by_repo()

    assert set(grouped.keys()) == {"alpha", "beta"}
    assert grouped["alpha"][0]["topic"] == "thread-one"
    assert grouped["beta"][0]["status"] == "IN_REVIEW"

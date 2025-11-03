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


def test_entry_parser_ignores_internal_horizontal_rules(tmp_path):
    """Ensure entry parsing does not treat body '---' as new entries."""

    repo = tmp_path / "gamma-threads"
    repo.mkdir()

    thread_path = repo / "thread.md"
    thread_path.write_text(
        """# thread
Status: OPEN
Ball: Codex
Created: 2025-11-03T00:00:00Z

---
Entry: Codex 2025-11-03T00:00:00Z
Role: implementer
Type: Note
Title: Example entry

First paragraph before rule.

---

Second paragraph after rule.
"""
    )

    parser = ThreadParser(threads_base=str(tmp_path))
    data = parser._parse_thread_file(thread_path)

    assert data is not None
    assert data["entry_count"] == 1
    entry = data["entries"][0]
    assert "First paragraph" in entry["body"]
    assert "Second paragraph" in entry["body"]
    assert entry.get("is_new") is False


def test_last_entry_marked_new_when_ball_differs(tmp_path):
    repo = tmp_path / "delta-threads"
    repo.mkdir()
    thread_path = repo / "thread.md"
    thread_path.write_text(
        """# thread
Status: OPEN
Ball: Codex (caleb)
Created: 2025-11-03T00:00:00Z

---
Entry: Codex (caleb) 2025-11-03T01:00:00Z
Role: implementer
Type: Note
Title: First

Initial update.

---
Entry: Claude (caleb) 2025-11-03T02:00:00Z
Role: implementer
Type: Note
Title: Follow-up

Follow-up update.
""",
        encoding="utf-8",
    )

    parser = ThreadParser(threads_base=str(tmp_path))
    data = parser._parse_thread_file(thread_path)
    assert data is not None
    assert data["has_new"] is True
    assert data["entries"][-1]["is_new"] is True
    assert data["entries"][0]["is_new"] is False


def test_closed_thread_not_marked_new(tmp_path):
    repo = tmp_path / "epsilon-threads"
    repo.mkdir()
    thread_path = repo / "thread.md"
    thread_path.write_text(
        """# thread
Status: CLOSED
Ball: Codex (caleb)
Created: 2025-11-03T00:00:00Z

---
Entry: Claude (caleb) 2025-11-03T01:00:00Z
Role: implementer
Type: Note
Title: Review

All good.
""",
        encoding="utf-8",
    )

    parser = ThreadParser(threads_base=str(tmp_path))
    data = parser._parse_thread_file(thread_path)
    assert data is not None
    assert data["has_new"] is False
    assert not data["entries"][-1]["is_new"]

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

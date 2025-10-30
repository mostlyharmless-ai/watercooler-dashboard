"""Tests for configuration helpers."""

from pathlib import Path

from watercooler_dashboard.config import (
    DashboardConfig,
    default_threads_base,
    load_config,
    save_config,
)


def test_config_round_trip(tmp_path, monkeypatch):
    """Ensure configuration persists to disk and loads back."""

    config_path = tmp_path / "config.json"
    monkeypatch.setenv("WATERCOOLER_DASHBOARD_CONFIG", str(config_path))

    original = DashboardConfig(threads_base=str(tmp_path / "threads"))
    original.repo_order = ["repo-a", "repo-b"]
    original.thread_order = {"repo-a": ["thread-1", "thread-2"]}

    save_config(original)
    loaded = load_config()

    assert loaded.threads_base == str((tmp_path / "threads").resolve())
    assert loaded.repo_order == ["repo-a", "repo-b"]
    assert loaded.thread_order["repo-a"] == ["thread-1", "thread-2"]


def test_config_handles_missing_file(monkeypatch, tmp_path):
    """Loading without a file should return defaults."""

    config_path = tmp_path / "missing" / "config.json"
    monkeypatch.setenv("WATERCOOLER_DASHBOARD_CONFIG", str(config_path))

    config = load_config()
    assert config.repo_order == []
    assert Path(config.threads_base) == Path(default_threads_base())

"""Configuration utilities for the local dashboard."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List


CONFIG_ENV_VAR = "WATERCOOLER_DASHBOARD_CONFIG"
DEFAULT_CONFIG_PATH = (
    Path.home() / ".config" / "watercooler-dashboard" / "config.json"
)


def _candidate_roots() -> List[Path]:
    """Return potential directories that may contain `*-threads` repos."""

    cwd = Path.cwd().resolve()
    module_root = Path(__file__).resolve().parents[2]

    candidates: List[Path] = [cwd, cwd.parent, module_root, module_root.parent]

    unique: List[Path] = []
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _contains_thread_repos(path: Path) -> bool:
    """Return True if the directory contains at least one `*-threads` folder."""

    if not path.exists():
        return False

    try:
        for child in path.iterdir():
            if child.is_dir() and child.name.endswith("-threads"):
                return True
    except (OSError, PermissionError):
        return False

    return False


def default_threads_base() -> str:
    """Determine a sensible default threads base directory."""

    env_value = os.getenv("WATERCOOLER_THREADS_BASE")
    if env_value:
        return str(Path(env_value).expanduser().resolve())

    for candidate in _candidate_roots():
        if _contains_thread_repos(candidate):
            return str(candidate.resolve())

    return str(Path.cwd().resolve())


@dataclass
class DashboardConfig:
    """Serializable configuration for the local dashboard."""

    threads_base: str = field(default_factory=default_threads_base)
    repo_order: List[str] = field(default_factory=list)
    thread_order: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return the config as a JSON-serializable dictionary."""

        data = asdict(self)
        data["threads_base"] = str(Path(self.threads_base).expanduser())
        return data

    def ensure_repo_order(self, repos: List[str]) -> None:
        """Ensure repo order contains all repositories once."""

        existing = [repo for repo in self.repo_order if repo in repos]
        missing = [repo for repo in repos if repo not in existing]
        self.repo_order = existing + missing

    def apply_thread_order(self, repo: str, threads: List[str]) -> None:
        """Ensure stored thread order matches available threads."""

        stored = self.thread_order.get(repo, [])
        existing = [thread for thread in stored if thread in threads]
        missing = [thread for thread in threads if thread not in existing]
        self.thread_order[repo] = existing + missing


def config_path() -> Path:
    """Return the filesystem path where config is stored."""

    override = os.getenv(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()

    return DEFAULT_CONFIG_PATH


def load_config() -> DashboardConfig:
    """Load configuration from disk, falling back to defaults."""

    path = config_path()
    if not path.exists():
        return DashboardConfig()

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        # Malformed config; fall back to defaults but keep original file for inspection.
        return DashboardConfig()

    config = DashboardConfig()
    threads_base = data.get("threads_base", config.threads_base)
    resolved_base = Path(threads_base).expanduser().resolve()
    if resolved_base.exists():
        config.threads_base = str(resolved_base)
    else:
        config.threads_base = default_threads_base()
    config.repo_order = list(data.get("repo_order", []))
    config.thread_order = {
        repo: list(order) for repo, order in data.get("thread_order", {}).items()
    }
    return config


def save_config(config: DashboardConfig) -> None:
    """Persist configuration to disk."""

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2))

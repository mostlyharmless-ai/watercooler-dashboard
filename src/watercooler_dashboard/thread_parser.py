"""Parse Watercooler threads from the threads repository."""

import os
import re
from pathlib import Path
from typing import Any

ThreadData = dict[str, Any]


class ThreadParser:
    """Parses Watercooler thread files and extracts metadata."""

    def __init__(self, threads_base: str | None = None):
        """Initialize the thread parser.

        Args:
            threads_base: Base directory for threads repositories.
                         If None, uses WATERCOOLER_THREADS_BASE env var or default.
        """
        self.threads_base = self._resolve_threads_base(threads_base)

    # ------------------------------------------------------------------
    # Repository discovery helpers
    # ------------------------------------------------------------------

    def list_repositories(self) -> list[Path]:
        """Return all thread repositories discovered under the base path."""

        if not self.threads_base.exists():
            return []

        repos = [
            item
            for item in self.threads_base.iterdir()
            if item.is_dir() and item.name.endswith("-threads")
        ]

        return sorted(repos, key=lambda path: path.name.lower())

    def get_threads_by_repo(self) -> dict[str, list[ThreadData]]:
        """Return thread metadata grouped by repository display name."""

        grouped: dict[str, list[ThreadData]] = {}
        for repo_path in self.list_repositories():
            repo_name = self._repo_display_name(repo_path)
            grouped[repo_name] = self._collect_threads(repo_path, repo_name=repo_name)

        return grouped

    def get_threads_for_repo(self, repo_name: str) -> list[ThreadData]:
        """Return threads for a single repository by its display name."""

        for repo_path in self.list_repositories():
            if self._repo_display_name(repo_path) == repo_name:
                return self._collect_threads(repo_path, repo_name=repo_name)

        return []

    def _resolve_threads_base(self, threads_base: str | None) -> Path:
        """Resolve the threads base directory."""
        if threads_base:
            return Path(threads_base).expanduser().resolve()

        env_base = os.getenv("WATERCOOLER_THREADS_BASE")
        if env_base:
            return Path(env_base).expanduser().resolve()

        return Path.home() / ".watercooler-threads"


    def get_all_threads(self) -> list[dict[str, Any]]:
        """Get all threads from the threads repository.

        Returns:
            List of thread metadata dictionaries.
        """
        threads: list[ThreadData] = []

        for repo_path in self.list_repositories():
            repo_threads = self._collect_threads(repo_path, repo_name=self._repo_display_name(repo_path))
            threads.extend(repo_threads)

        return threads

    def _collect_threads(self, repo_path: Path, repo_name: str) -> list[ThreadData]:
        """Collect thread metadata for a single repository."""

        threads: list[ThreadData] = []
        for thread_file in repo_path.rglob("*.md"):
            if thread_file.name in {"README.md", "INDEX.md"}:
                continue

            thread_data = self._parse_thread_file(thread_file)
            if thread_data:
                thread_data["repo"] = repo_name
                threads.append(thread_data)

        return sorted(threads, key=lambda thread: thread["topic"].lower())

    def _parse_thread_file(self, file_path: Path) -> dict[str, Any] | None:
        """Parse a single thread file.

        Args:
            file_path: Path to the thread markdown file.

        Returns:
            Thread metadata dictionary or None if parsing fails.
        """
        try:
            content = file_path.read_text()

            # Extract metadata from the thread header
            topic = file_path.stem
            status = self._extract_field(content, "Status")
            ball_owner = self._extract_field(content, "Ball")
            created = self._extract_field(content, "Created")

            # Count entries
            entry_count = len(re.findall(r"^---\s*$", content, re.MULTILINE))

            # Find last entry timestamp
            timestamps = re.findall(
                r"Entry:.*?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", content
            )
            last_update = timestamps[-1] if timestamps else created

            titles = re.findall(r"^Title:\s*(.+)$", content, re.MULTILINE)
            last_title = titles[-1] if titles else None

            # Check for NEW markers
            has_new = "NEW" in content

            return {
                "topic": topic,
                "status": status or "UNKNOWN",
                "ball_owner": ball_owner or "Unknown",
                "created": created,
                "last_update": last_update,
                "entry_count": entry_count,
                "has_new": has_new,
                "file_path": str(file_path),
                "last_title": last_title,
            }
        except Exception as e:
            print(f"Error parsing {file_path}: {e}")
            return None

    def _extract_field(self, content: str, field_name: str) -> str | None:
        """Extract a field value from thread content.

        Args:
            content: Thread file content.
            field_name: Name of the field to extract.

        Returns:
            Field value or None if not found.
        """
        pattern = rf"^{field_name}:\s*(.+)$"
        match = re.search(pattern, content, re.MULTILINE)
        return match.group(1).strip() if match else None

    def _repo_display_name(self, repo_path: Path) -> str:
        """Derive a human-friendly repository name."""

        name = repo_path.name
        return name[:-8] if name.endswith("-threads") else name

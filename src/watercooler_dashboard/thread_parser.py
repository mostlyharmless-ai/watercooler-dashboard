"""Parse Watercooler threads from the threads repository."""

import os
import re
from pathlib import Path
from typing import Any
from datetime import datetime


class ThreadParser:
    """Parses Watercooler thread files and extracts metadata."""

    def __init__(self, threads_base: str | None = None):
        """Initialize the thread parser.

        Args:
            threads_base: Base directory for threads repositories.
                         If None, uses WATERCOOLER_THREADS_BASE env var or default.
        """
        self.threads_base = self._resolve_threads_base(threads_base)

    def _resolve_threads_base(self, threads_base: str | None) -> Path:
        """Resolve the threads base directory."""
        if threads_base:
            return Path(threads_base).expanduser().resolve()

        env_base = os.getenv("WATERCOOLER_THREADS_BASE")
        if env_base:
            return Path(env_base).expanduser().resolve()

        return Path.home() / ".watercooler-threads"

    def _find_threads_repo(self) -> Path | None:
        """Find the threads repository for the current context.

        This will use Watercooler's git-aware resolution logic.
        For now, we'll look for *-threads directories.
        """
        if not self.threads_base.exists():
            return None

        # Look for thread repositories in the base directory
        for item in self.threads_base.iterdir():
            if item.is_dir() and item.name.endswith("-threads"):
                return item

        return None

    def get_all_threads(self) -> list[dict[str, Any]]:
        """Get all threads from the threads repository.

        Returns:
            List of thread metadata dictionaries.
        """
        threads_repo = self._find_threads_repo()
        if not threads_repo:
            return []

        threads = []
        # Look for .md files in the threads repository
        for thread_file in threads_repo.rglob("*.md"):
            if thread_file.name == "README.md" or thread_file.name == "INDEX.md":
                continue

            thread_data = self._parse_thread_file(thread_file)
            if thread_data:
                threads.append(thread_data)

        return threads

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

"""Parse Watercooler threads from the threads repository."""

import os
import re
from pathlib import Path
from typing import Any, Iterator

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
            content = file_path.read_text(encoding="utf-8")

            header_lines, body_text = self._split_header_and_body(content)
            title, metadata, order = self._parse_header_lines(
                header_lines, default_title=file_path.stem
            )
            entries = list(self._parse_entries(body_text))

            status = metadata.get("Status", "UNKNOWN")
            priority = metadata.get("Priority", "P2")
            ball_owner_raw = metadata.get("Ball")
            ball_owner = ball_owner_raw or "Unknown"
            created = metadata.get("Created")
            spec = metadata.get("Spec")
            topic = metadata.get("Topic", file_path.stem)

            def _normalize(name: str | None) -> str:
                if not name:
                    return ""
                return re.sub(r"\s*\(.*\)\s*$", "", name.strip()).lower()

            normalized_ball = _normalize(ball_owner_raw)
            last_author = next(
                (entry.get("author") for entry in reversed(entries) if entry.get("author")),
                "",
            )
            normalized_author = _normalize(last_author)
            has_new_flag = (
                bool(entries)
                and bool(normalized_author)
                and normalized_author != normalized_ball
                and status.upper() != "CLOSED"
            )
            for entry in entries:
                entry["is_new"] = False
            if has_new_flag and entries:
                entries[-1]["is_new"] = True

            timestamps = [
                entry["timestamp"] for entry in entries if entry.get("timestamp")
            ]
            last_update = timestamps[-1] if timestamps else created

            last_title = None
            for entry in reversed(entries):
                if entry.get("title"):
                    last_title = entry["title"]
                    break

            return {
                "topic": topic,
                "title": title,
                "status": status,
                "priority": priority,
                "ball_owner": ball_owner,
                "spec": spec,
                "created": created,
                "last_update": last_update,
                "entry_count": len(entries),
                "has_new": bool(entries) and entries[-1].get("is_new", False),
                "file_path": str(file_path),
                "last_title": last_title,
                "metadata": metadata,
                "header_order": order,
                "entries": entries,
            }
        except Exception as e:
            print(f"Error parsing {file_path}: {e}")
            return None

    def _repo_display_name(self, repo_path: Path) -> str:
        """Derive a human-friendly repository name."""

        name = repo_path.name
        return name[:-8] if name.endswith("-threads") else name

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def update_thread_metadata(
        self,
        file_path: Path,
        updates: dict[str, Any],
        git_helper: Any = None
    ) -> ThreadData | None:
        """Update thread header metadata and return the refreshed thread record.

        Args:
            file_path: Path to the thread file.
            updates: Dictionary of metadata fields to update.
            git_helper: Optional GitHelper instance for committing/pushing changes.

        Returns:
            Updated thread data, or None if parsing fails.
        """

        resolved_path = file_path.expanduser().resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"Thread file not found: {resolved_path}")

        content = resolved_path.read_text(encoding="utf-8")
        header_lines, body_text = self._split_header_and_body(content)
        title, metadata, order = self._parse_header_lines(header_lines, default_title=resolved_path.stem)

        for key, value in updates.items():
            normalized_key = str(key).strip()
            if normalized_key == "Title":
                title = str(value).strip() or title
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                metadata.pop(normalized_key, None)
            else:
                coerced_value = str(value).strip()
                if normalized_key in {"Status", "Priority"}:
                    coerced_value = coerced_value.upper()
                metadata[normalized_key] = coerced_value
                if normalized_key not in order:
                    order.append(normalized_key)

        header_text = self._render_header(title, metadata, order)
        new_content = header_text + "\n\n---"
        if body_text:
            new_content += "\n" + body_text.strip() + "\n"
        else:
            new_content += "\n"

        resolved_path.write_text(new_content, encoding="utf-8")

        # Commit and push changes if git helper is available
        git_status = {"committed": False, "pushed": False, "error": None}
        if git_helper:
            if git_helper.is_available():
                fields = ", ".join(updates.keys())
                message = f"Update thread metadata via dashboard: {fields}"
                success, error = git_helper.commit_and_push(
                    resolved_path,
                    message,
                    author_name="Watercooler Dashboard",
                    author_email="dashboard@watercooler.dev"
                )
                if success:
                    git_status["committed"] = True
                    if not error:  # error can contain "push failed" message
                        git_status["pushed"] = True
                    else:
                        git_status["error"] = error
                else:
                    git_status["error"] = error
                    print(f"Git operation failed: {error}")
            else:
                git_status["error"] = "Git repository not available"
                print(f"Git helper not available for {resolved_path}")
        else:
            git_status["error"] = "No git helper provided"
            print(f"No git helper provided for update to {resolved_path}")

        parsed = self._parse_thread_file(resolved_path)
        if parsed:
            parsed["git_status"] = git_status
        return parsed

    # ------------------------------------------------------------------
    # Internal parsing helpers
    # ------------------------------------------------------------------

    def _split_header_and_body(self, content: str) -> tuple[list[str], str]:
        """Split raw thread content into header lines and body text."""

        lines = content.splitlines()
        header_lines: list[str] = []
        body_lines: list[str] = []

        in_header = True
        for line in lines:
            if in_header and line.strip() == "---":
                in_header = False
                continue

            if in_header:
                header_lines.append(line)
            else:
                body_lines.append(line)

        while header_lines and not header_lines[-1].strip():
            header_lines.pop()

        while body_lines and body_lines[0].strip() == "---":
            body_lines.pop(0)

        body_text = "\n".join(body_lines).strip()
        return header_lines, body_text

    def _parse_header_lines(
        self, header_lines: list[str], default_title: str
    ) -> tuple[str, dict[str, str], list[str]]:
        """Parse header lines into a title, metadata map, and original order."""

        if not header_lines:
            return default_title, {}, []

        title_line = header_lines[0].strip()
        if title_line.startswith("#"):
            title = title_line.lstrip("#").strip() or default_title
            meta_lines = header_lines[1:]
        else:
            title = default_title
            meta_lines = header_lines

        metadata: dict[str, str] = {}
        order: list[str] = []

        pattern = re.compile(r"^([\w \-]+):\s*(.+)$")
        for line in meta_lines:
            stripped = line.strip()
            if not stripped:
                continue
            match = pattern.match(stripped)
            if not match:
                continue
            key = match.group(1).strip()
            value = match.group(2).strip()
            metadata[key] = value
            order.append(key)

        return title, metadata, order

    def _parse_entries(self, body_text: str) -> Iterator[dict[str, Any]]:
        """Yield structured entries from the thread body text."""

        if not body_text:
            return

        segments = re.split(r"\n---\s*\n(?=Entry:)", body_text.strip())
        for raw_segment in segments:
            segment = raw_segment.strip()
            if not segment:
                continue

            lines = segment.splitlines()
            meta: dict[str, str] = {}
            cursor = 0
            pattern = re.compile(r"^([\w \-]+):\s*(.*)$")

            while cursor < len(lines):
                line = lines[cursor]
                if not line.strip():
                    cursor += 1
                    break

                match = pattern.match(line)
                if not match:
                    break

                key = match.group(1).strip()
                value = match.group(2).strip()
                meta[key] = value
                cursor += 1

            body = "\n".join(lines[cursor:]).strip()

            author = None
            actor = None
            timestamp = None
            entry_line = meta.get("Entry")
            if entry_line:
                match = re.match(
                    r"^(.+?)(?:\s+\((.+?)\))?\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$",
                    entry_line,
                )
                if match:
                    author = match.group(1).strip()
                    actor = match.group(2).strip() if match.group(2) else None
                    timestamp = match.group(3).strip()
                else:
                    author = entry_line

            yield {
                "meta": meta,
                "body": body,
                "author": author,
                "actor": actor,
                "timestamp": timestamp,
                "title": meta.get("Title"),
                "role": meta.get("Role"),
                "type": meta.get("Type"),
                "spec": meta.get("Spec"),
                "is_new": "NEW" in (meta.get("Type", "") or ""),
            }

    def _render_header(self, title: str, metadata: dict[str, str], order: list[str]) -> str:
        """Render the header portion of a thread file from metadata."""

        normalized_order: list[str] = []
        seen: set[str] = set()

        for key in order:
            if key not in seen:
                normalized_order.append(key)
                seen.add(key)

        preferred = ["Status", "Priority", "Ball", "Spec", "Topic", "Created"]
        for key in preferred:
            if key in metadata and key not in seen:
                normalized_order.append(key)
                seen.add(key)

        for key in metadata:
            if key not in seen:
                normalized_order.append(key)
                seen.add(key)

        lines = [f"# {title}"]
        for key in normalized_order:
            value = metadata.get(key)
            if value is not None and str(value).strip():
                lines.append(f"{key}: {value}")

        return "\n".join(lines)

"""Git helper for committing and pushing thread metadata changes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from git import Repo, InvalidGitRepositoryError, GitCommandError, Actor


class GitHelper:
    """Simple git helper for committing and pushing thread changes."""

    def __init__(self, repo_path: Path):
        """Initialize git helper for a repository.

        Args:
            repo_path: Path to the git repository (threads repo).
        """
        self.repo_path = repo_path
        print(f"[GitHelper] Initializing for repo_path: {repo_path}")
        try:
            self.repo = Repo(repo_path, search_parent_directories=False)
            print(f"[GitHelper] Successfully initialized repo at {self.repo.working_dir}")
        except InvalidGitRepositoryError as e:
            print(f"[GitHelper] Failed to initialize - not a git repository: {e}")
            self.repo = None

    def is_available(self) -> bool:
        """Check if git operations are available."""
        return self.repo is not None

    def commit_and_push(
        self,
        file_path: Path,
        message: str,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Commit and push a file change.

        Args:
            file_path: Path to the file to commit.
            message: Commit message.
            author_name: Override author name (defaults to git config).
            author_email: Override author email (defaults to git config).

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        if not self.repo:
            print(f"[GitHelper] commit_and_push called but repo not initialized")
            return False, "Git repository not initialized"

        print(f"[GitHelper] commit_and_push called for {file_path}")
        print(f"[GitHelper] Message: {message}")

        try:
            # Make path relative to repo root
            try:
                relative_path = file_path.relative_to(self.repo.working_dir)
                print(f"[GitHelper] Relative path: {relative_path}")
            except ValueError as e:
                print(f"[GitHelper] File not in repository: {e}")
                return False, f"File {file_path} is not in repository {self.repo.working_dir}"

            # Stage the file
            self.repo.index.add([str(relative_path)])
            print(f"[GitHelper] File staged successfully")

            # Check if there are changes to commit
            diffs = list(self.repo.index.diff("HEAD"))
            print(f"[GitHelper] Diffs found: {len(diffs)}")
            if not diffs:
                print(f"[GitHelper] No changes to commit")
                return True, None  # No changes, but not an error

            # Configure author if provided
            if author_name:
                author = Actor(author_name, author_email or 'dashboard@watercooler.dev')
                print(f"[GitHelper] Using author: {author.name} <{author.email}>")
                commit = self.repo.index.commit(message, author=author)
            else:
                commit = self.repo.index.commit(message)
            print(f"[GitHelper] Committed successfully: {commit.hexsha[:7]}")

            # Push to remote (if available)
            if self.repo.remotes:
                print(f"[GitHelper] Found {len(self.repo.remotes)} remote(s)")
                try:
                    origin = self.repo.remotes.origin
                    print(f"[GitHelper] Pushing to origin...")
                    push_info = origin.push()
                    print(f"[GitHelper] Push completed: {push_info}")
                except GitCommandError as e:
                    # Push failed, but commit succeeded
                    print(f"[GitHelper] Push failed: {e}")
                    return True, f"Committed but push failed: {str(e)}"
            else:
                print(f"[GitHelper] No remotes configured")

            return True, None

        except GitCommandError as e:
            return False, f"Git error: {str(e)}"
        except Exception as e:
            return False, f"Unexpected error: {str(e)}"


def get_repo_root(file_path: Path) -> Optional[Path]:
    """Find the git repository root for a file.

    Args:
        file_path: Path to a file within a repository.

    Returns:
        Path to repository root, or None if not in a git repo.
    """
    try:
        repo = Repo(file_path, search_parent_directories=True)
        return Path(repo.working_dir) if repo.working_dir else None
    except InvalidGitRepositoryError:
        return None

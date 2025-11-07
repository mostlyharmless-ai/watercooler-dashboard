"""Auto-refresh service for watercooler dashboard.

Polls threads repositories for changes and coordinates refresh events
to keep the dashboard UI in sync without manual refreshes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional

from git import Repo, GitCommandError

logger = logging.getLogger(__name__)


class RefreshCoordinator:
    """Coordinates refresh events between pollers and subscribers.

    Singleton pattern - use get_instance() to access.
    """

    _instance: Optional[RefreshCoordinator] = None

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._last_refresh: Optional[datetime] = None
        self._refresh_count = 0

    @classmethod
    def get_instance(cls) -> RefreshCoordinator:
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def subscribe(self) -> AsyncGenerator[dict, None]:
        """Subscribe to refresh events.

        Yields:
            Event dictionaries with type, timestamp, and optional data.
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)

        try:
            # Send initial heartbeat
            yield {
                "type": "heartbeat",
                "timestamp": datetime.now().isoformat(),
            }

            while True:
                event = await queue.get()
                yield event

        finally:
            # Clean up on disconnect
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    async def trigger_refresh(self, repo_path: str, reason: str = "update"):
        """Trigger a refresh event for all subscribers.

        Args:
            repo_path: Path to the repository that changed
            reason: Reason for refresh (e.g., "update", "fetch", "manual")
        """
        self._last_refresh = datetime.now()
        self._refresh_count += 1

        event = {
            "type": "threads:updated",
            "timestamp": self._last_refresh.isoformat(),
            "repo": repo_path,
            "reason": reason,
            "count": self._refresh_count,
        }

        logger.info(f"Refresh triggered: {reason} for {repo_path} (#{self._refresh_count})")

        # Send to all subscribers
        dead_queues = []
        for queue in self._subscribers:
            try:
                await asyncio.wait_for(queue.put(event), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("Subscriber queue full, dropping event")
                dead_queues.append(queue)
            except Exception as e:
                logger.error(f"Error sending to subscriber: {e}")
                dead_queues.append(queue)

        # Clean up dead queues
        for queue in dead_queues:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def get_stats(self) -> dict:
        """Get coordinator statistics."""
        return {
            "subscribers": len(self._subscribers),
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "refresh_count": self._refresh_count,
        }


class ThreadsPoller:
    """Background service that polls threads repositories for changes."""

    def __init__(
        self,
        repo_path: Path,
        interval: int = 20,
        coordinator: Optional[RefreshCoordinator] = None,
    ):
        """Initialize the poller.

        Args:
            repo_path: Path to threads repository to monitor
            interval: Polling interval in seconds (default: 20)
            coordinator: RefreshCoordinator instance (creates one if None)
        """
        self.repo_path = repo_path
        self.interval = interval
        self.coordinator = coordinator or RefreshCoordinator.get_instance()

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_commit: Optional[str] = None
        self._last_fetch: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._error_count = 0
        self._fetch_count = 0

        try:
            self.repo = Repo(repo_path)
            logger.info(f"ThreadsPoller initialized for {repo_path}")
        except Exception as e:
            logger.error(f"Failed to initialize repo at {repo_path}: {e}")
            self.repo = None

    async def start(self):
        """Start the polling loop."""
        if self._running:
            logger.warning(f"Poller already running for {self.repo_path}")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"Started polling {self.repo_path} every {self.interval}s")

    async def stop(self):
        """Stop the polling loop."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info(f"Stopped polling {self.repo_path}")

    async def _poll_loop(self):
        """Main polling loop."""
        # Get initial commit
        self._update_last_commit()

        while self._running:
            try:
                await asyncio.sleep(self.interval)

                if not self._running:
                    break

                # Fetch from remote
                changed = await self._fetch_and_check()

                if changed:
                    await self.coordinator.trigger_refresh(
                        str(self.repo_path),
                        reason="git-update"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in poll loop: {e}")
                self._last_error = str(e)
                self._error_count += 1

                # Back off on repeated errors
                if self._error_count > 3:
                    logger.warning(f"Multiple errors, backing off to {self.interval * 2}s")
                    await asyncio.sleep(self.interval)

    def _update_last_commit(self):
        """Update the cached commit SHA."""
        if not self.repo:
            return

        try:
            # Get current HEAD commit
            self._last_commit = self.repo.head.commit.hexsha
        except Exception as e:
            logger.error(f"Error getting HEAD commit: {e}")
            self._last_commit = None

    async def _fetch_and_check(self) -> bool:
        """Fetch from remote and check if HEAD changed.

        Returns:
            True if repository was updated, False otherwise.
        """
        if not self.repo:
            return False

        try:
            # Run git fetch in a thread to avoid blocking
            await asyncio.to_thread(self._do_fetch)

            self._last_fetch = datetime.now()
            self._fetch_count += 1
            self._error_count = 0  # Reset error count on success
            self._last_error = None

            # Check if remote HEAD differs from cached commit
            current_commit = self.repo.head.commit.hexsha

            if current_commit != self._last_commit:
                logger.info(
                    f"Detected change: {self._last_commit[:7]} -> {current_commit[:7]}"
                )

                # Pull changes
                await asyncio.to_thread(self._do_pull)

                self._last_commit = current_commit
                return True

            return False

        except GitCommandError as e:
            self._last_error = f"Git error: {e}"
            self._error_count += 1
            logger.error(self._last_error)
            return False
        except Exception as e:
            self._last_error = f"Unexpected error: {e}"
            self._error_count += 1
            logger.error(self._last_error)
            return False

    def _do_fetch(self):
        """Perform git fetch (blocking operation)."""
        if not self.repo or not self.repo.remotes:
            return

        origin = self.repo.remotes.origin
        origin.fetch(prune=True)

    def _do_pull(self):
        """Perform git pull (blocking operation)."""
        if not self.repo or not self.repo.remotes:
            return

        # Use reset --hard to avoid merge conflicts
        origin = self.repo.remotes.origin
        branch = self.repo.active_branch.name
        origin_ref = f"origin/{branch}"

        if origin_ref in [ref.name for ref in self.repo.refs]:
            self.repo.head.reset(origin_ref, index=True, working_tree=True)

    def get_stats(self) -> dict:
        """Get poller statistics."""
        return {
            "repo_path": str(self.repo_path),
            "running": self._running,
            "interval": self.interval,
            "last_commit": self._last_commit,
            "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            "fetch_count": self._fetch_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
        }

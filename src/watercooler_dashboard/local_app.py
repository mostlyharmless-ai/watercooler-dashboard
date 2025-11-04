"""Local web dashboard for viewing Watercooler threads."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Set
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from watercooler_dashboard.config import load_config, save_config
from watercooler_dashboard.thread_parser import ThreadParser
from watercooler_dashboard.git_helper import GitHelper, get_repo_root
from watercooler_dashboard.auto_refresh import ThreadsPoller, RefreshCoordinator

logger = logging.getLogger(__name__)

app = FastAPI(title="Watercooler Dashboard (Local)")

# Cache GitHelper instances per repository
_git_helpers: Dict[str, GitHelper] = {}

# Auto-refresh service instances
_pollers: List[ThreadsPoller] = []
_coordinator: RefreshCoordinator = RefreshCoordinator.get_instance()

PRIORITY_LEVELS = ("P0", "P1", "P2", "P3", "P4", "P5")
CSRF_HEADER = "X-Watercooler-CSRF"
CSRF_TOKEN = secrets.token_urlsafe(32)
ALLOWED_HOSTS = {"127.0.0.1:8080", "localhost:8080", "testserver"}


def _get_parser(threads_base: str | None = None) -> ThreadParser:
    return ThreadParser(threads_base=threads_base)


def _get_git_helper(file_path: Path) -> GitHelper | None:
    """Get or create a GitHelper for the repository containing the file.

    Args:
        file_path: Path to a file within a git repository.

    Returns:
        GitHelper instance, or None if not in a git repo.
    """
    repo_root = get_repo_root(file_path)
    if not repo_root:
        return None

    repo_key = str(repo_root)
    if repo_key not in _git_helpers:
        _git_helpers[repo_key] = GitHelper(repo_root)

    return _git_helpers[repo_key]


def _build_payload() -> Dict[str, Any]:
    config = load_config()
    base_path = Path(config.threads_base)

    if not base_path.exists():
        return {
            "threadsBase": config.threads_base,
            "repos": [],
            "error": "Threads base directory not found.",
        }

    parser = _get_parser(config.threads_base)
    grouped = parser.get_threads_by_repo()

    repos = list(grouped.keys())
    config.ensure_repo_order(repos)

    repo_entries: List[Dict[str, Any]] = []

    for repo_name in config.repo_order:
        threads = grouped.get(repo_name, [])
        thread_topics = [thread["topic"] for thread in threads]
        config.apply_thread_order(repo_name, thread_topics)

        ordered_topics = config.thread_order.get(repo_name, thread_topics)
        ordered_threads = _order_threads(threads, ordered_topics)

        serialized_threads = [_serialize_thread(thread, repo_name) for thread in ordered_threads]

        repo_entries.append(
            {
                "name": repo_name,
                "threads": serialized_threads,
            }
        )

    save_config(config)

    error_message = None
    if not repo_entries:
        error_message = "No thread repositories found in the selected directory."

    return {
        "threadsBase": config.threads_base,
        "repos": repo_entries,
        "error": error_message,
    }


def _order_threads(threads: List[Dict[str, Any]], order: List[str]) -> List[Dict[str, Any]]:
    lookup = {thread["topic"]: thread for thread in threads}
    ordered = [lookup[topic] for topic in order if topic in lookup]

    # Include any new threads not yet present in ordering at the end.
    remaining = [thread for topic, thread in lookup.items() if topic not in order]
    ordered.extend(sorted(remaining, key=lambda thread: thread["topic"].lower()))
    return ordered


def _serialize_thread(thread: Dict[str, Any], repo: str) -> Dict[str, Any]:
    """Normalize thread data for the frontend."""

    status = (thread.get("status") or "UNKNOWN").upper()
    priority = (thread.get("priority") or "P2").upper()
    if priority not in PRIORITY_LEVELS:
        priority = "P2"

    status_normalized = status.lower().replace(" ", "_")
    priority_rank = PRIORITY_LEVELS.index(priority) if priority in PRIORITY_LEVELS else len(PRIORITY_LEVELS)

    file_path = thread.get("file_path")
    is_archived = False
    if file_path:
        try:
            is_archived = "_archive" in Path(file_path).parts
        except Exception:
            is_archived = "_archive" in str(file_path)

    def _serialize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        body = entry.get("body") or ""
        preview_lines = [line.strip() for line in body.splitlines() if line.strip()]
        preview = "\n".join(preview_lines[:3])
        return {
            "title": entry.get("title"),
            "role": entry.get("role"),
            "type": entry.get("type"),
            "spec": entry.get("spec"),
            "author": entry.get("author"),
            "actor": entry.get("actor"),
            "timestamp": entry.get("timestamp"),
            "preview": preview,
            "body": body,
            "meta": entry.get("meta", {}),
            "entryLine": entry.get("meta", {}).get("Entry"),
        }

    entries = [_serialize_entry(entry) for entry in thread.get("entries", [])]

    return {
        "repo": repo,
        "topic": thread.get("topic"),
        "title": thread.get("title"),
        "status": status,
        "statusNormalized": status_normalized,
        "priority": priority,
        "priorityRank": priority_rank,
        "ballOwner": thread.get("ball_owner"),
        "spec": thread.get("spec"),
        "created": thread.get("created"),
        "lastUpdate": thread.get("last_update"),
        "entryCount": thread.get("entry_count", 0),
        "hasNew": bool(thread.get("has_new")),
        "filePath": thread.get("file_path"),
        "lastTitle": thread.get("last_title"),
        "metadata": thread.get("metadata", {}),
        "headerOrder": thread.get("header_order", []),
        "entries": entries,
        "isArchived": is_archived,
    }


def _origin_from_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _expected_origins(request: Request) -> Set[str]:
    host = request.headers.get("host")
    allowed_hosts = set(ALLOWED_HOSTS)
    if host:
        allowed_hosts.add(host)
    origins: Set[str] = set()
    for entry in allowed_hosts:
        origins.add(f"http://{entry}")
        origins.add(f"https://{entry}")
    return origins


def _require_authorized_post(request: Request) -> None:
    allowed_origins = _expected_origins(request)

    origin = _origin_from_url(request.headers.get("origin"))
    if origin and origin not in allowed_origins:
        raise HTTPException(status_code=403, detail="Cross-origin POST blocked")

    referer = _origin_from_url(request.headers.get("referer"))
    if referer and referer not in allowed_origins:
        raise HTTPException(status_code=403, detail="Cross-site POST blocked")

    token = request.headers.get(CSRF_HEADER)
    if token != CSRF_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="watercooler-csrf" content="__CSRF_TOKEN__" />
    <title>Watercooler Dashboard (Local)</title>
    <style>
      :root {
        color-scheme: light;
        font-family: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
        --surface: #ffffff;
        --surface-alt: #f2f5fb;
        --border: #d6dae5;
        --border-strong: #c0c6d4;
        --accent: #2563eb;
        --accent-soft: rgba(37, 99, 235, 0.14);
        --text: #1f2933;
        --text-muted: #52606d;
        --success: #047857;
        --danger: #b91c1c;
        background: linear-gradient(180deg, #f8fafc 0%, #eff3f9 100%);
      }

      @media (prefers-color-scheme: dark) {
        :root {
          color-scheme: dark;
          --surface: #1e1e1e;
          --surface-alt: #2d2d2d;
          --border: #3a3a3a;
          --border-strong: #4a4a4a;
          --accent: #60a5fa;
          --accent-soft: rgba(96, 165, 250, 0.18);
          --text: #e5e7eb;
          --text-muted: #9ca3af;
          --success: #10b981;
          --danger: #ef4444;
          background: linear-gradient(180deg, #111111 0%, #1a1a1a 100%);
        }
      }

      body {
        margin: 0;
        color: var(--text);
      }
      .app-header {
        padding: 2rem clamp(1.5rem, 2vw + 1rem, 3.5rem) 1.25rem;
      }
      .app-header h1 {
        margin: 0;
        font-size: clamp(1.8rem, 2vw + 1.2rem, 2.6rem);
      }
      .app-header p {
        margin: 0.5rem 0 0;
        max-width: 60ch;
        color: var(--text-muted);
      }
      .config-panel {
        margin: 0 clamp(1.5rem, 2vw + 1rem, 3.5rem) 1.5rem;
        padding: 1.1rem 1.3rem;
        border-radius: 1rem;
        background: var(--surface);
        border: 1px solid rgba(15, 23, 42, 0.07);
        box-shadow: 0 12px 32px rgba(15, 23, 42, 0.08);
      }
      .config-form {
        display: grid;
        gap: 0.6rem;
      }
      .config-form label {
        font-weight: 600;
        font-size: 0.95rem;
      }
      .input-row {
        display: flex;
        gap: 0.8rem;
        flex-wrap: wrap;
      }
      .input-row input {
        flex: 1 1 260px;
        padding: 0.55rem 0.8rem;
        border-radius: 0.65rem;
        border: 1px solid var(--border);
        font-size: 0.95rem;
        background: var(--surface);
        color: var(--text);
      }
      .input-row input::placeholder {
        color: var(--text-muted);
      }
      .input-row button {
        padding: 0.55rem 1.1rem;
        border-radius: 0.65rem;
        border: 1px solid transparent;
        background: var(--accent);
        color: #fff;
        font-weight: 600;
        cursor: pointer;
        box-shadow: 0 10px 20px rgba(37, 99, 235, 0.25);
      }
      .input-row button:hover {
        background: #1d4ed8;
      }
      #configStatus {
        font-size: 0.85rem;
        color: var(--success);
      }
      .layout {
        padding: 0 clamp(1.5rem, 2vw + 1rem, 3.5rem) 3rem;
        display: grid;
        grid-template-columns: 260px 1fr;
        gap: 1.5rem;
        align-items: flex-start;
      }
      .repo-pane {
        display: grid;
        gap: 1rem;
      }
      .repo-heading {
        margin: 0;
        font-size: 1.05rem;
        font-weight: 600;
        color: var(--text-muted);
        letter-spacing: 0.02em;
      }
      .repo-list {
        display: grid;
        gap: 0.75rem;
      }
      .repo-card {
        position: relative;
        display: grid;
        grid-template-columns: 1fr auto;
        row-gap: 0.55rem;
        align-items: center;
        border-radius: 1rem;
        padding: 0.95rem 1rem;
        background: var(--surface);
        border: 1px solid rgba(15, 23, 42, 0.1);
        box-shadow: 0 12px 28px rgba(15, 23, 42, 0.1);
        cursor: pointer;
        transition: transform 0.18s ease, box-shadow 0.18s ease, border 0.18s ease, background 0.18s ease;
        overflow: hidden;
      }
      .repo-card::before {
        content: "";
        position: absolute;
        inset: 0;
        border-radius: inherit;
        background: linear-gradient(135deg, rgba(37, 99, 235, 0.15), rgba(37, 99, 235, 0));
        opacity: 0;
        transition: opacity 0.18s ease;
      }
      .repo-card.active::before {
        opacity: 1;
      }
      .repo-card.active {
        border-color: rgba(37, 99, 235, 0.55);
        box-shadow: 0 18px 40px rgba(37, 99, 235, 0.2);
        transform: translateY(-2px);
      }
      .repo-card:not(.active):hover {
        border-color: rgba(37, 99, 235, 0.25);
        box-shadow: 0 16px 34px rgba(37, 99, 235, 0.15);
      }
      .repo-card strong {
        position: relative;
        z-index: 1;
        grid-column: 1 / span 2;
        font-size: 1.02rem;
        font-weight: 650;
        color: var(--text);
        letter-spacing: 0.01em;
      }
      .repo-card:not(.active) strong {
        color: var(--text);
      }
      .repo-controls {
        position: relative;
        z-index: 1;
        justify-self: end;
        display: inline-flex;
        gap: 0.4rem;
      }
      .repo-controls button {
        border-radius: 0.55rem;
        border: 1px solid rgba(37, 99, 235, 0.4);
        padding: 0.28rem 0.52rem;
        font-size: 0.78rem;
        font-weight: 600;
        color: var(--accent);
        background: rgba(37, 99, 235, 0.12);
        cursor: pointer;
        transition: background 0.16s ease, color 0.16s ease, border 0.16s ease;
      }
      .repo-card:not(.active) .repo-controls button {
        border-color: rgba(15, 23, 42, 0.08);
        color: var(--text-muted);
        background: rgba(15, 23, 42, 0.05);
      }
      .repo-controls button:hover:not(:disabled) {
        background: rgba(37, 99, 235, 0.2);
        color: #1d4ed8;
      }
      .content {
        display: grid;
        gap: 1.25rem;
      }
      .toolbar {
        display: flex;
        flex-wrap: wrap;
        gap: 1rem 1.5rem;
        align-items: center;
        padding: 1rem 1.2rem;
        border-radius: 1rem;
        background: var(--surface);
        border: 1px solid rgba(15, 23, 42, 0.07);
        box-shadow: 0 12px 25px rgba(15, 23, 42, 0.07);
      }
      .toolbar label {
        display: flex;
        gap: 0.55rem;
        align-items: center;
        color: var(--text-muted);
        font-size: 0.9rem;
      }
      .toolbar label input[type="checkbox"] {
        accent-color: var(--accent);
      }
      .toolbar select,
      .toolbar input[type="search"] {
        border-radius: 0.6rem;
        border: 1px solid var(--border);
        padding: 0.45rem 0.7rem;
        font-size: 0.92rem;
        background: var(--surface);
        min-width: 160px;
        color: var(--text);
      }
      .toolbar input[type="search"] {
        min-width: 200px;
      }
      .toolbar input::placeholder {
        color: var(--text-muted);
      }
      .toolbar-note {
        color: var(--text-muted);
        font-size: 0.85rem;
        flex-basis: 100%;
      }
      .connection-indicator {
        flex-basis: 100%;
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        font-size: 0.82rem;
        font-weight: 600;
        color: var(--text-muted);
        padding: 0.35rem 0.6rem;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.04);
        transition: color 0.18s ease, background 0.18s ease;
      }
      .connection-indicator::before {
        content: "";
        width: 0.55rem;
        height: 0.55rem;
        border-radius: 50%;
        background: var(--border);
        box-shadow: 0 0 0 2px rgba(15, 23, 42, 0.08);
        transition: background 0.18s ease, box-shadow 0.18s ease;
      }
      .connection-indicator[data-state="connected"] {
        color: #047857;
        background: rgba(5, 150, 105, 0.12);
      }
      .connection-indicator[data-state="connected"]::before {
        background: #22c55e;
        box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.2);
      }
      .connection-indicator[data-state="reconnecting"],
      .connection-indicator[data-state="connecting"] {
        color: #ea580c;
        background: rgba(234, 88, 12, 0.12);
      }
      .connection-indicator[data-state="reconnecting"]::before,
      .connection-indicator[data-state="connecting"]::before {
        background: #f97316;
        box-shadow: 0 0 0 2px rgba(249, 115, 22, 0.2);
      }
      .connection-indicator[data-state="offline"],
      .connection-indicator[data-state="unsupported"] {
        color: #b91c1c;
        background: rgba(185, 28, 28, 0.12);
      }
      .connection-indicator[data-state="offline"]::before,
      .connection-indicator[data-state="unsupported"]::before {
        background: #ef4444;
        box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.2);
      }
      .thread-list {
        display: grid;
        gap: 1.1rem;
      }
      details.thread-card {
        border-radius: 1rem;
        border: 1px solid rgba(15, 23, 42, 0.08);
        background: var(--surface);
        box-shadow: 0 16px 36px rgba(15, 23, 42, 0.11);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
        overflow: hidden;
      }
      details.thread-card[open] {
        transform: translateY(-2px);
        box-shadow: 0 22px 40px rgba(15, 23, 42, 0.18);
      }
      summary.thread-summary {
        list-style: none;
        display: grid;
        grid-template-columns: auto 1fr auto;
        gap: 0.75rem 1rem;
        align-items: center;
        padding: 1.15rem 1.3rem;
        cursor: pointer;
      }
      summary.thread-summary::-webkit-details-marker {
        display: none;
      }
      .summary-badge {
        width: 2.2rem;
        height: 2.2rem;
        border-radius: 0.8rem;
        background: var(--accent-soft);
        color: var(--accent);
        display: grid;
        place-items: center;
        font-weight: 600;
        font-size: 1rem;
      }
      .thread-heading {
        display: grid;
        gap: 0.35rem;
      }
      .thread-heading h2 {
        margin: 0;
        font-size: 1.1rem;
      }
      .thread-actions {
        display: inline-flex;
        gap: 0.4rem;
        align-items: center;
      }
      .thread-actions button {
        border-radius: 0.55rem;
        border: 1px solid rgba(37, 99, 235, 0.3);
        background: rgba(37, 99, 235, 0.12);
        padding: 0.32rem 0.55rem;
        font-size: 0.82rem;
        font-weight: 600;
        color: var(--accent);
        cursor: pointer;
        transition: background 0.15s ease, border 0.15s ease;
      }
      .thread-actions button:hover:not(:disabled) {
        background: rgba(37, 99, 235, 0.2);
        border-color: rgba(37, 99, 235, 0.5);
      }
      .thread-actions button:disabled {
        opacity: 0.5;
        cursor: not-allowed;
        background: rgba(148, 163, 184, 0.3);
        border-color: rgba(148, 163, 184, 0.4);
        color: #475569;
      }
      .thread-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem 0.9rem;
        font-size: 0.85rem;
        color: var(--text-muted);
      }
      .badge {
        border-radius: 999px;
        padding: 0.2rem 0.6rem;
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        text-transform: uppercase;
      }
      .badge-status-open { background: rgba(16, 185, 129, 0.16); color: #047857; }
      .badge-status-blocked { background: rgba(248, 113, 113, 0.18); color: #b91c1c; }
      .badge-status-in_review { background: rgba(251, 191, 36, 0.2); color: #92400e; }
      .badge-status-closed { background: rgba(148, 163, 184, 0.24); color: #475569; }
      .badge-priority { background: rgba(37, 99, 235, 0.16); color: var(--accent); }
      .badge-new { background: rgba(225, 29, 72, 0.18); color: #be123c; }
      .thread-detail {
        border-top: 1px solid rgba(15, 23, 42, 0.08);
        padding: 1.3rem 1.3rem 1.6rem;
        display: grid;
        gap: 1.15rem;
      }
      .editor {
        background: var(--surface);
        border-radius: 0.8rem;
        border: 1px dashed rgba(37, 99, 235, 0.35);
        padding: 1rem 1.15rem;
        display: grid;
        gap: 0.75rem;
      }
      .editor-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 0.75rem;
      }
      .editor-controls {
        display: grid;
        gap: 0.75rem;
      }
      .field-row {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 0.75rem;
      }
      .field-row label {
        display: grid;
        gap: 0.35rem;
        font-size: 0.84rem;
        color: var(--text-muted);
      }
      .field-row input,
      .field-row select {
        border-radius: 0.55rem;
        border: 1px solid var(--border);
        background: var(--surface);
        padding: 0.45rem 0.6rem;
        font-size: 0.9rem;
        color: var(--text);
        box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.08);
      }
      .field-row input:focus,
      .field-row select:focus {
        outline: none;
        border-color: rgba(37, 99, 235, 0.5);
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18);
      }
      .field-row input::placeholder {
        color: var(--text-muted);
      }
      .editor-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 0.6rem;
        align-items: center;
      }
      .editor-actions button {
        border-radius: 0.55rem;
        padding: 0.45rem 0.9rem;
        font-size: 0.88rem;
        font-weight: 600;
        border: 1px solid transparent;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
      }
      .editor-actions button.primary {
        background: var(--accent);
        color: #fff;
        box-shadow: 0 10px 22px rgba(37, 99, 235, 0.25);
      }
      .editor-actions button.secondary {
        background: rgba(15, 23, 42, 0.06);
        color: var(--text);
      }
      .editor-actions button:disabled {
        opacity: 0.6;
        cursor: not-allowed;
        box-shadow: none;
      }
      .editor-message {
        font-size: 0.82rem;
        color: var(--success);
      }
      #updateToast {
        position: fixed;
        bottom: 2.2rem;
        right: 2.2rem;
        padding: 0.75rem 1rem;
        border-radius: 0.9rem;
        background: var(--accent);
        color: #fff;
        font-size: 0.9rem;
        font-weight: 600;
        box-shadow: 0 14px 32px rgba(37, 99, 235, 0.35);
        opacity: 0;
        transform: translateY(8px);
        pointer-events: none;
        transition: opacity 0.2s ease, transform 0.2s ease;
        z-index: 1000;
      }
      #updateToast.visible {
        opacity: 1;
        transform: translateY(0);
      }
      .entries {
        display: grid;
        gap: 0.85rem;
      }
      .entries h3 {
        margin: 0;
        font-size: 0.95rem;
        color: var(--text-muted);
      }
      details.entry {
        border: 1px solid rgba(15, 23, 42, 0.08);
        border-radius: 0.7rem;
        background: var(--surface);
      }
      summary.entry-summary {
        padding: 0.7rem 0.85rem;
        display: flex;
        flex-direction: column;
        gap: 0.4rem;
        cursor: pointer;
        font-size: 0.9rem;
      }
      summary.entry-summary::-webkit-details-marker { display: none; }
      .entry-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem 0.7rem;
        color: var(--text-muted);
        font-size: 0.82rem;
      }
      .entry-line {
        font-weight: 600;
      }
      .entry-body {
        padding: 0 0.85rem 0.95rem;
        font-size: 0.9rem;
        line-height: 1.5;
        color: var(--text);
      }
      /* Markdown content styling */
      .entry-body h1, .entry-body h2, .entry-body h3,
      .entry-body h4, .entry-body h5, .entry-body h6 {
        margin: 1.2em 0 0.6em;
        font-weight: 600;
        line-height: 1.3;
        color: var(--text);
      }
      .entry-body h1:first-child, .entry-body h2:first-child,
      .entry-body h3:first-child { margin-top: 0; }
      .entry-body h1 { font-size: 1.6em; }
      .entry-body h2 { font-size: 1.4em; }
      .entry-body h3 { font-size: 1.2em; }
      .entry-body h4 { font-size: 1.1em; }
      .entry-body p { margin: 0.8em 0; }
      .entry-body ul, .entry-body ol {
        margin: 0.8em 0;
        padding-left: 2em;
      }
      .entry-body li { margin: 0.4em 0; }
      .entry-body code {
        background: var(--surface-alt);
        padding: 0.15em 0.4em;
        border-radius: 0.3em;
        font-size: 0.9em;
        font-family: "Monaco", "Consolas", "Courier New", monospace;
        color: var(--text);
        border: 1px solid var(--border);
      }
      .entry-body pre {
        background: var(--surface-alt);
        padding: 1em;
        border-radius: 0.6em;
        overflow-x: auto;
        margin: 1em 0;
        border: 1px solid var(--border);
      }
      .entry-body pre code {
        background: transparent;
        padding: 0;
        border: none;
        font-size: 0.85em;
        line-height: 1.5;
      }
      .entry-body blockquote {
        margin: 1em 0;
        padding-left: 1em;
        border-left: 3px solid var(--accent);
        color: var(--text-muted);
        font-style: italic;
      }
      .entry-body a {
        color: var(--accent);
        text-decoration: none;
      }
      .entry-body a:hover {
        text-decoration: underline;
      }
      .entry-body hr {
        border: none;
        border-top: 1px solid var(--border);
        margin: 1.5em 0;
      }
      .entry-body table {
        border-collapse: collapse;
        width: 100%;
        margin: 1em 0;
      }
      .entry-body th, .entry-body td {
        border: 1px solid var(--border);
        padding: 0.6em 0.8em;
        text-align: left;
      }
      .entry-body th {
        background: var(--surface-alt);
        font-weight: 600;
      }
      .empty-state {
        padding: 2rem;
        text-align: center;
        border-radius: 1rem;
        border: 1px dashed rgba(15, 23, 42, 0.12);
        color: var(--text-muted);
        background: rgba(15, 23, 42, 0.03);
        font-style: italic;
      }
      .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        border: 0;
      }
      @media (max-width: 960px) {
        .layout {
          grid-template-columns: 1fr;
          padding: 0 1.25rem 2.5rem;
        }
        .repo-pane {
          order: 2;
        }
        .content {
          order: 1;
        }
      }
    </style>
    <!-- Markdown rendering and syntax highlighting -->
    <script src="https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.8/dist/purify.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css" media="(prefers-color-scheme: light)">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css" media="(prefers-color-scheme: dark)">
  </head>
  <body>
    <header class="app-header">
      <h1>Watercooler Threads Dashboard</h1>
      <p>
        Inspect and prioritize Watercooler threads locally. Sort by status or priority, tweak metadata inline, and expand entries to review conversation history.
      </p>
    </header>
    <section class="config-panel">
      <form id="threadsBaseForm" class="config-form">
        <label for="threadsBaseInput">Threads base directory</label>
        <div class="input-row">
          <input id="threadsBaseInput" type="text" name="threadsBase" value="__THREADS_BASE__" placeholder="/path/to/*-threads root" />
          <button type="submit">Save</button>
        </div>
        <small class="toolbar-note">Updates persist to your local dashboard config.</small>
      </form>
      <span id="configStatus" role="status"></span>
    </section>
    <main class="layout">
      <aside class="repo-pane">
        <h2 class="repo-heading">Repositories</h2>
        <div id="reposContainer" class="repo-list" role="tablist" aria-label="Thread repositories"></div>
      </aside>
      <section class="content">
        <div id="toolbarControls" class="toolbar" role="region" aria-label="Thread filters">
          <label>
            Status
            <select id="filter-status"></select>
          </label>
          <label>
            Sort by
            <select id="sort-order"></select>
          </label>
          <label>
            Search
            <input id="search-term" type="search" placeholder="Filter by title, topic, or ball owner" />
          </label>
          <label>
            <input id="toggle-archived" type="checkbox" />
            Show archived threads
          </label>
          <span class="toolbar-note">Default sort: open → highest priority → most recent.</span>
          <span id="liveIndicator" class="connection-indicator" aria-live="polite" data-state="connecting">Connecting…</span>
        </div>
        <section id="threadsContainer" class="thread-list" aria-live="polite"></section>
      </section>
    </main>
    <div id="updateToast" role="status" aria-live="polite"></div>
<script type="module">
      const CSRF_TOKEN = document.querySelector('meta[name="watercooler-csrf"]')?.content || "";
      const withCsrf = (headers = {}) =>
        Object.assign({ "X-Watercooler-CSRF": CSRF_TOKEN }, headers || {});
      const PRIORITY_LEVELS = ["P0", "P1", "P2", "P3", "P4", "P5"];
      const STATUS_FILTERS = [
        { value: "all", label: "All statuses" },
        { value: "open", label: "Open" },
        { value: "in_review", label: "In Review" },
        { value: "blocked", label: "Blocked" },
        { value: "closed", label: "Closed" },
      ];
      const SORT_OPTIONS = [
        { value: "default", label: "Default (open → priority → recent)" },
        { value: "updated-desc", label: "Last updated (newest first)" },
        { value: "updated-asc", label: "Last updated (oldest first)" },
        { value: "priority", label: "Priority (P0 → P5)" },
        { value: "title", label: "Title (A → Z)" },
      ];
      const STORAGE_PREFIX = "wc-dashboard-local";
      const STORAGE_KEYS = {
        status: STORAGE_PREFIX + "-status-filter",
        sort: STORAGE_PREFIX + "-sort-order",
        search: STORAGE_PREFIX + "-search",
        archived: STORAGE_PREFIX + "-show-archived",
      };
      const SSE_CONFIG = {
        reconnectDelay: 5000,
        maxDelay: 60000,
        fallbackInterval: 60000,
      };
      const sseState = {
        source: null,
        reconnectDelay: SSE_CONFIG.reconnectDelay,
        reconnectTimer: null,
        fallbackTimer: null,
        toastTimer: null,
      };
      let refreshInFlight = null;

      // Configure marked.js for markdown rendering with syntax highlighting
      if (typeof marked !== 'undefined') {
        marked.setOptions({
          highlight: function(code, lang) {
            if (lang && hljs.getLanguage(lang)) {
              try {
                return hljs.highlight(code, { language: lang }).value;
              } catch (e) {
                console.warn('Highlight.js error:', e);
              }
            }
            try {
              return hljs.highlightAuto(code).value;
            } catch (e) {
              return code;
            }
          },
          breaks: true,
          gfm: true,
        });
      }

      // Helper function to render markdown with sanitization
      function renderMarkdown(text) {
        if (!text || typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
          return text || '';
        }
        try {
          const rawHTML = marked.parse(text);
          return DOMPurify.sanitize(rawHTML);
        } catch (e) {
          console.warn('Markdown rendering error:', e);
          return text;
        }
      }

      // URL-based state persistence
      function getStateFromURL() {
        const params = new URLSearchParams(window.location.search);
        const threadsParam = params.get('thread');
        return {
          repo: params.get('repo') || null,
          status: params.get('status') || null,
          search: params.get('search') || null,
          threads: threadsParam ? new Set(threadsParam.split(',')) : new Set(),
        };
      }

      function updateURL(updates) {
        const params = new URLSearchParams(window.location.search);

        // Apply updates
        Object.entries(updates).forEach(([key, value]) => {
          if (value) {
            params.set(key, value);
          } else {
            params.delete(key);
          }
        });

        // Update URL without page reload
        const newURL = `${window.location.pathname}?${params.toString()}`;
        window.history.replaceState({}, '', newURL);
      }

      function syncThreadsToURL() {
        const threadsList = Array.from(state.openThreads).join(',');
        updateURL({ thread: threadsList || null });
      }

      const elements = {
        repos: document.getElementById("reposContainer"),
        threads: document.getElementById("threadsContainer"),
        toolbar: document.getElementById("toolbarControls"),
        statusSelect: document.getElementById("filter-status"),
        sortSelect: document.getElementById("sort-order"),
        searchInput: document.getElementById("search-term"),
        archivedToggle: document.getElementById("toggle-archived"),
        form: document.getElementById("threadsBaseForm"),
        threadsBaseInput: document.getElementById("threadsBaseInput"),
        statusBanner: document.getElementById("configStatus"),
        liveIndicator: document.getElementById("liveIndicator"),
        updateToast: document.getElementById("updateToast"),
      };
      // Initialize state from URL parameters (preferred) or localStorage (fallback)
      const urlState = getStateFromURL();
      const state = {
        repos: [],
        activeRepo: urlState.repo || null,
        filters: {
          status: urlState.status || localStorage.getItem(STORAGE_KEYS.status) || "all",
          sort: localStorage.getItem(STORAGE_KEYS.sort) || "default",
          search: urlState.search || localStorage.getItem(STORAGE_KEYS.search) || "",
          showArchived: localStorage.getItem(STORAGE_KEYS.archived) === "true",
        },
        openThreads: urlState.threads || new Set(),
      };

      bindForm();
      populateToolbar();
      bindToolbar();
      refreshData("initial").catch((error) => {
        renderStatus(error.message, "error");
        if (elements.threads) {
          elements.threads.innerHTML = '<p class="empty-state">' + error.message + "</p>";
        }
      });
      startFallbackPolling();
      initEventStream();

      async function fetchData() {
        const response = await fetch("/api/data", { cache: "no-store" });
        if (!response.ok) {
          throw new Error("Failed to load dashboard data");
        }
        const data = await response.json();
        if (elements.threadsBaseInput && typeof data.threadsBase === "string") {
          elements.threadsBaseInput.value = data.threadsBase;
        }

        state.repos = data.repos || [];
        const hasActive = state.activeRepo && state.repos.some((repo) => repo.name === state.activeRepo);
        if (!hasActive && state.repos.length) {
          state.activeRepo = state.repos[0].name;
        } else if (!state.repos.length) {
          state.activeRepo = null;
        }

        renderStatus(data.error || "", data.error ? "error" : "info");
        renderRepos();
        renderThreads();
      }

      async function refreshData(reason = "manual") {
        if (refreshInFlight) {
          return refreshInFlight;
        }

        const threadsEl = elements.threads;
        const scrollTop = threadsEl ? threadsEl.scrollTop : 0;

        refreshInFlight = (async () => {
          await fetchData();
          if (threadsEl) {
            threadsEl.scrollTop = scrollTop;
          }
        })();

        try {
          await refreshInFlight;
        } catch (error) {
          if (reason !== "initial") {
            console.error("Refresh failed:", error);
          }
          throw error;
        } finally {
          refreshInFlight = null;
        }
      }

      function startFallbackPolling() {
        if (sseState.fallbackTimer) {
          clearInterval(sseState.fallbackTimer);
        }
        sseState.fallbackTimer = setInterval(() => {
          refreshData("interval").catch(() => {
            // Suppress errors during background polling; status banner covers explicit failures.
          });
        }, SSE_CONFIG.fallbackInterval);
      }

      function initEventStream() {
        if (!window.EventSource) {
          setConnectionState("unsupported", "Live updates unavailable (EventSource unsupported)");
          return;
        }
        connectEventStream();
      }

      function connectEventStream() {
        teardownEventSource();
        clearTimeout(sseState.reconnectTimer);
        sseState.reconnectTimer = null;
        setConnectionState("connecting");

        const source = new EventSource("/api/events");
        sseState.source = source;

        source.addEventListener("open", () => {
          sseState.reconnectDelay = SSE_CONFIG.reconnectDelay;
          setConnectionState("connected");
        });

        source.addEventListener("message", (event) => {
          if (!event.data) return;
          try {
            const payload = JSON.parse(event.data);
            handleEventMessage(payload);
          } catch (error) {
            console.error("Invalid event payload", error);
          }
        });

        source.addEventListener("error", () => {
          scheduleReconnect();
        });
      }

      function teardownEventSource() {
        if (sseState.source) {
          sseState.source.close();
          sseState.source = null;
        }
      }

      function scheduleReconnect() {
        setConnectionState(
          sseState.reconnectDelay >= SSE_CONFIG.maxDelay ? "offline" : "reconnecting"
        );

        teardownEventSource();

        if (sseState.reconnectTimer) {
          clearTimeout(sseState.reconnectTimer);
        }

        const delay = sseState.reconnectDelay;
        sseState.reconnectTimer = setTimeout(() => {
          sseState.reconnectTimer = null;
          connectEventStream();
        }, delay);

        sseState.reconnectDelay = Math.min(
          Math.round(sseState.reconnectDelay * 1.8),
          SSE_CONFIG.maxDelay
        );
      }

      function handleEventMessage(payload) {
        if (!payload || typeof payload.type !== "string") {
          return;
        }

        if (payload.type === "threads:updated") {
          refreshData("push")
            .then(() => {
              const timeText = formatUpdateTime(payload.timestamp);
              showToast(`Threads updated ${timeText}`);
            })
            .catch(() => {
              // Errors already surfaced via banner/log.
            });
        } else if (payload.type === "heartbeat") {
          setConnectionState("connected");
        }
      }

      function setConnectionState(state, overrideText) {
        const indicator = elements.liveIndicator;
        if (!indicator) return;
        indicator.dataset.state = state;
        const defaultText = {
          connected: "Live updates on",
          connecting: "Connecting…",
          reconnecting: "Reconnecting…",
          offline: "Offline – retrying soon",
          unsupported: "Live updates unavailable",
        };
        indicator.textContent = overrideText || defaultText[state] || "Status unknown";
      }

      function showToast(message) {
        const toast = elements.updateToast;
        if (!toast) return;
        toast.textContent = message;
        toast.classList.add("visible");
        if (sseState.toastTimer) {
          clearTimeout(sseState.toastTimer);
        }
        sseState.toastTimer = setTimeout(() => {
          toast.classList.remove("visible");
        }, 2600);
      }

      function formatUpdateTime(value) {
        try {
          const date = value ? new Date(value) : new Date();
          if (Number.isNaN(date.getTime())) {
            return "just now";
          }
          return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        } catch (error) {
          console.warn("Unable to format update timestamp", error);
          return "just now";
        }
      }

      function populateToolbar() {
        if (elements.statusSelect) {
          elements.statusSelect.innerHTML = STATUS_FILTERS.map(
            (item) => '<option value="' + item.value + '">' + item.label + "</option>"
          ).join("");
          elements.statusSelect.value = state.filters.status;
        }
        if (elements.sortSelect) {
          elements.sortSelect.innerHTML = SORT_OPTIONS.map(
            (item) => '<option value="' + item.value + '">' + item.label + "</option>"
          ).join("");
          elements.sortSelect.value = state.filters.sort;
        }
        if (elements.searchInput) {
          elements.searchInput.value = state.filters.search;
        }
        if (elements.archivedToggle) {
          elements.archivedToggle.checked = state.filters.showArchived;
        }
      }

      function bindToolbar() {
        if (elements.statusSelect) {
          elements.statusSelect.addEventListener("change", (event) => {
            state.filters.status = event.target.value;
            localStorage.setItem(STORAGE_KEYS.status, state.filters.status);
            updateURL({ status: state.filters.status !== 'all' ? state.filters.status : null });
            renderThreads();
          });
        }
        if (elements.sortSelect) {
          elements.sortSelect.addEventListener("change", (event) => {
            state.filters.sort = event.target.value;
            localStorage.setItem(STORAGE_KEYS.sort, state.filters.sort);
            renderThreads();
          });
        }
        if (elements.searchInput) {
          const debounced = debounce((event) => {
            state.filters.search = event.target.value.trim().toLowerCase();
            localStorage.setItem(STORAGE_KEYS.search, state.filters.search);
            updateURL({ search: state.filters.search || null });
            renderThreads();
          }, 180);
          elements.searchInput.addEventListener("input", debounced);
        }
        if (elements.archivedToggle) {
          elements.archivedToggle.addEventListener("change", (event) => {
            state.filters.showArchived = event.target.checked;
            localStorage.setItem(STORAGE_KEYS.archived, state.filters.showArchived ? "true" : "false");
            renderThreads();
          });
        }
      }

      function bindForm() {
        if (!elements.form) return;
        elements.form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const formData = new FormData(elements.form);
          const threadsBase = formData.get("threadsBase");

          const response = await fetch("/api/config/threads-base", {
            method: "POST",
            headers: withCsrf({ "Content-Type": "application/json" }),
            body: JSON.stringify({ threadsBase }),
          });

          if (response.ok) {
            flashStatus("Threads directory updated");
            await refreshData("manual");
          } else {
            const data = await response.json().catch(() => ({}));
            renderStatus(data.detail || "Failed to update threads directory", "error");
          }
        });
      }

      function renderStatus(message = "", kind = "info") {
        if (!elements.statusBanner) return;
        if (!message) {
          elements.statusBanner.textContent = "";
          elements.statusBanner.style.color = "#059669";
          return;
        }
        elements.statusBanner.textContent = message;
        elements.statusBanner.style.color = kind === "error" ? "#b91c1c" : "#059669";
      }

      function flashStatus(message, kind = "info", timeout = 1600) {
        renderStatus(message, kind);
        if (timeout > 0) {
          setTimeout(() => renderStatus("", "info"), timeout);
        }
      }

      function renderRepos() {
        if (!elements.repos) return;
        elements.repos.innerHTML = "";
        if (!state.repos.length) {
          const empty = document.createElement("p");
          empty.className = "empty-state";
          empty.textContent = "No threads repositories discovered.";
          elements.repos.append(empty);
          return;
        }

        state.repos.forEach((repo, index) => {
          const card = document.createElement("button");
          card.type = "button";
          card.className = "repo-card" + (repo.name === state.activeRepo ? " active" : "");
          card.dataset.repoName = repo.name;
          card.setAttribute("role", "tab");
          card.setAttribute("aria-selected", repo.name === state.activeRepo ? "true" : "false");

          const label = document.createElement("strong");
          label.textContent = repo.name;
          card.append(label);

          const controlWrap = document.createElement("span");
          controlWrap.className = "repo-controls";

          const up = document.createElement("button");
          up.type = "button";
          up.textContent = "↑";
          up.title = "Move repository up";
          up.disabled = index === 0;
          up.addEventListener("click", (event) => {
            event.stopPropagation();
            reorderRepo(repo.name, -1);
          });

          const down = document.createElement("button");
          down.type = "button";
          down.textContent = "↓";
          down.title = "Move repository down";
          down.disabled = index === state.repos.length - 1;
          down.addEventListener("click", (event) => {
            event.stopPropagation();
            reorderRepo(repo.name, 1);
          });

          controlWrap.append(up, down);
          card.append(controlWrap);

          card.addEventListener("click", () => {
            state.activeRepo = repo.name;
            updateURL({ repo: repo.name });
            renderRepos();
            renderThreads();
          });

          elements.repos.append(card);
        });
      }

      function applyFilters(threads) {
        const search = state.filters.search;
        const statusFilter = state.filters.status;
        const includeArchived = state.filters.showArchived;
        return threads.filter((thread) => {
          const isArchived = Boolean(thread.isArchived);
          if (isArchived && !includeArchived) {
            return false;
          }
          const matchesStatus =
            statusFilter === "all" || (thread.statusNormalized || "").toLowerCase() === statusFilter;
          const matchesSearch =
            !search ||
            (thread.title || "").toLowerCase().includes(search) ||
            (thread.topic || "").toLowerCase().includes(search) ||
            (thread.ballOwner || "").toLowerCase().includes(search);
          return matchesStatus && matchesSearch;
        });
      }

      function sortThreads(threads) {
        const key = state.filters.sort;
        const comparator = getComparator(key);
        return [...threads].sort(comparator);
      }

      function getComparator(key) {
        const statusRank = (value) => {
          switch (value) {
            case "open":
              return 0;
            case "blocked":
              return 1;
            case "in_review":
              return 2;
            case "closed":
              return 3;
            default:
              return 4;
          }
        };

        if (key === "updated-desc") {
          return (a, b) => compareDates(b.lastUpdate, a.lastUpdate);
        }
        if (key === "updated-asc") {
          return (a, b) => compareDates(a.lastUpdate, b.lastUpdate);
        }
        if (key === "priority") {
          return (a, b) => {
            const diff = a.priorityRank - b.priorityRank;
            return diff !== 0 ? diff : compareDates(b.lastUpdate, a.lastUpdate);
          };
        }
        if (key === "title") {
          return (a, b) => (a.title || "").localeCompare(b.title || "", undefined, { sensitivity: "base" });
        }

        return (a, b) => {
          const first = statusRank(a.statusNormalized) - statusRank(b.statusNormalized);
          if (first !== 0) return first;
          const second = a.priorityRank - b.priorityRank;
          if (second !== 0) return second;
          return compareDates(b.lastUpdate, a.lastUpdate);
        };
      }

      function compareDates(a, b) {
        const dateA = a ? new Date(a) : new Date(0);
        const dateB = b ? new Date(b) : new Date(0);
        if (Number.isNaN(dateA) && Number.isNaN(dateB)) return 0;
        if (Number.isNaN(dateA)) return 1;
        if (Number.isNaN(dateB)) return -1;
        return dateA - dateB;
      }

      function getActiveRepo() {
        if (!state.activeRepo) return null;
        return state.repos.find((repo) => repo.name === state.activeRepo) || null;
      }

      function getThreadKey(thread, index) {
        if (!thread) return String(index);
        const candidate = thread.topic || thread.title || thread.filePath;
        return candidate ? String(candidate) : String(index);
      }

      function renderThreads() {
        if (!elements.threads) return;
        const currentlyOpen = new Set(
          Array.from(elements.threads.querySelectorAll("details[open][data-topic]")).map(
            (detail) => detail.dataset.topic || ""
          )
        );
        currentlyOpen.forEach((topic) => {
          if (topic) state.openThreads.add(topic);
        });

        elements.threads.innerHTML = "";
        const repo = getActiveRepo();

        if (!repo) {
          const empty = document.createElement("p");
          empty.className = "empty-state";
          empty.textContent = "Select a repository to view its threads.";
          elements.threads.append(empty);
          state.openThreads = new Set();
          return;
        }

        const filtered = sortThreads(applyFilters(repo.threads || []));
        if (!filtered.length) {
          const empty = document.createElement("p");
          empty.className = "empty-state";
          empty.textContent = "No threads match the current filters.";
          elements.threads.append(empty);
          state.openThreads = new Set();
          return;
        }

        const nextOpen = new Set();

        filtered.forEach((thread, index) => {
          const key = getThreadKey(thread, index);
          const shouldOpen = key ? state.openThreads.has(key) : false;
          const card = buildThreadCard(thread, index, repo, shouldOpen, key);
          if (card.open && key) {
            nextOpen.add(key);
          }
          elements.threads.append(card);
        });

        state.openThreads = nextOpen;
      }

      function buildThreadCard(thread, index, repo, shouldOpen = false, threadKey = null) {
        const details = document.createElement("details");
        details.className = "thread-card";
        details.dataset.status = thread.statusNormalized || "";
        details.dataset.priority = thread.priority || "";
        const identifier = threadKey || getThreadKey(thread, index);
        if (identifier) {
          details.dataset.topic = identifier;
        }
        if (shouldOpen && identifier) {
          details.open = true;
        }
        const summary = document.createElement("summary");
        summary.className = "thread-summary";

        const badge = document.createElement("span");
        badge.className = "summary-badge";
        badge.textContent = (thread.topic || "?").slice(0, 2).toUpperCase();

        const heading = document.createElement("div");
        heading.className = "thread-heading";
        const title = document.createElement("h2");
        title.textContent = thread.title || thread.topic || "Untitled thread";
        heading.append(title);

        const metaLine = document.createElement("div");
        metaLine.className = "thread-meta";
        const statusBadge = document.createElement("span");
        statusBadge.className = "badge badge-status-" + (thread.statusNormalized || "unknown");
        statusBadge.textContent = formatStatus(thread.status);
        metaLine.append(statusBadge);

        const priorityBadge = document.createElement("span");
        priorityBadge.className = "badge badge-priority";
        priorityBadge.textContent = "Priority " + thread.priority;
        metaLine.append(priorityBadge);

        if (thread.hasNew) {
          const newBadge = document.createElement("span");
          newBadge.className = "badge badge-new";
          newBadge.textContent = "NEW";
          metaLine.append(newBadge);
        }

        if (thread.ballOwner) {
          const ball = document.createElement("span");
          ball.textContent = "Ball: " + thread.ballOwner;
          metaLine.append(ball);
        }
        if (thread.lastUpdate) {
          const updated = document.createElement("span");
          updated.textContent = "Updated " + relativeTime(thread.lastUpdate);
          updated.title = new Date(thread.lastUpdate).toISOString();
          metaLine.append(updated);
        }
        metaLine.append("Entries: " + (thread.entryCount || 0));
        heading.append(metaLine);

        const actions = buildThreadActions(thread, index, repo);

        summary.append(badge, heading, actions);
        details.append(summary);

        const body = document.createElement("div");
        body.className = "thread-detail";
        body.append(buildEditor(thread));
        body.append(buildEntryList(thread));
        details.append(body);

        if (identifier) {
          details.addEventListener("toggle", () => {
            if (details.open) {
              state.openThreads.add(identifier);
            } else {
              state.openThreads.delete(identifier);
            }
            syncThreadsToURL();
          });
        }

        return details;
      }

      function buildThreadActions(thread, index, repo) {
        const actions = document.createElement("div");
        actions.className = "thread-actions";

        const up = document.createElement("button");
        up.type = "button";
        up.textContent = "↑";
        up.title = "Move thread up";
        up.disabled = index === 0;
        up.addEventListener("click", async (event) => {
          event.stopPropagation();
          await reorderThread(repo.name, index, -1);
        });

        const down = document.createElement("button");
        down.type = "button";
        down.textContent = "↓";
        down.title = "Move thread down";
        down.disabled = index === (repo.threads?.length || 0) - 1;
        down.addEventListener("click", async (event) => {
          event.stopPropagation();
          await reorderThread(repo.name, index, 1);
        });

        const copy = document.createElement("button");
        copy.type = "button";
        copy.textContent = "Copy path";
        copy.title = "Copy thread file path";
        copy.disabled = !thread.filePath;
        copy.addEventListener("click", async (event) => {
          event.stopPropagation();
          if (!thread.filePath) return;
          try {
            await navigator.clipboard.writeText(thread.filePath);
            flashStatus("Thread path copied to clipboard");
          } catch (error) {
            flashStatus("Unable to copy path", "error");
          }
        });

        actions.append(up, down, copy);
        return actions;
      }

      function buildEditor(thread) {
        const wrapper = document.createElement("section");
        wrapper.className = "editor";

        const header = document.createElement("div");
        header.className = "editor-header";
        const title = document.createElement("strong");
        title.textContent = "Thread settings";
        const dirty = document.createElement("span");
        dirty.className = "editor-message";
        dirty.hidden = true;
        dirty.textContent = "Unsaved changes";
        header.append(title, dirty);

        const controls = document.createElement("div");
        controls.className = "editor-controls";

        const baseMeta = {
          Status: thread.status || "OPEN",
          Priority: thread.priority || "P2",
          Ball: thread.ballOwner || "",
          Spec: thread.spec || "",
          Topic: (thread.metadata && thread.metadata.Topic) || thread.topic || "",
        };
        let working = { ...baseMeta };

        const statusSelect = document.createElement("select");
        ["OPEN", "IN_REVIEW", "BLOCKED", "CLOSED"].forEach((option) => {
          const opt = document.createElement("option");
          opt.value = option;
          opt.textContent = formatStatus(option);
          if (option === working.Status) opt.selected = true;
          statusSelect.append(opt);
        });

        const prioritySelect = document.createElement("select");
        PRIORITY_LEVELS.forEach((level) => {
          const opt = document.createElement("option");
          opt.value = level;
          opt.textContent = level;
          if (level === working.Priority) opt.selected = true;
          prioritySelect.append(opt);
        });

        const ballInput = document.createElement("input");
        ballInput.type = "text";
        ballInput.value = working.Ball;
        ballInput.placeholder = "Codex / Claude";

        const specInput = document.createElement("input");
        specInput.type = "text";
        specInput.value = working.Spec;
        specInput.placeholder = "Spec (optional)";

        const topicInput = document.createElement("input");
        topicInput.type = "text";
        topicInput.value = working.Topic;
        topicInput.placeholder = "Topic label";

        controls.append(
          createField("Status", statusSelect),
          createField("Priority", prioritySelect),
          createField("Ball owner", ballInput),
          createField("Spec", specInput),
          createField("Topic", topicInput)
        );

        const actions = document.createElement("div");
        actions.className = "editor-actions";
        const saveBtn = document.createElement("button");
        saveBtn.type = "button";
        saveBtn.className = "primary";
        saveBtn.textContent = "Save metadata";

        const resetBtn = document.createElement("button");
        resetBtn.type = "button";
        resetBtn.className = "secondary";
        resetBtn.textContent = "Reset";

        const message = document.createElement("span");
        message.className = "editor-message";
        message.hidden = true;

        actions.append(saveBtn, resetBtn, message);

        wrapper.append(header, controls, actions);

        function markDirty() {
          dirty.hidden = false;
          saveBtn.disabled = false;
        }

        statusSelect.addEventListener("change", () => {
          working.Status = statusSelect.value;
          markDirty();
        });
        prioritySelect.addEventListener("change", () => {
          working.Priority = prioritySelect.value;
          markDirty();
        });
        ballInput.addEventListener("input", () => {
          working.Ball = ballInput.value;
          markDirty();
        });
        specInput.addEventListener("input", () => {
          working.Spec = specInput.value;
          markDirty();
        });
        topicInput.addEventListener("input", () => {
          working.Topic = topicInput.value;
          markDirty();
        });

        resetBtn.addEventListener("click", () => {
          working = { ...baseMeta };
          statusSelect.value = working.Status;
          prioritySelect.value = working.Priority;
          ballInput.value = working.Ball;
          specInput.value = working.Spec;
          topicInput.value = working.Topic;
          dirty.hidden = true;
          saveBtn.disabled = false;
          showEditorMessage("Values reset to last saved state");
        });

        saveBtn.addEventListener("click", async () => {
          saveBtn.disabled = true;
          try {
            const updates = {
              Status: (working.Status || "OPEN").toUpperCase(),
              Priority: (working.Priority || "P2").toUpperCase(),
              Ball: working.Ball || "",
              Spec: working.Spec || "",
              Topic: working.Topic || "",
            };
            const response = await saveThreadMetadata(thread, updates);
            showEditorMessage("Thread metadata saved");
            flashStatus("Thread metadata updated");
            dirty.hidden = true;
            baseMeta.Status = response.status;
            baseMeta.Priority = response.priority;
            baseMeta.Ball = response.ballOwner || "";
            baseMeta.Spec = response.spec || "";
            baseMeta.Topic = (response.metadata && response.metadata.Topic) || response.topic || "";
            working = { ...baseMeta };
            await refreshData("manual");
          } catch (error) {
            showEditorMessage(error.message || "Save failed", true);
            saveBtn.disabled = false;
          }
        });

        function showEditorMessage(text, isError = false) {
          message.hidden = false;
          message.textContent = text;
          message.style.color = isError ? "var(--danger)" : "var(--success)";
          setTimeout(() => {
            message.hidden = true;
          }, 3200);
        }

        return wrapper;
      }

      function createField(labelText, control) {
        const label = document.createElement("label");
        label.textContent = labelText;
        label.append(control);
        return label;
      }

      function buildEntryList(thread) {
        const container = document.createElement("section");
        container.className = "entries";
        const heading = document.createElement("h3");
        heading.textContent = "Entries";
        container.append(heading);

        const entries = thread.entries || [];
        if (!entries.length) {
          const empty = document.createElement("p");
          empty.className = "empty-state";
          empty.textContent = "No entries recorded for this thread.";
          container.append(empty);
          return container;
        }

        entries.forEach((entry, index) => {
          const detail = document.createElement("details");
          detail.className = "entry";
          const summary = document.createElement("summary");
          summary.className = "entry-summary";
          const headerLine = document.createElement("span");
          headerLine.className = "entry-line";
          headerLine.textContent = entry.entryLine || (entry.author ? entry.author : "Entry " + (index + 1));
          summary.append(headerLine);

          const title = document.createElement("strong");
          title.textContent = entry.title || "Entry " + (index + 1);
          summary.append(title);

          const meta = document.createElement("div");
          meta.className = "entry-meta";
          if (entry.role) meta.append(createMeta("Role: " + entry.role));
          if (entry.type) meta.append(createMeta("Type: " + entry.type));
          if (entry.author) meta.append(createMeta("Author: " + entry.author));
          if (entry.timestamp) meta.append(createMeta(entry.timestamp));

          summary.append(meta);
          detail.append(summary);

          const body = document.createElement("div");
          body.className = "entry-body";
          const renderedContent = entry.body ? renderMarkdown(entry.body) : "(No entry body)";
          body.innerHTML = renderedContent;
          detail.append(body);

          container.append(detail);
        });

        return container;
      }

      function createMeta(text) {
        const span = document.createElement("span");
        span.textContent = text;
        return span;
      }

      async function reorderRepo(repoName, direction) {
        const index = state.repos.findIndex((repo) => repo.name === repoName);
        const target = index + direction;
        if (index < 0 || target < 0 || target >= state.repos.length) return;
        const reordered = [...state.repos];
        const [item] = reordered.splice(index, 1);
        reordered.splice(target, 0, item);
        state.repos = reordered;
        await persistRepoOrder();
        renderRepos();
      }

      async function persistRepoOrder() {
        const response = await fetch("/api/repo-order", {
          method: "POST",
          headers: withCsrf({ "Content-Type": "application/json" }),
          body: JSON.stringify({ order: state.repos.map((repo) => repo.name) }),
        });
        if (!response.ok) {
          renderStatus("Failed to save repo order", "error");
        } else {
          flashStatus("Repository order saved");
        }
      }

      async function reorderThread(repoName, index, direction) {
        const repo = state.repos.find((item) => item.name === repoName);
        if (!repo) return;
        const threads = [...repo.threads];
        const target = index + direction;
        if (target < 0 || target >= threads.length) return;
        const [item] = threads.splice(index, 1);
        threads.splice(target, 0, item);
        repo.threads = threads;
        await persistThreadOrder(repoName, threads.map((thread) => thread.topic));
        renderThreads();
      }

      async function persistThreadOrder(repoName, order) {
        const response = await fetch("/api/thread-order", {
          method: "POST",
          headers: withCsrf({ "Content-Type": "application/json" }),
          body: JSON.stringify({ repo: repoName, order }),
        });
        if (!response.ok) {
          renderStatus("Failed to save thread order", "error");
        } else {
          flashStatus("Thread order saved");
        }
      }

      async function saveThreadMetadata(thread, updates) {
        const payload = {
          filePath: thread.filePath,
          repo: thread.repo,
          originalTopic: thread.topic,
          updates,
        };
        const response = await fetch("/api/thread-metadata", {
          method: "POST",
          headers: withCsrf({ "Content-Type": "application/json" }),
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.detail || "Failed to save metadata");
        }
        const data = await response.json();
        return data.thread || thread;
      }

      function formatStatus(status) {
        return (status || "UNKNOWN")
          .toLowerCase()
          .replace(/_/g, " ")
          .replace(/(^|\\s)\\w/g, (match) => match.toUpperCase());
      }

      function relativeTime(timestamp) {
        const date = new Date(timestamp);
        if (Number.isNaN(date)) {
          return "unknown";
        }
        const diffSeconds = (Date.now() - date.getTime()) / 1000;
        const divisions = [
          { amount: 60, unit: "second" },
          { amount: 60, unit: "minute" },
          { amount: 24, unit: "hour" },
          { amount: 7, unit: "day" },
          { amount: 4.34524, unit: "week" },
          { amount: 12, unit: "month" },
          { amount: Number.POSITIVE_INFINITY, unit: "year" },
        ];
        const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
        let duration = diffSeconds;
        for (const division of divisions) {
          if (Math.abs(duration) < division.amount) {
            return rtf.format(Math.round(duration), division.unit);
          }
          duration /= division.amount;
        }
        return "awhile ago";
      }

      function debounce(fn, delay) {
        let timeout;
        return function (...args) {
          clearTimeout(timeout);
          timeout = setTimeout(() => fn.apply(this, args), delay);
        };
      }

      function escapeHtml(value) {
        return String(value || "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#39;");
      }
    </script>
  </body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Render the dashboard page."""

    config = load_config()
    html = (
        INDEX_HTML
        .replace("__THREADS_BASE__", config.threads_base)
        .replace("__CSRF_TOKEN__", CSRF_TOKEN)
    )
    return HTMLResponse(content=html)


@app.get("/api/data")
async def get_data() -> JSONResponse:
    """Return current dashboard data."""

    payload = _build_payload()
    return JSONResponse(payload)


@app.post("/api/config/threads-base")
async def update_threads_base(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """Update the threads base directory in the config."""

    _require_authorized_post(request)

    threads_base = payload.get("threadsBase")
    if not threads_base:
        raise HTTPException(status_code=400, detail="Missing threadsBase value")

    path = Path(threads_base).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=400, detail="Path must be an existing directory")

    if path == Path(path.anchor):
        raise HTTPException(status_code=400, detail="Cannot use filesystem root as threads base")

    try:
        contains_threads_repo = path.name.endswith("-threads") or any(
            child.is_dir() and child.name.endswith("-threads") for child in path.iterdir()
        )
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail="Unable to inspect directory") from exc

    if not contains_threads_repo:
        raise HTTPException(
            status_code=400,
            detail="Directory must contain at least one '*-threads' repository",
        )

    config = load_config()
    config.threads_base = str(path)
    save_config(config)
    return JSONResponse({"status": "ok"})


@app.post("/api/repo-order")
async def update_repo_order(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """Persist a new repo ordering."""

    _require_authorized_post(request)

    order = payload.get("order")
    if not isinstance(order, list):
        raise HTTPException(status_code=400, detail="order must be a list")

    config = load_config()
    config.repo_order = [str(repo) for repo in order]
    save_config(config)
    return JSONResponse({"status": "ok"})


@app.post("/api/thread-order")
async def update_thread_order(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """Persist thread ordering for a repository."""

    _require_authorized_post(request)

    repo = payload.get("repo")
    order = payload.get("order")

    if not repo or not isinstance(order, list):
        raise HTTPException(status_code=400, detail="Invalid payload")

    config = load_config()
    config.thread_order[repo] = [str(topic) for topic in order]
    save_config(config)
    return JSONResponse({"status": "ok"})


@app.post("/api/thread-metadata")
async def update_thread_metadata(payload: Dict[str, Any], request: Request) -> JSONResponse:
    """Update thread metadata fields (status, priority, ball, spec, topic)."""

    _require_authorized_post(request)

    file_path = payload.get("filePath")
    updates = payload.get("updates")
    repo = payload.get("repo")
    original_topic = payload.get("originalTopic")

    if not file_path or not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="filePath and updates are required")

    config = load_config()
    base_path = Path(config.threads_base).expanduser().resolve()

    if base_path == Path(base_path.anchor):
        raise HTTPException(status_code=400, detail="Configured threads base is not permitted")

    resolved_path = Path(file_path).expanduser().resolve()

    if not resolved_path.is_file():
        raise HTTPException(status_code=400, detail="Thread file does not exist")

    try:
        resolved_path.relative_to(base_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid thread path") from exc

    repo_root = next(
        (parent for parent in resolved_path.parents if parent.name.endswith("-threads")),
        None,
    )
    if not repo_root:
        raise HTTPException(status_code=400, detail="Thread file must be inside a '*-threads' repository")
    if repo_root != base_path and base_path not in repo_root.parents:
        raise HTTPException(status_code=400, detail="Thread file is outside the configured threads base")

    parser = _get_parser(config.threads_base)
    git_helper = _get_git_helper(resolved_path)
    updated_thread = parser.update_thread_metadata(resolved_path, updates, git_helper=git_helper)
    if not updated_thread:
        raise HTTPException(status_code=500, detail="Unable to update thread metadata")

    updated_topic = updated_thread.get("topic")
    config_changed = False

    if repo and original_topic:
        order = config.thread_order.get(repo, [])
        if order:
            new_order = [
                updated_topic if topic == original_topic else topic
                for topic in order
            ]
            if new_order != order:
                config.thread_order[repo] = new_order
                config_changed = True
        if updated_topic:
            current_order = config.thread_order.get(repo, [])
            if updated_topic not in current_order:
                config.thread_order[repo] = current_order + [updated_topic]
                config_changed = True

    if config_changed:
        save_config(config)

    repo_name = repo or updated_thread.get("repo") or ""
    serialized = _serialize_thread(updated_thread, repo_name)

    # Include git operation status in response
    git_status = updated_thread.get("git_status", {"committed": False, "pushed": False, "error": "Git status unknown"})
    return JSONResponse({
        "status": "ok",
        "thread": serialized,
        "git": git_status
    })


@app.get("/api/events")
async def events_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events stream for real-time updates.

    Clients connect to this endpoint to receive push notifications
    when threads repositories are updated.
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        """Generate SSE-formatted events."""
        try:
            async for event in _coordinator.subscribe():
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                # Format as SSE
                yield f"data: {json.dumps(event)}\n\n"

        except asyncio.CancelledError:
            logger.info("SSE client disconnected")
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@app.get("/api/health")
async def health_check() -> JSONResponse:
    """Health check endpoint with poller status."""

    poller_stats = [poller.get_stats() for poller in _pollers]
    coordinator_stats = _coordinator.get_stats()

    return JSONResponse({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "pollers": poller_stats,
        "coordinator": coordinator_stats,
    })


@app.on_event("startup")
async def startup_event():
    """Initialize background polling services on startup."""
    config = load_config()
    threads_base = Path(config.threads_base).expanduser().resolve()

    if not threads_base.exists():
        logger.warning(f"Threads base does not exist: {threads_base}")
        return

    # Find all *-threads repositories
    for item in threads_base.iterdir():
        if item.is_dir() and item.name.endswith("-threads"):
            try:
                # Create and start a poller for this repo
                poller = ThreadsPoller(
                    repo_path=item,
                    interval=20,  # Poll every 20 seconds
                    coordinator=_coordinator,
                )
                await poller.start()
                _pollers.append(poller)
                logger.info(f"Started poller for {item}")
            except Exception as e:
                logger.error(f"Failed to start poller for {item}: {e}")

    logger.info(f"Auto-refresh initialized with {len(_pollers)} poller(s)")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up polling services on shutdown."""
    logger.info(f"Stopping {len(_pollers)} poller(s)...")

    for poller in _pollers:
        try:
            await poller.stop()
        except Exception as e:
            logger.error(f"Error stopping poller {poller.repo_path}: {e}")

    _pollers.clear()
    logger.info("Auto-refresh shutdown complete")


def run() -> None:
    """Convenience entry point for running with `python -m`."""

    import uvicorn

    uvicorn.run(
        "watercooler_dashboard.local_app:app",
        host="127.0.0.1",
        port=8080,
        reload=True,
    )


if __name__ == "__main__":
    run()

# Watercooler Dashboard

A lightweight HTML dashboard for reviewing Watercooler threads locally.

> **Roadmap:** This project began as a Slack App Home prototype. Slack support is still planned, but the current focus is the standalone HTML experience described here.

## Overview

The HTML dashboard displays:
- Active threads list
- Status for each thread
- Ball owner (who has the action)
- Last action + timestamp
- Memory snapshots and contextual summaries
- NEW markers for unread entries

## Features

- **Local HTML UI** – quickly review threads without leaving your editor/terminal workflow
- **Repository tabs** – switch between multiple Watercooler projects
- **Sorting & filtering** – status/priority filters, free-text search, archived toggle
- **Inline metadata editor** – adjust Status, Priority, Ball owner, Spec, and Topic, then copy the header snippet back to markdown
- **Expandable entries** – preview entry headers and expand to read the full content
- **Git-aware** – respects Watercooler’s branch-pairing model

## Architecture

The dashboard service:
1. Monitors threads repositories for changes
2. Parses thread metadata (status, ball owner, timestamps)
3. Caches derived data for quick rendering
4. Serves dashboard view via Slack Block Kit

## Setup

### Prerequisites

- Python 3.10+
- Access to at least one Watercooler `*-threads` repository (local clone)

### Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/mostlyharmless-ai/watercooler-dashboard.git
   cd watercooler-dashboard
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

3. Launch the local dashboard:
   ```bash
   uv run python -m watercooler_dashboard.local_app
   ```

4. Open your browser to [http://127.0.0.1:8080](http://127.0.0.1:8080)

### What you can do from the HTML dashboard

- Point the dashboard at a threads base directory (saved per session)
- Switch between repositories via tabs
- Reorder repositories and threads to match your priorities
- Filter by status, priority, or search text
- Toggle inclusion of archived threads
- Expand a thread to review entries and copy header snippets after editing metadata
- Copy the markdown path for a thread to jump into your editor

## Configuration

The local app stores preferences in `~/.config/watercooler-dashboard/config.json`:

- `threads_base` – the directory that contains your `*-threads` repos (auto-detected the first time)
- `repo_order` – current tab order (updated when you reorder tabs)
- `thread_order` – per-repo thread ordering

Remove the file to reset the dashboard state. To run on a different host/port, invoke Uvicorn directly (e.g. `uv run uvicorn watercooler_dashboard.local_app:app --host 0.0.0.0 --port 9000`).

## Development

```bash
# Install dev dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Format code
uv run black .
uv run ruff check .
```

## Deployment / Slack Roadmap

The HTML server can be hosted anywhere FastAPI + Uvicorn run (Render, Fly.io, Heroku, etc.). Deployment scripts will land once we take it beyond local development.

Slack App Home support remains on the roadmap. The original Slack implementation (Block Kit rendering, Socket Mode, manifest) lives in this repo and will resurface once the HTML UX stabilizes; until then this README focuses on the HTML dashboard.

## Thread Resolution

The dashboard uses Watercooler's git-aware thread resolution to automatically:
1. Detect the current project context
2. Find the matching `<repo>-threads` repository
3. Follow branch pairing (e.g., `main` ↔ `main`, `feature-x` ↔ `feature-x`)

This ensures the dashboard always shows relevant threads for your current work.

## License

MIT

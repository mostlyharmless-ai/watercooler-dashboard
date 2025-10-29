# Watercooler Dashboard

A Slack app that provides a live dashboard view of Watercooler threads across your projects.

## Overview

The Watercooler Dashboard displays:
- Active threads list
- Status for each thread
- Ball owner (who has the action)
- Last action + timestamp
- Memory snapshots and contextual summaries
- NEW markers for unread entries

## Features

- **App Home Tab**: Persistent dashboard view in Slack
- **Auto-refresh**: Updates when you open the Home tab
- **Multi-project support**: Automatically detects and follows the correct `<repo>-threads` repository
- **Git-aware**: Works with Watercooler's branch-pairing model

## Architecture

The dashboard service:
1. Monitors threads repositories for changes
2. Parses thread metadata (status, ball owner, timestamps)
3. Caches derived data for quick rendering
4. Serves dashboard view via Slack Block Kit

## Setup

### Prerequisites

- Python 3.11+
- A Slack workspace with permissions to install apps
- Access to Watercooler threads repositories

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

3. Create a Slack app using the manifest in `slack-manifest.yaml`

4. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your Slack credentials
   ```

5. Run the app:
   ```bash
   uv run python -m watercooler_dashboard
   ```

## Configuration

Required environment variables:

- `SLACK_BOT_TOKEN`: Bot User OAuth Token (starts with `xoxb-`)
- `SLACK_APP_TOKEN`: App-Level Token for Socket Mode (starts with `xapp-`)
- `SLACK_SIGNING_SECRET`: Signing secret from Slack app settings
- `WATERCOOLER_THREADS_BASE`: Base directory for threads repositories (optional)

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

## Deployment

The dashboard can be deployed to:
- Render
- Fly.io
- Heroku
- Any platform supporting Python web apps

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed instructions.

## Thread Resolution

The dashboard uses Watercooler's git-aware thread resolution to automatically:
1. Detect the current project context
2. Find the matching `<repo>-threads` repository
3. Follow branch pairing (e.g., `main` ↔ `main`, `feature-x` ↔ `feature-x`)

This ensures the dashboard always shows relevant threads for your current work.

## License

MIT

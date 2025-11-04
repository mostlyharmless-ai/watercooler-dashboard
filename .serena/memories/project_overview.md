# Watercooler Dashboard Overview
- Purpose: FastAPI-based local HTML dashboard for browsing and editing Watercooler thread metadata alongside repos.
- Tech stack: Python 3.10+, FastAPI with Uvicorn, front-end HTML/JS template embedded in `local_app.py`; tests via pytest.
- Structure: `src/watercooler_dashboard/` holds app code (local_app, thread parser, git helper, config), `tests/` contains pytest suites, `slack-manifest.yaml` retains legacy Slack App Home data.
- Key behaviours: Parses `*-threads` repositories, surfaces thread metadata, allows inline edits, persists dashboard prefs under `~/.config/watercooler-dashboard/config.json`.
- Roadmap: Slack App Home reintegration planned once HTML UX solidifies.
# Suggested Commands
- Install deps: `uv sync` (add `--extra dev` for development extras).
- Run tests: `uv run pytest`.
- Run formatter: `uv run black .`.
- Lint: `uv run ruff check .`.
- Launch local dashboard: `uv run python -m watercooler_dashboard.local_app` (serves at http://127.0.0.1:8080 by default).
- Alternate server run: `uv run uvicorn watercooler_dashboard.local_app:app --reload`.
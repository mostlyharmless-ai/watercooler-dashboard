# Task Completion Checklist
- Run `uv run pytest` if code changes touch parsing, API, or utilities.
- Reformat and lint via `uv run black .` and `uv run ruff check .` when Python files are edited.
- For UI/template tweaks in `local_app.py`, smoke-test via `uv run python -m watercooler_dashboard.local_app` and verify in browser.
- Update relevant thread markdown in `watercooler-dashboard-threads` with summary of changes/next steps.
- Ensure configuration path (`~/.config/watercooler-dashboard/config.json`) remains backward compatible before shipping.
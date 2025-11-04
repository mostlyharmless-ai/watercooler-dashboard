# Style & Conventions
- Python code targets 3.10+, typed (uses type hints) and follows Black formatting; Ruff lints the codebase.
- FastAPI endpoints defined in `local_app.py`; prefer dataclasses and pathlib for config/IO.
- Front-end embedded in Python string; CSS/JS kept within template, but follow semantic naming and modular JS utilities.
- Threads parsing expects markdown headers with Watercooler metadata; avoid breaking header formats.
- Configuration stored via dataclasses (`DashboardConfig`) and JSON; Git operations handled through `GitPython` wrapper (`GitHelper`).
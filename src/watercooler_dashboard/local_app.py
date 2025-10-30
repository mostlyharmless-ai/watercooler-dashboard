"""Local web dashboard for viewing Watercooler threads."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from watercooler_dashboard.config import load_config, save_config
from watercooler_dashboard.thread_parser import ThreadParser


app = FastAPI(title="Watercooler Dashboard (Local)")


def _get_parser(threads_base: str | None = None) -> ThreadParser:
    return ThreadParser(threads_base=threads_base)


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

        repo_entries.append(
            {
                "name": repo_name,
                "threads": ordered_threads,
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


INDEX_HTML = """<!DOCTYPE html>
<html lang=\"en\">
  <head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>Watercooler Dashboard (Local)</title>
    <style>
      :root { font-family: Inter, system-ui, sans-serif; color: #222; background: #f8f9fb; }
      body { margin: 0; padding: 0 1.5rem 3rem; }
      .app-header { padding: 1.5rem 0 1rem; border-bottom: 1px solid #dbe1ec; margin-bottom: 1.5rem; }
      .app-header h1 { margin: 0; font-size: 1.75rem; }
      .subtitle { margin: 0.25rem 0 0; color: #4b5563; }
      .config-panel { background: #fff; border: 1px solid #e5e7eb; border-radius: 0.75rem; padding: 1rem; margin-bottom: 1.5rem; box-shadow: 0 2px 4px rgba(15, 23, 42, 0.05); }
      .config-form { display: flex; flex-direction: column; gap: 0.5rem; }
      .config-form label { font-weight: 600; font-size: 0.95rem; }
      .input-row { display: flex; gap: 0.75rem; }
      .input-row input { flex: 1; padding: 0.6rem 0.8rem; border: 1px solid #cbd5f5; border-radius: 0.55rem; font-size: 0.95rem; }
      .input-row button { padding: 0.6rem 1.2rem; background: #2563eb; color: #fff; border: none; border-radius: 0.55rem; cursor: pointer; font-weight: 600; }
      .input-row button:hover { background: #1d4ed8; }
      .hint { color: #6b7280; font-size: 0.85rem; }
      #configStatus { display: inline-block; margin-top: 0.5rem; font-size: 0.85rem; color: #059669; }
      .dashboard { display: grid; grid-template-columns: 240px 1fr; gap: 1.5rem; align-items: flex-start; }
      .repo-tabs { display: flex; flex-direction: column; gap: 0.5rem; }
      .repo-tab { border: 1px solid #cbd5f5; border-radius: 0.65rem; background: #fff; padding: 0.75rem 0.9rem; cursor: pointer; display: flex; align-items: center; justify-content: space-between; transition: background 0.2s; }
      .repo-tab.active { background: #2563eb; color: #fff; border-color: #1d4ed8; }
      .repo-tab span { font-weight: 600; }
      .repo-controls { display: flex; gap: 0.35rem; }
      .repo-controls button { background: transparent; border: 1px solid rgba(255,255,255,0.7); border-radius: 0.45rem; padding: 0.2rem 0.4rem; color: inherit; cursor: pointer; font-size: 0.75rem; }
      .repo-tab:not(.active) .repo-controls button { border-color: #cbd5f5; color: #374151; }
      .threads { display: flex; flex-direction: column; gap: 1rem; }
      .thread-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 0.9rem; padding: 1rem 1.2rem; box-shadow: 0 10px 25px rgba(15, 23, 42, 0.08); }
      .thread-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.5rem; }
      .thread-title { margin: 0; font-size: 1.15rem; }
      .thread-status { font-size: 0.85rem; font-weight: 600; padding: 0.25rem 0.6rem; border-radius: 999px; background: #e5e7eb; color: #374151; display: inline-flex; gap: 0.35rem; align-items: center; }
      .thread-status.open { background: #dcfce7; color: #166534; }
      .thread-status.closed { background: #f3f4f6; color: #6b7280; }
      .thread-status.blocked { background: #fee2e2; color: #b91c1c; }
      .thread-status.review { background: #fef3c7; color: #92400e; }
      .thread-status .pill { background: rgba(255, 255, 255, 0.35); border-radius: 999px; padding: 0.1rem 0.4rem; font-size: 0.75rem; }
      .thread-meta { margin: 0; color: #4b5563; font-size: 0.9rem; }
      .thread-footer { margin-top: 0.75rem; display: flex; gap: 0.6rem; align-items: center; }
      .thread-footer button { background: #f3f4f6; border: 1px solid #e2e8f0; border-radius: 0.45rem; padding: 0.35rem 0.6rem; cursor: pointer; font-weight: 600; font-size: 0.85rem; }
      .thread-footer button:hover { background: #e2e8f0; }
      .thread-footer a { margin-left: auto; font-size: 0.85rem; color: #2563eb; text-decoration: none; }
      .thread-footer a:hover { text-decoration: underline; }
      .empty-state { color: #6b7280; font-style: italic; }
      @media (max-width: 900px) { .dashboard { grid-template-columns: 1fr; } .repo-tabs { flex-direction: row; overflow-x: auto; } }
    </style>
  </head>
  <body>
    <header class=\"app-header\">
      <h1>Watercooler Dashboard</h1>
      <p class=\"subtitle\">Quick local view of Watercooler threads across repositories.</p>
    </header>
    <section class=\"config-panel\">
      <form id=\"threadsBaseForm\" class=\"config-form\">
        <label for=\"threadsBaseInput\">Threads base directory</label>
        <div class=\"input-row\">
          <input id=\"threadsBaseInput\" type=\"text\" name=\"threadsBase\" value=\"__THREADS_BASE__\" placeholder=\"/path/to/*-threads root\" />
          <button type=\"submit\">Save</button>
        </div>
        <small class=\"hint\">Updates are saved to your local session config.</small>
      </form>
      <span id=\"configStatus\" role=\"status\"></span>
    </section>
    <main id=\"dashboard\" class=\"dashboard\">
      <div id=\"reposContainer\" class=\"repo-tabs\" role=\"tablist\"></div>
      <section id=\"threadsContainer\" class=\"threads\" aria-live=\"polite\"></section>
    </main>
    <script type=\"module\">
      const reposContainer = document.getElementById('reposContainer');
      const threadsContainer = document.getElementById('threadsContainer');
      const form = document.getElementById('threadsBaseForm');
      const statusEl = document.getElementById('configStatus');

      const state = { repos: [], activeRepo: null };

      async function fetchData() {
        const response = await fetch('/api/data');
        if (!response.ok) {
          throw new Error('Unable to load thread data');
        }
        const data = await response.json();
        renderStatus(data.error || '', data.error ? 'error' : 'info');

        state.repos = data.repos || [];
        if (state.repos.length === 0) {
          state.activeRepo = null;
        } else if (!state.activeRepo || !state.repos.some((repo) => repo.name === state.activeRepo)) {
          state.activeRepo = state.repos[0].name;
        }
        renderRepos();
        renderThreads();
      }

      function renderStatus(message = '', kind = 'info') {
        if (!message) {
          statusEl.textContent = '';
          statusEl.style.color = '#059669';
          return;
        }
        statusEl.textContent = message;
        statusEl.style.color = kind === 'error' ? '#b91c1c' : '#059669';
      }

      function flashStatus(message, kind = 'info', delay = 1500) {
        renderStatus(message, kind);
        if (delay > 0) {
          setTimeout(() => renderStatus('', 'info'), delay);
        }
      }

      function renderRepos() {
        reposContainer.innerHTML = '';
        if (!state.repos.length) {
          const empty = document.createElement('p');
          empty.textContent = 'No repositories discovered.';
          empty.classList.add('empty-state');
          reposContainer.appendChild(empty);
          return;
        }

        state.repos.forEach((repo, index) => {
          const tab = document.createElement('button');
          tab.type = 'button';
          tab.className = 'repo-tab' + (repo.name === state.activeRepo ? ' active' : '');
          tab.setAttribute('role', 'tab');
          tab.dataset.repoName = repo.name;

          const label = document.createElement('span');
          label.textContent = repo.name;
          tab.appendChild(label);

          const controls = document.createElement('span');
          controls.className = 'repo-controls';

          const up = document.createElement('button');
          up.textContent = '↑';
          up.disabled = index === 0;
          up.addEventListener('click', (event) => {
            event.stopPropagation();
            reorderRepo(repo.name, -1);
          });

          const down = document.createElement('button');
          down.textContent = '↓';
          down.disabled = index === state.repos.length - 1;
          down.addEventListener('click', (event) => {
            event.stopPropagation();
            reorderRepo(repo.name, 1);
          });

          controls.append(up, down);
          tab.appendChild(controls);

          tab.addEventListener('click', () => {
            state.activeRepo = repo.name;
            renderRepos();
            renderThreads();
          });

          reposContainer.appendChild(tab);
        });
      }

      function renderThreads() {
        threadsContainer.innerHTML = '';
        if (!state.activeRepo) {
          const empty = document.createElement('p');
          empty.textContent = 'Select a repository to view threads.';
          empty.classList.add('empty-state');
          threadsContainer.appendChild(empty);
          return;
        }

        const repo = state.repos.find((r) => r.name === state.activeRepo);
        if (!repo || !repo.threads.length) {
          const empty = document.createElement('p');
          empty.textContent = 'No threads found for this repository.';
          empty.classList.add('empty-state');
          threadsContainer.appendChild(empty);
          return;
        }

        repo.threads.forEach((thread, index) => {
          const card = document.createElement('article');
          card.className = 'thread-card';

          const header = document.createElement('header');
          header.className = 'thread-header';

          const title = document.createElement('h2');
          title.className = 'thread-title';
          title.textContent = thread.topic;

          const status = document.createElement('span');
          status.className = 'thread-status';
          const displayStatus = (thread.status || 'UNKNOWN').replace(/_/g, ' ');
          const statusLabel = document.createElement('span');
          statusLabel.textContent = displayStatus;
          status.appendChild(statusLabel);
          const normalized = displayStatus.toLowerCase();
          if (normalized === 'open') status.classList.add('open');
          if (normalized === 'in review') status.classList.add('review');
          if (normalized === 'blocked') status.classList.add('blocked');
          if (normalized === 'closed') status.classList.add('closed');
          if (thread.has_new) {
            const badge = document.createElement('span');
            badge.className = 'pill';
            badge.textContent = 'NEW';
            status.appendChild(badge);
          }

          header.append(title, status);

          const meta = document.createElement('p');
          meta.className = 'thread-meta';
          const details = [`Ball: ${thread.ball_owner}`, `Entries: ${thread.entry_count}`];
          if (thread.last_title) {
            details.push(`Last title: ${thread.last_title}`);
          }
          if (thread.last_update) {
            details.push(`Updated: ${formatTimestamp(thread.last_update)}`);
          }
          meta.textContent = details.join(' · ');

          const footer = document.createElement('footer');
          footer.className = 'thread-footer';

          const up = document.createElement('button');
          up.textContent = 'Move Up';
          up.disabled = index === 0;
          up.addEventListener('click', () => reorderThread(repo.name, index, -1));

          const down = document.createElement('button');
          down.textContent = 'Move Down';
          down.disabled = index === repo.threads.length - 1;
          down.addEventListener('click', () => reorderThread(repo.name, index, 1));

          const anchor = document.createElement('a');
          anchor.href = `file://${thread.file_path}`;
          anchor.textContent = 'Open file';

          footer.append(up, down, anchor);

          card.append(header, meta, footer);
          threadsContainer.appendChild(card);
        });
      }

      function formatTimestamp(value) {
        try {
          const parsed = new Date(value);
          if (!Number.isNaN(parsed.getTime())) {
            return parsed.toLocaleString();
          }
        } catch (error) {
          // Fall back to the raw value below.
        }
        return value || 'Unknown';
      }

      async function reorderRepo(repoName, direction) {
        const index = state.repos.findIndex((r) => r.name === repoName);
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
        const response = await fetch('/api/repo-order', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ order: state.repos.map((repo) => repo.name) }),
        });
        if (!response.ok) {
          renderStatus('Failed to save repo order', 'error');
        } else {
          flashStatus('Repo order saved');
        }
      }

      async function reorderThread(repoName, index, direction) {
        const repo = state.repos.find((r) => r.name === repoName);
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
        const response = await fetch('/api/thread-order', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ repo: repoName, order }),
        });
        if (!response.ok) {
          renderStatus('Failed to save thread order', 'error');
        } else {
          flashStatus('Thread order saved');
        }
      }

      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const formData = new FormData(form);
        const threadsBase = formData.get('threadsBase');

        const response = await fetch('/api/config/threads-base', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ threadsBase }),
        });

        if (response.ok) {
          flashStatus('Threads directory updated');
          await fetchData();
        } else {
          const data = await response.json().catch(() => ({}));
          renderStatus(data.detail || 'Failed to update threads directory', 'error');
        }
      });

      fetchData().catch((error) => {
        renderStatus(error.message, 'error');
        threadsContainer.innerHTML = `<p class=\"empty-state\">${error.message}</p>`;
      });
    </script>
  </body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Render the dashboard page."""

    config = load_config()
    html = INDEX_HTML.replace("__THREADS_BASE__", config.threads_base)
    return HTMLResponse(content=html)


@app.get("/api/data")
async def get_data() -> JSONResponse:
    """Return current dashboard data."""

    payload = _build_payload()
    return JSONResponse(payload)


@app.post("/api/config/threads-base")
async def update_threads_base(payload: Dict[str, Any]) -> JSONResponse:
    """Update the threads base directory in the config."""

    threads_base = payload.get("threadsBase")
    if not threads_base:
        raise HTTPException(status_code=400, detail="Missing threadsBase value")

    path = Path(threads_base).expanduser()
    if not path.exists():
        raise HTTPException(status_code=400, detail="Path does not exist")

    config = load_config()
    config.threads_base = str(path.resolve())
    save_config(config)
    return JSONResponse({"status": "ok"})


@app.post("/api/repo-order")
async def update_repo_order(payload: Dict[str, Any]) -> JSONResponse:
    """Persist a new repo ordering."""

    order = payload.get("order")
    if not isinstance(order, list):
        raise HTTPException(status_code=400, detail="order must be a list")

    config = load_config()
    config.repo_order = [str(repo) for repo in order]
    save_config(config)
    return JSONResponse({"status": "ok"})


@app.post("/api/thread-order")
async def update_thread_order(payload: Dict[str, Any]) -> JSONResponse:
    """Persist thread ordering for a repository."""

    repo = payload.get("repo")
    order = payload.get("order")

    if not repo or not isinstance(order, list):
        raise HTTPException(status_code=400, detail="Invalid payload")

    config = load_config()
    config.thread_order[repo] = [str(topic) for topic in order]
    save_config(config)
    return JSONResponse({"status": "ok"})


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

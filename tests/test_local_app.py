"""Tests for security and validation of the local dashboard FastAPI app."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from watercooler_dashboard import local_app
from watercooler_dashboard.config import DashboardConfig, save_config, load_config


def _setup_config(tmp_path: Path, monkeypatch) -> Path:
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("WATERCOOLER_DASHBOARD_CONFIG", str(config_path))
    threads_root = tmp_path / "threads-root"
    threads_root.mkdir()
    (threads_root / "alpha-threads").mkdir()
    config = DashboardConfig(threads_base=str(threads_root))
    save_config(config)
    return threads_root


def _client() -> TestClient:
    return TestClient(local_app.app)


def _auth_headers(origin: str = "http://testserver") -> dict[str, str]:
    return {
        "Origin": origin,
        "X-Watercooler-CSRF": local_app.CSRF_TOKEN,
    }


def test_update_threads_base_requires_csrf(monkeypatch, tmp_path):
    threads_root = _setup_config(tmp_path, monkeypatch)
    with _client() as client:
        response = client.post(
            "/api/config/threads-base",
            json={"threadsBase": str(threads_root)},
        )
    assert response.status_code == 403


def test_update_threads_base_rejects_untrusted_origin(monkeypatch, tmp_path):
    threads_root = _setup_config(tmp_path, monkeypatch)
    with _client() as client:
        response = client.post(
            "/api/config/threads-base",
            headers=_auth_headers(origin="http://evil.test"),
            json={"threadsBase": str(threads_root)},
        )
    assert response.status_code == 403


def test_update_threads_base_accepts_valid_request(monkeypatch, tmp_path):
    threads_root = _setup_config(tmp_path, monkeypatch)
    with _client() as client:
        response = client.post(
            "/api/config/threads-base",
            headers=_auth_headers(),
            json={"threadsBase": str(threads_root)},
        )
    assert response.status_code == 200
    config = load_config()
    assert Path(config.threads_base) == threads_root.resolve()


def test_update_thread_metadata_enforces_path_validation(monkeypatch, tmp_path):
    threads_root = _setup_config(tmp_path, monkeypatch)
    outside_file = tmp_path / "other.md"
    outside_file.write_text("# outside\n", encoding="utf-8")

    with _client() as client:
        response = client.post(
            "/api/thread-metadata",
            headers=_auth_headers(),
            json={"filePath": str(outside_file), "updates": {}},
        )
    assert response.status_code == 400


def test_update_thread_metadata_accepts_valid_thread(monkeypatch, tmp_path):
    threads_root = _setup_config(tmp_path, monkeypatch)
    thread_repo = threads_root / "alpha-threads"
    thread_path = thread_repo / "sample.md"
    thread_path.write_text(
        """# sample
Status: OPEN
Ball: Codex (caleb)
Topic: sample
Created: 2025-11-03T00:00:00Z

---
Entry: Codex (caleb) 2025-11-03T00:00:00Z
Role: implementer
Type: Note
Title: Kickoff

Ready to go.
""",
        encoding="utf-8",
    )

    payload = {
        "filePath": str(thread_path),
        "updates": {"Priority": "P0"},
        "repo": "alpha",
        "originalTopic": "sample",
    }
    with _client() as client:
        response = client.post(
            "/api/thread-metadata",
            headers=_auth_headers(),
            json=payload,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["thread"]["priority"] == "P0"


def test_health_response_reports_ok_for_recent_pollers():
    now = datetime.now()
    payload = local_app._build_health_response(
        [
            {
                "repo_path": "/tmp/alpha-threads",
                "running": True,
                "interval": 20,
                "last_commit": "abc123",
                "last_fetch": now.isoformat(),
                "fetch_count": 2,
                "error_count": 0,
                "last_error": None,
            }
        ],
        {"subscribers": 1, "last_refresh": now.isoformat(), "refresh_count": 1},
    )

    assert payload["status"] == "OK"
    assert payload["reasons"] == []
    assert payload["pollers"][0]["status"] == "OK"


def test_health_response_degraded_for_stale_fetch():
    now = datetime.now()
    stale_time = (now - timedelta(seconds=120)).isoformat()

    payload = local_app._build_health_response(
        [
            {
                "repo_path": "/tmp/beta-threads",
                "running": True,
                "interval": 20,
                "last_commit": "def456",
                "last_fetch": stale_time,
                "fetch_count": 5,
                "error_count": 0,
                "last_error": None,
            }
        ],
        {"subscribers": 0, "last_refresh": None, "refresh_count": 0},
    )

    assert payload["status"] == "DEGRADED"
    assert any("beta-threads" in reason for reason in payload["reasons"])
    poller = payload["pollers"][0]
    assert poller["status"] == "DEGRADED"
    assert any("Last fetch becoming stale" in issue for issue in poller["issues"])


def test_health_response_error_when_poller_stopped():
    now = datetime.now()
    payload = local_app._build_health_response(
        [
            {
                "repo_path": "/tmp/gamma-threads",
                "running": False,
                "interval": 20,
                "last_commit": "ghi789",
                "last_fetch": (now - timedelta(seconds=10)).isoformat(),
                "fetch_count": 1,
                "error_count": 4,
                "last_error": "Git timeout",
            }
        ],
        {"subscribers": 2, "last_refresh": now.isoformat(), "refresh_count": 5},
    )

    assert payload["status"] == "ERROR"
    poller = payload["pollers"][0]
    assert poller["status"] == "ERROR"
    assert any("Poller not running" in issue for issue in poller["issues"])
    assert any("Repeated errors" in issue for issue in poller["issues"])

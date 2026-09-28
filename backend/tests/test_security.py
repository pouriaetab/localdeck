"""Regression tests for the local-only access guard and path-traversal fix.

These lock in the three fixes made after an external security review:
  1. ``serve_frontend`` must never serve a file outside ``frontend/dist`` even
     when the request smuggles ``..%2f`` or an absolute path.
  2. A request whose ``Host`` header is not localhost/127.0.0.1 is refused
     (defence against DNS rebinding).
  3. A state-changing request whose ``Origin`` is a non-local site is refused
     (defence against cross-site POST / CSRF).

No third-party HTTP client is needed: the real app is started under uvicorn on
a loopback port and probed with the standard library ``http.client``, so an
encoded ``..%2f`` path and a forged ``Host`` header reach the app byte-for-byte
the way a browser or an attacker would send them.

Run directly (``python tests/test_security.py``) or under pytest.
"""
from __future__ import annotations

import http.client
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

# A clean, empty project list so the server's startup spawns nothing.
_STATE = Path(tempfile.gettempdir()) / "localdeck-security-test-state.json"
_STATE.write_text(
    '{"version": 1, "dashboard": {"expanded_project_id": null}, "projects": []}\n'
)
os.environ["LOCALDECK_STATE_FILE"] = str(_STATE)

import uvicorn  # noqa: E402

from app.main import FRONTEND_DIST, app  # noqa: E402


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


PORT = _free_port()


class _BackgroundServer:
    def __init__(self, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        # uvicorn skips signal-handler installation off the main thread, so a
        # daemon thread is a fine way to run it for the duration of the tests.
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "_BackgroundServer":
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.05)
        else:  # pragma: no cover - only if the server never comes up
            raise RuntimeError("test server did not start")
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(scope="module")
def server():
    with _BackgroundServer(PORT):
        yield


def _request(method: str, path: str, headers: dict | None = None,
             host: str | None = None) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    # skip_host lets us send the path verbatim and set Host ourselves.
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host or f"127.0.0.1:{PORT}")
    for key, value in (headers or {}).items():
        conn.putheader(key, value)
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, body


needs_dist = pytest.mark.skipif(
    not FRONTEND_DIST.exists(), reason="frontend/dist is not built"
)


# --- 1. Path traversal -------------------------------------------------------
@needs_dist
def test_encoded_dotdot_cannot_read_app_source(server):
    status, body = _request("GET", "/..%2f..%2fbackend%2fapp%2fmain.py")
    assert "local_only_guard" not in body  # its own source must not leak
    assert status == 200  # unknown/blocked path falls back to the SPA index


@needs_dist
def test_absolute_path_cannot_escape_dist(server):
    status, body = _request("GET", "//etc/passwd")
    assert "root:x:0:0" not in body
    assert status == 200


# --- 2. Host header (DNS rebinding) ------------------------------------------
def test_non_local_host_refused(server):
    status, _ = _request("GET", "/api/health", host="evil.com")
    assert status == 421


def test_local_host_allowed(server):
    assert _request("GET", "/api/health", host=f"localhost:{PORT}")[0] == 200
    assert _request("GET", "/api/health", host=f"127.0.0.1:{PORT}")[0] == 200


# --- 3. Cross-origin state change (CSRF) -------------------------------------
def test_cross_origin_state_change_refused(server):
    status, _ = _request(
        "POST", "/api/projects/stop-all", headers={"Origin": "http://evil.com"}
    )
    assert status == 403


def test_cross_origin_shutdown_refused_and_server_survives(server):
    status, _ = _request(
        "POST", "/api/shutdown", headers={"Origin": "https://attacker.example"}
    )
    assert status == 403
    assert _request("GET", "/api/health")[0] == 200  # still up


def test_same_origin_state_change_allowed(server):
    status, _ = _request(
        "POST",
        "/api/projects/stop-all",
        headers={"Origin": f"http://127.0.0.1:{PORT}"},
    )
    assert status == 200


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))

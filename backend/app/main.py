from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import schedules
from .process_manager import ProcessManagerError
from .runtime import AppRuntime
from .schemas import (
    ArchivePayload,
    ExpandedPayload,
    PanelPayload,
    CategoryPayload,
    ProjectPayload,
    ReorderPayload,
    ReschedulePayload,
    SetCategoryPayload,
    ScheduleActionPayload,
    StartPayload,
    VisibilityPayload,
)

# How this process was started, captured before anything can modify sys.argv, so
# /api/restart can start an identical one in its place.
LAUNCH_ARGV = list(sys.argv[1:])

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_FILE = ROOT / "data" / "localdeck.json"
FRONTEND_DIST = (ROOT / "frontend" / "dist").resolve()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state_path = Path(os.environ.get("LOCALDECK_STATE_FILE", DEFAULT_STATE_FILE))
    runtime = AppRuntime(state_path)
    app.state.runtime = runtime
    await runtime.startup()
    try:
        yield
    finally:
        await runtime.shutdown()


app = FastAPI(title="localdeck", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8900",
        "http://localhost:8900",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Local-only access guard -------------------------------------------------
# localdeck can start, stop and (via a project's command) run anything on
# this machine, so its API must only ever answer the person sitting at it.
# Two browser-based attacks otherwise reach a loopback-only server:
#   * DNS rebinding - a page on evil.com re-points evil.com at 127.0.0.1 and
#     then talks to localdeck as same-origin. Defeated by refusing any
#     request whose Host header is not localhost/127.0.0.1: the browser sends
#     the site's own hostname there and cannot forge it.
#   * Cross-site POST (CSRF) - any page can fire Start/Stop/Shutdown at
#     127.0.0.1 without reading the reply. Defeated by refusing a
#     state-changing request whose Origin is a non-local site; the browser
#     attaches Origin on cross-site requests and JS cannot strip or spoof it.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _host_is_local(host_header: str) -> bool:
    # host_header carries no scheme (e.g. "127.0.0.1:8000" or "[::1]:8000");
    # urlsplit parses off the port and IPv6 brackets for us.
    return (urlsplit("//" + host_header).hostname or "").lower() in _LOCAL_HOSTS


def _origin_is_local(origin_header: str) -> bool:
    return (urlsplit(origin_header).hostname or "").lower() in _LOCAL_HOSTS


@app.middleware("http")
async def local_only_guard(request: Request, call_next):
    if not _host_is_local(request.headers.get("host", "")):
        return JSONResponse({"detail": "Invalid host header."}, status_code=421)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None and not _origin_is_local(origin):
            return JSONResponse(
                {"detail": "Cross-origin request refused."}, status_code=403
            )
    return await call_next(request)


def get_runtime(request: Request) -> AppRuntime:
    return request.app.state.runtime


@app.get("/api/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
async def get_state(request: Request) -> dict:
    return get_runtime(request).serialize_state()


@app.post("/api/projects")
async def add_project(request: Request, payload: ProjectPayload) -> dict:
    runtime = get_runtime(request)
    return await runtime.add_project(payload.dict())


@app.put("/api/projects/{project_id}")
async def update_project(project_id: str, request: Request, payload: ProjectPayload) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.update_project(project_id, payload.dict())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, request: Request) -> JSONResponse:
    runtime = get_runtime(request)
    try:
        await runtime.remove_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    return JSONResponse({"deleted": True})


@app.post("/api/projects/{project_id}/start")
async def start_project(project_id: str, request: Request, payload: StartPayload = StartPayload()) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.start_project(project_id, extra_args=payload.extra_args)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except ProcessManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/stop")
async def stop_project(project_id: str, request: Request) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.stop_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@app.post("/api/projects/{project_id}/restart")
async def restart_project(project_id: str, request: Request,
                          payload: StartPayload = StartPayload()) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.restart_project(project_id, extra_args=payload.extra_args)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except ProcessManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/clear-logs")
async def clear_logs(project_id: str, request: Request) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.clear_logs(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@app.post("/api/projects/{project_id}/visibility")
async def set_visibility(project_id: str, request: Request, payload: VisibilityPayload) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.set_visibility(project_id, payload.visible)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@app.post("/api/projects/{project_id}/archive")
async def set_archived(project_id: str, request: Request, payload: ArchivePayload) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.set_archived(project_id, payload.archived)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@app.post("/api/projects/{project_id}/panel")
async def update_panel(project_id: str, request: Request, payload: PanelPayload) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.set_panel(project_id, payload.dict())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@app.get("/api/projects/{project_id}/conformance")
async def check_conformance(project_id: str, request: Request) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.check_conformance(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@app.post("/api/projects/{project_id}/conformance/apply")
async def apply_conformance(project_id: str, request: Request) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.apply_conformance(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except ProcessManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/reorder")
async def reorder_projects(request: Request, payload: ReorderPayload) -> dict:
    return await get_runtime(request).reorder_projects(payload.order)


@app.post("/api/dashboard/categories/add")
async def add_category(request: Request, payload: CategoryPayload) -> dict:
    return await get_runtime(request).add_category(payload.name)


@app.post("/api/dashboard/categories/remove")
async def remove_category(request: Request, payload: CategoryPayload) -> dict:
    return await get_runtime(request).remove_category(payload.name)


@app.post("/api/projects/{project_id}/category")
async def set_project_category(project_id: str, request: Request, payload: SetCategoryPayload) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.set_project_category(project_id, payload.category)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@app.post("/api/projects/start-all")
async def start_all_projects(request: Request) -> dict:
    return await get_runtime(request).start_all()


@app.post("/api/projects/stop-all")
async def stop_all_projects(request: Request) -> dict:
    return await get_runtime(request).stop_all()


@app.post("/api/dashboard/reset-layout")
async def reset_layout(request: Request) -> dict:
    return await get_runtime(request).reset_layout()


@app.post("/api/dashboard/resolve-ports")
async def resolve_ports(request: Request) -> dict:
    return await get_runtime(request).resolve_ports()


@app.get("/api/schedules")
async def get_schedules() -> dict:
    tasks = await asyncio.to_thread(schedules.list_tasks)
    return {"tasks": tasks}


@app.post("/api/schedules/{label}/action")
async def schedule_action(label: str, payload: ScheduleActionPayload) -> dict:
    try:
        return await asyncio.to_thread(schedules.perform_action, label, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/schedules/{label}/log")
async def schedule_log(label: str) -> dict:
    try:
        return await asyncio.to_thread(schedules.tail_log, label)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/schedules/{label}/reschedule")
async def schedule_reschedule(label: str, payload: ReschedulePayload) -> dict:
    try:
        return await asyncio.to_thread(
            schedules.reschedule, label, payload.hour, payload.minute, payload.weekdays
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/shutdown")
async def shutdown_deck(request: Request) -> JSONResponse:
    """Stop every project, then shut down localdeck's own server."""
    runtime = get_runtime(request)
    await runtime.stop_all()

    async def _terminate() -> None:
        await asyncio.sleep(0.4)  # let the HTTP response flush first
        os.kill(os.getpid(), signal.SIGINT)

    asyncio.create_task(_terminate())
    return JSONResponse({"shutting_down": True})


@app.post("/api/restart")
async def restart_deck(request: Request) -> JSONResponse:
    """Stop every project, then replace this server with a fresh copy of itself.

    os.execv keeps the process id, the terminal window and the port: the window
    that launched localdeck keeps streaming, nothing has to be double-clicked,
    and the dashboard reconnects on its own. That is the whole point — restarting
    to pick up backend changes should not mean quitting, finding the Terminal
    window and launching again.
    """
    # Never take the projects down for a restart we cannot actually perform: if
    # this process was not started in a way we can reproduce, say so and change
    # nothing, rather than leaving a stopped deck that has to be rescued from a
    # Terminal window.
    if not any(":" in argument for argument in LAUNCH_ARGV):
        raise HTTPException(
            status_code=400,
            detail=(
                "localdeck cannot restart itself: it was not started with a "
                "uvicorn command line it can repeat. Quit and relaunch instead."
            ),
        )

    runtime = get_runtime(request)
    await runtime.stop_all()

    command = [sys.executable, "-m", "uvicorn", *LAUNCH_ARGV]

    async def _replace() -> None:
        await asyncio.sleep(0.4)  # let the HTTP response flush first
        try:
            await runtime.shutdown()
        except Exception:
            pass
        os.execv(sys.executable, command)

    asyncio.create_task(_replace())
    return JSONResponse({"restarting": True, "command": " ".join(command)})


@app.post("/api/dashboard/expanded")
async def set_expanded(request: Request, payload: ExpandedPayload) -> dict:
    runtime = get_runtime(request)
    try:
        return await runtime.set_expanded_project(payload.project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


LAUNCH_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Starting…</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; height:100vh; display:grid; place-items:center;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#f4f2ec; color:#23211d; }
  @media (prefers-color-scheme: dark) { body { background:#1e1d1b; color:#efece4; } }
  .box { text-align:center; max-width:420px; padding:24px; }
  .spinner { width:34px; height:34px; margin:0 auto 16px; border-radius:50%;
             border:3px solid rgba(201,100,66,.25); border-top-color:#c96442;
             animation:spin 0.9s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  h2 { margin:0 0 6px; font-size:1.15rem; }
  p { margin:0; color:#857f74; font-size:.9rem; }
  a { color:#c96442; }
</style></head>
<body><div class="box">
  <div class="spinner"></div>
  <h2 id="msg">Starting…</h2>
  <p id="sub">Waiting for the app to come online.</p>
</div>
<script>
  var ID = "__PROJECT_ID__";
  var tries = 0;
  async function tick() {
    tries++;
    try {
      var s = await fetch("/api/state").then(function(r){return r.json();});
      var p = (s.projects || []).find(function(x){ return x.id === ID; });
      if (p) {
        document.getElementById("msg").textContent = "Starting " + p.name + "…";
        var reachable = p.preview && p.preview.reachable;
        if (p.web_url && reachable) {
          // Carry the project's build id into the URL. Without it, a browser
          // holding a cached copy of the app's index.html can answer this
          // navigation from disk and silently show the previous build.
          var target = p.web_url;
          if (p.build && p.build.fingerprint) {
            target += (target.indexOf("?") === -1 ? "?" : "&") +
                      "deckBuild=" + encodeURIComponent(p.build.fingerprint);
          }
          location.replace(target); return;
        }
        if (tries > 90 && p.web_url) {
          document.getElementById("sub").innerHTML =
            "Still starting. <a href='" + p.web_url + "'>Open it anyway →</a>";
        }
      }
    } catch (e) {}
    setTimeout(tick, 1000);
  }
  tick();
</script></body></html>
"""


@app.get("/launch/{project_id}")
async def launch_page(project_id: str) -> HTMLResponse:
    safe = "".join(c for c in project_id if c.isalnum() or c in "-_")
    return HTMLResponse(LAUNCH_PAGE.replace("__PROJECT_ID__", safe))


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    # The HTTP middleware above never sees websocket handshakes, so repeat the
    # local-only checks here: a cross-site page can open a websocket (CORS does
    # not apply to them) and would otherwise stream the whole project list.
    if not _host_is_local(websocket.headers.get("host", "")):
        await websocket.close(code=1008)
        return
    ws_origin = websocket.headers.get("origin")
    if ws_origin is not None and not _origin_is_local(ws_origin):
        await websocket.close(code=1008)
        return
    runtime: AppRuntime = websocket.app.state.runtime
    await runtime.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        runtime.disconnect(websocket)


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.middleware("http")
    async def cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/assets/"):
            # Vite content-hashes these filenames, so a given path's bytes never
            # change — safe (and correct) to let the browser cache them forever.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # index.html (served for "/" and every unknown client route below)
            # names the *current* hashed asset files. If the browser is allowed
            # to cache it, a later rebuild deletes the old hashed files and any
            # tab still holding the stale index.html 404s trying to load them.
            # Forcing a revalidation on every load keeps that from happening.
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str) -> FileResponse:
        index_file = FRONTEND_DIST / "index.html"
        if full_path:
            # Resolve the requested path and confirm it stays inside the build
            # directory. Without this, "..%2f..%2f" (or an absolute path, which
            # pathlib's "/" operator would honour) escapes FRONTEND_DIST and
            # serves any file the account can read - SSH keys, .env, app data.
            candidate = (FRONTEND_DIST / full_path).resolve()
            try:
                candidate.relative_to(FRONTEND_DIST)
            except ValueError:
                return FileResponse(index_file)
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index_file)
else:

    @app.get("/")
    async def dev_hint() -> JSONResponse:
        return JSONResponse(
            {
                "message": "Frontend build not found. Run the Vite dev server on http://127.0.0.1:5173 or build frontend/dist.",
            }
        )

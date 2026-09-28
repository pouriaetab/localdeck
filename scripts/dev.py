from __future__ import annotations

import os
import signal
import subprocess
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "data" / "localdeck.json"
BACKEND_PYTHON = ROOT / ".venv" / "bin" / "python"
VITE_ENTRYPOINT = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"


def terminate_process_group(process: subprocess.Popen, sig: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except ProcessLookupError:
        return


def main() -> int:
    if not BACKEND_PYTHON.exists():
        print("Missing .venv. Run ./setup.sh first.", file=sys.stderr)
        return 1
    if not VITE_ENTRYPOINT.exists():
        print("Missing frontend dependencies. Run ./setup.sh first.", file=sys.stderr)
        return 1

    node_binary = os.environ.get("LOCALDECK_NODE_BIN")
    if node_binary:
        node_path = Path(node_binary)
    else:
        discovered = shutil.which("node")
        node_path = Path(discovered) if discovered else Path()

    if not node_path.exists():
        print("Unable to find a Node.js binary. Set LOCALDECK_NODE_BIN or install Node.", file=sys.stderr)
        return 1

    backend_env = os.environ.copy()
    backend_env["LOCALDECK_STATE_FILE"] = str(STATE_FILE)

    backend_process = subprocess.Popen(
        [
            str(BACKEND_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            str(ROOT / "backend"),
            "--reload",
            "--host",
            "127.0.0.1",
            "--port",
            os.environ.get("LOCALDECK_PORT", "8900"),
        ],
        cwd=ROOT,
        env=backend_env,
        start_new_session=True,
    )
    frontend_process = subprocess.Popen(
        [
            str(node_path),
            str(VITE_ENTRYPOINT),
            "--host",
            "127.0.0.1",
            "--port",
            "5173",
        ],
        cwd=ROOT / "frontend",
        start_new_session=True,
    )

    def shutdown(_signum: int, _frame) -> None:
        terminate_process_group(frontend_process, signal.SIGTERM)
        terminate_process_group(backend_process, signal.SIGTERM)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            backend_code = backend_process.poll()
            frontend_code = frontend_process.poll()
            if backend_code is not None or frontend_code is not None:
                break
            time.sleep(0.25)
    finally:
        shutdown(signal.SIGTERM, None)
        time.sleep(0.5)
        terminate_process_group(frontend_process, signal.SIGKILL)
        terminate_process_group(backend_process, signal.SIGKILL)

    backend_code = backend_process.wait()
    frontend_code = frontend_process.wait()
    return backend_code if backend_code != 0 else frontend_code


if __name__ == "__main__":
    raise SystemExit(main())

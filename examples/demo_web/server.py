"""A tiny web app for trying localdeck. Standard library only.

It reads its port from FRONTEND_PORT, which localdeck sets from the project's
port mapping, and falls back to 5601 when run on its own. `--share` prints a
stand-in "public" address, so the dashboard's alternate-start button has
something to pick up; a real app would print its tunnel address here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Demo web app</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 0; padding: 32px;
         background: #f4f2ec; color: #23211d; }}
  .card {{ background: #fff; border-radius: 12px; padding: 24px; max-width: 420px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ margin: 0 0 8px; font-size: 1.3rem; }}
  p {{ margin: 4px 0; color: #5f5a50; }}
  code {{ background: #eee9df; padding: 1px 6px; border-radius: 4px; }}
</style></head>
<body><div class="card">
  <h1>Demo web app</h1>
  <p>Served on port <code>{port}</code>, read from <code>FRONTEND_PORT</code>.</p>
  <p>Page rendered at {now}.</p>
  <p>Requests so far: {count}</p>
</div></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--share", action="store_true", help="print a shareable address")
    args = parser.parse_args()
    port = int(os.environ.get("FRONTEND_PORT", "5601"))
    count = 0

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            nonlocal count
            count += 1
            body = PAGE.format(port=port, now=dt.datetime.now().strftime("%H:%M:%S"),
                               count=count).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # Never let a browser keep an old copy of this page.
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *values) -> None:
            print(f"GET {self.path} -> {values[1] if len(values) > 1 else ''}", flush=True)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Demo web app listening on http://127.0.0.1:{port}", flush=True)
    if args.share:
        print(f"Shareable address: http://127.0.0.1:{port}/shared", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

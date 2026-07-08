#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import urlparse

from github_issue_tracking import (
    TRACKING_FIELDS,
    apply_tracking_update,
    get_tracking_host,
    get_tracking_path,
    get_tracking_port,
    load_tracking,
)


_WRITE_LOCK = threading.Lock()


class TrackingHandler(BaseHTTPRequestHandler):
    server_version = "GitHubIssueTracking/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "tracking_path": get_tracking_path(),
                    "fields": list(TRACKING_FIELDS),
                },
            )
            return

        if path == "/tracking":
            self._send_json(200, load_tracking())
            return

        self._send_json(
            200,
            {
                "ok": True,
                "service": "github-issue-tracking",
                "tracking_path": get_tracking_path(),
            },
        )

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/track":
            self._send_json(404, {"ok": False, "error": "Unknown endpoint"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid JSON"})
            return

        metadata = {
            "repo": payload.get("repo"),
            "number": payload.get("number"),
            "title": payload.get("title"),
            "url": payload.get("url"),
        }

        try:
            with _WRITE_LOCK:
                entry = apply_tracking_update(
                    str(payload.get("issue_key", "")),
                    str(payload.get("field", "")),
                    bool(payload.get("value", False)),
                    metadata,
                )
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        except OSError as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})
            return

        self._send_json(200, {"ok": True, "entry": entry})


def main() -> None:
    host = get_tracking_host()
    port = get_tracking_port()
    server = ThreadingHTTPServer((host, port), TrackingHandler)
    print(f"Tracking server: http://{host}:{port}", flush=True)
    print(f"Tracking file: {get_tracking_path()}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped tracking server.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

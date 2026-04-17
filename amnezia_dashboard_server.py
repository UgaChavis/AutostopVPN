#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "scripts"):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

import amnezia_traffic_collector as collector


HOST = "127.0.0.1"
PORT = 18080


@lru_cache(maxsize=1)
def _summary_fallback() -> dict:
    current_time = collector.now_local()
    totals = collector.load_json(
        collector.TOTALS_FILE,
        {"peers": {}, "timezone": collector.TIMEZONE, "updated_at": ""},
    )
    return collector.build_default_summary(current_time, totals)


def load_current_summary() -> dict:
    current_time = collector.now_local()
    fallback = _summary_fallback()
    summary = collector.load_json(collector.SUMMARY_FILE, fallback)
    if not isinstance(summary, dict):
        return fallback
    summary.setdefault("timezone", fallback.get("timezone", collector.TIMEZONE))
    summary.setdefault("updated_at", fallback.get("updated_at", current_time.isoformat()))
    summary.setdefault("warnings", [])
    summary.setdefault("peers", [])
    summary.setdefault("server", fallback.get("server", {}))
    summary.setdefault("vpn", fallback.get("vpn", {}))
    summary.setdefault("server_info", fallback.get("server_info", {}))
    summary.setdefault("periods", fallback.get("periods", {}))
    return summary


def render_current_html() -> str:
    return collector.render_dashboard(load_current_summary())


def render_current_json() -> bytes:
    summary = load_current_summary()
    payload = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    return (payload + "\n").encode("utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AutostopVPNDashboard/1.0"

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path in {"/", "/index.html"}:
            body = render_current_html().encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/dashboard.json":
            body = render_current_json()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

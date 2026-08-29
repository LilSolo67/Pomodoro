"""Small SQLite-backed analytics API for the Pomodoro timer."""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ADMIN_USERNAME = "admin"
ADMIN_HTML_PATH = Path(__file__).resolve().parent / "admin.html"


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    phase TEXT,
    cycle INTEGER,
    duration_seconds INTEGER,
    elapsed_seconds INTEGER,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events (occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_session_id ON events (session_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def day_bounds(day: str | None) -> tuple[str, str]:
    selected = datetime.strptime(day, "%Y-%m-%d").date() if day else datetime.now(timezone.utc).date()
    start = datetime.combine(selected, datetime.min.time(), timezone.utc).isoformat()
    end = datetime.combine(selected, datetime.max.time(), timezone.utc).isoformat()
    return start, end


class AnalyticsStore:
    def __init__(self, database_path: str | Path = "pomodoro.db") -> None:
        self.database_path = str(database_path)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def record_event(self, event: dict[str, Any]) -> dict[str, Any]:
        recorded = {
            "session_id": event.get("session_id") or str(uuid.uuid4()),
            "event_type": event["event_type"],
            "phase": event.get("phase"),
            "cycle": event.get("cycle"),
            "duration_seconds": event.get("duration_seconds"),
            "elapsed_seconds": event.get("elapsed_seconds"),
            "occurred_at": event.get("occurred_at") or utc_now(),
        }
        with self.connection() as connection:
            cursor = connection.execute(
                """INSERT INTO events
                (session_id, event_type, phase, cycle, duration_seconds,
                 elapsed_seconds, occurred_at)
                VALUES (:session_id, :event_type, :phase, :cycle,
                        :duration_seconds, :elapsed_seconds, :occurred_at)""",
                recorded,
            )
            recorded["id"] = cursor.lastrowid
        return recorded

    def summary(self, day: str | None = None) -> dict[str, Any]:
        start, end = day_bounds(day)
        with self.connection() as connection:
            row = connection.execute(
                """SELECT
                    COUNT(DISTINCT session_id) AS sessions,
                    SUM(CASE WHEN event_type = 'phase_completed'
                             AND phase = 'work' THEN 1 ELSE 0 END) AS completed_work_phases,
                    COALESCE(SUM(CASE WHEN event_type = 'phase_completed'
                                      AND phase = 'work'
                                      THEN COALESCE(elapsed_seconds, duration_seconds, 0)
                                      ELSE 0 END), 0) AS focus_seconds,
                    SUM(CASE WHEN event_type = 'session_completed'
                             THEN 1 ELSE 0 END) AS completed_sessions
                FROM events
                WHERE occurred_at >= ? AND occurred_at <= ?""",
                (start, end),
            ).fetchone()
        return {
            "date": day or datetime.now(timezone.utc).date().isoformat(),
            "sessions": row["sessions"] or 0,
            "completed_work_phases": row["completed_work_phases"] or 0,
            "focus_seconds": row["focus_seconds"] or 0,
            "completed_sessions": row["completed_sessions"] or 0,
        }

    def daily(self, days: int = 7) -> list[dict[str, Any]]:
        if not 1 <= days <= 90:
            raise ValueError("days must be between 1 and 90")
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT substr(occurred_at, 1, 10) AS date,
                    COUNT(DISTINCT session_id) AS sessions,
                    SUM(CASE WHEN event_type = 'phase_completed'
                             AND phase = 'work' THEN 1 ELSE 0 END) AS completed_work_phases,
                    COALESCE(SUM(CASE WHEN event_type = 'phase_completed'
                                      AND phase = 'work'
                                      THEN COALESCE(elapsed_seconds, duration_seconds, 0)
                                      ELSE 0 END), 0) AS focus_seconds
                FROM events
                GROUP BY substr(occurred_at, 1, 10)
                ORDER BY date DESC LIMIT ?""",
                (days,),
            ).fetchall()
        return [dict(row) for row in rows]


class AnalyticsHandler(BaseHTTPRequestHandler):
    store: AnalyticsStore
    admin_password: str

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[len("Basic ") :]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(username, ADMIN_USERNAME) and hmac.compare_digest(
            password, self.admin_password
        )

    def _require_auth(self) -> None:
        body = b"Authentication required"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Pomodoro Admin"')
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        protected = parsed.path == "/admin" or parsed.path.startswith("/api/analytics/")
        if protected and not self._authorized():
            self._require_auth()
            return
        try:
            if parsed.path == "/health":
                self._send({"ok": True})
            elif parsed.path == "/admin":
                html = ADMIN_HTML_PATH.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif parsed.path == "/api/analytics/summary":
                self._send(self.store.summary(query.get("date", [None])[0]))
            elif parsed.path == "/api/analytics/daily":
                self._send(self.store.daily(int(query.get("days", [7])[0])))
            else:
                self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError) as error:
            self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/events":
            self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            event = json.loads(self.rfile.read(length))
            if not isinstance(event, dict) or not event.get("event_type"):
                raise ValueError("event_type is required")
            self._send({"event": self.store.record_event(event)}, HTTPStatus.CREATED)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._send({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8000, database: str = "pomodoro.db") -> None:
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if not admin_password:
        print(
            "ADMIN_PASSWORD environment variable is not set. "
            "Set it before starting the server, e.g.:\n"
            "  ADMIN_PASSWORD='your-password' python3 backend.py",
            file=sys.stderr,
        )
        raise SystemExit(1)
    AnalyticsHandler.store = AnalyticsStore(database)
    AnalyticsHandler.admin_password = admin_password
    server = ThreadingHTTPServer((host, port), AnalyticsHandler)
    print(f"Pomodoro analytics listening on http://{host}:{port}")
    print(f"Admin dashboard at http://{host}:{port}/admin (user: {ADMIN_USERNAME})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Pomodoro analytics API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", default="pomodoro.db")
    args = parser.parse_args()
    serve(args.host, args.port, args.database)
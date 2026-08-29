# Pomodoro

An instrument-panel Pomodoro timer — 25-minute work intervals and 5-minute breaks, repeating three times. Built as a single self-contained HTML file with an SVG dial, brass tick marks, and a Web Audio chime on phase changes.

Live at: https://lilsolo67.github.io/Pomodoro/

## Analytics backend

The optional Python backend stores timer events in SQLite and serves analytics
for the timer. It uses only the Python standard library. An admin password is
required to start it:

```sh
ADMIN_PASSWORD='your-password' python3 backend.py
```

The API listens on `http://127.0.0.1:8000` by default. The timer sends events
to it when started, paused, reset, skipped, or when a phase/session completes.
The timer still works when the backend is offline.

- `POST /api/events` records a timer event. Open.
- `GET /health` checks availability. Open.
- `GET /admin` serves the analytics dashboard. Requires auth.
- `GET /api/analytics/summary` returns today's totals. Requires auth.
- `GET /api/analytics/summary?date=2026-08-29` returns a UTC day's totals. Requires auth.
- `GET /api/analytics/daily?days=7` returns recent daily totals. Requires auth.

Routes marked "Requires auth" are gated with HTTP Basic Auth, username
`admin` and the password from `ADMIN_PASSWORD`.

Use `--database path/to/file.db` to choose a different SQLite file.

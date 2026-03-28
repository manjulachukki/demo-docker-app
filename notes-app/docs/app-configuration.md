# Notes App — Application Configuration

## Overview

The notes-app is an advanced Flask application designed to demonstrate real-time log collection and monitoring with the ELK Stack. Unlike a simple Hello World, it handles user input, applies security controls, and emits structured logs that are meaningful for observability.

**Key capabilities:**
- Create, list, and delete text notes via a browser UI
- JSON-structured logs with request tracing, IP privacy, and security metadata
- Per-IP rate limiting to simulate realistic traffic control
- XSS prevention via HTML escaping
- Demo endpoints to manually trigger different log levels for Kibana exploration

---

## Directory Structure

```
notes-app/
├── app.py                          # Flask application (all logic here)
├── Dockerfile                      # Container image definition
├── requirements.txt                # Python dependencies
├── docker-compose.yml              # Service orchestration
├── .env.example                    # Template — copy to .env and fill in values
├── elk/
│   ├── certs/
│   │   └── http_ca.crt             # Copied from es01 — NOT in git
│   ├── filebeat/
│   │   └── filebeat.yml            # Filebeat log shipping config
│   └── logstash/
│       └── pipeline/
│           └── logstash.conf       # Logstash transform pipeline
└── docs/
    ├── app-configuration.md        # This file
    ├── elk-configuration.md        # ELK setup and integration
    ├── kibana-setup.md             # Kibana Data View, dashboards, queries
    └── teardown.md                 # Full cleanup procedure
```

---

## Application Architecture

```
Browser / curl
     │
     ▼
Flask app (port 5001)
     │
     ├── before_request hook  →  assign request_id, check rate limit
     ├── Route handler        →  process request, write notes to memory
     └── after_request hook   →  log status_code + duration_ms
     │
     ▼
stdout (JSON log line)
     │
     ▼  (Docker captures stdout and writes to container log file)
/var/lib/docker/containers/<id>/<id>-json.log
     │
     ▼  (Filebeat tails this file)
Logstash :5045
     │
     ▼  (Logstash transforms and indexes)
Elasticsearch notes-app-YYYY.MM.dd index
     │
     ▼
Kibana dashboard
```

---

## Dockerfile Explained

The Dockerfile is intentionally layered for build efficiency:

```dockerfile
FROM python:3.13-alpine    # Minimal base — small image, fewer vulnerabilities

WORKDIR /app

# Copy requirements FIRST, install dependencies.
# Docker caches this layer. On subsequent builds, if only app.py changes,
# pip install is skipped — saving significant build time.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code AFTER dependencies.
# This layer changes on every code edit, but that's fast since deps are cached.
COPY app.py .

EXPOSE 5001                # Documentation only — actual port binding is in docker-compose.yml

CMD ["python", "app.py"]   # Exec form (not shell form) — PID 1 receives signals correctly
```

**Why Alpine?** The Alpine base image is ~5MB vs ~900MB for the full Python image. Smaller images mean faster pulls, less storage, and a smaller attack surface.

**Why exec form for CMD?** `CMD ["python", "app.py"]` runs Python as PID 1 directly. `CMD python app.py` wraps it in a shell (`/bin/sh -c`), so Python gets a different PID and won't receive `SIGTERM` from Docker correctly during `docker stop`.

---

## Dependencies (`requirements.txt`)

| Package | Version | Purpose |
|---|---|---|
| `flask` | 3.1.0 | Web framework |
| `python-json-logger` | 2.0.7 | Emit structured JSON log lines |

**Why `python-json-logger`?** Python's built-in logging emits plain text like `2026-03-28 INFO message`. ELK works much better with JSON: `{"timestamp": "...", "level": "INFO", "message": "...", "request_id": "..."}`. Each field is individually indexed and queryable in Kibana without needing regex parsing.

---

## Logging Setup (`app.py`)

```python
import logging
from pythonjsonlogger import jsonlogger

logger = logging.getLogger("notes_app")
logger.setLevel(logging.DEBUG)

handler = logging.StreamHandler()   # Write to stdout → Docker captures it
formatter = jsonlogger.JsonFormatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s"
)
handler.setFormatter(formatter)
logger.addHandler(handler)
```

**Every log line includes:**
- `timestamp` — ISO-8601 datetime
- `level` / `levelname` — DEBUG, INFO, WARNING, ERROR
- `message` — human-readable description
- `request_id` — 8-character UUID fragment, same across all log lines for one request
- `ip_hash` — SHA-256 of the client IP (first 16 chars) — privacy-safe IP tracking
- Additional context fields (endpoint, note_id, duration_ms, etc.)

---

## Security Features

### 1. IP Hashing
```python
def _hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:16]
```
Raw IP addresses are PII. We hash them so logs are useful for tracking patterns without storing personal data. The same IP always produces the same hash, so you can correlate events from one client without knowing their actual IP.

### 2. HTML Escaping (XSS Prevention)
```python
def _sanitize(text: str) -> str:
    return html.escape(str(text))[:500]
```
User input from the form is escaped before being stored or rendered. This prevents Cross-Site Scripting (XSS) attacks where a malicious user might inject `<script>` tags. The 500-character limit prevents storage bloat.

### 3. Per-IP Rate Limiting
```python
RATE_LIMIT  = int(os.environ.get("RATE_LIMIT", 10))
RATE_WINDOW = int(os.environ.get("RATE_WINDOW", 60))
```
Each IP is allowed at most `RATE_LIMIT` requests per `RATE_WINDOW` seconds. Excess requests return HTTP 429. The in-memory store uses sliding window counting. Configurable via environment variables — no code changes needed.

### 4. No `debug=True`
```python
app.run(host="0.0.0.0", port=5001, debug=False)
```
Flask's debug mode exposes an interactive debugger in the browser — a critical vulnerability in any shared environment. Always `debug=False`.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Browser UI — view and create notes |
| `POST` | `/notes` | Create a new note (form submission) |
| `POST` | `/notes/<id>` | Delete a note (HTML form uses `_method=DELETE`) |
| `GET` | `/api/notes` | JSON list of all notes (REST API) |
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |
| `GET` | `/stats` | App stats: note count, uptime, request count |
| `POST` | `/demo/error` | Trigger an ERROR log — for Kibana demos |
| `POST` | `/demo/warning` | Trigger a WARNING log — for Kibana demos |
| `POST` | `/demo/bulk` | Fire 5 logs at different levels — for dashboard demos |

**Why demo endpoints?** In a live classroom demo, you want to show different log levels appearing in Kibana without waiting for real errors. The demo endpoints let you trigger them on command.

---

## Request Tracing

Every request gets a unique `request_id`:

```python
@app.before_request
def before_request():
    g.request_id = str(uuid.uuid4())[:8]   # e.g., "a1b2c3d4"
    g.start_time = time.time()
    # ... rate limit check ...
    logger.info("request started", extra={"request_id": g.request_id, ...})

@app.after_request
def after_request(response):
    duration_ms = round((time.time() - g.start_time) * 1000, 2)
    logger.info("request completed", extra={
        "request_id": g.request_id,
        "status_code": response.status_code,
        "duration_ms": duration_ms
    })
    return response
```

All log lines emitted during a single request share the same `request_id`. In Kibana you can filter `request_id: "a1b2c3d4"` to see every log line for that specific request — even across multiple services if they propagate the ID.

---

## Running the App Locally (without Docker)

Useful for quick testing without the full ELK pipeline:

```bash
cd notes-app
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Visit http://localhost:5001

Logs will print to your terminal as JSON. You won't have Kibana, but you can verify the app logic works.

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `FLASK_ENV` | `production` | Flask environment |
| `RATE_LIMIT` | `10` | Max requests per IP per time window |
| `RATE_WINDOW` | `60` | Rate limit window in seconds |

All variables are optional — the app uses defaults if not set. Set them in `docker-compose.yml` under the `app` service's `environment` section.

---

## Extending the App

**Add a new endpoint:**
```python
@app.route("/my-endpoint", methods=["GET"])
def my_endpoint():
    logger.info("my endpoint called", extra={
        "request_id": getattr(g, "request_id", "n/a"),
        "ip_hash": _hash_ip(request.remote_addr)
    })
    return jsonify({"result": "ok"})
```

**Add a new log field:**
Pass any key-value pair in the `extra={}` dict — it appears as a top-level field in the JSON log line, gets indexed by Logstash, and becomes queryable in Kibana.

**Add persistent storage:**
Replace the in-memory `_notes = {}` dict with SQLite or Redis. The logging layer doesn't change.

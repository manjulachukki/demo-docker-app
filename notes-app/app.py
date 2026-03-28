"""
notes-app/app.py — Real-Time Notes Application

A Flask web application built for live classroom demos of ELK Stack log
monitoring. Students submit notes through a browser form, and every action
(submission, validation failure, rate-limit hit, error) appears as a
structured log event in Kibana within seconds.

────────────────────────────────────────────────────────────────────────
ENDPOINTS
────────────────────────────────────────────────────────────────────────
  GET  /                  Home page — HTML form + note list
  POST /notes             Submit a new note (validates + rate-limits)
  DELETE /notes/<id>      Delete a note by its ID
  GET  /api/notes         JSON API — list all notes (for curl demos)
  GET  /health            Health check — {"status": "ok"}
  GET  /stats             Live stats — note count, request count, uptime
  POST /demo/error        Simulate an ERROR log  (classroom button)
  POST /demo/warning      Simulate a WARNING log (classroom button)
  POST /demo/bulk         Generate 10 mixed log entries at once

────────────────────────────────────────────────────────────────────────
LOG LEVELS DEMONSTRATED
────────────────────────────────────────────────────────────────────────
  INFO    — Successful note submission, page views, health checks
  WARNING — Empty/too-long input, rate limit exceeded, missing fields
  ERROR   — Simulated via /demo/error, unhandled exceptions

────────────────────────────────────────────────────────────────────────
SECURITY FEATURES
────────────────────────────────────────────────────────────────────────
  - All user input is HTML-escaped before display  (prevents XSS)
  - Input length limits enforced in Python AND the HTML form
  - Per-IP rate limiting (configurable via RATE_LIMIT / RATE_WINDOW)
  - IPs are SHA-256 hashed in logs  (privacy — no raw IPs stored)
  - Request IDs link all log events for a single request
  - Secrets handled only via environment variables, never in code
"""

import hashlib
import html
import logging
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import flask
from flask import Flask, g, jsonify, redirect, render_template_string, request, url_for

# ── Third-party structured logging ────────────────────────────────────────────
# python-json-logger replaces the default plain-text log formatter with one
# that emits every log line as a single JSON object.  Logstash can then parse
# these events without any grok patterns — clean, reliable, and fast.
from pythonjsonlogger import jsonlogger

# ══════════════════════════════════════════════════════════════════════════════
# 1. APPLICATION SETUP
# ══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)

# ── Structured JSON Logging ───────────────────────────────────────────────────
# Root logger → JSON handler → stdout → Docker captures stdout →
# Filebeat reads the Docker log file → Logstash parses JSON → Elasticsearch.
#
# Every field in `extra={...}` on any log call becomes a top-level JSON key,
# which Elasticsearch indexes and Kibana can filter/aggregate on.

_logger = logging.getLogger()
_handler = logging.StreamHandler(sys.stdout)
_formatter = jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
_handler.setFormatter(_formatter)
_logger.addHandler(_handler)
_logger.setLevel(logging.INFO)

# Give Flask's own logger the same JSON handler so framework messages
# (startup, errors) also appear as JSON in Kibana.
app.logger.handlers = _logger.handlers
app.logger.setLevel(logging.INFO)

# ── Rate-limit configuration ──────────────────────────────────────────────────
# These can be overridden via environment variables without rebuilding the image.
# Example:  docker run -e RATE_LIMIT=5 -e RATE_WINDOW=30 ...
RATE_LIMIT = int(flask.os.environ.get("RATE_LIMIT", 10))   # max requests
RATE_WINDOW = int(flask.os.environ.get("RATE_WINDOW", 60)) # per N seconds

# ── In-memory stores ──────────────────────────────────────────────────────────
# Notes are stored as a list of dicts.  No database — intentional simplicity.
# Data resets on container restart, which is fine for a classroom demo.
notes: list[dict] = []

# Maps a hashed IP → list of request timestamps (Unix epoch floats).
# Used by the rate limiter to count requests per IP per time window.
_rate_store: dict[str, list[float]] = defaultdict(list)

# Tracks the total number of processed requests across all endpoints.
# Used by /stats to show a running counter in Kibana.
total_requests: int = 0

# Record when the app started (UTC) so /stats can report uptime.
_app_start: datetime = datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# 2. HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _get_request_id() -> str:
    """Return the unique request ID stored in the Flask request context (g)."""
    return getattr(g, "request_id", "unknown")


def _get_client_ip() -> str:
    """
    Return the real client IP.
    Checks X-Forwarded-For first (populated by reverse proxies / load balancers),
    then falls back to the direct TCP connection address.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        # X-Forwarded-For: client, proxy1, proxy2  — take the leftmost
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _hash_ip(ip: str) -> str:
    """
    Return a one-way SHA-256 hash of an IP address.

    Logging raw IP addresses may violate GDPR and other privacy regulations.
    A hash lets you correlate log events from the same source (same hash =
    same IP) without ever storing the IP itself.
    """
    return hashlib.sha256(ip.encode()).hexdigest()[:16]  # first 16 hex chars


def _sanitize(text: str) -> str:
    """
    Strip leading/trailing whitespace and HTML-escape the input.

    html.escape() replaces:  < > & " '
    with their safe HTML entity equivalents, preventing XSS attacks when
    user input is rendered inside an HTML page.
    """
    return html.escape(str(text).strip())


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """
    Check whether an IP has exceeded the rate limit.

    Returns:
        (limited: bool, retry_after: int)
        If limited=True, retry_after is the number of seconds to wait.

    Algorithm:
        1. Remove timestamps older than RATE_WINDOW seconds.
        2. If the remaining count >= RATE_LIMIT, the IP is throttled.
        3. Otherwise, record this request timestamp and allow it.
    """
    now = time.time()
    cutoff = now - RATE_WINDOW
    hashed = _hash_ip(ip)

    # Discard expired timestamps
    _rate_store[hashed] = [t for t in _rate_store[hashed] if t > cutoff]

    if len(_rate_store[hashed]) >= RATE_LIMIT:
        oldest = min(_rate_store[hashed])
        retry_after = int(RATE_WINDOW - (now - oldest)) + 1
        return True, retry_after

    _rate_store[hashed].append(now)
    return False, 0


# ══════════════════════════════════════════════════════════════════════════════
# 3. REQUEST LIFECYCLE HOOKS
# ══════════════════════════════════════════════════════════════════════════════

@app.before_request
def _before():
    """
    Runs before EVERY request regardless of which route handles it.

    Responsibilities:
      1. Generate a unique request_id (short UUID) and store it in Flask's
         per-request context object `g`.  Every log call in this request
         includes this ID, so you can filter all events for one request
         in Kibana by searching: request_id: "abc12345"

      2. Record the start time so _after() can compute request duration.

      3. Emit an "incoming request" INFO log — visible in Kibana immediately.
    """
    global total_requests
    total_requests += 1

    g.request_id = str(uuid.uuid4())[:8]  # short 8-character ID
    g.start_time = time.time()

    app.logger.info(
        "Incoming request",
        extra={
            "request_id": g.request_id,
            "method":     request.method,
            "path":       request.path,
            "ip_hash":    _hash_ip(_get_client_ip()),
        },
    )


@app.after_request
def _after(response):
    """
    Runs after EVERY request, just before the response is sent.

    Logs the HTTP status code and how long the request took in milliseconds.
    In Kibana you can visualise average/max response times by aggregating
    on the duration_ms field.
    """
    duration_ms = round((time.time() - g.start_time) * 1000, 2)

    app.logger.info(
        "Request completed",
        extra={
            "request_id":  g.request_id,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


# ══════════════════════════════════════════════════════════════════════════════
# 4. ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def home():
    """
    Home page — renders the HTML form and the current list of notes.

    The form fields:
      author  — who is submitting the note (required, max 50 chars)
      content — the note body              (required, max 300 chars)
    """
    app.logger.info(
        "Home page viewed",
        extra={"request_id": _get_request_id(), "endpoint": "/"},
    )
    return render_template_string(
        _HTML_TEMPLATE,
        notes=notes,
        message=request.args.get("msg"),
        msg_type=request.args.get("type", "info"),
    )


@app.route("/notes", methods=["POST"])
def submit_note():
    """
    Accept a note submitted via the HTML form.

    Validation rules (failures log at WARNING level):
      - author  : required, 1–50 characters after stripping whitespace
      - content : required, 1–300 characters after stripping whitespace

    Rate limiting (failure logs at WARNING level):
      - Max RATE_LIMIT requests per RATE_WINDOW seconds per IP

    Successful submission logs at INFO level with note_id, author length,
    and content length — NOT the raw text (to keep logs lean and avoid
    logging personally identifiable content unnecessarily).
    """
    ip = _get_client_ip()

    # ── Rate limit check ───────────────────────────────────────────────────
    limited, retry_after = _check_rate_limit(ip)
    if limited:
        app.logger.warning(
            "Rate limit exceeded",
            extra={
                "request_id":  _get_request_id(),
                "ip_hash":     _hash_ip(ip),
                "retry_after": retry_after,
                "endpoint":    "/notes",
            },
        )
        return redirect(url_for("home", msg=f"Too many requests. Please wait {retry_after}s.", type="error"))

    # ── Input extraction and sanitisation ─────────────────────────────────
    author  = _sanitize(request.form.get("author", ""))
    content = _sanitize(request.form.get("content", ""))

    # ── Validation ────────────────────────────────────────────────────────
    if not author:
        app.logger.warning(
            "Note rejected — missing author",
            extra={"request_id": _get_request_id(), "endpoint": "/notes", "reason": "empty_author"},
        )
        return redirect(url_for("home", msg="Author name is required.", type="error"))

    if len(author) > 50:
        app.logger.warning(
            "Note rejected — author too long",
            extra={"request_id": _get_request_id(), "endpoint": "/notes",
                   "reason": "author_too_long", "author_length": len(author)},
        )
        return redirect(url_for("home", msg="Author name must be 50 characters or fewer.", type="error"))

    if not content:
        app.logger.warning(
            "Note rejected — missing content",
            extra={"request_id": _get_request_id(), "endpoint": "/notes", "reason": "empty_content"},
        )
        return redirect(url_for("home", msg="Note content cannot be empty.", type="error"))

    if len(content) > 300:
        app.logger.warning(
            "Note rejected — content too long",
            extra={"request_id": _get_request_id(), "endpoint": "/notes",
                   "reason": "content_too_long", "content_length": len(content)},
        )
        return redirect(url_for("home", msg="Note must be 300 characters or fewer.", type="error"))

    # ── Store the note ─────────────────────────────────────────────────────
    note = {
        "id":        str(uuid.uuid4())[:8],
        "author":    author,
        "content":   content,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "ip_hash":   _hash_ip(ip),   # hashed — never store raw IPs
    }
    notes.append(note)

    # Log at INFO — include metadata but NOT the raw text content
    app.logger.info(
        "Note submitted successfully",
        extra={
            "request_id":     _get_request_id(),
            "note_id":        note["id"],
            "author_length":  len(author),
            "content_length": len(content),
            "total_notes":    len(notes),
            "endpoint":       "/notes",
        },
    )

    return redirect(url_for("home", msg=f"Note submitted! (ID: {note['id']})", type="success"))


@app.route("/notes/<note_id>", methods=["POST"])
def delete_note(note_id: str):
    """
    Delete a note by its ID.

    The HTML form uses POST (browsers don't support DELETE in forms).
    We look for a hidden field `_method=DELETE` to distinguish this from
    a regular POST — a common pattern called "method override".
    """
    # Only process if the form signals it is a delete action
    if request.form.get("_method") != "DELETE":
        return redirect(url_for("home"))

    original_count = len(notes)
    # Remove the note that matches the ID
    notes[:] = [n for n in notes if n["id"] != note_id]

    if len(notes) < original_count:
        app.logger.info(
            "Note deleted",
            extra={
                "request_id":  _get_request_id(),
                "note_id":     note_id,
                "total_notes": len(notes),
                "endpoint":    f"/notes/{note_id}",
            },
        )
        return redirect(url_for("home", msg=f"Note {note_id} deleted.", type="success"))

    app.logger.warning(
        "Delete failed — note not found",
        extra={
            "request_id": _get_request_id(),
            "note_id":    note_id,
            "endpoint":   f"/notes/{note_id}",
        },
    )
    return redirect(url_for("home", msg=f"Note {note_id} not found.", type="error"))


@app.route("/api/notes", methods=["GET"])
def api_notes():
    """
    JSON API endpoint — returns all notes as a JSON array.

    Useful for curl demos in the classroom:
      curl http://localhost:5001/api/notes

    Note: author and content ARE returned here (it's an API response),
    but ip_hash is excluded from the API response as it is internal data.
    """
    app.logger.info(
        "API notes list requested",
        extra={
            "request_id":  _get_request_id(),
            "note_count":  len(notes),
            "endpoint":    "/api/notes",
        },
    )
    # Return notes without the ip_hash field (internal data)
    public_notes = [{k: v for k, v in n.items() if k != "ip_hash"} for n in notes]
    return jsonify({"notes": public_notes, "total": len(public_notes)})


@app.route("/health", methods=["GET"])
def health():
    """
    Health check endpoint.

    Used by:
      - Docker Compose health checks
      - Load balancers to decide whether to send traffic here
      - Monitoring systems (Kibana Uptime, Prometheus, etc.)

    Always returns HTTP 200 with {"status": "ok"} when the app is running.
    """
    app.logger.info(
        "Health check",
        extra={"request_id": _get_request_id(), "endpoint": "/health", "status": "ok"},
    )
    return jsonify({"status": "ok"})


@app.route("/stats", methods=["GET"])
def stats():
    """
    Live application statistics.

    Returns note count, total requests processed since startup, and uptime.
    Great to show in Kibana alongside log volume — students see both the
    app metrics and the log events that drove them.
    """
    uptime_seconds = int((datetime.now(timezone.utc) - _app_start).total_seconds())

    payload = {
        "total_notes":    len(notes),
        "total_requests": total_requests,
        "uptime_seconds": uptime_seconds,
        "started_at":     _app_start.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

    app.logger.info(
        "Stats requested",
        extra={"request_id": _get_request_id(), "endpoint": "/stats", **payload},
    )
    return jsonify(payload)


# ── Demo / classroom helper endpoints ─────────────────────────────────────────

@app.route("/demo/error", methods=["POST"])
def demo_error():
    """
    Simulate an ERROR log entry.

    Use this during a classroom demo to show students what ERROR-level
    events look like in Kibana without actually breaking anything.

    The log includes a simulated stack trace excerpt and error type so
    students learn the fields to look for when debugging real errors.
    """
    app.logger.error(
        "Simulated error — database connection timeout",
        extra={
            "request_id": _get_request_id(),
            "endpoint":   "/demo/error",
            "error_type": "ConnectionTimeoutError",
            "component":  "database",
            "simulated":  True,   # Tag so you can filter simulated events out in production
        },
    )
    return redirect(url_for("home", msg="ERROR log generated — check Kibana!", type="error"))


@app.route("/demo/warning", methods=["POST"])
def demo_warning():
    """
    Simulate a WARNING log entry.

    Shows students how WARNING events look in Kibana — useful for
    teaching alert thresholds and anomaly detection.
    """
    app.logger.warning(
        "Simulated warning — high memory usage detected",
        extra={
            "request_id":    _get_request_id(),
            "endpoint":      "/demo/warning",
            "warning_type":  "HighMemoryUsage",
            "memory_percent": 87,
            "threshold":      80,
            "simulated":      True,
        },
    )
    return redirect(url_for("home", msg="WARNING log generated — check Kibana!", type="warn"))


@app.route("/demo/bulk", methods=["POST"])
def demo_bulk():
    """
    Generate 10 log entries with varied levels in one click.

    Useful at the start of a demo session to quickly populate Kibana
    with enough events to make charts and aggregations meaningful.
    Mix of INFO, WARNING, and ERROR logs across different endpoints.
    """
    bulk_events = [
        (logging.INFO,    "User login successful",         {"component": "auth",    "user_count": 1}),
        (logging.INFO,    "Cache miss — fetching from DB", {"component": "cache",   "latency_ms": 45}),
        (logging.WARNING, "Slow query detected",           {"component": "db",      "duration_ms": 2300, "threshold_ms": 1000}),
        (logging.INFO,    "File upload completed",         {"component": "storage", "file_size_kb": 512}),
        (logging.ERROR,   "Payment gateway timeout",       {"component": "payment", "error_type": "GatewayTimeout", "simulated": True}),
        (logging.INFO,    "Email notification sent",       {"component": "email",   "recipient_count": 3}),
        (logging.WARNING, "Session token expiring soon",   {"component": "auth",    "expires_in_minutes": 5}),
        (logging.INFO,    "Background job completed",      {"component": "worker",  "job_id": "job_" + str(uuid.uuid4())[:6]}),
        (logging.ERROR,   "Config reload failed",          {"component": "config",  "error_type": "FileNotFoundError", "simulated": True}),
        (logging.INFO,    "Health check passed",           {"component": "monitor", "checks": 5, "passed": 5}),
    ]

    for level, message, extra in bulk_events:
        extra["request_id"] = _get_request_id()
        extra["endpoint"] = "/demo/bulk"
        _logger.log(level, message, extra=extra)

    app.logger.info(
        "Bulk demo logs generated",
        extra={
            "request_id": _get_request_id(),
            "log_count":  len(bulk_events),
            "endpoint":   "/demo/bulk",
        },
    )
    return redirect(url_for("home", msg=f"{len(bulk_events)} demo log entries generated — check Kibana!", type="success"))


# ══════════════════════════════════════════════════════════════════════════════
# 5. HTML TEMPLATE
# ══════════════════════════════════════════════════════════════════════════════

_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Notes App — ELK Live Demo</title>
  <style>
    /* ── Base ─────────────────────────────────────────────────────────── */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', Arial, sans-serif;
      background: #0f1117;
      color: #e0e0e0;
      padding: 24px;
    }
    a { color: #4da6ff; }

    /* ── Layout ───────────────────────────────────────────────────────── */
    .container { max-width: 860px; margin: 0 auto; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
    @media (max-width: 640px) { .grid { grid-template-columns: 1fr; } }

    /* ── Card ─────────────────────────────────────────────────────────── */
    .card {
      background: #1a1d27;
      border: 1px solid #2a2d3e;
      border-radius: 10px;
      padding: 20px;
    }
    .card h2 { font-size: 1rem; font-weight: 600; color: #a0a8c0; margin-bottom: 14px;
               text-transform: uppercase; letter-spacing: .05em; }

    /* ── Header ───────────────────────────────────────────────────────── */
    header { margin-bottom: 24px; }
    header h1 { font-size: 1.5rem; color: #fff; }
    header p  { color: #808898; font-size: .9rem; margin-top: 4px; }
    .badge {
      display: inline-block; background: #1e4d2b; color: #4ade80;
      padding: 2px 10px; border-radius: 20px; font-size: .78rem; margin-left: 8px;
    }

    /* ── Form ─────────────────────────────────────────────────────────── */
    label { display: block; font-size: .85rem; color: #a0a8c0; margin-bottom: 4px; margin-top: 10px; }
    input[type=text], textarea {
      width: 100%; padding: 9px 12px;
      background: #0f1117; border: 1px solid #2a2d3e; border-radius: 6px;
      color: #e0e0e0; font-size: .9rem; outline: none;
    }
    input[type=text]:focus, textarea:focus { border-color: #4da6ff; }
    textarea { resize: vertical; min-height: 80px; }
    .char-hint { font-size: .75rem; color: #505870; margin-top: 2px; }

    /* ── Buttons ──────────────────────────────────────────────────────── */
    .btn {
      display: inline-block; padding: 9px 18px; border: none; border-radius: 6px;
      font-size: .88rem; cursor: pointer; width: 100%; margin-top: 14px; font-weight: 600;
    }
    .btn-primary { background: #2563eb; color: #fff; }
    .btn-primary:hover { background: #1d4ed8; }
    .btn-error   { background: #7f1d1d; color: #fca5a5; }
    .btn-error:hover { background: #991b1b; }
    .btn-warn    { background: #713f12; color: #fde68a; }
    .btn-warn:hover  { background: #854d0e; }
    .btn-info    { background: #1e3a5f; color: #93c5fd; }
    .btn-info:hover  { background: #1e40af; }
    .btn-delete  {
      background: none; border: 1px solid #4a1942; color: #f87171;
      padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: .78rem;
      width: auto; margin-top: 0;
    }
    .btn-delete:hover { background: #4a1942; }

    /* ── Alerts ───────────────────────────────────────────────────────── */
    .alert {
      padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; font-size: .88rem;
    }
    .alert-success { background: #14532d; color: #86efac; border: 1px solid #166534; }
    .alert-error   { background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; }
    .alert-warn    { background: #422006; color: #fde68a; border: 1px solid #713f12; }
    .alert-info    { background: #0c1a2e; color: #93c5fd; border: 1px solid #1e3a5f; }

    /* ── Note cards ───────────────────────────────────────────────────── */
    .note-list { margin-top: 4px; }
    .note-item {
      background: #0f1117; border: 1px solid #2a2d3e; border-radius: 8px;
      padding: 12px 14px; margin-bottom: 10px;
    }
    .note-header { display: flex; justify-content: space-between; align-items: flex-start; }
    .note-author { font-weight: 600; color: #7dd3fc; font-size: .9rem; }
    .note-time   { font-size: .75rem; color: #505870; }
    .note-body   { margin-top: 6px; font-size: .88rem; color: #c0c8d8; line-height: 1.5; }
    .note-id     { font-size: .72rem; color: #404560; margin-top: 6px; }
    .empty-state { color: #404560; font-style: italic; font-size: .88rem; padding: 10px 0; }

    /* ── API quick ref ────────────────────────────────────────────────── */
    .api-ref code {
      display: block; background: #0f1117; border: 1px solid #2a2d3e;
      border-radius: 4px; padding: 6px 10px; font-size: .78rem; color: #a0c8a0;
      margin: 4px 0;
    }
  </style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <header>
    <h1>Notes App <span class="badge">ELK Live Demo</span></h1>
    <p>Every action on this page generates a structured log event visible in Kibana within seconds.</p>
  </header>

  <!-- Flash message -->
  {% if message %}
  <div class="alert alert-{{ msg_type }}">{{ message }}</div>
  {% endif %}

  <div class="grid">

    <!-- Left column: submit form -->
    <div>
      <div class="card">
        <h2>Submit a Note</h2>
        <form action="/notes" method="POST">
          <label for="author">Your Name <span style="color:#f87171">*</span></label>
          <input type="text" id="author" name="author" maxlength="50"
                 placeholder="e.g. Alice" required autocomplete="off">
          <p class="char-hint">Max 50 characters</p>

          <label for="content">Note <span style="color:#f87171">*</span></label>
          <textarea id="content" name="content" maxlength="300"
                    placeholder="Type your note here..." required></textarea>
          <p class="char-hint">Max 300 characters</p>

          <button type="submit" class="btn btn-primary">Submit Note →</button>
        </form>
      </div>

      <!-- Demo log generators -->
      <div class="card" style="margin-top:16px">
        <h2>Demo Tools — Generate Logs</h2>
        <p style="font-size:.82rem;color:#505870;margin-bottom:10px">
          Click a button to generate a specific log level in Kibana.
          Use these during a live demo to show different event types.
        </p>

        <form action="/demo/bulk" method="POST">
          <button type="submit" class="btn btn-info">Generate 10 Mixed Logs</button>
        </form>
        <form action="/demo/warning" method="POST" style="margin-top:8px">
          <button type="submit" class="btn btn-warn">Simulate WARNING Log</button>
        </form>
        <form action="/demo/error" method="POST" style="margin-top:8px">
          <button type="submit" class="btn btn-error">Simulate ERROR Log</button>
        </form>
      </div>

      <!-- Quick API reference -->
      <div class="card api-ref" style="margin-top:16px">
        <h2>API Endpoints (curl)</h2>
        <code>GET  http://localhost:5001/health</code>
        <code>GET  http://localhost:5001/stats</code>
        <code>GET  http://localhost:5001/api/notes</code>
      </div>
    </div>

    <!-- Right column: note list -->
    <div>
      <div class="card">
        <h2>Notes ({{ notes|length }})</h2>
        <div class="note-list">
          {% if notes %}
            {% for note in notes | reverse %}
            <div class="note-item">
              <div class="note-header">
                <span class="note-author">{{ note.author }}</span>
                <form action="/notes/{{ note.id }}" method="POST"
                      onsubmit="return confirm('Delete this note?')">
                  <input type="hidden" name="_method" value="DELETE">
                  <button type="submit" class="btn-delete">Delete</button>
                </form>
              </div>
              <p class="note-body">{{ note.content }}</p>
              <p class="note-time">{{ note.timestamp }}</p>
              <p class="note-id">ID: {{ note.id }}</p>
            </div>
            {% endfor %}
          {% else %}
            <p class="empty-state">No notes yet — submit one to see logs in Kibana.</p>
          {% endif %}
        </div>
      </div>
    </div>

  </div><!-- /grid -->
</div><!-- /container -->
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════════
# 6. ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # debug=False in all environments — debug mode can leak internal state
    # and disables some Flask security protections.
    app.run(host="0.0.0.0", port=5001, debug=False)

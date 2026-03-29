# demo-docker-app

A collection of Flask applications containerised with Docker, each integrated with an **external ELK stack** (Elasticsearch + Kibana) for centralised structured logging. Logs are collected from Docker containers by **Filebeat**, processed by **Logstash**, and stored in **Elasticsearch** where they are searchable and visualisable in **Kibana**.

Elasticsearch and Kibana run as a **separate external ELK stack** — not inside this repo. Each app's Logstash bridges between its own internal network and the shared `elastic` Docker network.

> For ELK stack setup, see the [elk-setup repository](https://github.com/cloud-prakhar/elk-setup).

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Step 1 — Clone the Repository](#step-1--clone-the-repository)
4. [Step 2 — Start the External ELK Stack](#step-2--start-the-external-elk-stack)
5. [Step 3 — Copy the CA Certificate](#step-3--copy-the-ca-certificate)
6. [Step 4 — Create the .env File](#step-4--create-the-env-file)
7. [Step 5 — Start the Application Stack](#step-5--start-the-application-stack)
8. [Step 6 — Generate Log Traffic](#step-6--generate-log-traffic)
9. [Step 7 — Verify Logs in Elasticsearch](#step-7--verify-logs-in-elasticsearch)
10. [Step 8 — View Logs in Kibana](#step-8--view-logs-in-kibana)
11. [Pipeline Deep Dive](#pipeline-deep-dive)
12. [Troubleshooting](#troubleshooting)
13. [Teardown & Cleanup](#teardown--cleanup)
14. [Applications Reference](#applications-reference)

---

## Architecture Overview

```
┌──────────────────────┐    ┌──────────────────────────────┐
│  demo-app            │    │  notes-app                   │
│  Flask :5000         │    │  Flask :5001                 │
│  Filebeat            │    │  Filebeat                    │
│  Logstash :5044      │    │  Logstash :5045              │
└──────────┬───────────┘    └────────────┬─────────────────┘
           │  elastic network            │  elastic network
           └─────────────┬───────────────┘
                         ▼
         ┌───────────────────────────────────┐
         │  External ELK Stack               │
         │  Elasticsearch (es01)  :9200      │
         │  Kibana        (kib01) :5601      │
         └───────────────────────────────────┘
```

**The complete log journey for demo-app:**

```
Flask App (stdout JSON)
      │
      ▼  Docker json-file log driver writes to /var/lib/docker/containers/
Filebeat (Docker autodiscovery)
      │  port 5044 (Beats protocol)
      ▼
Logstash (parse + enrich + rename fields)
      │  HTTPS :9200
      ▼
Elasticsearch (es01) — index: flask-app-YYYY.MM.dd
      │
      ▼
Kibana (kib01) — visualise and search
```

Both apps share the same `elastic` Docker network and Elasticsearch/Kibana instance. Logs are separated by index name (`flask-app-*` vs `notes-app-*`) and by `app_name` field.

---

## Prerequisites

Before starting, ensure:

1. **Docker Desktop** is installed and running (Linux containers mode on Windows)
2. **External ELK stack** is deployed — `es01` (Elasticsearch) and `kib01` (Kibana) containers must exist
3. The **`elastic` Docker network** must exist (created by the ELK stack setup)

Verify the ELK stack is healthy:
```bash
docker ps | grep -E "es01|kib01"
# Both must show "Up"

docker network ls | grep elastic
# Must list the elastic network
```

If the ELK stack is not set up yet, follow the [elk-setup repository](https://github.com/cloud-prakhar/elk-setup) first.

---

## Step 1 — Clone the Repository

```bash
git clone https://github.com/manjulachukki/demo-docker-app.git
cd demo-docker-app
```

---

## Step 2 — Start the External ELK Stack

The ELK stack must be running before the apps are started. The `es01` and `kib01` containers are managed separately from this repo.

```bash
# Start Elasticsearch first, then wait for it to be ready
docker start es01
```

Wait about 20–30 seconds, then start Kibana:
```bash
docker start kib01
```

Verify both are up:
```bash
docker ps | grep -E "es01|kib01"
```

Test Elasticsearch is reachable (use your actual password):
```bash
curl -k -u "elastic:<your-password>" "https://localhost:9200"
# Should return cluster info JSON with "status": "green" or "yellow"
```

---

## Step 3 — Copy the CA Certificate

Logstash connects to Elasticsearch over HTTPS. It needs the CA certificate from the `es01` container to trust that connection.

```bash
# Navigate to the demo-app directory
cd demo-docker-app/demo-app

# Create the certs directory
mkdir -p elk/certs

# Copy the CA cert out of the running es01 container
docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt \
  elk/certs/http_ca.crt
```

Verify the cert was copied:
```bash
ls -la elk/certs/http_ca.crt
```

> **Why this cert?** It is the Certificate Authority that signed Elasticsearch's own HTTPS certificate. Without it, Logstash refuses the HTTPS connection. This cert is specific to your `es01` instance and is excluded from git.

---

## Step 4 — Create the .env File

Logstash needs your Elasticsearch password at startup. Store it in a `.env` file (never commit this to git).

```bash
# From inside demo-app/
cat > .env << 'EOF'
ELASTIC_PASSWORD=<your-elastic-password>
EOF
```

Replace `<your-elastic-password>` with the actual password for the `elastic` superuser.

To retrieve or reset the password:
```bash
echo "y" | docker exec -i es01 elasticsearch-reset-password -u elastic -s
```

> **Password requirements:** Use only alphanumeric characters (`A–Z`, `a–z`, `0–9`). Special characters like `+`, `*`, `=` can break Logstash's variable expansion and cause silent authentication failures.

Docker Compose reads `.env` automatically and injects `ELASTIC_PASSWORD` into the Logstash container environment, where `logstash.conf` reads it as `${ELASTIC_PASSWORD}`.

---

## Step 5 — Start the Application Stack

**First-time start or after a code change:**
```bash
# From inside demo-app/
docker compose up -d --build
```

**After changing configuration files (e.g. `.env`, `logstash.conf`, `filebeat.yml`) without rebuilding the image:**
```bash
docker compose up -d --force-recreate
```

`--force-recreate` stops and recreates all containers even if their configuration has not changed, ensuring they pick up any updated environment variables, mounted files, or network settings. Use this whenever you:
- Update the `.env` file (e.g. changed `ELASTIC_PASSWORD`)
- Edit `logstash.conf` or `filebeat.yml` without modifying `Dockerfile`
- Need to reset a container to a clean state without rebuilding the image

This starts three containers:

| Container | Image | Role |
|---|---|---|
| `demo-app-app-1` | Built from `Dockerfile` | Flask web app on port 5000 |
| `demo-app-logstash-1` | `logstash:9.3.2` | Receives logs from Filebeat, forwards to Elasticsearch |
| `demo-app-filebeat-1` | `filebeat:9.3.2` | Watches Docker container logs, ships to Logstash |

Wait for all containers to be healthy:
```bash
docker ps | grep demo-app
# All three should show "Up"
```

Watch Logstash start up (takes ~30–60 seconds):
```bash
docker logs -f demo-app-logstash-1
# Wait until you see: "Pipelines running {:count=>1 ...}"
```

---

## Step 6 — Generate Log Traffic

Hit the Flask app endpoints to produce logs:

```bash
# Home page
curl http://localhost:5000/

# Health check
curl http://localhost:5000/health
```

Or open in a browser:
- App: http://localhost:5000
- Health check: http://localhost:5000/health

Each request produces two JSON log lines:
1. One from `@app.before_request` — logs method, path, and client IP
2. One from the route handler — logs the endpoint-specific message

Example JSON emitted by the Flask app on a `/health` request:
```json
{"asctime": "2026-03-29T10:00:00", "name": "flask.app", "levelname": "INFO", "message": "Incoming request", "method": "GET", "path": "/health", "remote_addr": "172.18.0.1"}
{"asctime": "2026-03-29T10:00:00", "name": "flask.app", "levelname": "INFO", "message": "Health check", "endpoint": "/health", "status": "ok"}
```

---

## Step 7 — Verify Logs in Elasticsearch

Check that the index was created and contains documents:

```bash
# List flask-app indices
curl -k -u "elastic:<your-password>" \
  "https://localhost:9200/_cat/indices/flask-app-*?v"
```

Expected output:
```
health status index                    uuid   pri rep docs.count ...
green  open   flask-app-2026.03.29     ...      1   1         12 ...
```

Query the latest 3 log documents:
```bash
curl -k -u "elastic:<your-password>" \
  "https://localhost:9200/flask-app-*/_search?pretty&size=3&sort=@timestamp:desc"
```

A processed document stored in Elasticsearch looks like:
```json
{
  "@timestamp": "2026-03-29T10:00:00.000Z",
  "level": "INFO",
  "log_message": "Health check",
  "logger": "flask.app",
  "timestamp": "2026-03-29T10:00:00",
  "endpoint": "/health",
  "service": "flask-demo-app",
  "container": { "name": "demo-app-app-1", "image": { "name": "demo-app-app" } }
}
```

---

## Step 8 — View Logs in Kibana

1. Open Kibana: http://localhost:5601
2. Log in with `elastic` / `<your-password>`
3. Go to **Stack Management → Data Views → Create data view**
4. Set **Index pattern** to `flask-app-*`
5. Set **Timestamp field** to `@timestamp`
6. Click **Save data view to Kibana**

Now go to **Discover**:
- Select the `flask-app-*` data view
- Set time range to **Last 15 minutes**
- You should see log documents with fields like `level`, `log_message`, `endpoint`, `service`, `client_ip`

Useful KQL queries in Discover:
```kql
# All logs from this app
service: "flask-demo-app"

# Only error logs
level: "ERROR"

# Logs for a specific endpoint
endpoint: "/health"
```

---

## Pipeline Deep Dive

### How Each Component Works

**Flask App (`app.py`)**

Uses `python-json-logger` to format every log line as a JSON object written to stdout. Docker's `json-file` log driver captures stdout and writes it to `/var/lib/docker/containers/<id>/<id>-json.log`.

Key configuration:
```python
formatter = jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
```

The container labels opt it into Filebeat collection:
```yaml
labels:
  - "co.elastic.logs/enabled=true"
  - "co.elastic.logs/json.keys_under_root=true"
  - "co.elastic.logs/json.overwrite_keys=true"
```

**Filebeat (`elk/filebeat/filebeat.yml`)**

Uses Docker autodiscovery to watch for containers labelled `co.elastic.logs/enabled=true`. Reads log files from `/var/lib/docker/containers/`, parses JSON, attaches Docker metadata, and ships events to Logstash on port 5044.

**Logstash (`elk/logstash/pipeline/logstash.conf`)**

Three-stage pipeline:

| Stage | What it does |
|---|---|
| **Input** | Listens on port 5044 for Beats protocol events from Filebeat |
| **Filter** | Detects JSON messages, parses them, renames fields to cleaner names, adds `service` tag, drops empty heartbeats |
| **Output** | Sends enriched events to `https://es01:9200` using HTTPS with the mounted CA cert |

Field renaming in Logstash:

| Raw field from Flask | Stored in Elasticsearch as |
|---|---|
| `[log][levelname]` | `level` |
| `[log][message]` | `log_message` |
| `[log][asctime]` | `timestamp` |
| `[log][endpoint]` | `endpoint` |
| `[log][method]` | `http_method` |
| `[log][remote_addr]` | `client_ip` |
| `[log][status]` | `response_status` |

**Networks**

Two Docker networks are used:

```
[app] ── elk ──▶ [filebeat] ── elk ──▶ [logstash]
                                             │
                                          elastic
                                             │
                                          [es01]
```

- `elk` — internal bridge network created by this compose file; used for Filebeat → Logstash communication
- `elastic` — external network (already exists from ELK setup); Logstash joins it to reach `es01` by hostname

---

## Troubleshooting

### Logstash fails to start — "Pipeline error"

```bash
docker logs demo-app-logstash-1 | grep -i error
```

**Common cause:** Wrong `ELASTIC_PASSWORD` in `.env` or the `.env` file is missing.

Fix: Verify `.env` exists and the password is correct:
```bash
cat demo-app/.env
curl -k -u "elastic:<password>" "https://localhost:9200"
```

### Logstash fails to connect to es01 — SSL/TLS error

```bash
docker logs demo-app-logstash-1 | grep -i "ssl\|cert\|tls"
```

**Common cause:** `elk/certs/http_ca.crt` is missing or was copied from the wrong container.

Fix: Re-copy the cert:
```bash
docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt \
  demo-app/elk/certs/http_ca.crt
docker compose restart logstash
```

### No index appears in Elasticsearch

```bash
# Check Filebeat is running and connecting to Logstash
docker logs demo-app-filebeat-1 | tail -20

# Check Logstash is receiving events
docker logs demo-app-logstash-1 | grep "events"
```

**Common cause:** Logstash is not yet on the `elastic` network, or `es01` is not running.

Fix:
```bash
docker start es01
docker compose restart logstash
```

### `elastic` network not found

```bash
docker compose up -d
# Error: network elastic declared as external, but could not be found
```

**Fix:** The ELK stack must be running before starting this compose file. Start `es01` and `kib01` first.

### Containers start but no documents in Elasticsearch

```bash
# Hit the app to generate traffic
curl http://localhost:5000/health

# Wait 10–15 seconds then check
curl -k -u "elastic:<password>" \
  "https://localhost:9200/_cat/indices/flask-app-*?v"
```

If still empty, trace the pipeline:
```bash
# Is Filebeat seeing the app logs?
docker logs demo-app-filebeat-1 2>&1 | grep -i "harvester\|event"

# Is Logstash receiving from Filebeat?
docker logs demo-app-logstash-1 2>&1 | grep -i "received\|events"
```

---

## Teardown & Cleanup

### Stop and remove containers (keep images)

```bash
cd demo-app/
docker compose down
```

### Stop and remove containers + images

```bash
docker compose down --rmi all
```

### Remove the Elasticsearch index

```bash
curl -k -u "elastic:<your-password>" \
  -X DELETE "https://localhost:9200/flask-app-*"
```

### Remove local credential files

```bash
rm demo-app/.env
rm demo-app/elk/certs/http_ca.crt
```

> **The external ELK stack (`es01`, `kib01`) is not touched by the above commands.** To tear that down separately, refer to the [elk-setup teardown guide](https://github.com/cloud-prakhar/elk-setup/blob/main/elk-stack-complete-teardown.md).

---

## Applications Reference

### App 1 — demo-app (Basic Flask App)

A simple Flask application demonstrating the fundamentals of Docker + ELK log integration.

**Features:** JSON structured logging, Filebeat autodiscovery, Logstash pipeline, daily index rotation

**Port:** `5000`
**Elasticsearch index:** `flask-app-YYYY.MM.dd`

| Document | What it covers |
|---|---|
| [App Configuration](./demo-app/docs/app-configuration.md) | Flask app, Dockerfile, JSON logging, routes |
| [ELK Configuration](./demo-app/docs/elk-configuration.md) | Filebeat, Logstash pipeline, networks, cert setup, troubleshooting |
| [Teardown & Cleanup](./demo-app/docs/teardown.md) | Containers, images, networks, ES index cleanup |

```bash
cd demo-app/
docker compose up -d --build
# App: http://localhost:5000
```

---

### App 2 — notes-app (Advanced Flask App)

An advanced Flask application with user input, real-time log collection, and built-in security features. Designed for live classroom demos with Kibana.

**Features:**
- Create, list, and delete text notes via browser UI
- Per-IP rate limiting (configurable via env vars)
- IP privacy — SHA-256 hashed IPs, never logged raw
- Request ID tracing — correlate all logs for a single request
- XSS prevention via HTML escaping
- Demo endpoints to trigger different log levels on demand

**Port:** `5001`
**Elasticsearch index:** `notes-app-YYYY.MM.dd`

| Document | What it covers |
|---|---|
| [App Configuration](./notes-app/docs/app-configuration.md) | Architecture, security features, endpoints, request tracing, logging design |
| [ELK Configuration](./notes-app/docs/elk-configuration.md) | Filebeat, Logstash pipeline, networks, cert setup, troubleshooting |
| [Kibana Setup](./notes-app/docs/kibana-setup.md) | Data View creation, visualizations, dashboards, sample KQL/DSL queries |
| [Teardown & Cleanup](./notes-app/docs/teardown.md) | Containers, images, networks, ES index cleanup |

```bash
cd notes-app/
docker compose up -d --build
# App: http://localhost:5001
```

---

## One-Time Setup (Per App)

Each app needs its own copy of the CA certificate and its own `.env` file:

```bash
# For demo-app
cd demo-app/
mkdir -p elk/certs
docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt elk/certs/http_ca.crt
cp .env.example .env    # then edit .env with your password

# For notes-app
cd notes-app/
mkdir -p elk/certs
docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt elk/certs/http_ca.crt
cp .env.example .env    # then edit .env with your password
```

**Password rule:** Use alphanumeric characters only — no `*`, `+`, `=`, `!`. Special characters break Logstash's environment variable expansion.

---

## Project Structure

```
demo-docker-app/
├── README.md
├── .gitignore
├── demo-app/                       ← App 1: Basic Flask app
│   ├── app.py
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── elk/
│   │   ├── certs/                  ← NOT in git
│   │   ├── filebeat/filebeat.yml
│   │   └── logstash/pipeline/logstash.conf
│   └── docs/
│       ├── app-configuration.md
│       ├── elk-configuration.md
│       └── teardown.md
└── notes-app/                      ← App 2: Advanced Flask app
    ├── app.py
    ├── Dockerfile
    ├── requirements.txt
    ├── docker-compose.yml
    ├── .env.example
    ├── elk/
    │   ├── certs/                  ← NOT in git
    │   ├── filebeat/filebeat.yml
    │   └── logstash/pipeline/logstash.conf
    └── docs/
        ├── app-configuration.md
        ├── elk-configuration.md
        ├── kibana-setup.md
        └── teardown.md
```

---

## Security Notes

The following files are **excluded from git** and must be created locally by each developer:

| File | How to create |
|---|---|
| `demo-app/.env` | `cp demo-app/.env.example demo-app/.env` then fill in password |
| `notes-app/.env` | `cp notes-app/.env.example notes-app/.env` then fill in password |
| `demo-app/elk/certs/http_ca.crt` | `docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt demo-app/elk/certs/http_ca.crt` |
| `notes-app/elk/certs/http_ca.crt` | `docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt notes-app/elk/certs/http_ca.crt` |

**Verify nothing sensitive is tracked:**
```bash
git ls-files | grep -E "\.env|certs"
# Should return nothing
```

---

## Port Summary

| Service | Port | Notes |
|---|---|---|
| demo-app Flask | 5000 | `http://localhost:5000` |
| demo-app Logstash | 5044 | Beats input |
| notes-app Flask | 5001 | `http://localhost:5001` |
| notes-app Logstash | 5045 | Beats input |
| Elasticsearch | 9200 | External (`es01`) |
| Kibana | 5601 | External (`kib01`) — `http://localhost:5601` |

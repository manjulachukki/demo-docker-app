# demo-docker-app

A collection of Flask applications containerised with Docker, each integrated with an **external ELK stack** (Elasticsearch + Kibana) for centralised structured logging. Logs are collected from Docker containers by **Filebeat**, processed by **Logstash**, and stored in **Elasticsearch** where they are searchable and visualisable in **Kibana**.

Elasticsearch and Kibana run as a **separate external ELK stack** — not inside this repo. Each app's Logstash bridges between its own internal network and the shared `elastic` Docker network.

> For ELK stack setup, see the [elk-setup repository](https://github.com/cloud-prakhar/elk-setup).

---

## Applications

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

## Shared Architecture

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

Both apps share the same `elastic` Docker network and the same Elasticsearch/Kibana instance. Logs are separated by index name (`flask-app-*` vs `notes-app-*`) and by `app_name` field.

---

## Prerequisites

1. Docker and Docker Compose installed
2. External ELK stack running (`es01` and `kib01` containers)
3. The `elastic` Docker network exists:
   ```bash
   docker network ls | grep elastic
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

# demo-docker-app

A lightweight Flask application containerised with Docker, integrated with an **external ELK stack** (Elasticsearch + Kibana) for centralised structured logging. Logs are collected from Docker containers by **Filebeat**, processed by **Logstash**, and stored in **Elasticsearch** where they are searchable and visualisable in **Kibana**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  This repository                                            │
│                                                             │
│  ┌──────────┐  JSON stdout   ┌──────────┐  port 5044       │
│  │Flask App │ ─────────────▶ │ Filebeat │ ──────────────┐  │
│  │  :5000   │                │          │               │  │
│  └──────────┘                └──────────┘               │  │
│                                                          │  │
│                              ┌──────────┐  ◀────────────┘  │
│                              │ Logstash │                   │
│                              │  :5044   │                   │
│                              └────┬─────┘                   │
└───────────────────────────────────┼─────────────────────────┘
                                    │ HTTPS :9200
                   ─ ─ ─ ─ ─ ─ ─ ─ ┼ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                   External ELK     │
                   ┌────────────────▼───────────────────────┐
                   │  ┌───────────────┐  ┌───────────────┐  │
                   │  │ Elasticsearch │  │    Kibana     │  │
                   │  │   es01 :9200  │  │  kib01 :5601  │  │
                   │  └───────────────┘  └───────────────┘  │
                   └────────────────────────────────────────┘
```

Elasticsearch and Kibana run as a **separate external ELK stack** — not inside this repo. Logstash bridges between the two environments by joining both Docker networks.

---

## Documentation

| Document | What it covers |
|---|---|
| [App Configuration](./docs/app-configuration.md) | Flask app, Dockerfile, JSON logging, routes, how to extend the app |
| [ELK Configuration](./docs/elk-configuration.md) | Filebeat, Logstash pipeline, Docker networks, credentials setup, cert setup, troubleshooting |

---

## Quick Start

### Prerequisites

1. Docker and Docker Compose installed
2. External ELK stack running (`es01` and `kib01` containers on the `elastic` Docker network)
3. The `elastic` Docker network exists: `docker network ls | grep elastic`

> For ELK stack setup, see the [elk-setup repository](https://github.com/cloud-prakhar/elk-setup).

### One-time local setup

```bash
cd demo-app/

# 1. Copy the CA certificate from the running es01 container
mkdir -p elk/certs
docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt elk/certs/http_ca.crt

# 2. Create the .env file with your Elasticsearch password
#    (use only alphanumeric characters in the password)
cat > .env << 'EOF'
ELASTIC_PASSWORD=<your-elastic-password>
EOF
```

### Start the app

```bash
docker compose up -d --build
```

### Verify

```bash
# All three containers running
docker compose ps

# Flask app responding
curl http://localhost:5000/health

# Logstash pipeline started
docker compose logs logstash | grep "Pipelines running"

# Logs reaching Elasticsearch (wait ~30 seconds after first request)
curl --cacert ~/ELK/http_ca.crt \
  -u "elastic:<your-elastic-password>" \
  "https://localhost:9200/_cat/indices/flask-app-*?v"
```

---

## Project Structure

```
demo-docker-app/
├── README.md
├── .gitignore
├── docs/
│   ├── app-configuration.md    ← Flask app deep-dive
│   └── elk-configuration.md    ← ELK pipeline deep-dive
└── demo-app/
    ├── app.py                  ← Flask application
    ├── Dockerfile
    ├── requirements.txt
    ├── docker-compose.yml      ← app + logstash + filebeat
    ├── .env                    ← NOT in git — contains password
    └── elk/
        ├── certs/              ← NOT in git — contains TLS cert
        │   └── http_ca.crt
        ├── filebeat/
        │   └── filebeat.yml
        └── logstash/
            └── pipeline/
                └── logstash.conf
```

---

## Security Notes

The following files are excluded from git and must be created locally by each developer:

| File | How to create |
|---|---|
| `demo-app/.env` | See Quick Start above |
| `demo-app/elk/certs/http_ca.crt` | `docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt demo-app/elk/certs/http_ca.crt` |

**Never commit these files.** Verify with: `git ls-files | grep -E "\.env|certs"` — should return nothing.

---

## Ports

| Service | Port | Notes |
|---|---|---|
| Flask App | 5000 | `http://localhost:5000` |
| Logstash | 5044 | Beats input — Filebeat only |
| Elasticsearch | 9200 | External (`es01`) |
| Kibana | 5601 | External (`kib01`) — `http://localhost:5601` |

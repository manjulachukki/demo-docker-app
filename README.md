# demo-docker-app

A lightweight Flask application containerised with Docker and integrated with the **ELK stack** (Elasticsearch + Logstash + Kibana) for centralised, structured logging. Logs are collected from Docker containers by **Filebeat** and shipped through Logstash into Elasticsearch, where they can be explored in Kibana.

---

## Architecture

```
┌─────────────┐     stdout      ┌──────────┐     beats     ┌──────────┐
│  Flask App  │ ─── JSON ────▶  │ Filebeat │ ────5044────▶ │ Logstash │
│  :5000      │                 │          │               │          │
└─────────────┘                 └──────────┘               └────┬─────┘
                                                                 │ HTTP
                                                                 ▼
                                                        ┌───────────────┐
                                                        │ Elasticsearch │
                                                        │    :9200      │
                                                        └───────┬───────┘
                                                                │
                                                                ▼
                                                        ┌───────────────┐
                                                        │    Kibana     │
                                                        │    :5601      │
                                                        └───────────────┘
```

| Service       | Port  | Description                              |
|---------------|-------|------------------------------------------|
| Flask App     | 5000  | Web application                          |
| Elasticsearch | 9200  | Log storage and search engine            |
| Logstash      | 5044  | Log processing pipeline                  |
| Kibana        | 5601  | Log visualisation dashboard              |

---

## Directory Structure

```
demo-docker-app/
├── README.md
└── demo-app/
    ├── app.py                          # Flask app with JSON logging
    ├── Dockerfile
    ├── docker-compose.yml              # All services (app + ELK)
    ├── requirements.txt
    ├── .env                            # ELK_VERSION pin
    └── elk/
        ├── logstash/
        │   └── pipeline/
        │       └── logstash.conf       # Logstash input → filter → output
        └── filebeat/
            └── filebeat.yml            # Filebeat Docker autodiscover config
```

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) >= 24
- [Docker Compose](https://docs.docker.com/compose/) >= 2.20 (plugin, not standalone)
- At least **4 GB of RAM** allocated to Docker (Elasticsearch is memory-hungry)

### Increase virtual memory for Elasticsearch (Linux / WSL2)

Elasticsearch requires a higher `vm.max_map_count`. Run once per boot:

```bash
sudo sysctl -w vm.max_map_count=262144
```

To make it permanent:

```bash
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

---

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd demo-docker-app/demo-app

# 2. (Optional) Change the ELK version in .env
#    All four ELK services must use the same version.
cat .env

# 3. Build and start all services
docker compose up --build -d

# 4. Check that every service is healthy
docker compose ps

# 5. Verify the Flask app is running
curl http://localhost:5000/
curl http://localhost:5000/health

# 6. Open Kibana
#    Navigate to http://localhost:5601 in your browser
```

> First startup takes 2–3 minutes while Elasticsearch initialises.

---

## Viewing Logs in Kibana

### Create a Data View

1. Open **http://localhost:5601**
2. Go to **Stack Management → Data Views → Create data view**
3. Set **Index pattern** to `flask-app-*`
4. Set **Timestamp field** to `@timestamp`
5. Click **Save data view to Kibana**

### Explore Logs

- Go to **Discover** (left sidebar)
- Select the `flask-app-*` data view
- Use KQL to filter, e.g.:
  ```
  level: "INFO" and endpoint: "/health"
  ```

### Useful fields

| Field           | Description                         |
|-----------------|-------------------------------------|
| `level`         | Log level (INFO, WARNING, ERROR)    |
| `log_message`   | Human-readable log message          |
| `endpoint`      | Flask route that was hit            |
| `http_method`   | HTTP method (GET, POST, …)          |
| `client_ip`     | Remote IP address                   |
| `service`       | Always `flask-demo-app`             |
| `@timestamp`    | Event time                          |

---

## Common Commands

### Start / Stop

```bash
# Start all services in the background
docker compose up -d

# Stop all services (keeps volumes)
docker compose down

# Stop and remove volumes (wipes Elasticsearch data)
docker compose down -v
```

### Build & Rebuild

```bash
# Rebuild only the Flask app image (e.g. after code changes)
docker compose up --build app -d

# Force a full rebuild of all images
docker compose build --no-cache
```

### View Logs

```bash
# All services (streamed)
docker compose logs -f

# Flask app only
docker compose logs -f app

# Logstash only
docker compose logs -f logstash

# Filebeat only
docker compose logs -f filebeat
```

### Service Status & Health

```bash
# Show running containers and health status
docker compose ps

# Check Elasticsearch cluster health directly
curl http://localhost:9200/_cluster/health?pretty

# List indices created by Logstash
curl http://localhost:9200/_cat/indices?v
```

### Scale / Restart a Single Service

```bash
docker compose restart logstash
docker compose restart filebeat
```

---

## Changing the ELK Version

Edit `.env`:

```dotenv
ELK_VERSION=8.14.0
```

Then recreate the ELK containers:

```bash
docker compose up -d --force-recreate elasticsearch logstash kibana filebeat
```

> All four services (Elasticsearch, Logstash, Kibana, Filebeat) **must** run the same version.

---

## Troubleshooting

### Elasticsearch exits immediately

Most likely `vm.max_map_count` is too low. Run:

```bash
sudo sysctl -w vm.max_map_count=262144
```

### No logs appearing in Kibana

1. Check Filebeat is running and targeting the right containers:
   ```bash
   docker compose logs filebeat
   ```
2. Check Logstash received events:
   ```bash
   docker compose logs logstash
   ```
3. Confirm the index was created in Elasticsearch:
   ```bash
   curl http://localhost:9200/_cat/indices?v
   ```
4. Make sure the Flask app container has the label `co.elastic.logs/enabled=true` (set in `docker-compose.yml`).

### Port conflicts

If ports 5000, 5044, 9200, or 5601 are already in use, edit the `ports` section of `docker-compose.yml` for the affected service and restart.

---

## Logging in the Flask App

The app uses [`python-json-logger`](https://github.com/madzak/python-json-logger) to emit structured JSON on stdout. Every request is logged via a `@before_request` hook, and each route adds context fields.

Example log line (pretty-printed):

```json
{
  "asctime": "2026-03-25T12:00:00",
  "name": "app",
  "levelname": "INFO",
  "message": "Incoming request",
  "method": "GET",
  "path": "/health",
  "remote_addr": "172.18.0.1"
}
```

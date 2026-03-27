# ELK Integration — Configuration Guide

> This document covers everything about the log shipping pipeline: how Filebeat, Logstash, and the Docker Compose network wiring work, how to set up credentials and certificates securely, and how to troubleshoot the full pipeline.

---

## How the Pipeline Works

Before touching any config file, understand the complete journey a log takes:

```
┌─────────────────────────────────────────────────────────────┐
│  This repo (demo-docker-app)                                │
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
                   ─────────────────┼────────────────────────
                   External ELK     │
                   ┌────────────────▼───────────────────────┐
                   │                                        │
                   │  ┌───────────────┐  ┌───────────────┐ │
                   │  │ Elasticsearch │  │    Kibana     │ │
                   │  │   es01 :9200  │  │  kib01 :5601  │ │
                   │  └───────────────┘  └───────────────┘ │
                   │                                        │
                   └────────────────────────────────────────┘
```

**The key architectural point:** Elasticsearch and Kibana are **not** part of this repo. They run as a separate standalone ELK stack (`es01` and `kib01`). This repo only contains the app and the log shippers (Filebeat + Logstash). Logstash bridges between the two by being on both Docker networks.

---

## Prerequisites

Before starting anything in this repo, the external ELK stack must already be running:

```bash
# Both of these must show "Up"
docker ps | grep -E "es01|kib01"
```

If they are not running, start them first:
```bash
docker start es01
sleep 20
docker start kib01
```

For ELK stack setup instructions, refer to the [elk-setup repository](https://github.com/cloud-prakhar/elk-setup).

---

## Security — What Must Never Be in Git

This project handles credentials and TLS certificates. The following files are **excluded from git** via `.gitignore` and must **never be committed**:

| File | Why it must stay local |
|---|---|
| `demo-app/.env` | Contains the `elastic` user password in plain text |
| `demo-app/elk/certs/http_ca.crt` | TLS Certificate Authority cert from your Elasticsearch instance. Every instance generates its own unique cert — committing it would expose your specific setup and is meaningless to anyone else |

**How to verify these are not tracked:**
```bash
git ls-files | grep -E "\.env|certs"
```

This should return **nothing**. If it returns any result, those files are tracked and must be removed immediately:
```bash
git rm --cached demo-app/.env
git rm --cached demo-app/elk/certs/http_ca.crt
git commit -m "remove sensitive files from tracking"
```

---

## One-Time Setup

Every developer or machine running this project must perform these steps once locally. They are **not** stored in git.

### Step 1 — Get the CA Certificate

Logstash needs to trust the HTTPS connection to Elasticsearch. The certificate lives inside the running `es01` container. Copy it into the project:

```bash
# Create the certs folder
mkdir -p demo-app/elk/certs

# Copy the cert out of the es01 container
docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt \
  demo-app/elk/certs/http_ca.crt
```

**What this cert is:**
It is a Certificate Authority (CA) file — specifically, the CA that signed Elasticsearch's own HTTPS certificate. By giving it to Logstash, we are saying "trust any certificate signed by this CA". Without it, Logstash would refuse the HTTPS connection.

**Why it is not in git:**
Every Elasticsearch installation generates its own unique CA. The cert in your `es01` container is specific to your machine. It would be wrong (and useless) for someone else cloning this repo.

### Step 2 — Create the `.env` File

Create `demo-app/.env` with your Elasticsearch password:

```bash
cat > demo-app/.env << 'EOF'
# Password for the elastic superuser in your running ELK stack
# Logstash uses this to authenticate when sending logs to Elasticsearch
# To get/reset this password:
#   echo "y" | docker exec -i es01 elasticsearch-reset-password -u elastic -s
ELASTIC_PASSWORD=<your-elastic-password>
EOF
```

Replace `<your-elastic-password>` with your actual password.

**How Docker Compose uses this file:**
Docker Compose automatically reads `.env` in the same directory as `docker-compose.yml`. The value of `ELASTIC_PASSWORD` is then injected into the Logstash container via the `environment` section, where Logstash reads it as `${ELASTIC_PASSWORD}` in the pipeline config.

**Important notes on passwords:**
- Use only alphanumeric characters (`A-Z`, `a-z`, `0-9`) in the password — special characters like `*`, `+`, `=` can break Logstash's variable expansion
- If you need to set a clean password, use the Elasticsearch API directly:

```bash
curl --cacert ~/ELK/http_ca.crt \
  -u "elastic:<current-password>" \
  -X POST "https://localhost:9200/_security/user/elastic/_password" \
  -H 'Content-Type: application/json' \
  -d '{"password":"YourNewCleanPassword"}'
```

---

## Docker Compose — `docker-compose.yml`

### Services Overview

This compose file starts **three services**. Elasticsearch and Kibana are intentionally absent — they are external.

```
Services in this file:       External (already running):
─────────────────────        ───────────────────────────
app      (Flask app)         es01   (Elasticsearch)
logstash (log processor)     kib01  (Kibana)
filebeat (log collector)
```

### The `app` Service

```yaml
app:
  build: .
  ports:
    - "5000:5000"
  logging:
    driver: "json-file"
    options:
      max-size: "10m"
      max-file: "3"
  labels:
    - "co.elastic.logs/enabled=true"
    - "co.elastic.logs/json.keys_under_root=true"
    - "co.elastic.logs/json.overwrite_keys=true"
  networks:
    - elk
```

| Setting | Purpose |
|---|---|
| `build: .` | Build the image from the `Dockerfile` in the current directory |
| `ports: 5000:5000` | Map container port 5000 to host port 5000 (format is `host:container`) |
| `logging.driver: json-file` | Store container logs as JSON files on the host at `/var/lib/docker/containers/` — Filebeat reads these files |
| `max-size: 10m` | Rotate log files when they reach 10 MB |
| `max-file: 3` | Keep only the last 3 rotated files — prevents disk fill |
| `co.elastic.logs/enabled: true` | **The opt-in label** — Filebeat only collects logs from containers with this label |
| `co.elastic.logs/json.keys_under_root: true` | Tell Filebeat the log content is JSON and to promote fields to root level |

### The `logstash` Service

```yaml
logstash:
  image: docker.elastic.co/logstash/logstash:9.3.2
  volumes:
    - ./elk/logstash/pipeline:/usr/share/logstash/pipeline:ro
    - ./elk/certs/http_ca.crt:/usr/share/logstash/certs/http_ca.crt:ro
  ports:
    - "5044:5044"
  environment:
    - LS_JAVA_OPTS=-Xms256m -Xmx256m
    - xpack.monitoring.enabled=false
    - ELASTIC_PASSWORD=${ELASTIC_PASSWORD}
  networks:
    - elk
    - elastic
```

| Setting | Purpose |
|---|---|
| `image: logstash:9.3.2` | Must match the version of your running Elasticsearch |
| `./elk/logstash/pipeline:/usr/share/logstash/pipeline:ro` | Mount the pipeline config into the container. `:ro` = read-only (the container cannot modify it) |
| `./elk/certs/http_ca.crt:...:ro` | Mount the CA cert into the container so Logstash can trust `es01`'s HTTPS cert |
| `LS_JAVA_OPTS=-Xms256m -Xmx256m` | Set Java heap memory: min 256 MB, max 256 MB. Keeps Logstash from consuming too much RAM |
| `xpack.monitoring.enabled=false` | Disable Logstash's built-in monitoring to avoid extra noise and connections |
| `ELASTIC_PASSWORD=${ELASTIC_PASSWORD}` | Read `ELASTIC_PASSWORD` from the host environment (populated from `.env`) and inject it into the container |
| `networks: [elk, elastic]` | **Logstash is on both networks.** `elk` = receives from Filebeat. `elastic` = sends to `es01` |

### The `filebeat` Service

```yaml
filebeat:
  image: docker.elastic.co/beats/filebeat:9.3.2
  user: root
  volumes:
    - ./elk/filebeat/filebeat.yml:/usr/share/filebeat/filebeat.yml:ro
    - /var/lib/docker/containers:/var/lib/docker/containers:ro
    - /var/run/docker.sock:/var/run/docker.sock:ro
  depends_on:
    - logstash
  networks:
    - elk
```

| Setting | Purpose |
|---|---|
| `user: root` | Filebeat needs root to read Docker's container log files and the Docker socket |
| `/var/lib/docker/containers:ro` | Mounts the host directory where Docker stores all container log files. Filebeat reads here |
| `/var/run/docker.sock:ro` | Mounts the Docker socket — Filebeat uses this to discover running containers and read their metadata (name, image, labels) |
| `depends_on: logstash` | Docker starts Logstash before Filebeat (so Filebeat does not try to connect before Logstash is ready) |

### Networks

```yaml
networks:
  elk:
    driver: bridge   # Internal — app, filebeat, logstash communicate here

  elastic:
    external: true   # External — already exists, we join it to reach es01
```

**Why two networks?**

```
[app] ──── elk network ────▶ [filebeat] ──── elk network ────▶ [logstash]
                                                                     │
                                                              elastic network
                                                                     │
                                                                  [es01]
```

- `elk` is an internal bridge network created by this compose file. It allows `app`, `filebeat`, and `logstash` to communicate using service names (`logstash:5044`).
- `elastic` is the external network where `es01` and `kib01` live. Logstash joins it so it can reach `es01` by name.

**`external: true` means:** "This network already exists. Do not create it. Just connect to it."

---

## Filebeat Configuration — `elk/filebeat/filebeat.yml`

```yaml
filebeat.autodiscover:
  providers:
    - type: docker
      hints.enabled: true
      templates:
        - condition:
            equals:
              docker.container.labels.co.elastic.logs/enabled: "true"
          config:
            - type: container
              paths:
                - /var/lib/docker/containers/${data.docker.container.id}/*.log
              json.keys_under_root: true
              json.overwrite_keys: true
              json.message_key: log

processors:
  - add_docker_metadata:
      host: "unix:///var/run/docker.sock"
  - add_host_metadata: ~

output.logstash:
  hosts: ["logstash:5044"]

logging.level: warning
```

### Section by Section

**`autodiscover`**

Filebeat does not have a fixed list of containers to watch. Instead it uses Docker autodiscovery — it monitors the Docker daemon for container events and automatically starts or stops log collection as containers come and go.

**`hints.enabled: true`**

Tells Filebeat to read `co.elastic.logs/*` labels on each container to decide how to handle its logs.

**`templates.condition`**

Only collect logs from containers where the label `co.elastic.logs/enabled` equals `"true"`. Containers without this label are completely ignored. This is the **opt-in mechanism** — you control which containers contribute logs.

**`json.keys_under_root: true`**

The Flask app emits JSON. This tells Filebeat to parse the JSON and place each field at the root of the event object (rather than nesting everything under a `message` key). Combined with `overwrite_keys: true`, this means if the JSON contains a `message` field, it replaces Filebeat's own `message` field.

**`processors`**

Processors run on every event before it is sent:
- `add_docker_metadata` — attaches container name, image, labels, and ID to each log event. This is how you know which container a log came from when viewing in Kibana.
- `add_host_metadata` — attaches the hostname of the machine running Filebeat.

**`output.logstash`**

Send all collected log events to Logstash at `logstash:5044`. The service name `logstash` resolves to the Logstash container because both are on the same `elk` Docker network.

**`logging.level: warning`**

Only print Filebeat's own internal logs at WARNING level or above. This prevents Filebeat's operational messages from cluttering `docker logs filebeat`.

---

## Logstash Pipeline — `elk/logstash/pipeline/logstash.conf`

A Logstash pipeline has three sections: **input** (where data comes from), **filter** (how to process it), **output** (where to send it).

### Input

```
input {
  beats {
    port => 5044
  }
}
```

Listen on port 5044 for connections from Filebeat. The `beats` input understands the Beats protocol — a lightweight binary protocol that Filebeat uses to ship logs reliably.

### Filter

```
filter {
  if [message] =~ /^\s*\{/ {
    json {
      source  => "message"
      target  => "log"
    }
    mutate {
      rename => {
        "[log][levelname]"   => "level"
        "[log][message]"     => "log_message"
        "[log][name]"        => "logger"
        "[log][asctime]"     => "timestamp"
        "[log][endpoint]"    => "endpoint"
        "[log][method]"      => "http_method"
        "[log][remote_addr]" => "client_ip"
        "[log][status]"      => "response_status"
      }
    }
  }

  mutate {
    add_field => { "service" => "flask-demo-app" }
  }

  if [type] == "log" and ![message] {
    drop {}
  }
}
```

**`if [message] =~ /^\s*\{/`**
Only process events where the message looks like JSON (starts with `{`). This prevents Logstash from crashing on non-JSON lines (startup messages, warnings, etc.).

**`json { source => "message" target => "log" }`**
Parse the JSON string in `message` and store the resulting fields under a temporary key called `log`. So `{"levelname": "INFO"}` becomes `[log][levelname] = "INFO"`.

**`mutate { rename => {...} }`**
Promote fields from `[log][fieldname]` to top-level fields with cleaner names. This makes them immediately visible in Kibana's field list without having to expand nested objects.

| Original field | Renamed to | Why |
|---|---|---|
| `[log][levelname]` | `level` | Shorter, standard name |
| `[log][message]` | `log_message` | Avoids collision with Logstash's built-in `message` field |
| `[log][asctime]` | `timestamp` | Cleaner name |
| `[log][endpoint]` | `endpoint` | Promote to top level for easy filtering |
| `[log][method]` | `http_method` | Clarifies it is HTTP-specific |
| `[log][remote_addr]` | `client_ip` | More descriptive |

**`add_field { "service" => "flask-demo-app" }`**
Tags every event with the service name. Useful when you have multiple apps sending logs to the same Elasticsearch — you can filter by `service: "flask-demo-app"`.

**`drop {}`**
Silently discard Filebeat internal heartbeat events that have no real log content.

### Output

```
output {
  elasticsearch {
    hosts    => ["https://es01:9200"]
    user     => "elastic"
    password => "${ELASTIC_PASSWORD}"
    ssl_enabled                 => true
    ssl_certificate_authorities => ["/usr/share/logstash/certs/http_ca.crt"]
    ssl_verification_mode       => "none"
    index    => "flask-app-%{+YYYY.MM.dd}"
  }
}
```

| Setting | Purpose |
|---|---|
| `hosts: ["https://es01:9200"]` | Connect to `es01` on the `elastic` Docker network over HTTPS |
| `user / password` | Authenticate with Elasticsearch. Password is read from the `ELASTIC_PASSWORD` environment variable — **never hardcoded** |
| `ssl_enabled: true` | Use HTTPS (encrypted connection). Required because `es01` enforces HTTPS |
| `ssl_certificate_authorities` | Path inside the Logstash container to the CA cert we mounted. This is what allows Logstash to trust `es01`'s certificate |
| `ssl_verification_mode: none` | Skip hostname verification. Required because `es01`'s certificate was issued to the container ID, not to the hostname `es01`. In production this should be `full` with a properly issued cert |
| `index: flask-app-%{+YYYY.MM.dd}` | Store logs in daily indices: `flask-app-2026.03.27`, `flask-app-2026.03.28`, etc. Daily indices make it easy to delete old data and keep searches fast |

---

## Starting the Stack

```bash
cd demo-app/

# First time: pull images and build the app
docker compose up -d --build

# Subsequent starts
docker compose up -d
```

### Expected startup order

Docker Compose respects `depends_on`, so services start in this order:
1. `app` and `logstash` start together
2. `filebeat` starts after `logstash` is up

Logstash itself takes 30–60 seconds to fully initialise its pipeline. Filebeat will retry the connection automatically.

### Verify the pipeline is running

```bash
# 1. All three containers are up
docker compose ps

# 2. Logstash pipeline started successfully
docker compose logs logstash | grep "Pipelines running"

# 3. Filebeat connected to Logstash
docker compose logs filebeat | grep -i "connect"

# 4. Logs are reaching Elasticsearch
curl --cacert ~/ELK/http_ca.crt \
  -u "elastic:<your-elastic-password>" \
  "https://localhost:9200/_cat/indices/flask-app-*?v"
```

---

## Stopping and Restarting

```bash
# Stop the app stack only (ELK stack keeps running)
docker compose down

# Restart a single service after a config change
docker compose restart logstash
docker compose restart filebeat

# Start ELK stack first, then the app
docker start es01 && sleep 20 && docker start kib01
docker compose up -d
```

---

## Updating the Elasticsearch Password

If you reset the `elastic` password in Elasticsearch, update it here too:

1. Set the new password in Elasticsearch:
```bash
curl --cacert ~/ELK/http_ca.crt \
  -u "elastic:<current-password>" \
  -X POST "https://localhost:9200/_security/user/elastic/_password" \
  -H 'Content-Type: application/json' \
  -d '{"password":"YourNewPassword"}'
```

2. Update `demo-app/.env`:
```
ELASTIC_PASSWORD=YourNewPassword
```

3. Restart Logstash to pick up the new value:
```bash
docker compose up -d --force-recreate logstash
```

---

## Troubleshooting

### Logstash: `401 Unauthorized` connecting to Elasticsearch

**What it means:** The password Logstash is using does not match what Elasticsearch expects.

**Diagnose:**
```bash
docker compose logs logstash | grep "401"
```

**Fix:**
1. Verify the `.env` file has the correct password
2. Verify the password has no special characters (`*`, `+`, `=`)
3. Test the password manually:
```bash
curl --cacert ~/ELK/http_ca.crt \
  -u "elastic:<password-from-env>" \
  https://localhost:9200
```
If this returns JSON with `cluster_name`, the password is correct. If it returns a 401, reset the password (see section above).

4. Check Docker Compose is reading `.env` correctly — the container must see the variable:
```bash
docker exec demo-app-logstash-1 env | grep ELASTIC_PASSWORD
```

---

### Logstash: `ELASTIC_PASSWORD is not defined`

**What it means:** Logstash started before the environment variable was available.

**Fix:**
```bash
# Verify .env exists and has the variable
cat demo-app/.env | grep ELASTIC_PASSWORD

# Recreate logstash to force a fresh read of .env
docker compose up -d --force-recreate logstash
```

---

### Logstash: `network elastic not found`

**What it means:** The external `elastic` Docker network does not exist — the ELK stack is not running.

**Fix:**
```bash
# Start the ELK stack first
docker start es01
sleep 20
docker start kib01

# Then start this app stack
docker compose up -d
```

---

### Filebeat: no logs reaching Logstash

**Step 1 — Check the Flask app container has the required label:**
```bash
docker inspect demo-app-app-1 | grep -A 10 "Labels"
```
Must include `co.elastic.logs/enabled: true`. If not, check the `labels:` section in `docker-compose.yml`.

**Step 2 — Check Filebeat can reach the Docker socket:**
```bash
docker compose logs filebeat | grep -i "error\|docker"
```

**Step 3 — Check Filebeat can connect to Logstash:**
```bash
docker compose logs filebeat | tail -20
```
Look for connection established or retrying messages.

---

### No logs appearing in the Elasticsearch index

```bash
# Check whether the index exists at all
curl --cacert ~/ELK/http_ca.crt \
  -u "elastic:<your-elastic-password>" \
  "https://localhost:9200/_cat/indices/flask-app-*?v"
```

- **Index exists, `docs.count` is 0** — Logstash connected but no events have been processed. Generate traffic on the Flask app and wait 10–15 seconds.
- **Index does not exist** — Logstash has not successfully written anything. Check Logstash logs for errors.
- **`index_not_found_exception`** — Normal if no logs have been sent yet. Generate traffic first.

Generate test traffic:
```bash
for i in {1..5}; do
  curl -s http://localhost:5000/health > /dev/null
  curl -s http://localhost:5000/ > /dev/null
done
echo "Traffic generated — wait 15 seconds then check the index"
```

---

### CA certificate errors in Logstash

**Symptom in logs:**
```
SSL certificate problem: unable to get local issuer certificate
```

**What it means:** The cert file at `elk/certs/http_ca.crt` is missing, wrong, or empty.

**Fix:**
```bash
# Re-copy the cert from the running es01 container
docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt \
  demo-app/elk/certs/http_ca.crt

# Verify it has content
cat demo-app/elk/certs/http_ca.crt | head -3
# Should start with: -----BEGIN CERTIFICATE-----

# Restart Logstash
docker compose restart logstash
```

---

## Quick Reference — All Ports

| Service | Port | Protocol | Direction |
|---|---|---|---|
| Flask App | 5000 | HTTP | Inbound (your browser / curl) |
| Logstash | 5044 | Beats protocol | Inbound from Filebeat only |
| Elasticsearch (`es01`) | 9200 | HTTPS | Logstash → es01 |
| Kibana (`kib01`) | 5601 | HTTP | Your browser |

---

## Quick Reference — All Files

| File | Committed to git | Contains |
|---|---|---|
| `app.py` | Yes | Flask application code |
| `Dockerfile` | Yes | Image build instructions |
| `requirements.txt` | Yes | Python dependencies |
| `docker-compose.yml` | Yes | Service definitions |
| `.gitignore` | Yes | List of excluded files |
| `elk/filebeat/filebeat.yml` | Yes | Filebeat collection config |
| `elk/logstash/pipeline/logstash.conf` | Yes | Logstash pipeline config |
| `demo-app/.env` | **No** | Elasticsearch password |
| `demo-app/elk/certs/http_ca.crt` | **No** | TLS certificate (machine-specific) |

---

*For Flask application details, see [app-configuration.md](./app-configuration.md)*
*For ELK Stack installation, see the [elk-setup repository](https://github.com/cloud-prakhar/elk-setup)*

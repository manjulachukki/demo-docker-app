# Notes App — ELK Integration Configuration

## Prerequisites

Before starting notes-app, your ELK stack must be running:

```bash
# Verify ELK is up
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "es01|kib01"
```

Expected output:
```
es01    Up X minutes (healthy)
kib01   Up X minutes
```

If not running, start it first — see the elk-setup repository.

---

## One-Time Setup (Do This Before First Run)

### Step 1 — Copy the CA Certificate

Logstash communicates with Elasticsearch over HTTPS. It needs the CA certificate to verify the connection.

```bash
cd /path/to/demo-docker-app/notes-app

# Create the certs directory
mkdir -p elk/certs

# Copy the cert from the running es01 container
docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt elk/certs/http_ca.crt

# Verify
ls -la elk/certs/http_ca.crt
```

> **Security:** `elk/certs/` is in `.gitignore`. The cert is machine-specific and must never be committed. Every developer copies it from their own ES instance.

### Step 2 — Create the `.env` File

```bash
cp .env.example .env
```

Edit `.env` and set your Elasticsearch password:

```
ELASTIC_PASSWORD=YourActualPasswordHere
```

> **Rules for the password:**
> - Use **alphanumeric characters only** — no `*`, `+`, `=`, `!`, `@`, `#`, etc.
> - Special characters break Logstash's `${ELASTIC_PASSWORD}` variable substitution silently, causing 401 Unauthorized errors that are hard to diagnose.
> - If your password has special characters, reset it: see the elk-setup docs.

---

## Architecture: How the Three Services Connect

```
┌─────────────────────────────────────────────────────────────────────┐
│  notes-app (docker-compose project)                                  │
│                                                                      │
│  ┌─────────┐    stdout     ┌──────────────────────────────────────┐ │
│  │  Flask  │ ─────────────►│ /var/lib/docker/containers/...       │ │
│  │  :5001  │               │         (Docker log files)           │ │
│  └─────────┘               └──────────────┬───────────────────────┘ │
│                                           │ tail (autodiscovery)     │
│  ┌────────────────────────────────────────▼───────────────────────┐ │
│  │  Filebeat                                                       │ │
│  │  reads container logs → forwards to Logstash via Beats :5045   │ │
│  └────────────────────────────────────────┬───────────────────────┘ │
│                                           │                          │
│  ┌────────────────────────────────────────▼───────────────────────┐ │
│  │  Logstash :5045                                                 │ │
│  │  INPUT: beats → FILTER: json/mutate/date → OUTPUT: ES HTTPS    │ │
│  └────────────────────────────────────────┬───────────────────────┘ │
│                                           │                          │
└───────────────────────────────────────────┼─────────────────────────┘
                                            │ elastic (external network)
                        ┌───────────────────▼───────────────────────┐
                        │  Elasticsearch (es01) :9200                │
                        │  Index: notes-app-YYYY.MM.dd               │
                        └───────────────────────────────────────────┘
```

**Two networks:**
- `notes_elk` (internal bridge) — Flask ↔ Filebeat ↔ Logstash
- `elastic` (external, shared) — Logstash → es01

---

## Service-by-Service Configuration

### 1. Flask App (`app` service)

**Container labels (in docker-compose.yml):**
```yaml
labels:
  - "co.elastic.logs/enabled=true"
  - "co.elastic.logs/json.keys_under_root=true"
  - "co.elastic.logs/json.overwrite_keys=true"
```

- `enabled=true` — opts this container into Filebeat collection. Containers without this label are ignored.
- `json.keys_under_root=true` — promotes JSON fields to top-level (e.g., `json.level` → `level`).
- `json.overwrite_keys=true` — if a JSON field conflicts with a Filebeat field (e.g., `message`), the JSON field wins.

---

### 2. Filebeat (`elk/filebeat/filebeat.yml`)

**Autodiscovery:**
```yaml
filebeat.autodiscover:
  providers:
    - type: docker
      hints.enabled: true
      hints.default_config.enabled: false
```

- Watches Docker via `/var/run/docker.sock`
- Only collects logs from containers with `co.elastic.logs/enabled=true`
- `hints.default_config.enabled: false` — ignores containers without the label

**Output:**
```yaml
output.logstash:
  hosts: ["notes-logstash:5045"]
```

Uses the Docker container name `notes-logstash` — Docker DNS resolves it because both containers are on the `notes_elk` network.

---

### 3. Logstash (`elk/logstash/pipeline/logstash.conf`)

**INPUT:** Beats on port 5045 (5044 is used by demo-app).

**FILTER stages:**
1. `json` — parse `message` field as JSON
2. `mutate rename` — `timestamp` → `@timestamp`
3. `date` — parse `@timestamp` string into a proper date object
4. `mutate rename` — `levelname` → `log.level`
5. `mutate add_field` — add `level` (copy) and `app_name`
6. `mutate remove_field` — strip noisy Filebeat metadata fields

**OUTPUT:**
```
hosts     => ["https://es01:9200"]
user      => "elastic"
password  => "${ELASTIC_PASSWORD}"
ssl_verification_mode => "none"
index     => "notes-app-%{+YYYY.MM.dd}"
```

> **Why `ssl_verification_mode => "none"`?**
> The ES TLS certificate is issued to the container ID (e.g., `3f3e07243b2c`), not the hostname `es01`. Strict hostname verification would fail. We set it to `none` — the CA cert is still validated so traffic is encrypted. This is acceptable in a local/lab environment.

---

## Starting the Stack

```bash
cd notes-app

# Verify the elastic network exists
docker network ls | grep elastic

# Start all services in detached mode
docker compose up -d

# Verify all three containers are running
docker compose ps
```

Expected:
```
NAME              IMAGE                              STATUS
notes-app         notes-app-app                      Up
notes-filebeat    docker.elastic.co/beats/filebeat   Up
notes-logstash    docker.elastic.co/logstash/logstash Up
```

---

## Verifying the Pipeline

### Check 1 — App is responding
```bash
curl http://localhost:5001/health
# Expected: {"status": "ok", "app": "notes-app"}
```

### Check 2 — Logs are being generated
```bash
docker logs notes-app --tail 10 -f
# Should see JSON log lines every time you hit the app
```

### Check 3 — Filebeat is running
```bash
docker logs notes-filebeat --tail 20
# Look for: "Successfully published N events"
# If you see permission errors, check the chmod command in docker-compose.yml
```

### Check 4 — Logstash is processing
```bash
docker logs notes-logstash --tail 30 -f
# Look for: "Sending batch" and no ERROR lines
# If you see "401 Unauthorized" — check ELASTIC_PASSWORD in .env
```

### Check 5 — Index exists in Elasticsearch
```bash
# Replace <your-elastic-password> with your actual password
curl -k -u elastic:<your-elastic-password> \
  "https://localhost:9200/notes-app-*/_count"
# Expected: {"count": N, ...}  where N > 0
```

### Check 6 — Generate test logs
```bash
# Create a note
curl -X POST http://localhost:5001/notes \
  -d "content=Hello+from+test" \
  -H "Content-Type: application/x-www-form-urlencoded"

# Trigger error log
curl -X POST http://localhost:5001/demo/error

# Trigger bulk logs (5 events at different levels)
curl -X POST http://localhost:5001/demo/bulk
```

---

## Troubleshooting

### Logstash shows "401 Unauthorized"
**Cause:** Wrong password or special characters in the password.

**Fix:**
1. Check `.env` has the correct password
2. Ensure the password is alphanumeric only (no `*`, `+`, `=`, `!`, etc.)
3. Check if your shell has `ELASTIC_PASSWORD` set — it overrides `.env`:
   ```bash
   echo $ELASTIC_PASSWORD
   ```
   If it's set and different, either unset it or update your shell's value.
4. Restart Logstash after fixing: `docker compose restart logstash`

---

### Filebeat "config file must be owned by the user running this process"
**Cause:** `filebeat.yml` has loose file permissions.

**Fix:** The `command` in docker-compose.yml runs `chmod go-w` before starting. If this error persists:
```bash
chmod go-w elk/filebeat/filebeat.yml
docker compose restart filebeat
```

---

### No index appearing in Elasticsearch
**Cause:** Pipeline broken at some stage.

**Debug steps:**
1. Check each container for errors in order: `docker logs notes-app`, `docker logs notes-filebeat`, `docker logs notes-logstash`
2. Verify the CA cert exists: `ls -la elk/certs/http_ca.crt`
3. Verify the `elastic` network exists: `docker network inspect elastic`
4. Generate traffic to the app: `curl http://localhost:5001/demo/bulk`
5. Wait 30 seconds, then check ES: `curl -k -u elastic:<pass> "https://localhost:9200/notes-app-*/_count"`

---

### Index exists but no data in Kibana
**Cause:** Data View not created or wrong index pattern.

**Fix:** See `kibana-setup.md` — create a Data View with pattern `notes-app-*`.

---

### Port 5001 already in use
**Cause:** Another process is on port 5001.

**Fix:**
```bash
# Find what's using port 5001
lsof -i :5001

# Or change the port in docker-compose.yml:
ports:
  - "5002:5001"   # host:container
```

---

### Rate limit triggered unexpectedly (HTTP 429)
**Cause:** `RATE_LIMIT` is low and you're hitting the app quickly.

**Fix:** Increase the limit in docker-compose.yml:
```yaml
environment:
  - RATE_LIMIT=100
  - RATE_WINDOW=60
```
Then `docker compose up -d --force-recreate app`.

---

## Important Security Notes

| What | Rule |
|---|---|
| `.env` file | Never commit — contains the Elasticsearch password |
| `elk/certs/http_ca.crt` | Never commit — machine-specific, regenerated per ES install |
| Passwords | Alphanumeric only — special chars break Logstash env var expansion |
| `debug=False` | Always — Flask debug mode exposes a browser-accessible debugger |
| IP addresses | Never logged raw — always SHA-256 hashed first |
| User input | Always HTML-escaped before storage or rendering |

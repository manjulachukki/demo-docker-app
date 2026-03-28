# Notes App — Teardown Guide

This guide covers all levels of cleanup — from pausing the app to full removal including Elasticsearch data.

Choose the level that matches your goal.

---

## Teardown Levels

| Level | What it does | Data preserved? |
|---|---|---|
| 1 | Stop containers | Yes — restart anytime |
| 2 | Stop and remove containers | Yes — ES index kept |
| 3 | Remove containers + internal network | Yes — ES index kept |
| 4 | Remove containers + images | Yes — ES index kept |
| 5 | Delete Elasticsearch index | No — index deleted |
| 6 | Delete Kibana Data View | No — Data View removed |

---

## Level 1 — Stop Containers (Pause)

Stops all three containers without removing them. Data and state are preserved. You can restart any time.

```bash
cd notes-app
docker compose stop
```

**To restart:**
```bash
docker compose start
```

---

## Level 2 — Stop and Remove Containers

Stops and removes the containers. Images are kept, so the next `up` re-creates containers quickly without rebuilding.

```bash
cd notes-app
docker compose down
```

**To restart:**
```bash
docker compose up -d
```

---

## Level 3 — Remove Containers and Internal Network

The `notes_elk` bridge network is removed along with the containers.

```bash
docker compose down
```

> `docker compose down` already removes the default project network. If you added `--volumes` flag, it would also remove named volumes — but notes-app has no named volumes.

**Verify the network is gone:**
```bash
docker network ls | grep notes
# Should show nothing
```

---

## Level 4 — Remove Containers, Network, and Images

Removes everything Docker created for this project, including the built Flask image.

```bash
docker compose down --rmi all
```

**Verify images are removed:**
```bash
docker images | grep notes-app
# Should show nothing
```

**To restart after this level:** Docker must rebuild the Flask image on next `up`:
```bash
docker compose up -d --build
```

---

## Level 5 — Delete Elasticsearch Index

The `notes-app-*` indices live in the external `es01` container. They are not touched by `docker compose down` — they persist until explicitly deleted.

**Delete all notes-app indices:**
```bash
# Replace <your-elastic-password> with your actual password
curl -k -u elastic:<your-elastic-password> \
  -X DELETE "https://localhost:9200/notes-app-*"
```

Expected response:
```json
{"acknowledged": true}
```

**Delete a specific day's index:**
```bash
curl -k -u elastic:<your-elastic-password> \
  -X DELETE "https://localhost:9200/notes-app-2026.03.28"
```

**List remaining indices:**
```bash
curl -k -u elastic:<your-elastic-password> \
  "https://localhost:9200/_cat/indices/notes-app-*?v"
# Should show nothing after deletion
```

---

## Level 6 — Delete Kibana Data View

Kibana Data Views are stored in the Kibana system index (`.kibana`). They are not removed by `docker compose down` or by deleting the ES index.

1. Open Kibana: http://localhost:5601
2. Hamburger menu → **Stack Management** → **Data Views**
3. Find **Notes App Logs** (`notes-app-*`)
4. Click the **trash icon** on the right
5. Confirm deletion

**Also delete saved dashboards and visualizations (optional):**
- Hamburger menu → **Stack Management** → **Saved Objects**
- Search for `notes` or `Notes App`
- Select all matching objects → **Delete**

---

## Full Teardown Script

Run this to perform all levels at once (stops containers, removes images, deletes ES index):

```bash
#!/bin/bash
# Full teardown for notes-app
# Run from: /path/to/demo-docker-app/notes-app/

ELASTIC_PASS="${ELASTIC_PASSWORD:-}"

if [ -z "$ELASTIC_PASS" ]; then
  echo "Enter your Elasticsearch password:"
  read -s ELASTIC_PASS
fi

echo ">>> Stopping and removing containers..."
docker compose down --rmi all

echo ">>> Deleting Elasticsearch index..."
curl -sk -u "elastic:${ELASTIC_PASS}" \
  -X DELETE "https://localhost:9200/notes-app-*" | python3 -m json.tool

echo ">>> Done."
echo "Note: Delete Kibana Data View manually via Stack Management > Data Views"
```

---

## Post-Teardown Verification

```bash
# 1. No containers running
docker compose ps
# Expected: empty table

# 2. No notes-app images
docker images | grep notes
# Expected: nothing

# 3. No notes-app indices in ES
curl -k -u elastic:<your-elastic-password> \
  "https://localhost:9200/_cat/indices/notes-app-*?v"
# Expected: empty (just the header line or no output)

# 4. internal network removed
docker network ls | grep notes_elk
# Expected: nothing
```

---

## What Is NOT Removed

| Item | Where it lives | How to remove |
|---|---|---|
| `elk/certs/http_ca.crt` | Local filesystem | `rm elk/certs/http_ca.crt` |
| `.env` file | Local filesystem | `rm .env` |
| Kibana Data View | Kibana system index | Stack Management → Data Views |
| Kibana dashboards | Kibana system index | Stack Management → Saved Objects |
| `elastic` Docker network | Docker | Do not remove — shared with ELK stack |
| `es01` / `kib01` containers | Docker | See elk-setup teardown guide |

---

## Restarting After Teardown

After a full teardown (Level 4), to bring notes-app back:

```bash
# 1. Ensure ELK stack is running
docker ps | grep es01

# 2. Re-copy the CA cert (cert may have changed if ES was recreated)
docker cp es01:/usr/share/elasticsearch/config/certs/http_ca.crt elk/certs/http_ca.crt

# 3. Ensure .env has the correct password
cat .env

# 4. Start notes-app (rebuilds the Flask image)
docker compose up -d --build

# 5. Verify
docker compose ps
curl http://localhost:5001/health
```

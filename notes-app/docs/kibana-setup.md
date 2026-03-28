# Notes App — Kibana Setup Guide

## Overview

This guide walks through:
1. Creating a Data View for notes-app logs
2. Exploring logs in Discover
3. Building visualizations and a dashboard
4. Sample searches and KQL queries

---

## Prerequisites

- ELK stack running (es01 + kib01)
- notes-app running and sending logs
- At least one index exists: `notes-app-YYYY.MM.dd`

Verify the index exists:
```bash
curl -k -u elastic:<your-elastic-password> \
  "https://localhost:9200/notes-app-*/_count"
# Should return {"count": N, ...} where N > 0
```

If count is 0, generate some logs first:
```bash
curl -X POST http://localhost:5001/demo/bulk
curl http://localhost:5001/health
curl http://localhost:5001/api/notes
```

---

## Step 1 — Create a Data View

A Data View (formerly called Index Pattern) tells Kibana which Elasticsearch indices to query.

1. Open Kibana: http://localhost:5601
2. Log in with `elastic` / `<your-elastic-password>`
3. Click the **hamburger menu** (top-left) → **Stack Management**
4. Under **Kibana** → click **Data Views**
5. Click **Create data view**
6. Fill in:
   - **Name:** `Notes App Logs`
   - **Index pattern:** `notes-app-*`  ← the `*` matches all daily indices
   - **Timestamp field:** `@timestamp`
7. Click **Save data view to Kibana**

> **Why `notes-app-*`?** Logstash creates one index per day: `notes-app-2026.03.28`, `notes-app-2026.03.29`, etc. The wildcard pattern queries all of them together.

---

## Step 2 — Explore Logs in Discover

**Discover** is the main log exploration screen in Kibana.

1. Hamburger menu → **Discover**
2. In the top-left data view dropdown, select **Notes App Logs**
3. Set the time range (top-right):
   - Click the time picker → select **Last 1 hour** or **Today**
4. You should see log events in the central panel

**Useful columns to add:**
- Click the `+` icon next to any field in the left panel to pin it as a column
- Recommended: `level`, `message`, `endpoint`, `request_id`, `duration_ms`

**Expand a log event:**
- Click the `>` arrow on any row to see all fields in that document

---

## Step 3 — Key Fields Reference

| Field | Description | Example |
|---|---|---|
| `@timestamp` | When the event occurred | `2026-03-28T10:00:00.000Z` |
| `message` | Human-readable log message | `"note created"` |
| `level` | Log level | `INFO`, `WARNING`, `ERROR` |
| `request_id` | 8-char ID, same for all logs in one request | `"a1b2c3d4"` |
| `ip_hash` | SHA-256 of client IP (first 16 chars) | `"3f4a9b2e1c8d7f6a"` |
| `endpoint` | URL path | `"/notes"` |
| `method` | HTTP method | `"POST"` |
| `status_code` | HTTP response code | `200`, `429`, `500` |
| `duration_ms` | Request processing time in ms | `12.5` |
| `note_id` | ID of the note (create/delete events) | `"abc123"` |
| `total_notes` | Number of notes in memory at event time | `5` |
| `app_name` | Always `"notes-app"` | `"notes-app"` |

---

## Step 4 — KQL Searches in Discover

KQL (Kibana Query Language) lets you filter logs. Type queries in the search bar at the top of Discover.

**Filter by log level:**
```kql
level: "ERROR"
level: "WARNING"
level: ("ERROR" or "WARNING")
```

**Filter by endpoint:**
```kql
endpoint: "/notes"
endpoint: "/health"
```

**Filter by HTTP status code:**
```kql
status_code: 429
status_code >= 400
status_code >= 500
```

**Trace a single request (all logs for one request_id):**
```kql
request_id: "a1b2c3d4"
```

**Find slow requests:**
```kql
duration_ms > 100
```

**Find rate-limited requests:**
```kql
status_code: 429 and message: "rate limit"
```

**Find note creation events:**
```kql
message: "note created"
```

**Combine conditions:**
```kql
level: "ERROR" and endpoint: "/notes"
```

---

## Step 5 — Build Visualizations

Go to: Hamburger menu → **Visualize Library** → **Create visualization**

---

### Visualization 1 — Log Level Distribution (Donut Chart)

Shows proportion of INFO / WARNING / ERROR logs.

1. Choose **Lens** → **Donut**
2. **Slice by:** `level.keyword` (Terms aggregation)
3. **Size:** `Count`
4. **Title:** `Log Levels`
5. Save

---

### Visualization 2 — Requests Over Time (Bar Chart)

Shows log volume per minute — spot traffic spikes.

1. Choose **Lens** → **Bar vertical stacked**
2. **Horizontal axis:** `@timestamp` (Date histogram, interval: Auto or 1 minute)
3. **Vertical axis:** `Count`
4. **Break down by:** `level.keyword`
5. **Title:** `Requests Over Time`
6. Save

---

### Visualization 3 — Top Endpoints (Horizontal Bar)

Shows which endpoints are called most.

1. Choose **Lens** → **Bar horizontal**
2. **Vertical axis:** `endpoint.keyword` (Terms, show top 10)
3. **Horizontal axis:** `Count`
4. **Title:** `Top Endpoints`
5. Save

---

### Visualization 4 — Average Response Time (Metric)

Shows average `duration_ms` — useful for spotting performance issues.

1. Choose **Lens** → **Metric**
2. **Primary metric:** `duration_ms` → Aggregation: **Average**
3. **Title:** `Avg Response Time (ms)`
4. Save

---

### Visualization 5 — Error Rate Over Time (Line Chart)

Shows error events only — useful for incident response.

1. Choose **Lens** → **Line**
2. **Horizontal axis:** `@timestamp` (Date histogram, 1 minute)
3. **Vertical axis:** `Count`
4. Add a **filter** on the visualization: `level: "ERROR"`
5. **Title:** `Error Rate`
6. Save

---

## Step 6 — Create a Dashboard

1. Hamburger menu → **Dashboards** → **Create dashboard**
2. Click **Add from library**
3. Add all 5 visualizations created above
4. Arrange by dragging — suggested layout:
   ```
   [ Log Levels (donut) ]  [ Avg Response Time (metric) ]
   [ Requests Over Time (bar) — full width              ]
   [ Top Endpoints (bar) ]  [ Error Rate (line)         ]
   ```
5. Set the time range to **Last 15 minutes** or **Today**
6. Save with name: `Notes App Overview`

---

## Step 7 — Dev Tools Queries

Dev Tools gives you direct access to the Elasticsearch API — useful for advanced exploration and troubleshooting.

Go to: Hamburger menu → **Dev Tools**

**Count all notes-app documents:**
```json
GET /notes-app-*/_count
```

**Get the 10 most recent logs:**
```json
GET /notes-app-*/_search
{
  "size": 10,
  "sort": [{"@timestamp": {"order": "desc"}}]
}
```

**Count by log level:**
```json
GET /notes-app-*/_search
{
  "size": 0,
  "aggs": {
    "by_level": {
      "terms": {"field": "level.keyword"}
    }
  }
}
```

**Count by endpoint:**
```json
GET /notes-app-*/_search
{
  "size": 0,
  "aggs": {
    "by_endpoint": {
      "terms": {"field": "endpoint.keyword", "size": 10}
    }
  }
}
```

**Find all ERROR logs:**
```json
GET /notes-app-*/_search
{
  "query": {
    "term": {"level.keyword": "ERROR"}
  },
  "sort": [{"@timestamp": {"order": "desc"}}],
  "size": 20
}
```

**Trace a specific request (replace the request_id value):**
```json
GET /notes-app-*/_search
{
  "query": {
    "term": {"request_id.keyword": "a1b2c3d4"}
  },
  "sort": [{"@timestamp": {"order": "asc"}}]
}
```

**Average response time per endpoint:**
```json
GET /notes-app-*/_search
{
  "size": 0,
  "aggs": {
    "by_endpoint": {
      "terms": {"field": "endpoint.keyword"},
      "aggs": {
        "avg_duration": {
          "avg": {"field": "duration_ms"}
        }
      }
    }
  }
}
```

**Check index mapping (see all field types):**
```json
GET /notes-app-*/_mapping
```

---

## Generating Demo Traffic

Use these commands to generate varied log data during a live demo:

```bash
# Normal usage
curl http://localhost:5001/
curl http://localhost:5001/api/notes
curl http://localhost:5001/health
curl http://localhost:5001/stats

# Create notes
curl -X POST http://localhost:5001/notes \
  -d "content=My+first+note" \
  -H "Content-Type: application/x-www-form-urlencoded"

# Trigger different log levels
curl -X POST http://localhost:5001/demo/warning
curl -X POST http://localhost:5001/demo/error

# Bulk log generation (5 events at mixed levels)
curl -X POST http://localhost:5001/demo/bulk

# Trigger rate limiting (11 rapid requests, last one gets 429)
for i in $(seq 1 12); do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5001/health; done
```

After running these, refresh Discover in Kibana. You should see events at multiple log levels with varied endpoints and status codes.

---

## Saved Searches (Optional)

Save commonly used searches for quick access:

1. In Discover, apply a filter (e.g., `level: "ERROR"`)
2. Click **Save** (top-right) → give it a name: `Error Logs Only`
3. Save with current time filter: No (so it always shows the current time range)

Saved searches can also be added to dashboards as panels.

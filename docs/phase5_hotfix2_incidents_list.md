# Phase 5 Hotfix 2: Incident List Internal Server Error

## Problem

After clicking **Detect incident candidates**, the incident generation endpoint completed, but the dashboard then called:

```text
GET /api/incidents?limit=300
```

The backend returned `500 Internal Server Error`.

Docker logs showed:

```text
psycopg.errors.AmbiguousParameter: could not determine data type of parameter $1
FROM incident_events WHERE ($1 IS NULL OR video_source_id=$1)
```

## Cause

The incident list endpoint used this optional nullable SQL bind parameter pattern:

```sql
WHERE (:video_source_id IS NULL OR video_source_id = :video_source_id)
```

When `video_source_id` was omitted, psycopg3 sent the parameter as `NULL`, and PostgreSQL could not infer the parameter type inside the `IS NULL OR` expression.

## Fix

The endpoint now builds the SQL filter dynamically:

- If `video_source_id` is omitted, no `WHERE` clause is used.
- If `video_source_id` is provided, it adds `WHERE video_source_id = :video_source_id`.

This avoids PostgreSQL ambiguous-parameter errors and keeps the endpoint compatible with existing database volumes.

## Files changed

```text
backend/app/main.py
```

## Recommended restart

```bash
docker compose down
docker compose up --build
```

A database reset is not required for this fix.

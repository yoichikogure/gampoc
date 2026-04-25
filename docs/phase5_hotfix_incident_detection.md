# Phase 5 Hotfix: Incident Detection Internal Server Error

This hotfix makes the **Detect incident candidates** action more robust.

## Changes

- Converts video/frame/detection preparation failures into clearer API messages.
- Automatically rolls back failed database transactions.
- Ignores partial or invalid vehicle detection rows instead of crashing.
- Returns a successful response with `incident_events_created: 0` when no valid detections are available, instead of throwing a server error.
- Adds additional schema compatibility checks for users reusing older Docker database volumes.

## Recommended restart

If you are reusing a previous database volume, run:

```bash
docker compose down
docker compose up --build
```

If database schema problems continue, reset the local PoC database:

```bash
docker compose down -v
docker compose up --build
```

Then upload/register a video, run **Sample frames**, run **Detect vehicles**, and then run **Detect incident candidates**.

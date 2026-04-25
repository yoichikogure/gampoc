# Phase 5: Incident Detection and Human Review

Phase 5 adds an incident workflow on top of the Phase 4 video pipeline.

## Scope

This phase generates **candidate** incident events from vehicle-candidate detections. It is a rule-based PoC implementation intended to validate the full workflow:

`video -> vehicle detections -> incident candidates -> human review -> incident log export`

The module is evaluation-only and does not connect to or control any operational traffic signal system.

## Incident types

1. `congestion_event`
   - Generated when the number of vehicle candidates in one sampled frame exceeds the configured congestion threshold.
   - Stores a queue-length proxy based on the number of detections in the frame.

2. `possible_stalled_vehicle`
   - Generated when a vehicle candidate remains within a small pixel-distance threshold across multiple sampled detections for a configured duration.
   - This is a simple explainable tracking heuristic, not a final production-grade tracker.

## Review status

Every incident starts as `unreviewed`. Operators can change status to:

- `confirmed`
- `false_positive`
- `uncertain`
- `unreviewed`

## Main endpoints

```text
POST /api/videos/{video_source_id}/detect-incidents
GET  /api/incidents
PATCH /api/incidents/{incident_id}/review
GET  /api/incidents/{incident_id}/snapshot
GET  /api/analytics/incident-summary
GET  /api/export/incidents.csv
```

## Suggested workflow

1. Import a video from the dashboard.
2. In Phase 4, click `Sample frames` and `Detect vehicles`.
3. In Phase 5, click `Detect incident candidates`.
4. Review each event using `Confirm`, `False +`, or `Uncertain`.
5. Export `incident_events.csv` for evaluation reporting.

## Limitations

- The incident detector uses Phase 4 vehicle-candidate metadata. Accuracy depends on the quality of vehicle detection.
- The Phase 4 detector is a CPU-only OpenCV fallback, not a final YOLO/ByteTrack implementation.
- Event results are intended for demonstration and human evaluation only.

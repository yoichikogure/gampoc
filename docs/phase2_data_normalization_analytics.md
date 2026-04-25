# Phase 2: Data Normalization and Historical Analytics

Phase 2 extends the Phase 1 working application with normalized detector/approach mapping and historical analytics for SCATS-style detector counts and signal timing logs.

## Added functions

### 1. Detector and approach normalization

The application now exposes normalized detector mapping through:

- `GET /api/mappings/detectors`
- `POST /api/mappings/detectors`

The default seed data represents the Wadi Saqra PoC intersection as INT 806 with 4 approaches x 5 detectors plus 2 extra detectors. The exact lane labels can be updated after GAM confirms the final detector layout.

Example update request:

```bash
curl -X POST http://localhost:8080/api/mappings/detectors \
  -H "Content-Type: application/json" \
  -d '{
    "intersection_code": "806",
    "approach_no": 1,
    "detector_no": 1,
    "approach_name": "Northbound approach",
    "lane_label": "NB-Through-1",
    "description": "Northbound through lane detector"
  }'
```

### 2. Historical analytics

New dashboard and API analytics include:

- Daily traffic summary by approach
- Hourly traffic summary by approach
- Peak hour by approach
- Missing 15-minute detector intervals
- Simple anomaly detection
- Signal phase duration summary

API endpoints:

- `GET /api/analytics/daily-summary`
- `GET /api/analytics/hourly-summary`
- `GET /api/analytics/peak-summary`
- `GET /api/analytics/missing-intervals`
- `GET /api/analytics/anomalies`
- `GET /api/analytics/signal-phase-durations`

### 3. CSV export

CSV export endpoints:

- `GET /api/export/detector-counts.csv`
- `GET /api/export/hourly-summary.csv`

These exports are useful for manual validation, Excel-based review, and handover discussions with GAM engineers.

## Data quality logic

### Missing intervals

For each detector, the system generates the expected 15-minute interval sequence between the first and latest imported record. Any missing timestamp is listed as a missing interval.

### Anomaly detection

The current anomaly detection is intentionally simple and transparent:

- non-`ok` quality flags from the parser
- zero count intervals
- values more than 3 standard deviations from the detector average

This should be treated as a baseline data quality check, not as a final incident detection method.

## Signal timing analytics

The signal phase duration summary estimates elapsed time between consecutive records for the same intersection and phase. It is useful for initial phase behavior analysis, but it should be validated against the detailed SCATS log format and operational signal design.

## Phase 2 validation checklist

1. Start the stack with `docker compose up --build`.
2. Open `http://localhost:8080`.
3. Import `docs/sample_detector_log.txt`.
4. Import `docs/sample_signal_log.txt`.
5. Confirm that daily, hourly, peak, and data quality tables populate.
6. Download `detector_counts_normalized.csv`.
7. Review detector mappings and update lane labels if needed through the API.

## Known limitations before Phase 3

- Forecasting is not yet implemented.
- Signal recommendations are not yet implemented.
- Video AI and incident detection are not yet implemented.
- The dashboard is intentionally simple and dependency-light; it does not yet use React/Vue.

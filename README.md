# GAM Traffic AI PoC - Phase 2 Working Application

This is a Docker-portable, open-source, local web application prototype for the GAM AI-Based Traffic Monitoring and Traffic Flow Forecasting PoC.

The application is a standalone analytical prototype for evaluation only. It is not connected to, and does not control, any operational traffic signal system.

## Run

```bash
docker compose up --build
```

Open:

```text
http://localhost:8080
```

API documentation:

```text
http://localhost:8080/docs
```

## Phase 1 functions

- Docker Compose deployment
- PostgreSQL database
- FastAPI backend
- Static local web dashboard
- SCATS-style detector log import
- Signal timing log import
- Historical video file registration and metadata probing
- Basic detector chart and signal event tables
- Ingestion file tracking

## Phase 2 functions

- Normalized detector / approach mapping
- Daily traffic summary by approach
- Hourly traffic summary by approach
- Peak hour by approach
- Missing 15-minute interval checks
- Simple detector anomaly detection
- Signal phase duration analytics
- CSV export of normalized detector counts
- CSV export of hourly summary

## Sample files

- `docs/sample_detector_log.txt`
- `docs/sample_signal_log.txt`

## Useful API endpoints

```text
GET  /api/summary
POST /api/import/detector-log
POST /api/import/signal-log
POST /api/import/video
GET  /api/mappings/detectors
POST /api/mappings/detectors
GET  /api/analytics/daily-summary
GET  /api/analytics/hourly-summary
GET  /api/analytics/peak-summary
GET  /api/analytics/missing-intervals
GET  /api/analytics/anomalies
GET  /api/analytics/signal-phase-durations
GET  /api/export/detector-counts.csv
GET  /api/export/hourly-summary.csv
```

## Next phase

Phase 3 should add traffic flow forecasting and signal timing recommendation:

1. Historical average baseline forecast
2. Machine-learning forecast using recent counts, time-of-day, weekday, detector, and approach features
3. 15 / 30 / 60-minute forecast horizons
4. Forecast evaluation metrics
5. Evaluation-only signal timing recommendation rules

## Phase 3: Forecasting and Signal Recommendation

Phase 3 adds short-term traffic demand forecasting and evaluation-only signal timing recommendations.

New dashboard sections:

- Phase 3: Traffic Flow Forecasting
- Phase 3: Signal Timing Recommendations

Recommended operation:

1. Import SCATS detector logs.
2. Review Phase 2 daily/hourly summaries and data quality checks.
3. Click **Run historical-average forecast**.
4. Review MAE/RMSE/MAPE back-test metrics.
5. Optionally click **Run gradient-boosting forecast** when sufficient data has been imported.
6. Click **Generate recommendations**.

New API endpoints:

```text
POST /api/forecast/run?model_name=historical_average&horizons=15,30,60
POST /api/forecast/run?model_name=gradient_boosting&horizons=15,30,60
GET  /api/forecast/evaluation
GET  /api/forecast/results
POST /api/recommendations/generate
GET  /api/recommendations
GET  /api/export/forecast-results.csv
GET  /api/export/signal-recommendations.csv
```

All signal recommendations are decision-support outputs only. The system does not connect to or control operational traffic signal infrastructure.

## Phase 4 video-processing increment

Phase 4 adds uploaded video processing, sampled-frame preview, and CPU-only OpenCV vehicle-candidate detection.

Recommended test sequence:

1. Import `docs/sample_traffic_video_20s.mp4` from the Historical Video upload card.
2. In the Phase 4 section, click `Sample frames`.
3. Click `Detect vehicles`.
4. Review the sampled frames, detection metadata, and CSV export.

The current detector is a portable fallback for pipeline testing. It is intentionally not dependent on proprietary services, GPU drivers, or automatic model downloads.

# GAM Traffic AI PoC — Shareable Prototype Package

This repository contains a Docker-portable, open-source prototype application for the **GAM AI-Based Traffic Monitoring and Traffic Flow Forecasting PoC**.

The application demonstrates a local web dashboard for ingesting traffic data, reviewing historical analytics, running short-term traffic forecasts, generating evaluation-only signal timing recommendations, processing historical traffic videos, reviewing incident candidates, and previewing real-time RTSP/HTTP video with a lightweight live AI overlay.

This package is intended to be a **shareable achievement version** of the phase-based development completed so far.

---

## 1. What this prototype does

### 1.1 Local web dashboard

The system provides a browser-based dashboard at:

```text
http://localhost:8080
```

The dashboard is designed around the **live video view** as the primary screen. Analytics, forecast, detection, and incident tables are placed in compact scrollable panels so they do not occupy the main screen.

### 1.2 Data import and normalization

The prototype can import and normalize:

- SCATS-style 15-minute detector count logs
- SCATS-style signal phase timing logs
- Historical traffic video files
- RTSP / HTTP live video source URLs

Detector and signal data are stored in PostgreSQL and made available to the dashboard and API.

### 1.3 Historical traffic analytics

The system provides basic traffic analytics, including:

- Latest detector count records
- Latest signal phase events
- Daily traffic summaries
- Hourly traffic summaries
- Peak hour by approach
- Missing detector interval checks
- Basic anomaly checks
- Signal phase duration summaries
- CSV export

### 1.4 Short-term traffic forecasting

The system can generate short-term traffic demand forecasts for:

- 15 minutes ahead
- 30 minutes ahead
- 60 minutes ahead

Implemented forecast modes:

- Historical average model
- Gradient boosting model with fallback behavior when data is insufficient

The dashboard also shows back-test metrics:

- MAE
- RMSE
- MAPE, where applicable

### 1.5 Signal timing recommendations

The prototype generates **evaluation-only** signal timing recommendations based on forecast demand.

Examples include:

- Extend green time for high-demand approaches
- Reduce green time for lower-demand approaches
- Flag anticipated congestion periods

These outputs are **decision-support information only**. They are not sent to any signal controller.

### 1.6 Historical video processing

The system can register uploaded traffic video files, sample frames, and run a lightweight CPU-only OpenCV vehicle-candidate detector.

Outputs include:

- Sampled frame gallery
- Vehicle-candidate metadata
- Detection summary
- Vehicle detection CSV export

### 1.7 Incident candidate detection and review

The prototype can generate rule-based incident candidates from stored vehicle-candidate detections.

Current incident candidate types:

- `congestion_event`
- `possible_stalled_vehicle`

The dashboard supports human review statuses:

- `unreviewed`
- `confirmed`
- `false_positive`
- `uncertain`

### 1.8 RTSP / HTTP live video preview

The system can register a live video source URL such as:

```text
rtsp://user:password@192.168.1.100:554/stream1
```

Because browsers cannot display RTSP directly, the backend converts the RTSP/HTTP source into an MJPEG stream for local browser preview.

### 1.9 Real-time RTSP AI overlay

The current package includes a lightweight live AI overlay for RTSP/HTTP streams.

It uses an OpenCV motion-based vehicle-candidate detector as a portable fallback. It can:

- Read frames from an RTSP/HTTP source
- Draw live detection boxes on the MJPEG output
- Log live detection metadata into PostgreSQL
- Show live stream health metrics

This is useful for confirming the real-time video processing pipeline before replacing the fallback detector with a more accurate YOLO/ONNX detector.

---

## 2. What is not included yet

This package is still a prototype. The following functions are not yet fully implemented.

### 2.1 AI / computer vision limitations

Not yet included:

- Production-grade YOLO / ONNX vehicle classification for live RTSP streams
- DeepSORT / ByteTrack multi-object tracking
- Camera calibration UI
- Lane polygon configuration
- Stop-line definition
- Queue-spillback zone definition
- Accurate queue length measurement in real-world units
- Unexpected trajectory detection
- Robust abnormal stop detection based on calibrated zones
- Short video clip extraction around incidents

The current live AI overlay is **motion-based**, not a trained vehicle classifier.

### 2.2 Forecasting limitations

Not yet included:

- Full LSTM / temporal neural network training workflow
- Formal model registry
- Scheduled retraining
- Advanced holiday/event feature management
- Deep use of signal phase logs as forecasting features
- Multi-intersection forecasting

### 2.3 Signal optimization limitations

Not yet included:

- Direct SCATS optimization
- Reinforcement learning optimization
- Simulation-based signal timing optimization
- Controller interface
- Automatic transmission of recommendations to traffic signal equipment

All recommendations are for review and evaluation only.

### 2.4 System integration limitations

Not yet included:

- Live connection to SCATS detector database or SCATS API
- Scheduled automatic batch ingestion from GAM systems
- Integration with GAM operational traffic control systems
- Multi-user role-based operation workflow
- Production cybersecurity hardening
- HTTPS certificate setup
- Centralized audit log
- Backup/restore automation

### 2.5 Evaluation and documentation limitations

Partially included, but not complete:

- Full ToR-style performance evaluation report generator
- Complete training material for GAM users
- Complete administrator manual
- Complete test evidence package
- Complete model validation documentation

---

## 3. Important disclaimer

This system is a **standalone analytical prototype**.

It is **not** a production system and must not be used as an operational traffic management system without further development, testing, cybersecurity review, and formal acceptance.

The prototype:

- Does not connect to operational traffic signal controllers
- Does not send commands to any traffic signal equipment
- Does not modify GAM operational systems
- Uses imported or read-only video/data sources
- Provides decision-support outputs for demonstration and evaluation only
- Stores processed metadata and selected outputs, not a full long-term raw video archive

The live AI overlay is intended to demonstrate the data pipeline and dashboard concept. Its detections are not certified incident detections and must be reviewed by humans.

---

## 4. Technology stack

The application uses open-source components only.

| Layer | Technology |
|---|---|
| Containerization | Docker Compose |
| Backend API | Python FastAPI |
| Database | PostgreSQL |
| Web dashboard | Static HTML / CSS / JavaScript |
| Video processing | OpenCV |
| Forecasting | pandas, NumPy, scikit-learn |
| RTSP / HTTP stream handling | OpenCV / FFmpeg-capable runtime |
| Web serving | FastAPI static file serving |

No proprietary platform subscription is required to run this prototype locally.

---

## 5. Repository structure

```text
gam-traffic-ai-poc/
  backend/
    app/
      main.py
      services/
      models/
      schemas/
    requirements.txt
    Dockerfile

  frontend/
    static/
      index.html
      style.css
      app.js

  docs/
    sample_detector_log.txt
    sample_signal_log.txt
    sample_detector_log_14days_22detectors.txt
    sample_signal_log_14days.txt
    sample_traffic_video_20s.mp4
    *.md

  data/
    input/
    output/

  docker-compose.yml
  .env.example
  README.md
```

---

## 6. Quick start

### 6.1 Prerequisites

Install:

- Docker
- Docker Compose

### 6.2 Start the application

From the project directory:

```bash
docker compose up --build
```

Open the dashboard:

```text
http://localhost:8080
```

Open API documentation:

```text
http://localhost:8080/docs
```

### 6.3 Stop the application

```bash
docker compose down
```

### 6.4 Reset the local database

Use this only when you want to delete all imported data and start fresh:

```bash
docker compose down -v
docker compose up --build
```

---

## 7. Recommended demonstration workflow

### Step 1 — Open the dashboard

Open:

```text
http://localhost:8080
```

The top screen focuses on live video. Tables and logs are shown in compact scrollable panels below.

### Step 2 — Import detector log data

Use the **Data Input** panel and import:

```text
docs/sample_detector_log_14days_22detectors.txt
```

This gives enough multi-day history to test analytics and forecasting.

### Step 3 — Import signal timing logs

Use the **Data Input** panel and import:

```text
docs/sample_signal_log_14days.txt
```

### Step 4 — Review historical analytics

Open the compact historical analytics panels to review:

- Daily summary
- Hourly summary
- Peak hour by approach
- Missing intervals
- Signal duration analytics

### Step 5 — Run traffic forecasting

In the forecasting panel:

1. Run historical-average forecast
2. Review back-test metrics
3. Run gradient-boosting forecast if enough records exist
4. Export forecast CSV if required

### Step 6 — Generate signal timing recommendations

Click:

```text
Generate recommendations
```

Review the recommendation table. These are decision-support outputs only.

### Step 7 — Test historical video processing

Upload or register:

```text
docs/sample_traffic_video_20s.mp4
```

Then:

1. Click `Sample frames`
2. Click `Detect vehicles`
3. Review the sampled frames and detection metadata

### Step 8 — Generate incident candidates

After vehicle detection has been run on a historical video:

1. Click `Detect incident candidates`
2. Review incident candidates
3. Mark each as confirmed, false positive, or uncertain
4. Export incident CSV if required

### Step 9 — Register a live RTSP / HTTP source

In the **Live Source** input panel, enter a URL such as:

```text
rtsp://user:password@192.168.1.100:554/stream1
```

Then click:

```text
Register RTSP source
```

### Step 10 — View live video or live AI overlay

After registering the source:

- Click `View live` to show the raw live preview
- Click `Live AI` or `Start live AI overlay` to show the OpenCV live detection overlay

---

## 8. Dashboard layout notes

The dashboard has been redesigned for sharing and demonstration.

Main design changes:

- Live video is the primary visual focus
- Live AI overlay is placed beside/near the raw live preview
- Data input and operating controls remain visible near the top
- Summary indicators are compact
- Forecast, recommendation, analytics, detection, and incident tables are placed in fixed-height scrollable panels
- Long JSON responses are shown in compact scrollable boxes
- Historical tables no longer dominate the main screen

---

## 9. Main API endpoints

### Summary and import

```text
GET  /api/summary
POST /api/import/detector-log
POST /api/import/signal-log
POST /api/import/video
POST /api/rtsp/register
```

### Detector and signal analytics

```text
GET /api/detector-counts
GET /api/signal-events
GET /api/analytics/daily-summary
GET /api/analytics/hourly-summary
GET /api/analytics/peak-summary
GET /api/analytics/missing-intervals
GET /api/analytics/anomalies
GET /api/analytics/signal-phase-durations
```

### Forecasting and recommendations

```text
POST /api/forecast/run?model_name=historical_average&horizons=15,30,60
POST /api/forecast/run?model_name=gradient_boosting&horizons=15,30,60
GET  /api/forecast/evaluation
GET  /api/forecast/results
POST /api/recommendations/generate
GET  /api/recommendations
```

### Video and incident processing

```text
GET  /api/videos
POST /api/videos/{video_source_id}/sample-frames
POST /api/videos/{video_source_id}/detect-vehicles
GET  /api/videos/{video_source_id}/frames
GET  /api/videos/{video_source_id}/detections
POST /api/videos/{video_source_id}/detect-incidents
GET  /api/incidents
PATCH /api/incidents/{incident_id}/review
```

### RTSP / live AI

```text
GET /api/videos/{video_source_id}/live.mjpg
GET /api/videos/{video_source_id}/live-ai.mjpg
GET /api/videos/{video_source_id}/live-health
GET /api/videos/{video_source_id}/live-detections
```

### CSV exports

```text
GET /api/export/detector-counts.csv
GET /api/export/hourly-summary.csv
GET /api/export/forecast-results.csv
GET /api/export/signal-recommendations.csv
GET /api/export/vehicle-detections.csv
GET /api/export/incidents.csv
```

---

## 10. Operational notes

### RTSP access from Docker

If the RTSP source is running on the host PC or another machine, make sure the Docker container can reach it by IP address.

For example, from inside the container, the source should be reachable as:

```text
rtsp://192.168.x.x:554/stream
```

Avoid using `localhost` in the RTSP URL unless the RTSP server is running inside the same container.

### Performance

The live AI overlay is CPU-based and intentionally lightweight. Performance depends on:

- Input video resolution
- RTSP bitrate
- Host CPU performance
- Docker resource allocation
- Network stability

For smoother local demonstrations, use lower resolution or lower frame rate streams where possible.

### Browser behavior

The live video is served as MJPEG. If the stream appears stale, stop and restart the preview, or refresh the browser.

---

## 11. Next recommended development work

The next development stage should focus on reliability and realism:

1. Replace the OpenCV motion detector with YOLO/ONNX vehicle detection for live RTSP
2. Add ByteTrack or DeepSORT tracking
3. Add camera calibration and lane/zone configuration UI
4. Add queue-spillback detection based on calibrated zones
5. Add short incident clip extraction
6. Add login and role-based access control
7. Add automated scheduled data ingestion
8. Add a formal test report and user training package

---

## 12. License and component responsibility

This prototype is assembled using open-source components. Before any formal procurement handover or public release, the project owner should review the licenses of all included dependencies and confirm compatibility with the intended use.

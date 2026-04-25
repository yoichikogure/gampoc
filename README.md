# GAM Traffic AI PoC — Phase 1 Working Application

This is the first working phase of a Docker-portable, open-source, local web-based dashboard application for the AI-Based Traffic Monitoring and Traffic Flow Forecasting PoC.

## Current phase

Phase 1 implements the foundation:

- Docker Compose deployment
- PostgreSQL database
- FastAPI backend
- Browser-based local dashboard
- SCATS-style detector log import
- Signal timing log import
- Historical video registration and metadata probing
- Basic dashboard charts and tables
- Ingestion file status tracking

The system is standalone and evaluation-only. It does not connect to or control any operational traffic signal system.

## Start

```bash
docker compose up --build
```

Open:

```text
http://localhost:8080
```

API docs:

```text
http://localhost:8080/docs
```

## Stop

```bash
docker compose down
```

To delete the database volume as well:

```bash
docker compose down -v
```

## Import data

Use the web dashboard upload cards or call the API endpoints:

- `POST /api/import/detector-log`
- `POST /api/import/signal-log`
- `POST /api/import/video`

## Sample files

Sample detector and signal logs are provided under:

```text
docs/sample_detector_log.txt
docs/sample_signal_log.txt
```

## Next development phases

- Phase 2: detector/approach mapping, better validation, historical analytics
- Phase 3: baseline forecasting and rule-based signal recommendation
- Phase 4: video processing and vehicle detection
- Phase 5: tracking and incident detection
- Phase 6: testing, manuals, training material, packaging

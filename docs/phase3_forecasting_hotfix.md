# Phase 3 Forecasting Hotfix

This build fixes a runtime error that could occur when running `/api/forecast/run`.

## What changed

- Replaced the grouped rolling-lag calculation in `backend/app/services/forecasting.py` with a safer `groupby(...).transform(...)` implementation.
- Added more robust handling of datetime and numeric values before inserting forecast rows into PostgreSQL.
- Added database rollback and clearer API error details for forecasting failures.
- Improved the back-test split to use time-based splitting rather than a simple row-order split.

## How to verify

1. Start the application:

```bash
docker compose up --build
```

2. Import:

```text
docs/sample_detector_log_14days_22detectors.txt
```

3. Run either forecast endpoint:

```text
POST /api/forecast/run?model_name=historical_average&horizons=15,30,60
POST /api/forecast/run?model_name=gradient_boosting&horizons=15,30,60
```

Expected result: JSON response with `forecast_rows_created` and metric values instead of `Internal Server Error`.

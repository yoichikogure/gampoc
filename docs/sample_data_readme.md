# Longer synthetic sample data for Phase 3

This folder includes multi-day synthetic data for testing the GAM Traffic AI PoC application.

## Files

- `sample_detector_log_14days_22detectors.txt`
  - 14 calendar days from 2026-01-01 to 2026-01-14
  - 22 detectors: 4 approaches x 5 detector lanes plus 2 auxiliary detectors
  - 15-minute count intervals
  - Same SCATS-style horizontal-hour / vertical-15-minute format as the ToR sample

- `sample_signal_log_14days.txt`
  - 14 calendar days from 2026-01-01 to 2026-01-14
  - Signal events for intersection INT 806
  - Phase sequence: 2, 4, 6, 8
  - GREEN ON, YELLOW ON, RED ON events with variable green durations

## How to use

1. Start the application with Docker Compose.
2. Open `http://localhost:8080`.
3. Import `sample_detector_log_14days_22detectors.txt` from the detector log import section.
4. Import `sample_signal_log_14days.txt` from the signal log import section.
5. Run Phase 2 analytics and Phase 3 forecasting/recommendation functions.

The data is artificial and is intended for parser, dashboard, analytics, forecasting, and recommendation testing only.

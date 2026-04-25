# Phase 3: Traffic Flow Forecasting and Signal Timing Recommendation

This phase adds the first AI / decision-support layer to the GAM Traffic AI PoC.

## Scope

Phase 3 implements:

1. Short-term traffic demand forecasting for 15, 30, and 60 minutes ahead.
2. Back-test metrics for forecast quality.
3. Evaluation-only signal timing recommendations based on forecast demand.
4. Dashboard controls and tables for forecast execution, results, and recommendations.
5. CSV export for forecast results and signal recommendations.

The recommendation engine is intentionally isolated from operational signal control. It produces decision-support advice only.

## Forecasting approach

Two forecasting modes are provided:

### Historical average model

This is the default model and is recommended for early PoC use because it is transparent and robust with limited data.

The model uses a fallback hierarchy:

1. Same approach + same weekday + same 15-minute time slot
2. Same approach + same 15-minute time slot
3. Same approach average
4. Global average

### Gradient boosting model

This option uses `scikit-learn`'s `HistGradientBoostingRegressor` when enough detector count records are available. If the dataset is too small, it automatically falls back to the historical average model.

Features include:

- approach number
- hour
- minute
- weekday
- 15-minute slot index
- lagged counts
- rolling count averages

## Evaluation metrics

The API calculates:

- MAE: Mean Absolute Error
- RMSE: Root Mean Squared Error
- MAPE: Mean Absolute Percentage Error, only when actual counts are non-zero

Because this is a PoC and the ToR does not define fixed accuracy targets, these metrics are used for evaluation and comparison rather than pass/fail scoring.

## Signal timing recommendation logic

Recommendations compare forecast demand with historical average demand for the same approach.

Typical outputs:

- extend green time for high-demand approaches
- consider reducing green time for low-demand approaches
- maintain current timing when demand is close to normal

The current phase-to-approach mapping is a demonstration assumption: `phase_no = approach_no`. This must be refined after GAM confirms the actual SCATS phase-to-approach relationship.

## API endpoints

```text
POST /api/forecast/run?model_name=historical_average&horizons=15,30,60
POST /api/forecast/run?model_name=gradient_boosting&horizons=15,30,60
GET  /api/forecast/evaluation?model_name=historical_average&horizons=15,30,60
GET  /api/forecast/results
GET  /api/forecast/chart
POST /api/recommendations/generate
GET  /api/recommendations
GET  /api/export/forecast-results.csv
GET  /api/export/signal-recommendations.csv
```

## Recommended usage flow

1. Import detector logs.
2. Check Phase 2 analytics and data quality.
3. Run historical-average forecast.
4. Review back-test metrics.
5. Optionally run gradient-boosting forecast if enough data exists.
6. Generate signal timing recommendations.
7. Review recommendations with traffic engineers.
8. Export results for reporting.

## Known limitations

- Forecasting reliability depends on the amount and quality of detector data.
- Deep learning models such as LSTM are not yet implemented in this phase because they require more data and tuning.
- Recommendations are rule-based and should be interpreted by traffic engineers.
- The current phase mapping is a placeholder and must be replaced with the confirmed SCATS phase configuration.

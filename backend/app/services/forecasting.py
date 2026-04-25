from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class ForecastMetric:
    horizon_minutes: int
    model_name: str
    mae: float | None
    rmse: float | None
    mape: float | None
    test_points: int
    notes: str


def _load_approach_counts(db: Session) -> pd.DataFrame:
    """Load detector counts aggregated by approach and 15-minute interval."""
    rows = db.execute(text("""
        SELECT intersection_id, approach_no, interval_start,
               SUM(vehicle_count)::float AS vehicle_count
        FROM detector_counts
        GROUP BY intersection_id, approach_no, interval_start
        ORDER BY intersection_id, approach_no, interval_start
    """)).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["interval_start"] = pd.to_datetime(df["interval_start"], utc=True, errors="coerce")
    df["vehicle_count"] = pd.to_numeric(df["vehicle_count"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["interval_start", "intersection_id", "approach_no"])
    return df


def _feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Create robust forecasting features.

    Earlier Phase 3 builds used a grouped rolling expression that can fail with
    some pandas/index combinations. This version uses groupby.transform() so the
    resulting series always aligns with the original dataframe index.
    """
    out = df.sort_values(["intersection_id", "approach_no", "interval_start"]).copy()
    out["hour"] = out["interval_start"].dt.hour.astype(int)
    out["minute"] = out["interval_start"].dt.minute.astype(int)
    out["weekday"] = out["interval_start"].dt.weekday.astype(int)
    out["slot"] = (out["hour"] * 4 + (out["minute"] // 15)).astype(int)

    grp = out.groupby(["intersection_id", "approach_no"], group_keys=False)
    out["lag_1"] = grp["vehicle_count"].shift(1)
    out["lag_2"] = grp["vehicle_count"].shift(2)
    out["lag_4"] = grp["vehicle_count"].shift(4)
    out["rolling_4"] = grp["vehicle_count"].transform(lambda s: s.shift(1).rolling(4, min_periods=1).mean())
    out["rolling_8"] = grp["vehicle_count"].transform(lambda s: s.shift(1).rolling(8, min_periods=1).mean())
    return out


def _safe_metrics(actual: np.ndarray, pred: np.ndarray) -> tuple[float | None, float | None, float | None]:
    if len(actual) == 0 or len(pred) == 0:
        return None, None, None
    n = min(len(actual), len(pred))
    actual = actual[:n].astype(float)
    pred = pred[:n].astype(float)
    err = pred - actual
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    mask = np.abs(actual) > 1e-9
    mape = float(np.mean(np.abs(err[mask] / actual[mask])) * 100.0) if mask.any() else None
    return mae, rmse, mape


def _historical_average_predict(train: pd.DataFrame, rows: pd.DataFrame) -> np.ndarray:
    """Hierarchical fallback forecast.

    1. approach + weekday + slot
    2. approach + slot
    3. approach mean
    4. global mean
    """
    if train.empty or rows.empty:
        return np.zeros(len(rows), dtype=float)

    global_mean = float(train["vehicle_count"].mean())
    by_aws = train.groupby(["approach_no", "weekday", "slot"])["vehicle_count"].mean().to_dict()
    by_as = train.groupby(["approach_no", "slot"])["vehicle_count"].mean().to_dict()
    by_a = train.groupby("approach_no")["vehicle_count"].mean().to_dict()

    preds: list[float] = []
    for r in rows.itertuples(index=False):
        value = by_aws.get(
            (int(r.approach_no), int(r.weekday), int(r.slot)),
            by_as.get(
                (int(r.approach_no), int(r.slot)),
                by_a.get(int(r.approach_no), global_mean),
            ),
        )
        preds.append(float(value))
    return np.asarray(preds, dtype=float)


def _add_target(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    steps = max(1, int(horizon / 15))
    data = df.copy()
    data["target"] = data.groupby(["intersection_id", "approach_no"])["vehicle_count"].shift(-steps)
    return data.dropna(subset=["target"])


def _time_based_split(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split by time while keeping all approaches represented."""
    if data.empty:
        return data, data
    unique_times = sorted(data["interval_start"].dropna().unique())
    if len(unique_times) < 5:
        cutoff_time = data["interval_start"].quantile(0.8)
    else:
        cutoff_time = unique_times[max(1, int(len(unique_times) * 0.8) - 1)]
    train = data[data["interval_start"] <= cutoff_time].copy()
    test = data[data["interval_start"] > cutoff_time].copy()
    if test.empty:
        cutoff = int(len(data) * 0.8)
        train = data.iloc[:cutoff].copy()
        test = data.iloc[cutoff:].copy()
    return train, test


def evaluate_forecast_models(db: Session, horizons: list[int], model_name: str = "historical_average") -> list[ForecastMetric]:
    base = _load_approach_counts(db)
    if base.empty:
        return [ForecastMetric(h, model_name, None, None, None, 0, "No detector count data available.") for h in horizons]

    df = _feature_frame(base)
    metrics: list[ForecastMetric] = []
    features = ["approach_no", "hour", "minute", "weekday", "slot", "lag_1", "lag_2", "lag_4", "rolling_4", "rolling_8"]

    for horizon in horizons:
        data = _add_target(df, horizon)
        if len(data) < 24:
            metrics.append(ForecastMetric(horizon, model_name, None, None, None, int(len(data)), "Insufficient data for reliable back-test."))
            continue

        train, test = _time_based_split(data)
        note = "Historical average back-test completed."

        if model_name == "gradient_boosting" and len(train) >= 50:
            try:
                from sklearn.ensemble import HistGradientBoostingRegressor
                train_ml = train.dropna(subset=features + ["target"])
                test_ml = test.dropna(subset=features + ["target"])
                if len(train_ml) < 30 or len(test_ml) < 5:
                    pred = _historical_average_predict(train, test)
                    actual = test["target"].to_numpy(dtype=float)
                    note = "Fell back to historical average because lag features were insufficient."
                else:
                    model = HistGradientBoostingRegressor(max_iter=100, learning_rate=0.08, random_state=42)
                    model.fit(train_ml[features], train_ml["target"])
                    pred = np.maximum(model.predict(test_ml[features]), 0.0)
                    actual = test_ml["target"].to_numpy(dtype=float)
                    note = "Gradient boosting back-test completed."
            except Exception as exc:
                pred = _historical_average_predict(train, test)
                actual = test["target"].to_numpy(dtype=float)
                note = f"Fell back to historical average because gradient boosting failed: {exc}"
        else:
            pred = _historical_average_predict(train, test)
            actual = test["target"].to_numpy(dtype=float)

        mae, rmse, mape = _safe_metrics(actual, pred)
        metrics.append(ForecastMetric(horizon, model_name, mae, rmse, mape, int(len(actual)), note))
    return metrics


def generate_forecasts(db: Session, horizons: list[int], model_name: str = "historical_average") -> dict[str, Any]:
    base = _load_approach_counts(db)
    if base.empty:
        raise ValueError("No detector count data available. Import detector logs before running forecasts.")

    df = _feature_frame(base)
    latest_by_approach = df.sort_values("interval_start").groupby(["intersection_id", "approach_no"], as_index=False).tail(1).copy()
    if latest_by_approach.empty:
        raise ValueError("No latest approach records found. Please check imported detector data.")

    metrics = evaluate_forecast_models(db, horizons, model_name)
    generated_rows = 0
    features = ["approach_no", "hour", "minute", "weekday", "slot", "lag_1", "lag_2", "lag_4", "rolling_4", "rolling_8"]

    for horizon in horizons:
        train = _add_target(df, horizon)
        pred_latest: np.ndarray
        used_model = model_name

        if model_name == "gradient_boosting" and len(train) >= 50:
            try:
                from sklearn.ensemble import HistGradientBoostingRegressor
                train_ml = train.dropna(subset=features + ["target"])
                latest_ml = latest_by_approach.dropna(subset=features)
                if len(train_ml) >= 30 and len(latest_ml) == len(latest_by_approach):
                    model = HistGradientBoostingRegressor(max_iter=100, learning_rate=0.08, random_state=42)
                    model.fit(train_ml[features], train_ml["target"])
                    pred_latest = np.maximum(model.predict(latest_by_approach[features]), 0.0)
                else:
                    pred_latest = _historical_average_predict(df, latest_by_approach)
                    used_model = "historical_average_fallback"
            except Exception:
                pred_latest = _historical_average_predict(df, latest_by_approach)
                used_model = "historical_average_fallback"
        else:
            pred_latest = _historical_average_predict(df, latest_by_approach)

        metric = next((m for m in metrics if m.horizon_minutes == horizon), None)
        for row, pred in zip(latest_by_approach.itertuples(index=False), pred_latest):
            target_time = row.interval_start + timedelta(minutes=int(horizon))
            # Convert pandas Timestamp to Python datetime for psycopg/SQLAlchemy.
            target_dt = target_time.to_pydatetime() if hasattr(target_time, "to_pydatetime") else target_time
            db.execute(text("""
                INSERT INTO forecast_results(
                  target_time, horizon_minutes, intersection_id, approach_no, detector_no,
                  model_name, predicted_count, actual_count, mae, rmse, mape
                ) VALUES (
                  :target_time, :horizon_minutes, :intersection_id, :approach_no, NULL,
                  :model_name, :predicted_count, NULL, :mae, :rmse, :mape
                )
            """), {
                "target_time": target_dt,
                "horizon_minutes": int(horizon),
                "intersection_id": int(row.intersection_id),
                "approach_no": int(row.approach_no),
                "model_name": used_model,
                "predicted_count": float(round(float(pred), 2)),
                "mae": float(metric.mae) if metric and metric.mae is not None else None,
                "rmse": float(metric.rmse) if metric and metric.rmse is not None else None,
                "mape": float(metric.mape) if metric and metric.mape is not None else None,
            })
            generated_rows += 1

    db.commit()
    return {
        "model_name": model_name,
        "horizons": [int(h) for h in horizons],
        "forecast_rows_created": int(generated_rows),
        "metrics": [m.__dict__ for m in metrics],
    }

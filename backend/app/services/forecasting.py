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
    rows = db.execute(text("""
        SELECT intersection_id, approach_no, interval_start,
               SUM(vehicle_count)::float AS vehicle_count
        FROM detector_counts
        GROUP BY intersection_id, approach_no, interval_start
        ORDER BY approach_no, interval_start
    """)).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["interval_start"] = pd.to_datetime(df["interval_start"], utc=True)
    df["vehicle_count"] = pd.to_numeric(df["vehicle_count"], errors="coerce").fillna(0.0)
    return df


def _feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["intersection_id", "approach_no", "interval_start"]).copy()
    out["hour"] = out["interval_start"].dt.hour
    out["minute"] = out["interval_start"].dt.minute
    out["weekday"] = out["interval_start"].dt.weekday
    out["slot"] = out["hour"] * 4 + (out["minute"] // 15)
    grp = out.groupby(["intersection_id", "approach_no"], group_keys=False)
    out["lag_1"] = grp["vehicle_count"].shift(1)
    out["lag_2"] = grp["vehicle_count"].shift(2)
    out["lag_4"] = grp["vehicle_count"].shift(4)
    out["rolling_4"] = grp["vehicle_count"].shift(1).rolling(4, min_periods=1).mean().reset_index(level=[0,1], drop=True)
    out["rolling_8"] = grp["vehicle_count"].shift(1).rolling(8, min_periods=1).mean().reset_index(level=[0,1], drop=True)
    return out


def _safe_metrics(actual: np.ndarray, pred: np.ndarray) -> tuple[float | None, float | None, float | None]:
    if len(actual) == 0:
        return None, None, None
    err = pred - actual
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    mask = np.abs(actual) > 1e-9
    mape = float(np.mean(np.abs(err[mask] / actual[mask])) * 100.0) if mask.any() else None
    return mae, rmse, mape


def _historical_average_predict(train: pd.DataFrame, rows: pd.DataFrame) -> np.ndarray:
    # Hierarchical fallback: approach + weekday + slot, then approach + slot, then approach mean, then global mean.
    global_mean = float(train["vehicle_count"].mean()) if not train.empty else 0.0
    by_aws = train.groupby(["approach_no", "weekday", "slot"])["vehicle_count"].mean().to_dict()
    by_as = train.groupby(["approach_no", "slot"])["vehicle_count"].mean().to_dict()
    by_a = train.groupby("approach_no")["vehicle_count"].mean().to_dict()
    preds = []
    for r in rows.itertuples(index=False):
        preds.append(
            by_aws.get((r.approach_no, r.weekday, r.slot),
                       by_as.get((r.approach_no, r.slot),
                                 by_a.get(r.approach_no, global_mean)))
        )
    return np.asarray(preds, dtype=float)


def evaluate_forecast_models(db: Session, horizons: list[int], model_name: str = "historical_average") -> list[ForecastMetric]:
    base = _load_approach_counts(db)
    if base.empty:
        return [ForecastMetric(h, model_name, None, None, None, 0, "No detector count data available.") for h in horizons]
    df = _feature_frame(base)
    metrics: list[ForecastMetric] = []
    for horizon in horizons:
        steps = max(1, int(horizon / 15))
        data = df.copy()
        data["target"] = data.groupby(["intersection_id", "approach_no"])["vehicle_count"].shift(-steps)
        data = data.dropna(subset=["target"])
        if len(data) < 24:
            metrics.append(ForecastMetric(horizon, model_name, None, None, None, int(len(data)), "Insufficient data for reliable back-test."))
            continue
        cutoff = int(len(data) * 0.8)
        train = data.iloc[:cutoff].copy()
        test = data.iloc[cutoff:].copy()
        if model_name == "gradient_boosting" and len(train) >= 50:
            try:
                from sklearn.ensemble import HistGradientBoostingRegressor
                features = ["approach_no", "hour", "minute", "weekday", "slot", "lag_1", "lag_2", "lag_4", "rolling_4", "rolling_8"]
                train_ml = train.dropna(subset=features)
                test_ml = test.dropna(subset=features)
                if len(train_ml) < 30 or len(test_ml) < 5:
                    pred = _historical_average_predict(train, test)
                    actual = test["target"].to_numpy(dtype=float)
                    note = "Fell back to historical average because lag features were insufficient."
                else:
                    model = HistGradientBoostingRegressor(max_iter=100, learning_rate=0.08, random_state=42)
                    model.fit(train_ml[features], train_ml["target"])
                    pred = model.predict(test_ml[features])
                    actual = test_ml["target"].to_numpy(dtype=float)
                    note = "Gradient boosting back-test completed."
            except Exception as exc:  # pragma: no cover - runtime fallback
                pred = _historical_average_predict(train, test)
                actual = test["target"].to_numpy(dtype=float)
                note = f"Fell back to historical average: {exc}"
        else:
            pred = _historical_average_predict(train, test)
            actual = test["target"].to_numpy(dtype=float)
            note = "Historical average back-test completed."
        mae, rmse, mape = _safe_metrics(actual, pred)
        metrics.append(ForecastMetric(horizon, model_name, mae, rmse, mape, int(len(actual)), note))
    return metrics


def generate_forecasts(db: Session, horizons: list[int], model_name: str = "historical_average") -> dict[str, Any]:
    base = _load_approach_counts(db)
    if base.empty:
        raise ValueError("No detector count data available. Import detector logs before running forecasts.")
    df = _feature_frame(base)
    latest_by_approach = df.sort_values("interval_start").groupby(["intersection_id", "approach_no"], as_index=False).tail(1)
    metrics = evaluate_forecast_models(db, horizons, model_name)
    generated_rows = 0

    # Fit optional ML model per horizon using all available rows. Historical average is still used as fallback.
    for horizon in horizons:
        steps = max(1, int(horizon / 15))
        train = df.copy()
        train["target"] = train.groupby(["intersection_id", "approach_no"])["vehicle_count"].shift(-steps)
        train = train.dropna(subset=["target"])
        pred_latest: np.ndarray
        used_model = model_name
        if model_name == "gradient_boosting" and len(train) >= 50:
            try:
                from sklearn.ensemble import HistGradientBoostingRegressor
                features = ["approach_no", "hour", "minute", "weekday", "slot", "lag_1", "lag_2", "lag_4", "rolling_4", "rolling_8"]
                train_ml = train.dropna(subset=features)
                latest_ml = latest_by_approach.copy()
                if len(train_ml) >= 30 and latest_ml[features].notna().all(axis=None):
                    model = HistGradientBoostingRegressor(max_iter=100, learning_rate=0.08, random_state=42)
                    model.fit(train_ml[features], train_ml["target"])
                    pred_latest = np.maximum(model.predict(latest_ml[features]), 0.0)
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
            target_time = row.interval_start + timedelta(minutes=horizon)
            db.execute(text("""
                INSERT INTO forecast_results(
                  target_time, horizon_minutes, intersection_id, approach_no, detector_no,
                  model_name, predicted_count, actual_count, mae, rmse, mape
                ) VALUES (
                  :target_time, :horizon_minutes, :intersection_id, :approach_no, NULL,
                  :model_name, :predicted_count, NULL, :mae, :rmse, :mape
                )
            """), {
                "target_time": target_time.to_pydatetime(),
                "horizon_minutes": horizon,
                "intersection_id": int(row.intersection_id),
                "approach_no": int(row.approach_no),
                "model_name": used_model,
                "predicted_count": float(round(float(pred), 2)),
                "mae": metric.mae if metric else None,
                "rmse": metric.rmse if metric else None,
                "mape": metric.mape if metric else None,
            })
            generated_rows += 1

    db.commit()
    return {
        "model_name": model_name,
        "horizons": horizons,
        "forecast_rows_created": generated_rows,
        "metrics": [m.__dict__ for m in metrics],
    }

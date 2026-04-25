from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def generate_signal_recommendations(db: Session) -> dict:
    latest_generated = db.execute(text("SELECT max(generated_at) FROM forecast_results")).scalar_one_or_none()
    if not latest_generated:
        raise ValueError("No forecast results available. Run forecasting before generating recommendations.")

    rows = db.execute(text("""
        WITH baseline AS (
          SELECT approach_no, avg(vehicle_count)::float AS avg_count
          FROM detector_counts
          GROUP BY approach_no
        )
        SELECT fr.target_time, fr.horizon_minutes, fr.approach_no, fr.predicted_count::float AS predicted_count,
               COALESCE(b.avg_count, 0)::float AS baseline_count,
               fr.model_name, fr.mae, fr.rmse, fr.mape
        FROM forecast_results fr
        LEFT JOIN baseline b ON b.approach_no = fr.approach_no
        WHERE fr.generated_at = :generated_at
        ORDER BY fr.horizon_minutes, fr.approach_no
    """), {"generated_at": latest_generated}).mappings().all()

    created = 0
    for r in rows:
        predicted = float(r["predicted_count"] or 0)
        baseline = float(r["baseline_count"] or 0)
        ratio = predicted / baseline if baseline > 0 else 1.0
        approach = int(r["approach_no"])
        phase_no = approach  # Demo mapping. Replace after GAM confirms phase-to-approach relationship.
        if ratio >= 1.30:
            recommendation = f"Extend green time for Phase {phase_no} / Approach {approach} by 5–10 seconds during the target interval."
            reason = f"Forecast demand is {ratio:.2f} times the historical average for this approach."
            confidence = min(0.9, 0.55 + (ratio - 1.0) / 2.0)
        elif ratio <= 0.70:
            recommendation = f"Consider reducing green time for Phase {phase_no} / Approach {approach} by 5 seconds, subject to minimum green constraints."
            reason = f"Forecast demand is only {ratio:.2f} times the historical average for this approach."
            confidence = min(0.85, 0.55 + (1.0 - ratio) / 2.0)
        else:
            recommendation = f"Maintain current signal timing for Phase {phase_no} / Approach {approach}."
            reason = f"Forecast demand is close to the historical average: ratio {ratio:.2f}."
            confidence = 0.60

        if r["horizon_minutes"] >= 60:
            reason += " This is a longer-horizon advisory and should be reviewed more cautiously."
            confidence = max(0.40, confidence - 0.05)

        db.execute(text("""
            INSERT INTO signal_recommendations(target_time, phase_no, approach_no, recommendation, reason, confidence, status)
            VALUES (:target_time, :phase_no, :approach_no, :recommendation, :reason, :confidence, 'evaluation_only')
        """), {
            "target_time": r["target_time"],
            "phase_no": phase_no,
            "approach_no": approach,
            "recommendation": recommendation,
            "reason": reason,
            "confidence": round(float(confidence), 3),
        })
        created += 1
    db.commit()
    return {"recommendations_created": created, "source_forecast_generated_at": latest_generated.isoformat()}

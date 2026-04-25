from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import APP_TITLE, DATA_ROOT, FRONTEND_ROOT
from .database import get_db
from .services.detector_log_parser import parse_detector_log
from .services.signal_log_parser import parse_signal_log
from .services.video_probe import probe_video
from .services.forecasting import evaluate_forecast_models, generate_forecasts
from .services.recommendation import generate_signal_recommendations

app = FastAPI(title=APP_TITLE, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_ROOT.mkdir(parents=True, exist_ok=True)
for sub in ["input/detector_logs", "input/signal_logs", "input/videos", "output/snapshots", "output/clips", "output/reports"]:
    (DATA_ROOT / sub).mkdir(parents=True, exist_ok=True)

if FRONTEND_ROOT.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_ROOT)), name="static")


def _save_upload(upload: UploadFile, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.filename or "upload.bin").name
    target = target_dir / safe_name
    suffix_no = 1
    while target.exists():
        target = target_dir / f"{Path(safe_name).stem}_{suffix_no}{Path(safe_name).suffix}"
        suffix_no += 1
    with target.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return target


def _create_ingestion_file(db: Session, file_type: str, original_filename: str, stored_path: str) -> int:
    row = db.execute(
        text(
            """
            INSERT INTO ingestion_files(file_type, original_filename, stored_path, status)
            VALUES (:file_type, :original_filename, :stored_path, 'uploaded')
            RETURNING id
            """
        ),
        {"file_type": file_type, "original_filename": original_filename, "stored_path": stored_path},
    ).one()
    db.commit()
    return int(row.id)


@app.get("/")
def index():
    index_path = FRONTEND_ROOT / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": APP_TITLE, "docs": "/docs"}


@app.get("/api/health")
def health(db: Annotated[Session, Depends(get_db)]):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@app.get("/api/summary")
def summary(db: Annotated[Session, Depends(get_db)]):
    detector_rows = db.execute(text("SELECT count(*) FROM detector_counts")).scalar_one()
    signal_rows = db.execute(text("SELECT count(*) FROM signal_events")).scalar_one()
    videos = db.execute(text("SELECT count(*) FROM video_sources")).scalar_one()
    incidents = db.execute(text("SELECT count(*) FROM incident_events")).scalar_one()
    latest_count = db.execute(text("SELECT max(interval_start) FROM detector_counts")).scalar_one_or_none()
    latest_signal = db.execute(text("SELECT max(event_time) FROM signal_events")).scalar_one_or_none()
    return {
        "detector_count_records": detector_rows,
        "signal_event_records": signal_rows,
        "video_sources": videos,
        "incident_events": incidents,
        "latest_detector_interval": latest_count.isoformat() if latest_count else None,
        "latest_signal_event": latest_signal.isoformat() if latest_signal else None,
    }


@app.post("/api/import/detector-log")
def import_detector_log(file: Annotated[UploadFile, File()], db: Annotated[Session, Depends(get_db)]):
    path = _save_upload(file, DATA_ROOT / "input/detector_logs")
    file_id = _create_ingestion_file(db, "detector_log", file.filename or path.name, str(path))
    try:
        records = parse_detector_log(path)
        imported = 0
        for r in records:
            intersection_id = db.execute(text("SELECT id FROM intersections WHERE code=:code"), {"code": r["intersection_code"]}).scalar_one_or_none()
            if not intersection_id:
                intersection_id = db.execute(
                    text("INSERT INTO intersections(code, name) VALUES (:code, :name) RETURNING id"),
                    {"code": r["intersection_code"], "name": f"Intersection {r['intersection_code']}"},
                ).scalar_one()
            db.execute(
                text(
                    """
                    INSERT INTO detector_counts(
                      intersection_id, approach_no, detector_no, interval_start,
                      interval_minutes, vehicle_count, source_file_id, quality_flag, raw_value
                    ) VALUES (
                      :intersection_id, :approach_no, :detector_no, :interval_start,
                      :interval_minutes, :vehicle_count, :source_file_id, :quality_flag, :raw_value
                    ) ON CONFLICT DO NOTHING
                    """
                ),
                {**r, "intersection_id": intersection_id, "source_file_id": file_id},
            )
            imported += 1
        db.execute(text("UPDATE ingestion_files SET status='imported', records_imported=:n WHERE id=:id"), {"n": imported, "id": file_id})
        db.commit()
        return {"file_id": file_id, "records_parsed": len(records), "records_imported_attempted": imported}
    except Exception as e:
        db.execute(text("UPDATE ingestion_files SET status='failed', error_message=:err WHERE id=:id"), {"err": str(e), "id": file_id})
        db.commit()
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/import/signal-log")
def import_signal_log(file: Annotated[UploadFile, File()], db: Annotated[Session, Depends(get_db)]):
    path = _save_upload(file, DATA_ROOT / "input/signal_logs")
    file_id = _create_ingestion_file(db, "signal_log", file.filename or path.name, str(path))
    try:
        records = parse_signal_log(path)
        imported = 0
        for r in records:
            intersection_id = db.execute(text("SELECT id FROM intersections WHERE code=:code"), {"code": r["intersection_code"]}).scalar_one_or_none()
            db.execute(
                text(
                    """
                    INSERT INTO signal_events(
                      intersection_id, intersection_code, event_time, phase_no, signal_state, source_file_id, raw_line
                    ) VALUES (
                      :intersection_id, :intersection_code, :event_time, :phase_no, :signal_state, :source_file_id, :raw_line
                    )
                    """
                ),
                {**r, "intersection_id": intersection_id, "source_file_id": file_id},
            )
            imported += 1
        db.execute(text("UPDATE ingestion_files SET status='imported', records_imported=:n WHERE id=:id"), {"n": imported, "id": file_id})
        db.commit()
        return {"file_id": file_id, "records_parsed": len(records), "records_imported": imported}
    except Exception as e:
        db.execute(text("UPDATE ingestion_files SET status='failed', error_message=:err WHERE id=:id"), {"err": str(e), "id": file_id})
        db.commit()
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/import/video")
def import_video(file: Annotated[UploadFile, File()], db: Annotated[Session, Depends(get_db)]):
    path = _save_upload(file, DATA_ROOT / "input/videos")
    file_id = _create_ingestion_file(db, "video", file.filename or path.name, str(path))
    try:
        meta = probe_video(path)
        row = db.execute(
            text(
                """
                INSERT INTO video_sources(camera_code, source_type, source_uri, width, height, fps, duration_seconds, frame_count, ingestion_file_id)
                VALUES ('CAM-1', 'file', :uri, :width, :height, :fps, :duration_seconds, :frame_count, :file_id)
                RETURNING id
                """
            ),
            {**meta, "uri": str(path), "file_id": file_id},
        ).one()
        db.execute(text("UPDATE ingestion_files SET status='imported', records_imported=1 WHERE id=:id"), {"id": file_id})
        db.commit()
        return {"video_source_id": row.id, **meta}
    except Exception as e:
        db.execute(text("UPDATE ingestion_files SET status='failed', error_message=:err WHERE id=:id"), {"err": str(e), "id": file_id})
        db.commit()
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/detector-counts")
def detector_counts(db: Annotated[Session, Depends(get_db)], limit: int = 500):
    rows = db.execute(
        text(
            """
            SELECT interval_start, approach_no, detector_no, vehicle_count, quality_flag
            FROM detector_counts
            ORDER BY interval_start DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/detector-chart")
def detector_chart(db: Annotated[Session, Depends(get_db)], approach_no: int | None = None, detector_no: int | None = None):
    where = []
    params = {}
    if approach_no is not None:
        where.append("approach_no=:approach_no")
        params["approach_no"] = approach_no
    if detector_no is not None:
        where.append("detector_no=:detector_no")
        params["detector_no"] = detector_no
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = db.execute(
        text(
            f"""
            SELECT interval_start, sum(vehicle_count) AS vehicle_count
            FROM detector_counts
            {where_sql}
            GROUP BY interval_start
            ORDER BY interval_start
            LIMIT 2000
            """
        ),
        params,
    ).mappings().all()
    return [{"time": r["interval_start"].isoformat(), "vehicle_count": float(r["vehicle_count"])} for r in rows]


@app.get("/api/signal-events")
def signal_events(db: Annotated[Session, Depends(get_db)], limit: int = 500):
    rows = db.execute(
        text(
            """
            SELECT event_time, intersection_code, phase_no, signal_state, raw_line
            FROM signal_events
            ORDER BY event_time DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/signal-phase-summary")
def signal_phase_summary(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(
        text(
            """
            SELECT phase_no, signal_state, count(*) AS event_count
            FROM signal_events
            GROUP BY phase_no, signal_state
            ORDER BY phase_no, signal_state
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/ingestion-files")
def ingestion_files(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(
        text(
            """
            SELECT id, file_type, original_filename, status, records_imported, error_message, uploaded_at
            FROM ingestion_files
            ORDER BY uploaded_at DESC
            LIMIT 100
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/mappings/detectors")
def detector_mappings(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        SELECT i.code AS intersection_code, d.id AS detector_id, d.approach_no, d.detector_no,
               COALESCE(a.name, 'Approach ' || d.approach_no) AS approach_name,
               d.lane_label, d.description
        FROM detectors d
        JOIN intersections i ON i.id = d.intersection_id
        LEFT JOIN approaches a ON a.intersection_id = d.intersection_id AND a.approach_no = d.approach_no
        ORDER BY d.approach_no, d.detector_no
    """)).mappings().all()
    return [dict(r) for r in rows]


@app.post("/api/mappings/detectors")
def upsert_detector_mapping(payload: dict, db: Annotated[Session, Depends(get_db)]):
    code = str(payload.get("intersection_code") or "806")
    approach_no = int(payload["approach_no"])
    detector_no = int(payload["detector_no"])
    lane_label = payload.get("lane_label")
    description = payload.get("description")
    approach_name = payload.get("approach_name")
    intersection_id = db.execute(text("SELECT id FROM intersections WHERE code=:code"), {"code": code}).scalar_one_or_none()
    if not intersection_id:
        intersection_id = db.execute(text("INSERT INTO intersections(code, name) VALUES (:code, :name) RETURNING id"), {"code": code, "name": f"Intersection {code}"}).scalar_one()
    if approach_name:
        db.execute(text("""
            INSERT INTO approaches(intersection_id, approach_no, name)
            VALUES (:intersection_id, :approach_no, :name)
            ON CONFLICT (intersection_id, approach_no) DO UPDATE SET name=EXCLUDED.name
        """), {"intersection_id": intersection_id, "approach_no": approach_no, "name": approach_name})
    db.execute(text("""
        INSERT INTO detectors(intersection_id, approach_no, detector_no, lane_label, description)
        VALUES (:intersection_id, :approach_no, :detector_no, :lane_label, :description)
        ON CONFLICT (intersection_id, approach_no, detector_no)
        DO UPDATE SET lane_label=EXCLUDED.lane_label, description=EXCLUDED.description
    """), {"intersection_id": intersection_id, "approach_no": approach_no, "detector_no": detector_no, "lane_label": lane_label, "description": description})
    db.commit()
    return {"status": "saved", "intersection_code": code, "approach_no": approach_no, "detector_no": detector_no}


@app.get("/api/analytics/daily-summary")
def daily_summary(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        SELECT date_trunc('day', dc.interval_start) AS day,
               dc.approach_no,
               COALESCE(a.name, 'Approach ' || dc.approach_no) AS approach_name,
               sum(dc.vehicle_count) AS total_count,
               avg(dc.vehicle_count)::numeric(10,2) AS avg_15min_count,
               max(dc.vehicle_count) AS max_15min_count,
               count(*) AS interval_records
        FROM detector_counts dc
        LEFT JOIN approaches a ON a.intersection_id=dc.intersection_id AND a.approach_no=dc.approach_no
        GROUP BY day, dc.approach_no, approach_name
        ORDER BY day DESC, dc.approach_no
        LIMIT 500
    """)).mappings().all()
    return [{**dict(r), "day": r["day"].date().isoformat()} for r in rows]


@app.get("/api/analytics/hourly-summary")
def hourly_summary(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        SELECT date_trunc('hour', interval_start) AS hour_start,
               approach_no,
               sum(vehicle_count) AS total_count,
               avg(vehicle_count)::numeric(10,2) AS avg_15min_count,
               count(*) AS interval_records
        FROM detector_counts
        GROUP BY hour_start, approach_no
        ORDER BY hour_start DESC, approach_no
        LIMIT 1000
    """)).mappings().all()
    return [{**dict(r), "hour_start": r["hour_start"].isoformat()} for r in rows]


@app.get("/api/analytics/peak-summary")
def peak_summary(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        WITH hourly AS (
          SELECT date_trunc('hour', interval_start) AS hour_start,
                 approach_no,
                 sum(vehicle_count) AS hourly_count
          FROM detector_counts
          GROUP BY hour_start, approach_no
        ), ranked AS (
          SELECT *, row_number() OVER (PARTITION BY approach_no ORDER BY hourly_count DESC, hour_start DESC) AS rn
          FROM hourly
        )
        SELECT approach_no, hour_start, hourly_count
        FROM ranked
        WHERE rn=1
        ORDER BY approach_no
    """)).mappings().all()
    return [{**dict(r), "hour_start": r["hour_start"].isoformat()} for r in rows]


@app.get("/api/analytics/missing-intervals")
def missing_intervals(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        WITH bounds AS (
          SELECT intersection_id, approach_no, detector_no,
                 min(interval_start) AS min_time, max(interval_start) AS max_time
          FROM detector_counts
          GROUP BY intersection_id, approach_no, detector_no
        ), expected AS (
          SELECT b.intersection_id, b.approach_no, b.detector_no,
                 generate_series(b.min_time, b.max_time, interval '15 minutes') AS expected_time
          FROM bounds b
        )
        SELECT e.approach_no, e.detector_no, e.expected_time
        FROM expected e
        LEFT JOIN detector_counts dc
          ON dc.intersection_id=e.intersection_id
         AND dc.approach_no=e.approach_no
         AND dc.detector_no=e.detector_no
         AND dc.interval_start=e.expected_time
        WHERE dc.id IS NULL
        ORDER BY e.expected_time DESC, e.approach_no, e.detector_no
        LIMIT 500
    """)).mappings().all()
    return [{**dict(r), "expected_time": r["expected_time"].isoformat()} for r in rows]


@app.get("/api/analytics/anomalies")
def detector_anomalies(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        WITH stats AS (
          SELECT approach_no, detector_no,
                 avg(vehicle_count) AS avg_count,
                 stddev_pop(vehicle_count) AS std_count
          FROM detector_counts
          GROUP BY approach_no, detector_no
        )
        SELECT dc.interval_start, dc.approach_no, dc.detector_no, dc.vehicle_count,
               CASE
                 WHEN dc.quality_flag <> 'ok' THEN dc.quality_flag
                 WHEN dc.vehicle_count = 0 THEN 'zero_count'
                 WHEN s.std_count IS NOT NULL AND s.std_count > 0
                      AND abs(dc.vehicle_count - s.avg_count) > 3 * s.std_count THEN 'statistical_outlier'
                 ELSE 'ok'
               END AS anomaly_type,
               round(s.avg_count::numeric, 2) AS detector_average,
               round(s.std_count::numeric, 2) AS detector_stddev
        FROM detector_counts dc
        JOIN stats s ON s.approach_no=dc.approach_no AND s.detector_no=dc.detector_no
        WHERE dc.quality_flag <> 'ok'
           OR dc.vehicle_count = 0
           OR (s.std_count IS NOT NULL AND s.std_count > 0 AND abs(dc.vehicle_count - s.avg_count) > 3 * s.std_count)
        ORDER BY dc.interval_start DESC
        LIMIT 500
    """)).mappings().all()
    return [{**dict(r), "interval_start": r["interval_start"].isoformat()} for r in rows]


@app.get("/api/analytics/signal-phase-durations")
def signal_phase_durations(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        WITH ordered AS (
          SELECT intersection_code, phase_no, signal_state, event_time,
                 lead(event_time) OVER (PARTITION BY intersection_code, phase_no ORDER BY event_time) AS next_time
          FROM signal_events
        ), durations AS (
          SELECT intersection_code, phase_no, signal_state,
                 extract(epoch from (next_time - event_time)) AS duration_seconds
          FROM ordered
          WHERE next_time IS NOT NULL
            AND next_time > event_time
            AND extract(epoch from (next_time - event_time)) < 3600
        )
        SELECT intersection_code, phase_no, signal_state,
               count(*) AS event_count,
               round(avg(duration_seconds)::numeric, 2) AS avg_seconds,
               round(min(duration_seconds)::numeric, 2) AS min_seconds,
               round(max(duration_seconds)::numeric, 2) AS max_seconds
        FROM durations
        GROUP BY intersection_code, phase_no, signal_state
        ORDER BY phase_no, signal_state
    """)).mappings().all()
    return [dict(r) for r in rows]


def _csv_response(filename: str, header: list[str], rows: list[dict]) -> Response:
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k) for k in header})
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/detector-counts.csv")
def export_detector_counts(db: Annotated[Session, Depends(get_db)]):
    rows = [dict(r) for r in db.execute(text("""
        SELECT dc.interval_start, i.code AS intersection_code, dc.approach_no,
               COALESCE(a.name, 'Approach ' || dc.approach_no) AS approach_name,
               dc.detector_no, d.lane_label, dc.vehicle_count, dc.interval_minutes, dc.quality_flag
        FROM detector_counts dc
        JOIN intersections i ON i.id=dc.intersection_id
        LEFT JOIN approaches a ON a.intersection_id=dc.intersection_id AND a.approach_no=dc.approach_no
        LEFT JOIN detectors d ON d.intersection_id=dc.intersection_id AND d.approach_no=dc.approach_no AND d.detector_no=dc.detector_no
        ORDER BY dc.interval_start, dc.approach_no, dc.detector_no
    """)).mappings().all()]
    for r in rows:
        r["interval_start"] = r["interval_start"].isoformat()
    return _csv_response("detector_counts_normalized.csv", [
        "interval_start", "intersection_code", "approach_no", "approach_name", "detector_no",
        "lane_label", "vehicle_count", "interval_minutes", "quality_flag"
    ], rows)


@app.get("/api/export/hourly-summary.csv")
def export_hourly_summary(db: Annotated[Session, Depends(get_db)]):
    rows = hourly_summary(db)
    return _csv_response("hourly_summary.csv", ["hour_start", "approach_no", "total_count", "avg_15min_count", "interval_records"], rows)
#test

# ---- Phase 3: Traffic flow forecasting and signal timing recommendations ----

def _parse_horizons(horizons: str) -> list[int]:
    values: list[int] = []
    for part in horizons.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            h = int(part)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Horizons must be comma-separated integers, e.g. 15,30,60") from exc
        if h <= 0 or h % 15 != 0:
            raise HTTPException(status_code=400, detail="Each horizon must be a positive multiple of 15 minutes.")
        values.append(h)
    return values or [15, 30, 60]

@app.post("/api/forecast/run")
def run_forecast(db: Annotated[Session, Depends(get_db)], horizons: str = "15,30,60", model_name: str = "historical_average"):
    if model_name not in {"historical_average", "gradient_boosting"}:
        raise HTTPException(status_code=400, detail="model_name must be historical_average or gradient_boosting")
    try:
        return generate_forecasts(db, _parse_horizons(horizons), model_name=model_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

@app.get("/api/forecast/evaluation")
def forecast_evaluation(db: Annotated[Session, Depends(get_db)], horizons: str = "15,30,60", model_name: str = "historical_average"):
    if model_name not in {"historical_average", "gradient_boosting"}:
        raise HTTPException(status_code=400, detail="model_name must be historical_average or gradient_boosting")
    metrics = evaluate_forecast_models(db, _parse_horizons(horizons), model_name=model_name)
    return [m.__dict__ for m in metrics]

@app.get("/api/forecast/results")
def forecast_results(db: Annotated[Session, Depends(get_db)], limit: int = 200):
    rows = db.execute(text("""
        SELECT fr.id, fr.generated_at, fr.target_time, fr.horizon_minutes,
               i.code AS intersection_code, fr.approach_no, fr.detector_no,
               fr.model_name, round(fr.predicted_count::numeric, 2) AS predicted_count,
               fr.actual_count, round(fr.mae::numeric, 2) AS mae,
               round(fr.rmse::numeric, 2) AS rmse, round(fr.mape::numeric, 2) AS mape
        FROM forecast_results fr
        LEFT JOIN intersections i ON i.id = fr.intersection_id
        ORDER BY fr.generated_at DESC, fr.horizon_minutes, fr.approach_no
        LIMIT :limit
    """), {"limit": limit}).mappings().all()
    return [{**dict(r), "generated_at": r["generated_at"].isoformat(), "target_time": r["target_time"].isoformat()} for r in rows]

@app.get("/api/forecast/chart")
def forecast_chart(db: Annotated[Session, Depends(get_db)]):
    latest_generated = db.execute(text("SELECT max(generated_at) FROM forecast_results")).scalar_one_or_none()
    if not latest_generated:
        return []
    rows = db.execute(text("""
        SELECT target_time, horizon_minutes, approach_no, predicted_count
        FROM forecast_results
        WHERE generated_at = :generated_at
        ORDER BY horizon_minutes, approach_no
    """), {"generated_at": latest_generated}).mappings().all()
    return [{**dict(r), "target_time": r["target_time"].isoformat(), "predicted_count": float(r["predicted_count"])} for r in rows]

@app.post("/api/recommendations/generate")
def generate_recommendations(db: Annotated[Session, Depends(get_db)]):
    try:
        return generate_signal_recommendations(db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

@app.get("/api/recommendations")
def recommendations(db: Annotated[Session, Depends(get_db)], limit: int = 200):
    rows = db.execute(text("""
        SELECT id, generated_at, target_time, phase_no, approach_no,
               recommendation, reason, round(confidence::numeric, 3) AS confidence, status
        FROM signal_recommendations
        ORDER BY generated_at DESC, target_time, approach_no
        LIMIT :limit
    """), {"limit": limit}).mappings().all()
    return [{**dict(r), "generated_at": r["generated_at"].isoformat(), "target_time": r["target_time"].isoformat() if r["target_time"] else None} for r in rows]

@app.get("/api/export/forecast-results.csv")
def export_forecast_results(db: Annotated[Session, Depends(get_db)]):
    rows = [dict(r) for r in db.execute(text("""
        SELECT generated_at, target_time, horizon_minutes, approach_no, detector_no,
               model_name, predicted_count, actual_count, mae, rmse, mape
        FROM forecast_results
        ORDER BY generated_at DESC, horizon_minutes, approach_no
    """)).mappings().all()]
    return _csv_response(
        "forecast_results.csv",
        ["generated_at", "target_time", "horizon_minutes", "approach_no", "detector_no", "model_name", "predicted_count", "actual_count", "mae", "rmse", "mape"],
        rows,
    )

@app.get("/api/export/signal-recommendations.csv")
def export_signal_recommendations(db: Annotated[Session, Depends(get_db)]):
    rows = [dict(r) for r in db.execute(text("""
        SELECT generated_at, target_time, phase_no, approach_no, recommendation, reason, confidence, status
        FROM signal_recommendations
        ORDER BY generated_at DESC, target_time, approach_no
    """)).mappings().all()]
    return _csv_response(
        "signal_recommendations.csv",
        ["generated_at", "target_time", "phase_no", "approach_no", "recommendation", "reason", "confidence", "status"],
        rows,
    )

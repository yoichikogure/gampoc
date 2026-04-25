from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import APP_TITLE, DATA_ROOT, FRONTEND_ROOT
from .database import get_db
from .services.detector_log_parser import parse_detector_log
from .services.signal_log_parser import parse_signal_log
from .services.video_probe import probe_video

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

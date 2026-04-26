from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Annotated

import cv2
import numpy as np

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import APP_TITLE, DATA_ROOT, FRONTEND_ROOT, DATABASE_URL
from .database import get_db
from .services.detector_log_parser import parse_detector_log
from .services.signal_log_parser import parse_signal_log
from .services.video_probe import probe_video
from .services.video_processing import detect_vehicle_candidates, sample_video_frames
from .services.incident_detection import generate_incident_candidates
from .services.forecasting import evaluate_forecast_models, generate_forecasts
from .services.recommendation import generate_signal_recommendations

app = FastAPI(title=APP_TITLE, version="0.6.0")
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


@app.on_event("startup")
def ensure_phase5_tables():
    # Allows Phase 5 code to run even when users reuse an existing earlier database volume.
    from sqlalchemy import create_engine
    from .config import DATABASE_URL
    engine = create_engine(DATABASE_URL, future=True)
    ddl = """
    CREATE TABLE IF NOT EXISTS video_frames (
      id BIGSERIAL PRIMARY KEY,
      video_source_id INT NOT NULL REFERENCES video_sources(id) ON DELETE CASCADE,
      frame_index BIGINT NOT NULL,
      frame_time_seconds NUMERIC,
      image_path TEXT,
      width INT,
      height INT,
      created_at TIMESTAMPTZ DEFAULT now(),
      UNIQUE(video_source_id, frame_index)
    );
    CREATE TABLE IF NOT EXISTS vehicle_detections (
      id BIGSERIAL PRIMARY KEY,
      video_source_id INT NOT NULL REFERENCES video_sources(id) ON DELETE CASCADE,
      frame_index BIGINT NOT NULL,
      frame_time_seconds NUMERIC,
      class_name TEXT NOT NULL,
      confidence NUMERIC,
      bbox_x INT,
      bbox_y INT,
      bbox_w INT,
      bbox_h INT,
      detection_method TEXT NOT NULL DEFAULT 'opencv_motion_fallback',
      created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_video_frames_source ON video_frames(video_source_id, frame_index);
    CREATE INDEX IF NOT EXISTS idx_vehicle_detections_source ON vehicle_detections(video_source_id, frame_index);
    ALTER TABLE vehicle_detections ADD COLUMN IF NOT EXISTS bbox_x INT;
    ALTER TABLE vehicle_detections ADD COLUMN IF NOT EXISTS bbox_y INT;
    ALTER TABLE vehicle_detections ADD COLUMN IF NOT EXISTS bbox_w INT;
    ALTER TABLE vehicle_detections ADD COLUMN IF NOT EXISTS bbox_h INT;
    ALTER TABLE vehicle_detections ADD COLUMN IF NOT EXISTS detection_method TEXT DEFAULT 'opencv_motion_fallback';
    ALTER TABLE video_frames ADD COLUMN IF NOT EXISTS image_path TEXT;
    CREATE TABLE IF NOT EXISTS incident_events (id BIGSERIAL PRIMARY KEY, event_time TIMESTAMPTZ NOT NULL DEFAULT now(), event_type TEXT NOT NULL, camera_code TEXT, zone_label TEXT, confidence NUMERIC, snapshot_path TEXT, clip_path TEXT, queue_length_estimate NUMERIC, review_status TEXT NOT NULL DEFAULT 'unreviewed', notes TEXT, created_at TIMESTAMPTZ DEFAULT now());
    ALTER TABLE incident_events ADD COLUMN IF NOT EXISTS video_source_id INT REFERENCES video_sources(id) ON DELETE SET NULL;
    ALTER TABLE incident_events ADD COLUMN IF NOT EXISTS frame_index BIGINT;
    ALTER TABLE incident_events ADD COLUMN IF NOT EXISTS frame_time_seconds NUMERIC;
    ALTER TABLE incident_events ADD COLUMN IF NOT EXISTS detection_method TEXT DEFAULT 'rule_based_phase5';
    CREATE INDEX IF NOT EXISTS idx_incident_events_video ON incident_events(video_source_id, frame_index);
    CREATE INDEX IF NOT EXISTS idx_incident_events_review ON incident_events(review_status, event_type);
    CREATE TABLE IF NOT EXISTS live_stream_health (
      video_source_id INT PRIMARY KEY REFERENCES video_sources(id) ON DELETE CASCADE,
      status TEXT NOT NULL DEFAULT 'unknown',
      connected BOOLEAN NOT NULL DEFAULT false,
      input_fps NUMERIC,
      output_fps NUMERIC,
      frames_read BIGINT NOT NULL DEFAULT 0,
      frames_sent BIGINT NOT NULL DEFAULT 0,
      detections_logged BIGINT NOT NULL DEFAULT 0,
      dropped_frames BIGINT NOT NULL DEFAULT 0,
      reconnect_count INT NOT NULL DEFAULT 0,
      last_frame_at TIMESTAMPTZ,
      last_error TEXT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_live_stream_health_updated ON live_stream_health(updated_at);
    """
    with engine.begin() as conn:
        for stmt in [x.strip() for x in ddl.split(";") if x.strip()]:
            conn.execute(text(stmt))



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


# ---- Real-time RTSP / HTTP video input and browser preview ----

def _make_status_jpeg(message: str, width: int = 960, height: int = 540) -> bytes:
    img = np.full((height, width, 3), 245, dtype=np.uint8)
    cv2.putText(img, message[:80], (40, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 40), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return buf.tobytes() if ok else b""


@app.post("/api/rtsp/register")
def register_rtsp_source(payload: dict, db: Annotated[Session, Depends(get_db)]):
    url = str(payload.get("rtsp_url") or payload.get("url") or "").strip()
    camera_code = str(payload.get("camera_code") or "CAM-RTSP-1").strip() or "CAM-RTSP-1"
    if not url:
        raise HTTPException(status_code=400, detail="rtsp_url is required")
    if not (url.lower().startswith("rtsp://") or url.lower().startswith("http://") or url.lower().startswith("https://")):
        raise HTTPException(status_code=400, detail="Video URL must start with rtsp://, http://, or https://")
    source_type = "rtsp" if url.lower().startswith("rtsp://") else "http"

    width = height = frame_count = None
    fps = duration_seconds = None
    probe_status = "not_checked"
    try:
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if cap.isOpened():
            width_val = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height_val = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps_val = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            width = width_val or None
            height = height_val or None
            fps = round(fps_val, 3) if fps_val > 0 else None
            probe_status = "opened"
        else:
            probe_status = "registered_but_not_opened"
        cap.release()
    except Exception as exc:
        probe_status = f"registered_probe_failed: {type(exc).__name__}: {exc}"

    row = db.execute(text("""
        INSERT INTO video_sources(camera_code, source_type, source_uri, width, height, fps, duration_seconds, frame_count)
        VALUES (:camera_code, :source_type, :source_uri, :width, :height, :fps, :duration_seconds, :frame_count)
        RETURNING id
    """), {
        "camera_code": camera_code, "source_type": source_type, "source_uri": url,
        "width": width, "height": height, "fps": fps,
        "duration_seconds": duration_seconds, "frame_count": frame_count,
    }).one()
    db.commit()
    return {
        "video_source_id": row.id, "camera_code": camera_code, "source_type": source_type,
        "stream_url": f"/api/videos/{row.id}/live.mjpg", "probe_status": probe_status,
        "width": width, "height": height, "fps": fps,
    }


def _stream_mjpeg_from_source(source_uri: str, fps_limit: float = 8.0, max_width: int = 960):
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-cache\r\n\r\n"
    fps_limit = max(1.0, min(float(fps_limit or 8.0), 20.0))
    frame_interval = 1.0 / fps_limit
    yield boundary + _make_status_jpeg("Connecting to video source...") + b"\r\n"
    while True:
        cap = cv2.VideoCapture(source_uri, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            yield boundary + _make_status_jpeg("Video source not available. Retrying in 5 seconds...") + b"\r\n"
            time.sleep(5)
            continue
        try:
            last_sent = 0.0
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                now = time.time()
                if now - last_sent < frame_interval:
                    continue
                last_sent = now
                h, w = frame.shape[:2]
                if max_width and w > max_width:
                    new_h = int(h * (max_width / w))
                    frame = cv2.resize(frame, (max_width, new_h), interpolation=cv2.INTER_AREA)
                cv2.putText(frame, time.strftime("%Y-%m-%d %H:%M:%S"), (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2, cv2.LINE_AA)
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ok:
                    yield boundary + buf.tobytes() + b"\r\n"
        finally:
            cap.release()
        yield boundary + _make_status_jpeg("Stream interrupted. Reconnecting in 5 seconds...") + b"\r\n"
        time.sleep(5)


@app.get("/api/videos/{video_source_id}/live.mjpg")
def live_mjpeg(video_source_id: int, db: Annotated[Session, Depends(get_db)], fps_limit: float = 8.0, max_width: int = 960):
    row = db.execute(text("SELECT source_uri, source_type FROM video_sources WHERE id=:id"), {"id": video_source_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Video source not found")
    if row["source_type"] not in {"rtsp", "http", "file"}:
        raise HTTPException(status_code=400, detail="Unsupported video source type")
    return StreamingResponse(
        _stream_mjpeg_from_source(row["source_uri"], fps_limit=fps_limit, max_width=max_width),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/videos/{video_source_id}/stream-info")
def stream_info(video_source_id: int, db: Annotated[Session, Depends(get_db)]):
    row = db.execute(text("""
        SELECT id, camera_code, source_type, source_uri, width, height, fps, created_at
        FROM video_sources WHERE id=:id
    """), {"id": video_source_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Video source not found")
    d = dict(row)
    d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
    d["browser_stream_url"] = f"/api/videos/{video_source_id}/live.mjpg"
    return d


# ---- Phase 6: real-time RTSP AI overlay, detection logging, and health ----

_PHASE6_ENGINE = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)


def _update_live_health(video_source_id: int, **values):
    payload = {
        "video_source_id": int(video_source_id),
        "status": values.get("status", "running"),
        "connected": bool(values.get("connected", False)),
        "input_fps": values.get("input_fps"),
        "output_fps": values.get("output_fps"),
        "frames_read": int(values.get("frames_read", 0) or 0),
        "frames_sent": int(values.get("frames_sent", 0) or 0),
        "detections_logged": int(values.get("detections_logged", 0) or 0),
        "dropped_frames": int(values.get("dropped_frames", 0) or 0),
        "reconnect_count": int(values.get("reconnect_count", 0) or 0),
        "last_error": values.get("last_error"),
    }
    try:
        with _PHASE6_ENGINE.begin() as conn:
            conn.execute(text("""
                INSERT INTO live_stream_health(
                  video_source_id, status, connected, input_fps, output_fps, frames_read,
                  frames_sent, detections_logged, dropped_frames, reconnect_count,
                  last_frame_at, last_error, updated_at
                ) VALUES (
                  :video_source_id, :status, :connected, :input_fps, :output_fps, :frames_read,
                  :frames_sent, :detections_logged, :dropped_frames, :reconnect_count,
                  CASE WHEN :connected THEN now() ELSE NULL END, :last_error, now()
                )
                ON CONFLICT (video_source_id) DO UPDATE SET
                  status=EXCLUDED.status,
                  connected=EXCLUDED.connected,
                  input_fps=EXCLUDED.input_fps,
                  output_fps=EXCLUDED.output_fps,
                  frames_read=EXCLUDED.frames_read,
                  frames_sent=EXCLUDED.frames_sent,
                  detections_logged=EXCLUDED.detections_logged,
                  dropped_frames=EXCLUDED.dropped_frames,
                  reconnect_count=EXCLUDED.reconnect_count,
                  last_frame_at=CASE WHEN EXCLUDED.connected THEN now() ELSE live_stream_health.last_frame_at END,
                  last_error=EXCLUDED.last_error,
                  updated_at=now()
            """), payload)
    except Exception:
        pass


def _detect_motion_boxes(frame, subtractor, min_area: int = 900, max_boxes: int = 12):
    fg = subtractor.apply(frame)
    fg = cv2.medianBlur(fg, 5)
    _, thresh = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    thresh = cv2.dilate(thresh, kernel, iterations=2)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        frame_area = frame.shape[0] * frame.shape[1]
        if area > frame_area * 0.35 or w < 12 or h < 12:
            continue
        boxes.append((int(x), int(y), int(w), int(h), float(area)))
    boxes.sort(key=lambda b: b[4], reverse=True)
    return boxes[:max_boxes]


def _log_live_detections(video_source_id: int, frame_index: int, frame_time_seconds: float, boxes, every_n: int = 10):
    if not boxes or frame_index % max(1, int(every_n)) != 0:
        return 0
    logged = 0
    try:
        with _PHASE6_ENGINE.begin() as conn:
            for x, y, w, h, area in boxes:
                confidence = min(0.95, max(0.25, area / 12000.0))
                conn.execute(text("""
                    INSERT INTO vehicle_detections(
                      video_source_id, frame_index, frame_time_seconds, class_name, confidence,
                      bbox_x, bbox_y, bbox_w, bbox_h, detection_method
                    ) VALUES (
                      :video_source_id, :frame_index, :frame_time_seconds, 'vehicle_candidate', :confidence,
                      :x, :y, :w, :h, 'opencv_rtsp_motion_phase6'
                    )
                """), {
                    "video_source_id": int(video_source_id),
                    "frame_index": int(frame_index),
                    "frame_time_seconds": float(frame_time_seconds),
                    "confidence": float(confidence),
                    "x": x, "y": y, "w": w, "h": h,
                })
                logged += 1
    except Exception:
        return 0
    return logged


def _stream_ai_mjpeg_from_source(video_source_id: int, source_uri: str, fps_limit: float = 5.0, max_width: int = 960, min_area: int = 900, log_every_n: int = 10):
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-cache\r\n\r\n"
    fps_limit = max(1.0, min(float(fps_limit or 5.0), 15.0))
    frame_interval = 1.0 / fps_limit
    reconnect_count = 0
    frames_read = frames_sent = detections_logged = dropped_frames = 0
    started_at = time.time()
    yield boundary + _make_status_jpeg("Connecting to RTSP AI stream...") + b"\r\n"
    while True:
        cap = cv2.VideoCapture(source_uri, cv2.CAP_FFMPEG)
        input_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) if cap.isOpened() else None
        if not cap.isOpened():
            reconnect_count += 1
            _update_live_health(video_source_id, status="reconnecting", connected=False, input_fps=input_fps, frames_read=frames_read, frames_sent=frames_sent, detections_logged=detections_logged, dropped_frames=dropped_frames, reconnect_count=reconnect_count, last_error="Could not open video source")
            yield boundary + _make_status_jpeg("AI stream source not available. Retrying in 5 seconds...") + b"\r\n"
            time.sleep(5)
            continue
        subtractor = cv2.createBackgroundSubtractorMOG2(history=120, varThreshold=32, detectShadows=True)
        last_sent = 0.0
        last_health = 0.0
        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    reconnect_count += 1
                    _update_live_health(video_source_id, status="interrupted", connected=False, input_fps=input_fps, frames_read=frames_read, frames_sent=frames_sent, detections_logged=detections_logged, dropped_frames=dropped_frames, reconnect_count=reconnect_count, last_error="Frame read failed")
                    break
                frames_read += 1
                now = time.time()
                if now - last_sent < frame_interval:
                    dropped_frames += 1
                    continue
                last_sent = now
                h0, w0 = frame.shape[:2]
                if max_width and w0 > max_width:
                    frame = cv2.resize(frame, (max_width, int(h0 * (max_width / w0))), interpolation=cv2.INTER_AREA)
                boxes = _detect_motion_boxes(frame, subtractor, min_area=int(min_area), max_boxes=12)
                detections_logged += _log_live_detections(video_source_id, frames_read, now - started_at, boxes, every_n=log_every_n)
                for x, y, w, h, area in boxes:
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 220, 0), 2)
                    cv2.putText(frame, "vehicle_candidate", (x, max(18, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 2, cv2.LINE_AA)
                status = f"PHASE 6 LIVE AI | boxes: {len(boxes)} | read: {frames_read} | logged: {detections_logged}"
                cv2.rectangle(frame, (0, 0), (frame.shape[1], 34), (0, 0, 0), -1)
                cv2.putText(frame, status, (12, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                if ok:
                    frames_sent += 1
                    yield boundary + buf.tobytes() + b"\r\n"
                if now - last_health > 2.0:
                    elapsed = max(now - started_at, 0.001)
                    _update_live_health(video_source_id, status="running_ai_overlay", connected=True, input_fps=input_fps, output_fps=round(frames_sent / elapsed, 3), frames_read=frames_read, frames_sent=frames_sent, detections_logged=detections_logged, dropped_frames=dropped_frames, reconnect_count=reconnect_count, last_error=None)
                    last_health = now
        finally:
            cap.release()
        yield boundary + _make_status_jpeg("AI stream interrupted. Reconnecting in 5 seconds...") + b"\r\n"
        time.sleep(5)


@app.get("/api/videos/{video_source_id}/live-ai.mjpg")
def live_ai_mjpeg(video_source_id: int, db: Annotated[Session, Depends(get_db)], fps_limit: float = 5.0, max_width: int = 960, min_area: int = 900, log_every_n: int = 10):
    row = db.execute(text("SELECT source_uri, source_type FROM video_sources WHERE id=:id"), {"id": video_source_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Video source not found")
    if row["source_type"] not in {"rtsp", "http", "file"}:
        raise HTTPException(status_code=400, detail="Unsupported video source type")
    return StreamingResponse(
        _stream_ai_mjpeg_from_source(video_source_id, row["source_uri"], fps_limit=fps_limit, max_width=max_width, min_area=min_area, log_every_n=log_every_n),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/videos/{video_source_id}/live-health")
def live_health(video_source_id: int, db: Annotated[Session, Depends(get_db)]):
    row = db.execute(text("""
        SELECT video_source_id, status, connected, input_fps, output_fps, frames_read,
               frames_sent, detections_logged, dropped_frames, reconnect_count,
               last_frame_at, last_error, updated_at
        FROM live_stream_health WHERE video_source_id=:id
    """), {"id": video_source_id}).mappings().first()
    if not row:
        return {"video_source_id": video_source_id, "status": "not_started", "connected": False}
    d = dict(row)
    for k in ["last_frame_at", "updated_at"]:
        d[k] = d[k].isoformat() if d.get(k) else None
    for k in ["input_fps", "output_fps"]:
        d[k] = float(d[k]) if d.get(k) is not None else None
    return d


@app.get("/api/videos/{video_source_id}/live-detections")
def live_detections(video_source_id: int, db: Annotated[Session, Depends(get_db)], limit: int = 200):
    rows = db.execute(text("""
        SELECT id, video_source_id, frame_index, frame_time_seconds, class_name,
               round(confidence::numeric, 3) AS confidence, bbox_x, bbox_y, bbox_w, bbox_h,
               detection_method, created_at
        FROM vehicle_detections
        WHERE video_source_id=:id AND detection_method='opencv_rtsp_motion_phase6'
        ORDER BY id DESC
        LIMIT :limit
    """), {"id": video_source_id, "limit": limit}).mappings().all()
    return [{**dict(r), "created_at": r["created_at"].isoformat() if r.get("created_at") else None} for r in rows]


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
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Forecasting failed: {type(e).__name__}: {e}") from e

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


# ---- Phase 4: video ingestion, frame sampling, and vehicle-candidate detection ----

@app.get("/api/videos")
def videos(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        SELECT vs.id, vs.camera_code, vs.source_type, vs.source_uri, vs.width, vs.height,
               round(vs.fps::numeric, 2) AS fps, round(vs.duration_seconds::numeric, 2) AS duration_seconds,
               vs.frame_count, vs.created_at,
               COALESCE(f.frame_samples, 0) AS frame_samples,
               COALESCE(d.detection_count, 0) AS detection_count
        FROM video_sources vs
        LEFT JOIN (SELECT video_source_id, count(*) AS frame_samples FROM video_frames GROUP BY video_source_id) f
          ON f.video_source_id = vs.id
        LEFT JOIN (SELECT video_source_id, count(*) AS detection_count FROM vehicle_detections GROUP BY video_source_id) d
          ON d.video_source_id = vs.id
        ORDER BY vs.created_at DESC
    """)).mappings().all()
    return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]


def _video_path_from_id(db: Session, video_source_id: int) -> Path:
    row = db.execute(text("SELECT source_uri, source_type FROM video_sources WHERE id=:id"), {"id": video_source_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Video source not found")
    if row["source_type"] != "file":
        raise HTTPException(status_code=400, detail="Phase 4 processing currently supports uploaded video files. RTSP registration will be processed in a later real-time phase.")
    path = Path(row["source_uri"])
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Video file is missing on disk: {path}")
    return path

@app.post("/api/videos/{video_source_id}/sample-frames")
def sample_frames(video_source_id: int, db: Annotated[Session, Depends(get_db)], every_seconds: float = 5.0, max_frames: int = 120):
    video_path = _video_path_from_id(db, video_source_id)
    out_dir = DATA_ROOT / "output" / "snapshots" / f"video_{video_source_id}"
    samples = sample_video_frames(video_path, out_dir, every_seconds=every_seconds, max_frames=max_frames)
    inserted = 0
    for f in samples:
        db.execute(text("""
            INSERT INTO video_frames(video_source_id, frame_index, frame_time_seconds, image_path, width, height)
            VALUES (:video_source_id, :frame_index, :frame_time_seconds, :image_path, :width, :height)
            ON CONFLICT (video_source_id, frame_index) DO UPDATE
            SET frame_time_seconds=EXCLUDED.frame_time_seconds, image_path=EXCLUDED.image_path,
                width=EXCLUDED.width, height=EXCLUDED.height
        """), {"video_source_id": video_source_id, **f.__dict__})
        inserted += 1
    db.commit()
    return {"video_source_id": video_source_id, "frames_sampled": inserted, "output_dir": str(out_dir)}

@app.post("/api/videos/{video_source_id}/detect-vehicles")
def detect_vehicles(video_source_id: int, db: Annotated[Session, Depends(get_db)], every_seconds: float = 1.0, max_frames: int = 600, min_area: int = 700):
    video_path = _video_path_from_id(db, video_source_id)
    detections = detect_vehicle_candidates(video_path, every_seconds=every_seconds, max_frames=max_frames, min_area=min_area)
    db.execute(text("DELETE FROM vehicle_detections WHERE video_source_id=:video_source_id"), {"video_source_id": video_source_id})
    for d in detections:
        db.execute(text("""
            INSERT INTO vehicle_detections(video_source_id, frame_index, frame_time_seconds, class_name, confidence,
                                           bbox_x, bbox_y, bbox_w, bbox_h, detection_method)
            VALUES (:video_source_id, :frame_index, :frame_time_seconds, :class_name, :confidence,
                    :x, :y, :w, :h, :method)
        """), {"video_source_id": video_source_id, **d.__dict__})
    db.commit()
    return {"video_source_id": video_source_id, "detections_created": len(detections), "method": "opencv_motion_fallback"}

@app.get("/api/videos/{video_source_id}/frames")
def video_frames(video_source_id: int, db: Annotated[Session, Depends(get_db)], limit: int = 50):
    rows = db.execute(text("""
        SELECT id, video_source_id, frame_index, frame_time_seconds, image_path, width, height, created_at
        FROM video_frames
        WHERE video_source_id=:video_source_id
        ORDER BY frame_index
        LIMIT :limit
    """), {"video_source_id": video_source_id, "limit": limit}).mappings().all()
    result = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat()
        d["image_url"] = f"/api/video-frames/{d['id']}/image"
        result.append(d)
    return result

@app.get("/api/video-frames/{frame_id}/image")
def video_frame_image(frame_id: int, db: Annotated[Session, Depends(get_db)]):
    path = db.execute(text("SELECT image_path FROM video_frames WHERE id=:id"), {"id": frame_id}).scalar_one_or_none()
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Frame image not found")
    return FileResponse(path, media_type="image/jpeg")

@app.get("/api/videos/{video_source_id}/detections")
def vehicle_detections(video_source_id: int, db: Annotated[Session, Depends(get_db)], limit: int = 500):
    rows = db.execute(text("""
        SELECT id, frame_index, round(frame_time_seconds::numeric, 2) AS frame_time_seconds,
               class_name, round(confidence::numeric, 3) AS confidence,
               bbox_x, bbox_y, bbox_w, bbox_h, detection_method, created_at
        FROM vehicle_detections
        WHERE video_source_id=:video_source_id
        ORDER BY frame_index, id
        LIMIT :limit
    """), {"video_source_id": video_source_id, "limit": limit}).mappings().all()
    return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]

@app.get("/api/analytics/video-detection-summary")
def video_detection_summary(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        SELECT vs.id AS video_source_id, vs.camera_code, vd.class_name, vd.detection_method,
               count(*) AS detections,
               round(avg(vd.confidence)::numeric, 3) AS avg_confidence,
               min(vd.frame_time_seconds) AS first_second,
               max(vd.frame_time_seconds) AS last_second
        FROM vehicle_detections vd
        JOIN video_sources vs ON vs.id = vd.video_source_id
        GROUP BY vs.id, vs.camera_code, vd.class_name, vd.detection_method
        ORDER BY vs.id DESC, detections DESC
    """)).mappings().all()
    return [dict(r) for r in rows]

@app.get("/api/export/vehicle-detections.csv")
def export_vehicle_detections(db: Annotated[Session, Depends(get_db)]):
    rows = [dict(r) for r in db.execute(text("""
        SELECT video_source_id, frame_index, frame_time_seconds, class_name, confidence,
               bbox_x, bbox_y, bbox_w, bbox_h, detection_method, created_at
        FROM vehicle_detections
        ORDER BY video_source_id, frame_index, id
    """)).mappings().all()]
    return _csv_response(
        "vehicle_detections.csv",
        ["video_source_id", "frame_index", "frame_time_seconds", "class_name", "confidence", "bbox_x", "bbox_y", "bbox_w", "bbox_h", "detection_method", "created_at"],
        rows,
    )

# ---- Phase 5: incident detection and human review workflow ----

def _ensure_video_prerequisites(db: Session, video_source_id: int) -> dict[str, int]:
    """Make the Phase 5 button safe to click.

    If users forgot to run Phase 4 manually, this tries to create frame samples and
    vehicle detections automatically. Any OpenCV/video problem is converted into a
    clear 400 response instead of a generic Internal Server Error.
    """
    frame_count = db.execute(text("SELECT count(*) FROM video_frames WHERE video_source_id=:id"), {"id": video_source_id}).scalar_one()
    if int(frame_count) == 0:
        try:
            sample_frames(video_source_id, db, every_seconds=1.0, max_frames=240)
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"Could not sample frames for this video: {exc}") from exc

    detection_count = db.execute(text("SELECT count(*) FROM vehicle_detections WHERE video_source_id=:id"), {"id": video_source_id}).scalar_one()
    if int(detection_count) == 0:
        try:
            detect_vehicles(video_source_id, db, every_seconds=1.0, max_frames=600, min_area=500)
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"Could not generate vehicle detections for this video: {exc}") from exc

    frame_count = db.execute(text("SELECT count(*) FROM video_frames WHERE video_source_id=:id"), {"id": video_source_id}).scalar_one()
    detection_count = db.execute(text("SELECT count(*) FROM vehicle_detections WHERE video_source_id=:id"), {"id": video_source_id}).scalar_one()
    return {"frame_count": int(frame_count), "detection_count": int(detection_count)}


@app.post("/api/videos/{video_source_id}/detect-incidents")
def detect_incidents(video_source_id: int, db: Annotated[Session, Depends(get_db)], congestion_threshold: int = 3, stalled_seconds: float = 8.0, replace_existing: bool = True):
    try:
        _video_path_from_id(db, video_source_id)
        prereq = _ensure_video_prerequisites(db, video_source_id)
        detections = [dict(r) for r in db.execute(text("""
            SELECT id, video_source_id, frame_index, frame_time_seconds, class_name, confidence, bbox_x, bbox_y, bbox_w, bbox_h, detection_method
            FROM vehicle_detections
            WHERE video_source_id=:video_source_id
              AND bbox_x IS NOT NULL AND bbox_y IS NOT NULL AND bbox_w IS NOT NULL AND bbox_h IS NOT NULL
              AND bbox_w > 0 AND bbox_h > 0
            ORDER BY frame_time_seconds, id
        """), {"video_source_id": video_source_id}).mappings().all()]
        if not detections:
            return {
                "video_source_id": video_source_id,
                "detections_used": 0,
                "incident_events_created": 0,
                "congestion_threshold": congestion_threshold,
                "stalled_seconds": stalled_seconds,
                "note": "No valid vehicle detections were available. Try Phase 4 Detect vehicles with a lower min_area value or use a clearer traffic video.",
                **prereq,
            }
        frames = {int(r["frame_index"]): r["image_path"] for r in db.execute(text("SELECT frame_index, image_path FROM video_frames WHERE video_source_id=:video_source_id"), {"video_source_id": video_source_id}).mappings().all() if r["frame_index"] is not None}
        candidates = generate_incident_candidates(detections, frames, congestion_threshold=congestion_threshold, stalled_seconds=stalled_seconds)
        if replace_existing:
            db.execute(text("DELETE FROM incident_events WHERE video_source_id=:video_source_id AND COALESCE(detection_method, 'rule_based_phase5')='rule_based_phase5'"), {"video_source_id": video_source_id})
        camera_code = db.execute(text("SELECT camera_code FROM video_sources WHERE id=:id"), {"id": video_source_id}).scalar_one_or_none() or "CAM-1"
        inserted = 0
        for c in candidates:
            db.execute(text("""
                INSERT INTO incident_events(event_time, event_type, camera_code, zone_label, confidence, snapshot_path, queue_length_estimate, review_status, notes, video_source_id, frame_index, frame_time_seconds, detection_method)
                VALUES (now(), :event_type, :camera_code, :zone_label, :confidence, :snapshot_path, :queue_length_estimate, 'unreviewed', :notes, :video_source_id, :frame_index, :frame_time_seconds, 'rule_based_phase5')
            """), {"event_type": c.event_type, "camera_code": camera_code, "zone_label": c.zone_label, "confidence": float(c.confidence), "snapshot_path": c.snapshot_path, "queue_length_estimate": c.queue_length_estimate, "notes": c.notes, "video_source_id": video_source_id, "frame_index": int(c.frame_index), "frame_time_seconds": float(c.frame_time_seconds)})
            inserted += 1
        db.commit()
        return {"video_source_id": video_source_id, "detections_used": len(detections), "incident_events_created": inserted, "congestion_threshold": congestion_threshold, "stalled_seconds": stalled_seconds, "note": "Incident events are candidates and require human review before confirmation.", **prereq}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Incident detection database error: {exc}") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Incident detection failed: {exc}") from exc

@app.get("/api/incidents")
def incidents(db: Annotated[Session, Depends(get_db)], limit: int = 300, video_source_id: int | None = None):
    """Return incident events without PostgreSQL ambiguous NULL parameter typing."""
    safe_limit = max(1, min(int(limit or 300), 1000))
    params = {"limit": safe_limit}
    where_clause = ""
    if video_source_id is not None:
        where_clause = "WHERE video_source_id = :video_source_id"
        params["video_source_id"] = int(video_source_id)

    rows = db.execute(text(f"""
        SELECT id, event_time, event_type, camera_code, zone_label,
               round(confidence::numeric, 3) AS confidence,
               queue_length_estimate, review_status, notes, video_source_id,
               frame_index, round(frame_time_seconds::numeric, 2) AS frame_time_seconds,
               detection_method, snapshot_path, created_at
        FROM incident_events
        {where_clause}
        ORDER BY created_at DESC, id DESC
        LIMIT :limit
    """), params).mappings().all()
    result = []
    for r in rows:
        d = dict(r)
        d["event_time"] = d["event_time"].isoformat() if d["event_time"] else None
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
        d["snapshot_url"] = f"/api/incidents/{d['id']}/snapshot" if d.get("snapshot_path") else None
        result.append(d)
    return result


@app.patch("/api/incidents/{incident_id}/review")
def review_incident(incident_id: int, payload: dict, db: Annotated[Session, Depends(get_db)]):
    status = str(payload.get("review_status") or "").strip()
    if status not in {"unreviewed", "confirmed", "false_positive", "uncertain"}:
        raise HTTPException(status_code=400, detail="review_status must be one of: unreviewed, confirmed, false_positive, uncertain")
    notes = payload.get("notes")
    rowcount = db.execute(text("UPDATE incident_events SET review_status=:status, notes=CASE WHEN :notes IS NULL OR :notes='' THEN notes ELSE :notes END WHERE id=:id"), {"id": incident_id, "status": status, "notes": notes}).rowcount
    db.commit()
    if not rowcount:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"id": incident_id, "review_status": status}


@app.get("/api/incidents/{incident_id}/snapshot")
def incident_snapshot(incident_id: int, db: Annotated[Session, Depends(get_db)]):
    path = db.execute(text("SELECT snapshot_path FROM incident_events WHERE id=:id"), {"id": incident_id}).scalar_one_or_none()
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Incident snapshot not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/analytics/incident-summary")
def incident_summary(db: Annotated[Session, Depends(get_db)]):
    rows = db.execute(text("""
        SELECT COALESCE(vs.id, ie.video_source_id) AS video_source_id, COALESCE(vs.camera_code, ie.camera_code) AS camera_code, ie.event_type, ie.review_status, count(*) AS events, round(avg(ie.confidence)::numeric, 3) AS avg_confidence, min(ie.frame_time_seconds) AS first_second, max(ie.frame_time_seconds) AS last_second
        FROM incident_events ie LEFT JOIN video_sources vs ON vs.id = ie.video_source_id
        GROUP BY COALESCE(vs.id, ie.video_source_id), COALESCE(vs.camera_code, ie.camera_code), ie.event_type, ie.review_status
        ORDER BY video_source_id DESC NULLS LAST, events DESC
    """)).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/export/incidents.csv")
def export_incidents(db: Annotated[Session, Depends(get_db)]):
    rows = [dict(r) for r in db.execute(text("""
        SELECT id, event_time, event_type, camera_code, zone_label, confidence, queue_length_estimate, review_status, notes, video_source_id, frame_index, frame_time_seconds, detection_method, snapshot_path, created_at
        FROM incident_events ORDER BY created_at DESC, id DESC
    """)).mappings().all()]
    return _csv_response("incident_events.csv", ["id", "event_time", "event_type", "camera_code", "zone_label", "confidence", "queue_length_estimate", "review_status", "notes", "video_source_id", "frame_index", "frame_time_seconds", "detection_method", "snapshot_path", "created_at"], rows)

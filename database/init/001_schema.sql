CREATE TABLE IF NOT EXISTS intersections (
  id SERIAL PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approaches (
  id SERIAL PRIMARY KEY,
  intersection_id INT NOT NULL REFERENCES intersections(id) ON DELETE CASCADE,
  approach_no INT NOT NULL,
  name TEXT,
  UNIQUE(intersection_id, approach_no)
);

CREATE TABLE IF NOT EXISTS detectors (
  id SERIAL PRIMARY KEY,
  intersection_id INT NOT NULL REFERENCES intersections(id) ON DELETE CASCADE,
  approach_no INT NOT NULL,
  detector_no INT NOT NULL,
  lane_label TEXT,
  description TEXT,
  UNIQUE(intersection_id, approach_no, detector_no)
);

CREATE TABLE IF NOT EXISTS ingestion_files (
  id SERIAL PRIMARY KEY,
  file_type TEXT NOT NULL CHECK (file_type IN ('detector_log','signal_log','video')),
  original_filename TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploaded',
  records_imported INT NOT NULL DEFAULT 0,
  error_message TEXT,
  uploaded_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS detector_counts (
  id BIGSERIAL PRIMARY KEY,
  intersection_id INT NOT NULL REFERENCES intersections(id) ON DELETE CASCADE,
  approach_no INT NOT NULL,
  detector_no INT NOT NULL,
  interval_start TIMESTAMPTZ NOT NULL,
  interval_minutes INT NOT NULL DEFAULT 15,
  vehicle_count INT NOT NULL,
  source_file_id INT REFERENCES ingestion_files(id) ON DELETE SET NULL,
  quality_flag TEXT NOT NULL DEFAULT 'ok',
  raw_value TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(intersection_id, approach_no, detector_no, interval_start, source_file_id)
);

CREATE INDEX IF NOT EXISTS idx_detector_counts_time ON detector_counts(interval_start);
CREATE INDEX IF NOT EXISTS idx_detector_counts_detector ON detector_counts(intersection_id, approach_no, detector_no);

CREATE TABLE IF NOT EXISTS signal_events (
  id BIGSERIAL PRIMARY KEY,
  intersection_id INT REFERENCES intersections(id) ON DELETE SET NULL,
  intersection_code TEXT NOT NULL,
  event_time TIMESTAMPTZ NOT NULL,
  phase_no INT NOT NULL,
  signal_state TEXT NOT NULL,
  source_file_id INT REFERENCES ingestion_files(id) ON DELETE SET NULL,
  raw_line TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_signal_events_time ON signal_events(event_time);
CREATE INDEX IF NOT EXISTS idx_signal_events_phase ON signal_events(intersection_code, phase_no);

CREATE TABLE IF NOT EXISTS video_sources (
  id SERIAL PRIMARY KEY,
  camera_code TEXT NOT NULL DEFAULT 'CAM-1',
  source_type TEXT NOT NULL CHECK (source_type IN ('file','rtsp','http')),
  source_uri TEXT NOT NULL,
  width INT,
  height INT,
  fps NUMERIC,
  duration_seconds NUMERIC,
  frame_count BIGINT,
  ingestion_file_id INT REFERENCES ingestion_files(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);



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

CREATE TABLE IF NOT EXISTS incident_events (
  id BIGSERIAL PRIMARY KEY,
  event_time TIMESTAMPTZ NOT NULL DEFAULT now(),
  event_type TEXT NOT NULL,
  camera_code TEXT,
  zone_label TEXT,
  confidence NUMERIC,
  snapshot_path TEXT,
  clip_path TEXT,
  queue_length_estimate NUMERIC,
  review_status TEXT NOT NULL DEFAULT 'unreviewed',
  notes TEXT,
  video_source_id INT REFERENCES video_sources(id) ON DELETE SET NULL,
  frame_index BIGINT,
  frame_time_seconds NUMERIC,
  detection_method TEXT DEFAULT 'rule_based_phase5',
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_incident_events_video ON incident_events(video_source_id, frame_index);
CREATE INDEX IF NOT EXISTS idx_incident_events_review ON incident_events(review_status, event_type);

CREATE TABLE IF NOT EXISTS forecast_results (
  id BIGSERIAL PRIMARY KEY,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  target_time TIMESTAMPTZ NOT NULL,
  horizon_minutes INT NOT NULL,
  intersection_id INT REFERENCES intersections(id) ON DELETE SET NULL,
  approach_no INT,
  detector_no INT,
  model_name TEXT NOT NULL,
  predicted_count NUMERIC NOT NULL,
  actual_count NUMERIC,
  mae NUMERIC,
  rmse NUMERIC,
  mape NUMERIC
);

CREATE TABLE IF NOT EXISTS signal_recommendations (
  id BIGSERIAL PRIMARY KEY,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  target_time TIMESTAMPTZ,
  phase_no INT,
  approach_no INT,
  recommendation TEXT NOT NULL,
  reason TEXT NOT NULL,
  confidence NUMERIC,
  status TEXT NOT NULL DEFAULT 'evaluation_only'
);

CREATE TABLE IF NOT EXISTS system_health_logs (
  id BIGSERIAL PRIMARY KEY,
  logged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  component TEXT NOT NULL,
  metric_name TEXT NOT NULL,
  metric_value NUMERIC,
  metric_text TEXT
);

INSERT INTO intersections(code, name, description)
VALUES ('806', 'Wadi Saqra Intersection', 'Default PoC target intersection')
ON CONFLICT (code) DO NOTHING;

INSERT INTO approaches(intersection_id, approach_no, name)
SELECT id, x, 'Approach ' || x FROM intersections, generate_series(1,4) x
WHERE code='806'
ON CONFLICT DO NOTHING;

INSERT INTO detectors(intersection_id, approach_no, detector_no, lane_label)
SELECT i.id, a.approach_no, d.detector_no, 'A' || a.approach_no || '-D' || d.detector_no
FROM intersections i
CROSS JOIN generate_series(1,4) a(approach_no)
CROSS JOIN generate_series(1,5) d(detector_no)
WHERE i.code='806'
ON CONFLICT DO NOTHING;

INSERT INTO detectors(intersection_id, approach_no, detector_no, lane_label)
SELECT i.id, 5, d.detector_no, 'Extra-D' || d.detector_no
FROM intersections i
CROSS JOIN generate_series(1,2) d(detector_no)
WHERE i.code='806'
ON CONFLICT DO NOTHING;

CREATE OR REPLACE VIEW normalized_detector_counts AS
SELECT dc.id,
       dc.interval_start,
       i.code AS intersection_code,
       i.name AS intersection_name,
       dc.approach_no,
       COALESCE(a.name, 'Approach ' || dc.approach_no) AS approach_name,
       dc.detector_no,
       d.lane_label,
       d.description AS detector_description,
       dc.interval_minutes,
       dc.vehicle_count,
       dc.quality_flag,
       dc.source_file_id
FROM detector_counts dc
JOIN intersections i ON i.id = dc.intersection_id
LEFT JOIN approaches a ON a.intersection_id = dc.intersection_id AND a.approach_no = dc.approach_no
LEFT JOIN detectors d ON d.intersection_id = dc.intersection_id AND d.approach_no = dc.approach_no AND d.detector_no = dc.detector_no;

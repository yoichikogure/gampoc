from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class IncidentCandidate:
    event_type: str
    frame_index: int
    frame_time_seconds: float
    zone_label: str
    confidence: float
    queue_length_estimate: float | None
    notes: str
    snapshot_path: str | None = None


def _valid_box(d: dict[str, Any]) -> bool:
    required = ["bbox_x", "bbox_y", "bbox_w", "bbox_h", "frame_index"]
    for key in required:
        if d.get(key) is None:
            return False
    try:
        return float(d["bbox_w"]) > 0 and float(d["bbox_h"]) > 0
    except (TypeError, ValueError):
        return False


def _center(d: dict[str, Any]) -> tuple[float, float]:
    return (float(d["bbox_x"]) + float(d["bbox_w"]) / 2.0, float(d["bbox_y"]) + float(d["bbox_h"]) / 2.0)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def generate_incident_candidates(
    detections: list[dict[str, Any]],
    frame_paths: dict[int, str],
    congestion_threshold: int = 3,
    stalled_seconds: float = 8.0,
    max_stationary_pixels: float = 45.0,
) -> list[IncidentCandidate]:
    """Create explainable Phase 5 incident candidates from Phase 4 detections.

    Bad/partial detection rows are ignored so that reused older database volumes
    do not crash the incident detection endpoint.
    """
    if congestion_threshold < 1:
        raise ValueError("congestion_threshold must be at least 1")
    if stalled_seconds <= 0:
        raise ValueError("stalled_seconds must be positive")

    detections = [d for d in detections if _valid_box(d)]
    detections = sorted(detections, key=lambda d: (float(d.get("frame_time_seconds") or 0), int(d.get("id") or 0)))
    candidates: list[IncidentCandidate] = []

    # 1) Congestion candidates: frame-level density of vehicle candidates.
    by_frame: dict[int, list[dict[str, Any]]] = {}
    for d in detections:
        by_frame.setdefault(int(d["frame_index"]), []).append(d)

    for frame_index, items in sorted(by_frame.items()):
        if len(items) >= congestion_threshold:
            t = float(items[0]["frame_time_seconds"] or 0)
            confidence = min(0.95, 0.45 + 0.10 * (len(items) - congestion_threshold + 1))
            candidates.append(IncidentCandidate(
                event_type="congestion_event",
                frame_index=frame_index,
                frame_time_seconds=t,
                zone_label="whole_camera_view",
                confidence=round(confidence, 3),
                queue_length_estimate=float(len(items)),
                snapshot_path=frame_paths.get(frame_index),
                notes=f"{len(items)} vehicle candidates detected in one sampled frame; threshold={congestion_threshold}. Human review required.",
            ))

    # 2) Possible stalled vehicles: simple nearest-neighbour tracking of boxes.
    tracks: list[dict[str, Any]] = []
    next_track_id = 1
    for d in detections:
        c = _center(d)
        t = float(d["frame_time_seconds"] or 0)
        best = None
        best_dist = 999999.0
        for tr in tracks:
            # Only match to tracks recently seen.
            if t - tr["last_time"] > max(stalled_seconds * 1.5, 15.0):
                continue
            dist = _distance(c, tr["last_center"])
            if dist < best_dist and dist <= max_stationary_pixels:
                best = tr
                best_dist = dist
        if best is None:
            tracks.append({
                "track_id": next_track_id,
                "first_time": t,
                "last_time": t,
                "first_center": c,
                "last_center": c,
                "first_frame": int(d["frame_index"]),
                "last_frame": int(d["frame_index"]),
                "count": 1,
                "confidence_values": [float(d.get("confidence") or 0.4)],
            })
            next_track_id += 1
        else:
            best["last_time"] = t
            best["last_center"] = c
            best["last_frame"] = int(d["frame_index"])
            best["count"] += 1
            best["confidence_values"].append(float(d.get("confidence") or 0.4))

    emitted_tracks: set[int] = set()
    for tr in tracks:
        duration = tr["last_time"] - tr["first_time"]
        movement = _distance(tr["first_center"], tr["last_center"])
        if duration >= stalled_seconds and movement <= max_stationary_pixels and tr["count"] >= 3:
            track_id = int(tr["track_id"])
            if track_id in emitted_tracks:
                continue
            emitted_tracks.add(track_id)
            avg_conf = sum(tr["confidence_values"]) / max(len(tr["confidence_values"]), 1)
            confidence = min(0.95, max(0.35, avg_conf + min(0.25, duration / 60.0)))
            candidates.append(IncidentCandidate(
                event_type="possible_stalled_vehicle",
                frame_index=int(tr["last_frame"]),
                frame_time_seconds=float(tr["last_time"]),
                zone_label="stationary_track_zone",
                confidence=round(confidence, 3),
                queue_length_estimate=None,
                snapshot_path=frame_paths.get(int(tr["last_frame"])),
                notes=(
                    f"Vehicle candidate remained within {movement:.1f}px for {duration:.1f}s "
                    f"over {tr['count']} sampled detections. Human review required."
                ),
            ))

    return sorted(candidates, key=lambda c: (c.frame_time_seconds, c.event_type))

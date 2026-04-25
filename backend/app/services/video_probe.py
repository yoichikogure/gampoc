from __future__ import annotations

from pathlib import Path
from typing import Dict

import cv2


def probe_video(path: str | Path) -> Dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("Cannot open video file")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration_seconds = frame_count / fps if fps > 0 else None
    cap.release()
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "duration_seconds": duration_seconds,
    }

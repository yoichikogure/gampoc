from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2


@dataclass
class SampledFrame:
    frame_index: int
    frame_time_seconds: float
    image_path: str
    width: int
    height: int


@dataclass
class Detection:
    frame_index: int
    frame_time_seconds: float
    class_name: str
    confidence: float
    x: int
    y: int
    w: int
    h: int
    method: str


def _open_video(video_path: Path):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")
    return cap


def sample_video_frames(video_path: Path, output_dir: Path, every_seconds: float = 5.0, max_frames: int = 120) -> list[SampledFrame]:
    """Extract JPEG frames from a video for dashboard preview and model calibration.

    This function is intentionally lightweight and CPU-only so Phase 4 runs in a basic
    Docker environment without GPU drivers or proprietary components.
    """
    if every_seconds <= 0:
        raise ValueError("every_seconds must be positive")
    if max_frames <= 0:
        raise ValueError("max_frames must be positive")

    output_dir.mkdir(parents=True, exist_ok=True)
    cap = _open_video(video_path)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(int(round(fps * every_seconds)), 1)

    samples: list[SampledFrame] = []
    frame_index = 0
    while len(samples) < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % step == 0:
            height, width = frame.shape[:2]
            image_name = f"frame_{frame_index:08d}.jpg"
            image_path = output_dir / image_name
            cv2.imwrite(str(image_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            samples.append(SampledFrame(
                frame_index=frame_index,
                frame_time_seconds=frame_index / fps,
                image_path=str(image_path),
                width=width,
                height=height,
            ))
        frame_index += 1
        if frame_count and frame_index >= frame_count:
            break
    cap.release()
    return samples


def detect_vehicle_candidates(video_path: Path, every_seconds: float = 1.0, max_frames: int = 600, min_area: int = 700) -> list[Detection]:
    """CPU-only vehicle-candidate detection using motion segmentation.

    This is a working Phase 4 fallback detector. It does not claim to be a final AI
    detector; it creates vehicle-candidate metadata and overlay inputs before YOLO/ONNX
    model weights are provided. It is useful for verifying the whole video pipeline:
    video -> frames -> detections -> database -> dashboard.
    """
    if every_seconds <= 0:
        raise ValueError("every_seconds must be positive")

    cap = _open_video(video_path)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
    step = max(int(round(fps * every_seconds)), 1)
    backsub = cv2.createBackgroundSubtractorMOG2(history=250, varThreshold=32, detectShadows=True)

    detections: list[Detection] = []
    frame_index = 0
    processed = 0
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    while processed < max_frames:
        ok, frame = cap.read()
        if not ok:
            break

        # Feed all frames to the background model, but only emit detections on sampled frames.
        fg = backsub.apply(frame)
        if frame_index % step == 0 and frame_index > int(fps):
            _, mask = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.dilate(mask, kernel, iterations=2)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                area = cv2.contourArea(c)
                if area < min_area:
                    continue
                x, y, w, h = cv2.boundingRect(c)
                aspect = w / max(h, 1)
                # Vehicle-like blobs in traffic videos are usually not extremely thin/tall.
                if aspect < 0.35 or aspect > 6.0:
                    continue
                confidence = min(0.95, max(0.25, area / 18000.0))
                detections.append(Detection(
                    frame_index=frame_index,
                    frame_time_seconds=frame_index / fps,
                    class_name="vehicle_candidate",
                    confidence=round(confidence, 3),
                    x=int(x), y=int(y), w=int(w), h=int(h),
                    method="opencv_motion_fallback",
                ))
            processed += 1
        frame_index += 1
    cap.release()
    return detections

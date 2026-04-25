# Phase 4: Video Processing and Vehicle-Candidate Detection

Phase 4 adds the first working video-processing increment on top of Phase 3.

## Added capabilities

- Uploaded video registration and metadata extraction.
- Video source listing with FPS, resolution, duration, frame count, sampled-frame count, and detection count.
- Frame sampling from uploaded videos into JPEG files.
- Browser display of sampled frames.
- CPU-only OpenCV vehicle-candidate detection using motion segmentation.
- Storage of detection metadata: frame index, timestamp, class name, confidence, bounding box, and method.
- Detection summary dashboard.
- CSV export of vehicle detections.

## Important limitation

The current detector is a working fallback pipeline named `opencv_motion_fallback`. It is designed to verify the video pipeline without GPU drivers or external model downloads. It does not replace a calibrated YOLO/ONNX detector for real traffic incident detection. In the next phase, YOLO-family inference can be added by placing open-source model weights under `models/vehicle_detection/` and adding a model-backed detector service.

## How to test

1. Start the application.
2. Import a video file from the dashboard. A small sample video is included at:

```text

docs/sample_traffic_video_20s.mp4
```

3. In the Phase 4 dashboard section, click **Sample frames**.
4. Click **Detect vehicles**.
5. Review the sampled frames, detection table, and detection summary.

## New endpoints

```text
GET  /api/videos
POST /api/videos/{video_source_id}/sample-frames
POST /api/videos/{video_source_id}/detect-vehicles
GET  /api/videos/{video_source_id}/frames
GET  /api/video-frames/{frame_id}/image
GET  /api/videos/{video_source_id}/detections
GET  /api/analytics/video-detection-summary
GET  /api/export/vehicle-detections.csv
```

## Database tables added

```text
video_frames
vehicle_detections
```

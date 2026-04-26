# Phase 6: Real-time RTSP AI Processing

Phase 6 adds real-time video processing for RTSP / HTTP video sources.

## Added functions

- RTSP / HTTP source registration remains available from the Data Import section.
- Normal live preview remains available through `/api/videos/{video_source_id}/live.mjpg`.
- New live AI overlay stream is available through `/api/videos/{video_source_id}/live-ai.mjpg`.
- The dashboard now has a **Phase 6: Real-time RTSP AI Processing** section.
- OpenCV motion-based vehicle-candidate boxes are drawn on the live MJPEG stream.
- Live detection metadata is periodically inserted into `vehicle_detections` with `detection_method='opencv_rtsp_motion_phase6'`.
- Stream health is stored in `live_stream_health` and displayed on the dashboard.

## New endpoints

```text
GET /api/videos/{video_source_id}/live-ai.mjpg
GET /api/videos/{video_source_id}/live-health
GET /api/videos/{video_source_id}/live-detections
```

## How to use

1. Open the dashboard.
2. Register an RTSP URL in **Data Import → RTSP / HTTP Live Video**.
3. Click **Live AI** in the video source table, or go to **Phase 6** and click **Start live AI overlay**.
4. The browser shows an MJPEG stream with detected vehicle-candidate boxes.
5. The **Live Stream Health** table refreshes every few seconds.
6. The **Recent Live AI Detections** table shows metadata logged from the live stream.

## Parameters

The live AI stream supports these query parameters:

```text
fps_limit=5       # output FPS target, 1–15
max_width=960     # resize stream for browser display
min_area=900      # minimum contour area for vehicle candidates
log_every_n=10    # insert detections every N read frames
```

Example:

```text
/api/videos/1/live-ai.mjpg?fps_limit=5&max_width=960&min_area=900&log_every_n=10
```

## Important limitation

This phase uses a lightweight OpenCV motion-based fallback detector so that the function works without paid components or GPU setup. It is useful for demonstrating real-time data flow, overlays, database logging, and stream health monitoring.

For production-quality vehicle classification, replace this fallback with a trained vehicle detector such as YOLO exported to ONNX, then keep the same endpoint/dashboard structure.

# RTSP / HTTP Real-Time Video Preview

This increment adds live video input support for RTSP or HTTP video URLs.

## Why MJPEG proxy is used

Standard web browsers cannot display `rtsp://` streams directly. The backend opens the RTSP stream with OpenCV/FFmpeg and exposes a browser-friendly MJPEG endpoint:

```text
/api/videos/{video_source_id}/live.mjpg
```

The dashboard uses this endpoint in an `<img>` tag to show the live preview.

## Dashboard workflow

1. Open `http://localhost:8080`.
2. In **Data Import → RTSP / HTTP Live Video**, enter:
   - Camera code, for example `CAM-RTSP-1`
   - RTSP URL, for example `rtsp://user:password@192.168.1.100:554/stream1`
3. Click **Register RTSP source**.
4. The page will automatically open **RTSP Live Video Preview**.
5. You can also click **View live** in the Phase 4 video source table.

## API workflow

Register a source:

```bash
curl -X POST http://localhost:8080/api/rtsp/register \
  -H "Content-Type: application/json" \
  -d '{"camera_code":"CAM-RTSP-1","rtsp_url":"rtsp://user:password@host:554/stream"}'
```

Open the browser-compatible stream:

```text
http://localhost:8080/api/videos/{video_source_id}/live.mjpg
```

## Notes and limitations

- RTSP is converted to MJPEG for browser preview.
- This is local PoC monitoring only, not a production video server.
- The live preview does not write to or control GAM's operational systems.
- If the stream is unreachable, the preview shows a status frame and retries every 5 seconds.
- Offline Phase 4 frame sampling and vehicle detection still operate on uploaded historical video files only. Real-time AI detection on RTSP can be added in a later increment.

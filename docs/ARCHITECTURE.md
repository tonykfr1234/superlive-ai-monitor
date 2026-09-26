# Architecture and Program Design

## Components

### `src/monitor.py`

- Locates the visible `MuMuNxDevice.exe` window.
- Captures the client area with MSS / Windows BitBlt.
- Runs YOLO classes `0,2,3,5,7` (Person, Car, Motorcycle, Bus, Truck).
- Uses ByteTrack for short-term tracking.
- Uses a temporal motion window, normalized displacement and direction consistency to reduce bounding-box jitter false positives.
- Applies category-level notification rate limiting instead of Track-ID rate limiting.
- Writes event JPG and CSV records.
- Sends Windows notifications and optional Telegram photo alerts.
- Recreates MSS after capture errors and escalates repeated failures to the process watchdog.

### `src/watchdog.py`

Runs the AI monitor as a child process. It writes PID/heartbeat files, restarts a crashed monitor after a delay, and performs retention cleanup. A deliberate `stop.flag` distinguishes user-requested shutdown from a crash.

### `src/recorder.py`

Captures the raw MuMu client area independently from AI annotations. It writes fixed-duration MP4 segments, checks disk usage periodically and deletes the oldest closed segments when the configured drive threshold is reached.

### `src/recorder_watchdog.py`

Restarts the recorder after an unexpected exit. It uses the same deliberate stop mechanism as the AI watchdog.

## Failure isolation

```text
Capture error
  → recreate MSS
  → retry
  → repeated failure → monitor exits
  → AI watchdog restarts monitor

Recorder crash
  → recorder watchdog restarts recorder

Telegram/network failure
  → event remains local
  → monitor continues
```

## Current limitation

The source is a GUI window, not a direct RTSP stream. The Windows interactive user session must remain logged in and MuMu must keep rendering the live view. A true Windows logoff removes the GUI source. Direct RTSP/ONVIF capture would be a cleaner future backend when camera access permits it.

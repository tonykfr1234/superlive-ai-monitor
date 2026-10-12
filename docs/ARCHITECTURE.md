# Architecture and Program Design

## Detection and preview

`src/monitor.py` locates the visible MuMu window and runs person-only YOLO detection. `capture_region.py` extracts the camera/display content while excluding the emulator title/menu area. Camera channel labels and timestamps inside the image remain visible. Monitor and recorder use the same crop logic.

`region_policy.py` validates camera-relative polygon vertices and tests positive-area overlap with person boxes, including concave regions. Legacy four-number rectangle settings remain supported. Old coordinates are migrated to the content area; if cropping cannot preserve a valid region, detection requires reselection instead of silently using the whole frame.

`preview_ui.py` places controls and status beneath the image. R begins selection; left click adds vertices, right click/Backspace undoes a vertex, Enter saves, Esc cancels, C selects the full frame and Q stops.

## Activity and event lifecycle

`person_activity.py` associates person boxes across valid observations and compares their centers and sizes with stable anchors. Small jitter is ignored; slow movement accumulates relative to an anchor. A new person is activity. The tracker persists across clip boundaries so a stationary person cannot immediately retrigger another clip.

```text
Valid person activity while recorder is idle
  → write an event recording request
  → local Windows alert + event JPG/CSV; no automatic Telegram start photo
  → recorder starts a clip and a 120-second Telegram quiet period
  → detector continues observing the selected region
     ├─ first 120 seconds → no automatic rollover; movement resets inactivity
     ├─ movement at 120–300 seconds → end this clip and immediately start the next
     ├─ 120 continuous seconds without movement → end, then wait for activity
     └─ unknown capture/detection or region selection → reset observation interval
  → close each clip no later than the 300-second cap
  → persist its actual-duration video in the Telegram queue
  → new clips each start another 120-second quiet period
  → queue sends/retries wait until the current quiet period ends
```

Normal clips have a 120-second minimum and a 300-second maximum. The minimum clip age and continuous no-movement interval are separate conditions: movement before age 120 resets inactivity without splitting, while movement from age 120 and before 300 requests rollover. No person and an observed stationary person both count as no movement. An inactivity stop returns to idle until new movement or arrival. Selection, failed capture and unavailable inference are unknown and cannot establish an inactivity timeout; changing the region resets activity context.

`record_protocol.py` provides atomic request/state files and event identifiers. End and rollover requests target the observed recording; stale requests cannot terminate a later clip. Readiness checks prevent duplicate event starts.

## Recording and delivery

`src/recorder.py` independently captures the camera content and writes actual-duration MP4 clips at a target 10 FPS. Normal automatic transitions produce 120–300-second clips. Manual stop or capture failure can leave shorter local partial clips; no padding fabricates footage to reach the minimum. The polygon controls detection, not the saved image boundary.

`telegram_video.py` persists pending deliveries and converts completed clips to H.264 MP4 for Telegram playback. Completed videos and daily CSV reports are the monitor's automatic Telegram deliveries; Windows alerts and local snapshots remain local at recording start.

The background worker does not start new Telegram sends during each clip's first 120 seconds. This restriction covers older queued videos and retries as well as the current event. Every rollover starts a fresh quiet period, so repeated rollovers can defer the backlog. A partial clip cannot be automatically delivered before its own quiet deadline; newer quiet periods also apply. Pending jobs survive restart. A network request already dispatched before a quiet period may finish or arrive during it and cannot be recalled.

`src/watchdog.py` and `src/recorder_watchdog.py` supervise their child processes. Capture recovery recreates capture resources before escalating repeated failures. The deliberate stop flag prevents restart after the user requests shutdown.

## Deployment

`installer/DEPLOY_RUNTIME.ps1` is shared by install/update entry points. It validates source files, backs up the installation, waits for its processes to stop, preserves local region/Telegram/storage settings, and deploys the runtime. Updates leave monitoring stopped for a deliberate restart. Credentials come from local setup, not the repository.

## Limitations

The source is a GUI window, not RTSP: Windows must stay logged in and MuMu must render the intended live view. Movement is a proxy derived from detection-box location/size, not pose analysis or a physical motion measurement. Occlusion, missed detections, camera movement and box jitter can affect activity tracking. Offline tests do not replace validation against the user's actual camera, emulator and Telegram account.

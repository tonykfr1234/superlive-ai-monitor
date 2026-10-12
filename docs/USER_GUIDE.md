# User Guide

## 1. Prepare and install

Keep the Windows monitoring session logged in. Install MuMu Player and SuperLive, sign in, and open the intended camera live view. From the repository run `installer\INSTALL_FROM_REPO.bat`, or:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

The runtime is deployed to `%USERPROFILE%\Downloads\SuperLive_AI`. Use `installer\UPDATE_FROM_REPO.bat` for an existing installation. Updates back up and stop the runtime, retain local credentials/region/storage settings, and leave it stopped for review and restart.

## 2. Configure Telegram and verify

Run `installer\SET_TELEGRAM.bat`. Enter your bot token locally and send `test` to the bot when prompted so setup can discover the chat ID. Credentials are saved only in the deployed `config\telegram.env` and must never be committed. No credentials are included in the repository. Without valid settings, Telegram notifications and video delivery are unavailable.

Run `installer\VERIFY_ALL.bat` while the real camera view is open for file/directory presence, capture, Python compilation and encoder diagnostics. Its Telegram and recording-folder PASS lines only confirm that a file or directory exists; they do not validate credentials, write access or delivery. Use `SET_TELEGRAM.bat` for your own credentials and its test message, then confirm an actual recorded video arrives.

## 3. Start, stop and choose a polygon

Use the deployed `Start_AI_Monitor.bat` and `Stop_AI_Monitor.bat`. Start launches both watchdogs; Stop requests a deliberate shutdown and saves the current partial clip. A manual stop may produce less than 120 seconds of footage, without padding. Pending video deliveries resume on a later start only after the applicable quiet deadlines.

The preview currently uses Traditional Chinese, with clickable R, C, Q, Save and Cancel controls below the camera image. README translations are documentation languages, not interface language settings. To define a region:

1. Click R / Select.
2. Left-click at least three distinct vertices in order around the boundary.
3. Right-click or press Backspace to remove the last point if needed.
4. Press Enter or click Save to close and save the polygon. Edges cannot cross and the region must have area.
5. Esc / Cancel discards this edit and keeps the previous region. C / Full frame clears the restriction; Q stops monitoring.

Keyboard shortcuts require preview focus. Concave polygons are supported. A person box must overlap the polygon with positive area; touching its edge alone is insufficient. With no region configured, the full camera content is used.

Settings persist as normalized camera-relative vertices in `config\notification_policy.json`; legacy rectangles remain supported. If old coordinates cannot be safely migrated after content cropping, monitoring waits for a valid selection or an explicit C / Full frame choice. Recheck the region after changing cameras or layouts.

## 4. Activity, recording and alerts

A newly observed person or renewed movement starts a clip when the recorder is idle. It creates a local Windows alert and snapshot, without an automatic Telegram start photo. Detection continues throughout recording.

Normal clips last **120–300 seconds**. During the first 120 seconds, movement resets the no-movement timer but does not split the clip. At or after 120 seconds and before the 300-second cap, observed movement immediately closes the current clip and starts a new one. Each new clip begins its own 120-second quiet period.

No person and a stationary person both count as no movement. After 120 continuous seconds without movement, the clip closes and monitoring waits for renewed movement or a new arrival; a still person does not immediately retrigger. Selection or invalid capture/inference is unknown and resets the inactivity observation interval. The minimum clip duration and no-movement interval are separate rules.

**Completed videos are the only automatic Telegram notifications.** The program does not start new Telegram sends during the current clip's first 120 seconds, including older queued videos and retries. Repeated rollovers can postpone the backlog. A network request already dispatched before the quiet period can still arrive during it; it cannot be recalled. Explicit Telegram setup tests remain available through `SET_TELEGRAM.bat`.

Movement uses changes in person-box position or size, not pose estimation. Small jitter is filtered, and slow displacement accumulates. Camera motion, occlusion or missed detections can affect the result; verify behavior with your camera scene.

## 5. Video and storage

The video contains the full cropped camera view, without UI status or a polygon-shaped crop. Default: 10 FPS, `C:\SuperLive_Recordings`, normal clip duration 120–300 seconds. Finished clips are converted to Telegram-playable H.264 MP4 and delivered in the background after applicable quiet periods; failures remain queued across restart.

Manual stop or capture failure can leave a local partial clip under 120 seconds. The program does not pad it to invent missing footage, and it cannot be automatically sent before its quiet deadline. If a newer clip is in its own quiet period, the older partial clip waits as well.

Rotation begins at 80% total recording-drive usage and stops at 78%, deleting eligible old closed clips. Pending-delivery and active files are protected. See [Recording Design](RECORDING.md).

## 6. Troubleshooting

- **Selection keys do nothing:** click the preview for focus or use its buttons.
- **Recording does not restart for a still person:** wait for renewed movement or a new arrival; this is expected.
- **Inactivity seems delayed:** movement or unknown capture/selection resets the observation interval.
- **A finished video is waiting:** all new Telegram sends pause during the current clip's first 120 seconds; repeated rollovers can extend that wait.
- **A new clip starts when someone moves:** this is expected once the old clip is at least 120 seconds old and below its 300-second cap.
- **Black capture or MuMu missing:** keep Windows logged in and the intended live view open.
- **VM inference is slow:** CPU fallback is normal without real NVIDIA CUDA support; VMware 3D acceleration does not provide CUDA compute.
- **Telegram fails:** verify your local settings and network. Pending video deliveries retry when the quiet-period rules allow sending.
- **Recorder restarts:** inspect the deployed `logs\Stage5C` directory. Keep logs and recordings out of Git.

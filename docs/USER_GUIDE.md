# User Guide

## 1. Prepare Windows

Use Windows 11, keep the monitoring user session logged in, and install MuMu Player. In MuMu, install SuperLive, sign in and open the camera live view that should be monitored.

## 2. Install

From the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

The runtime is deployed to `%USERPROFILE%\Downloads\SuperLive_AI`.

## 3. Telegram

Run `installer\SET_TELEGRAM.bat`. Enter the bot token locally. Send `test` to the bot when prompted so the setup can discover the numeric chat ID. The credentials are saved only to the runtime `config\telegram.env` and are excluded from Git.

## 4. Verify the live view

Keep MuMu/SuperLive open on the real camera view. Run the verification script. Confirm MuMu is found, capture is not black, Python files compile, Telegram is configured if desired, and the recording directory is writable.

## 5. Start and stop

Use the deployed BAT files:

```text
Start_AI_Monitor.bat
Stop_AI_Monitor.bat
```

Start launches both watchdogs. Stop creates a deliberate stop flag so watchdogs do not revive processes the user intentionally stopped.

## 6. Notifications

Default policy:

- first person/vehicle presence: immediate;
- moving: maximum once every 5 minutes per category;
- still/persistent: once every 10 minutes per category;
- absent for 30 seconds: reset session, then next appearance is immediate.

Edit `config\notification_policy.json` in the deployed runtime to tune the intervals.

## 7. Recording

Raw MuMu frames are recorded independently from the AI overlay. Default: 10 FPS, 30-minute MP4 segments, `C:\SuperLive_Recordings`, 80/78% disk-watermark rotation.

## 8. Troubleshooting

- **Black capture:** keep the Windows session logged in and avoid display/session teardown; check MSS/BitBlt recovery logs.
- **MuMu not found:** open MuMu and the live view before starting.
- **VM CPU slow:** reduce inference load or allocate sensible vCPU; VMware 3D acceleration does not provide CUDA.
- **Telegram fails:** verify local `telegram.env`, network access and bot/chat configuration.
- **Recorder restarts:** inspect `logs\Stage5C\recorder_process.log` and `recorder_watchdog.log`.

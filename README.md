# SuperLive AI Monitor

> A Windows-first, long-running person and vehicle monitoring pipeline for a MuMu-hosted SuperLive live view, with local recording, Windows alerts, Telegram photo alerts, self-healing capture, and watchdog recovery.

[繁體中文 README](README.zh-TW.md) · [English User Guide](docs/USER_GUIDE.md) · [繁中使用指南](docs/USER_GUIDE.zh-TW.md) · [Security](SECURITY.md)

## Why this project

Many camera apps are easy to watch but hard to automate. SuperLive AI Monitor turns a visible MuMu/SuperLive camera window into a practical monitoring pipeline without requiring a camera RTSP URL:

- detects **people and vehicles** with YOLO + ByteTrack;
- distinguishes **moving** and **still/persistent** objects;
- sends **Windows notifications** and **Telegram photo alerts**;
- saves event JPG/CSV evidence;
- records the raw MuMu view in rolling MP4 segments;
- recovers from transient MSS/BitBlt capture failures;
- restarts crashed monitor/recorder processes with watchdogs;
- supports VMware with CPU inference fallback when CUDA is unavailable.

This is an unofficial community project and is not affiliated with the SuperLive or MuMu vendors.

## Default notification policy

| Condition | Behavior |
|---|---|
| Person or vehicle appears | Notify immediately |
| Object remains moving | At most one alert per category every **5 minutes** |
| Object remains present but still | Reminder every **10 minutes** |
| Category disappears | Session resets after **30 seconds** |
| Reappears after reset | Notify immediately |

Rate limiting is category-based (`PERSON` / `VEHICLE`), not ByteTrack-ID-based, so tracker ID churn does not create alert storms.

## Recording

Default Windows/VMware profile:

- path: `C:\SuperLive_Recordings`
- target: **10 FPS**
- segment length: **30 minutes per MP4**
- disk check: every **60 seconds**
- at **80% total drive usage**, delete the oldest closed MP4 files;
- stop deleting when drive usage falls to **78%**.

The percentage applies to the **whole drive**, not only the recording folder. See [Recording Design](docs/RECORDING.md), especially for thin-provisioned VMware VMDKs.

## Architecture

```text
Camera live view
     ↓
SuperLive in MuMu
     ↓
Windows desktop capture (MSS / BitBlt)
     ├──────────────→ raw rolling MP4 recorder
     ↓
YOLO person/vehicle detection
     ↓
ByteTrack + temporal motion analysis
     ↓
Presence / moving / still policy
     ├── Windows notification
     ├── Telegram photo alert
     ├── event JPG
     └── events.csv

AI watchdog       → restart monitor after crash
Recorder watchdog → restart recorder after crash
Capture self-heal → recreate MSS before escalating to watchdog
```

## Quick start

1. Install MuMu Player, install/sign in to SuperLive, and open the live camera view.
2. Clone/download this repository.
3. Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

4. Configure Telegram locally with `installer\SET_TELEGRAM.bat`.
5. Start/stop with the deployed `Start_AI_Monitor.bat` and `Stop_AI_Monitor.bat`.

Runtime files are deployed to `%USERPROFILE%\Downloads\SuperLive_AI`. No Windows auto-start, Task Scheduler task or Windows Service is created by default.

## Hardware guidance

### Physical Windows PC

| Component | Minimum practical | Recommended for 24/7 |
|---|---|---|
| CPU | modern 4-core / 8-thread | modern 6+ performance-capable cores |
| RAM | 8 GB | 16–32 GB |
| GPU | CPU inference supported | NVIDIA CUDA GPU for higher inference FPS |
| Storage | SSD | dedicated high-endurance SSD/NVMe for recording |

### VMware

| Setting | Recommended starting point |
|---|---|
| vCPU | 6 vCPU, `1 processor × 6 cores` |
| RAM | 8 GB minimum; 16 GB when host RAM allows |
| 3D | `Accelerate 3D graphics` enabled |
| Graphics memory | ~2 GB is generally enough for this GUI workload |
| Nested virtualization | `Virtualize Intel VT-x/EPT` enabled for MuMu |
| AI | CPU fallback is normal without real NVIDIA GPU passthrough |

VMware 3D acceleration helps desktop/MuMu rendering; it does **not** turn VMware SVGA 3D or Intel Iris Xe into CUDA compute for PyTorch.

## What does “baseline” mean?

A **baseline** is a *known-good, verified version used as the reference point for later installs and changes*. It is not a secret bundle and not merely a backup. Earlier private installers used a `baseline/` directory for verified runtime files; the public repository uses `src/` for clearer open-source organization. See [Baseline](docs/BASELINE.md).

## Security first

This public repository must never contain Telegram tokens/chat IDs, OpenAI/ChatGPT/API keys, GitHub tokens, passwords, cookies, sessions, `telegram.env`, `.env`, logs, event images, recordings or private migration payloads.

Before every public push:

```powershell
python .\scripts\security_scan.py .
```

GitHub Actions runs the same scanner on pushes and pull requests. See [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).

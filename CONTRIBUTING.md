# Contributing

Contributions are welcome. Keep changes focused and testable.

Before opening a pull request:

1. Do not commit credentials or private camera/account data.
2. Run `python scripts/security_scan.py .`.
3. Run `python -m unittest discover -s tests -p "test_*.py"` and compile changed Python files.
4. For installer changes, run `powershell -NoProfile -ExecutionPolicy Bypass -File tests/test_deployment.ps1`. It uses an isolated temporary installation and never starts the live monitor.
5. Describe the Windows/VMware environment used for testing. For capture/motion changes, explain how false positives and long-running stability were checked.

The headless tests need no camera, model download or Telegram credentials. Install `numpy pillow opencv-python imageio-ffmpeg requests psutil` to include synthetic capture and real H.264 encoder checks. Some optional tests skip if their dependencies are absent.

For recording-policy changes, cover the boundaries before/at 120 seconds and at 300 seconds, activity-triggered rollover, inactivity stop, and partial recordings after interruption. Verify that no new Telegram sends or retries start during each clip's quiet period, including older queued jobs and repeated rollovers. Do not treat an already-dispatched request's later arrival as a new send, and do not pad a partial clip to manufacture the normal minimum length.

Keep the English and Traditional Chinese guides and all 12 README introductions aligned when behavior changes. README translations describe the same program; the application interface currently uses Traditional Chinese, with an English fallback if a compatible CJK font is unavailable.

## Generated demo media

Run `python scripts/generate_demo.py` with Pillow, NumPy, imageio-ffmpeg and Microsoft JhengHei or Noto Sans CJK installed. This regenerates the five files in `docs/media/`: three PNGs, a GIF preview and a 36-second silent H.264 MP4. It imports the actual preview renderer and supplies synthetic scene frames, scripted person boxes and scripted event states. No model, camera or Telegram connection is used.

The demo has Traditional Chinese/English captions and compressed time. Show the first-120-second Telegram quiet period and movement-triggered rollover, with a fresh quiet period for the new clip. It illustrates the workflow and is not a detector-accuracy benchmark. Keep that distinction in localized descriptions; do not substitute private camera footage.

`SHA256SUMS.txt` records SHA-256 hashes of committed file bytes, excluding itself. Git may convert text line endings in a Windows checkout; the committed/archive bytes are the reference for verification.

Run `python scripts/generate_icon.py` to regenerate the application PNG and multi-size ICO with Pillow. The original geometric design is code-generated and needs no external image assets.

# Recording Design / 錄影設計

## Defaults

| Setting | Default |
|---|---:|
| Folder | `C:\SuperLive_Recordings` |
| Target FPS | 10 |
| Segment | 30 minutes |
| Disk check | 60 seconds |
| Rotation starts | drive used >= 80% |
| Rotation stops | drive used <= 78% |

Files are organized by date and timestamp, for example:

```text
C:\SuperLive_Recordings\2026-09-27\SuperLive_20260927_013000.mp4
```

## Why segmented recording?

A single never-ending MP4 is fragile: a crash, forced shutdown or storage failure can damage a very large file. Thirty-minute segments reduce the loss window and make retention deletion simple.

## Rotation algorithm

The recorder never deletes the currently open segment. At the threshold it sorts closed `SuperLive_*.mp4` files by modification time, deletes the oldest first, and stops after the drive drops to the configured lower watermark.

## VMware thin-provision warning

A guest may report a 2 TB C: drive even when the host stores the thin VMDK on a much smaller physical SSD. Guest `80%` protection does **not** protect the host from running out of physical space first. Always size the VMDK and host storage together. For a 512 GB host SSD, use a smaller VMDK or a fixed recording-folder quota rather than trusting a guest 2 TB thin disk.

## Capacity estimate

Actual consumption depends on resolution, scene complexity, codec and FPS. Measure your own average MB/minute for several hours before planning retention. The recorder intentionally controls storage by disk pressure rather than assuming a fixed bitrate.

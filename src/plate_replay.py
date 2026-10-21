"""Resume private, CPU-limited plate scans of completed recordings.

Run with the installed .plate-venv Python. Explicit --video arguments take
priority, followed by the newest recordings from --record-root. This module
writes only local plate history; it never queues historical Telegram messages.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from plate_recognition import (DETECTOR_MODEL, MODEL_LABEL, OCR_MODEL, TAIPEI,
                               PlateConsensus, detect_plate_observations,
                               load_allowlist)
from record_protocol import atomic_json, read_json


SAMPLE_SECONDS = 0.5
FILE_QUIET_SECONDS = 15.0
PIPELINE_VERSION = "overlapping-tiles-2fps-v1"
NAME_TIME = re.compile(r"^SuperLive_(\d{8})_(\d{6})(?:_|\.)", re.IGNORECASE)


def identity(path):
    """A missing explicit input still gets a stable, reportable identity."""
    path = Path(path).resolve()
    try:
        stat = path.stat()
        size, modified = stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        size = modified = None
    return {"path": str(path), "size": size, "mtime_ns": modified}


def fingerprint(info):
    return hashlib.sha256(json.dumps(info, sort_keys=True).encode("utf-8")).hexdigest()


def video_started_at(path, info=None, now=None):
    """Recording filenames are wall-clock Taiwan time; unknown names use mtime."""
    match = NAME_TIME.match(Path(path).name)
    if match:
        try:
            value = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
            return value.replace(tzinfo=TAIPEI).timestamp(), "recording_filename"
        except ValueError:
            pass
    info = identity(path) if info is None else info
    if info["mtime_ns"] is not None:
        return info["mtime_ns"] / 1_000_000_000, "file_mtime"
    return time.time() if now is None else now, "missing_file"


def path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def path_shard(path, shard_count):
    """A newly recorded file must not move older files between workers."""
    return int(hashlib.sha256(path_key(path).encode()).hexdigest(), 16) % shard_count


def discover_videos(record_root=None, videos=(), days=7, now=None):
    """Keep explicit input order, including an explicitly supplied Telegram copy.

    Automatic discovery excludes these derived copies. The explicit override is
    needed when a user's original example exists only under that filename.
    """
    now = time.time() if now is None else float(now)
    cutoff = now - float(days) * 86400
    ordered = [Path(path).resolve() for path in videos]
    automatic = []
    if record_root is not None:
        for path in Path(record_root).rglob("*"):
            if path.suffix.lower() != ".mp4" or "_telegram" in path.stem.lower():
                continue
            try:
                info = identity(path)
                stamp, _ = video_started_at(path, info, now)
                if info["size"] is not None and cutoff <= stamp <= now + 60:
                    automatic.append((stamp, path.resolve()))
            except OSError:
                # A disappeared directory entry can be discovered next run.
                continue
    ordered.extend(path for _, path in sorted(automatic, key=lambda item: (
        item[0], str(item[1])), reverse=True))
    unique, seen = [], set()
    for path in ordered:
        key = path_key(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def source_busy(root, path, info, now=None, quiet_seconds=FILE_QUIET_SECONDS):
    now = time.time() if now is None else float(now)
    state = read_json(Path(root) / "runtime" / "recording_state.json", {})
    try:
        fresh = isinstance(state, dict) and 0 <= now - float(state["updated_at"]) <= 30
    except (KeyError, TypeError, ValueError, OverflowError):
        fresh = False
    if (fresh and state.get("status") in ("STARTING", "RECORDING")
            and state.get("file") and path_key(state["file"]) == path_key(path)):
        return True
    return (info["mtime_ns"] is not None
            and now - info["mtime_ns"] / 1_000_000_000 < quiet_seconds)


def make_alpr():
    """Use one CPU worker with two inference threads, leaving live AI running."""
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["OPENBLAS_NUM_THREADS"] = "2"
    import cv2
    import onnxruntime as ort
    from fast_alpr import ALPR
    cv2.setNumThreads(1)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ALPR(detector_model=DETECTOR_MODEL, ocr_model=OCR_MODEL,
                detector_providers=["CPUExecutionProvider"],
                ocr_providers=["CPUExecutionProvider"], ocr_device="cpu",
                detector_sess_options=options, ocr_sess_options=options)


class VideoReadError(RuntimeError):
    pass


def open_video(path):
    import cv2
    # Decoder threading is separate from OpenCV's image-operation pool.
    if hasattr(cv2, "CAP_PROP_N_THREADS"):
        capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
        if capture.isOpened():
            return capture
        capture.release()
    return cv2.VideoCapture(str(path))


def scan_video(path, alpr, allowlist=(), *, info=None, detector=detect_plate_observations,
               capture_factory=None, sleeper=time.sleep, progress=None):
    """Fully decode one file, sampling independent frames at 2 fps.

    Results remain in memory until run_batch verifies that the original file
    stayed unchanged. Each file has its own consensus and original timestamps.
    """
    import cv2
    path = Path(path).resolve()
    info = identity(path) if info is None else info
    digest = fingerprint(info)
    start, basis = video_started_at(path, info)
    result = {"events": [], "outcome": "no_plate", "processed_frames": 0,
              "decoded_frames": 0, "detections": 0, "duration_seconds": 0.0,
              "started_at": start, "timestamp_basis": basis, "error": ""}
    capture = None
    tracker = PlateConsensus(allowlist)
    try:
        capture = (capture_factory or open_video)(str(path))
        if not capture.isOpened():
            raise VideoReadError("open_failed")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or not math.isfinite(count) or count < 1:
            raise VideoReadError("invalid_video_metadata")
        count = int(count)
        result["duration_seconds"] = count / fps
        next_sample, previous_offset = 0.0, -1.0
        for index in range(count):
            if not capture.grab():
                raise VideoReadError("incomplete_decode")
            result["decoded_frames"] += 1
            position = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
            nominal = index / fps
            offset = (position if math.isfinite(position) and 0 <= position <= count / fps + 1
                      and position > previous_offset else nominal)
            # Some codecs do not expose presentation timestamps. Never create
            # duplicate samples or move a timestamp backwards in that case.
            if offset <= previous_offset:
                offset = max(nominal, previous_offset + 1 / fps)
            previous_offset = offset
            if offset + 1e-7 < next_sample:
                continue
            okay, frame = capture.retrieve()
            if not okay or frame is None:
                raise VideoReadError("frame_decode_failed")
            observations = detector(alpr, frame)
            result["processed_frames"] += 1
            result["detections"] += len(observations)
            for observation in observations:
                selected = observation.get("selected")
                if selected is None:
                    continue
                event = tracker.observe(selected["raw_text"], selected["confidence"],
                    start + offset, "replay:%s:%d" % (digest, index),
                    observation["bbox"], observation["detector_confidence"],
                    observation.get("variants"))
                if event is None:
                    continue
                event.update(source="recording_replay", source_video=str(path),
                    offset_seconds=offset, timestamp_basis=basis,
                    source_video_started_at=datetime.fromtimestamp(start, TAIPEI).isoformat())
                event_key = json.dumps([digest, index, event["text"], event.get("matched")])
                event["id"] = "replay:" + hashlib.sha256(event_key.encode()).hexdigest()
                for evidence in event["evidence"]:
                    evidence["offset_seconds"] = evidence["timestamp"] - start
                result["events"].append(event)
            next_sample = (math.floor((offset + 1e-7) / SAMPLE_SECONDS) + 1) * SAMPLE_SECONDS
            if progress is not None:
                progress(result["processed_frames"], offset, result["duration_seconds"])
            sleeper(0.02)
        # A growing file or inconsistent metadata cannot be called a full scan.
        if capture.grab():
            raise VideoReadError("frame_count_changed")
        result["outcome"] = ("confirmed" if result["events"] else
                             "insufficient" if result["detections"] else "no_plate")
    except Exception as error:
        result["outcome"] = "error"
        result["error"] = str(error) if isinstance(error, VideoReadError) else type(error).__name__
    finally:
        if capture is not None:
            capture.release()
    return result


def summary_event(path, info, result):
    stamp = result["started_at"]
    return {"id": "replay-summary:" + fingerprint(info), "timestamp": stamp,
            "time": datetime.fromtimestamp(stamp, TAIPEI).isoformat(timespec="seconds"),
            "source": "recording_replay", "source_video": str(Path(path).resolve()),
            "offset_seconds": 0.0, "timestamp_basis": result["timestamp_basis"],
            "confirmed": False, "outcome": result["outcome"], "error": result["error"],
            "processed_frames": result["processed_frames"], "detections": result["detections"],
            "duration_seconds": result["duration_seconds"], "model": MODEL_LABEL}


@contextmanager
def batch_lock(state_root, name="batch"):
    """Only one process may modify a manifest; the OS releases crashed locks."""
    path = Path(state_root) / (name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def run_batch(root, videos, state_root=None, *, alpr_factory=make_alpr,
              detector=detect_plate_observations, capture_factory=None,
              history_writer=None, clock=time.time, sleeper=time.sleep,
              quiet_seconds=FILE_QUIET_SECONDS, reporter=print,
              shard_index=0, shard_count=1):
    """Resume complete fingerprints; retry errors and files changed mid-scan."""
    if history_writer is None:
        from plate_history import record_result
        history_writer = record_result
    root = Path(root).resolve()
    state_root = Path(state_root) if state_root else root / "runtime" / "plate_replay"
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("invalid shard index/count")
    videos = [Path(path).resolve() for path in videos]
    batch_total = len(videos)
    videos = [path for path in videos if path_shard(path, shard_count) == shard_index]
    allowlist = load_allowlist(root)
    settings_key = fingerprint({"pipeline": PIPELINE_VERSION, "allowlist": allowlist})
    totals = dict(total=len(videos), complete=0, skipped=0, pending=0, errors=0,
                  confirmed=0, history_written=0, ignored_retention=0)
    shard_name = "%d-of-%d" % (shard_index, shard_count)
    with batch_lock(state_root, "batch-" + shard_name):
        alpr, model_error = None, None

        def status(stage, **extra):
            atomic_json(state_root / ("progress-" + shard_name + ".json"),
                dict(status=stage, updated_at=clock(), pid=os.getpid(),
                     shard_index=shard_index, shard_count=shard_count,
                     batch_total=batch_total, **totals, **extra))

        status("RUNNING")
        for number, path in enumerate(videos, 1):
            key = path_key(path)
            manifest_path = state_root / "manifest" / (hashlib.sha256(key.encode()).hexdigest() + ".json")
            info = identity(path)
            previous = read_json(manifest_path, {})
            previous = previous if isinstance(previous, dict) else {}
            if (previous.get("identity") == info and previous.get("settings") == settings_key
                    and previous.get("state") == "complete"):
                totals["skipped"] += 1
                continue
            if source_busy(root, path, info, clock(), quiet_seconds):
                atomic_json(manifest_path, {"identity": info, "settings": settings_key,
                    "state": "pending", "reason": "recording_or_recent_write"})
                totals["pending"] += 1
                reporter("[%d/%d] %s: pending" % (number, len(videos), path.name))
                continue
            reporter("[%d/%d] %s: scanning" % (number, len(videos), path.name))
            status("RUNNING", current_video=path.name, current_index=number,
                   processed_frames=0, offset_seconds=0)
            last_progress = [0.0]

            def progress(frames, offset, duration):
                now = clock()
                if now - last_progress[0] >= 2:
                    status("RUNNING", current_video=path.name, current_index=number,
                           processed_frames=frames, offset_seconds=offset, duration_seconds=duration)
                    last_progress[0] = now

            if alpr is None and model_error is None:
                try:
                    alpr = alpr_factory()
                except Exception as error:
                    model_error = type(error).__name__
            if model_error is not None:
                start, basis = video_started_at(path, info, clock())
                result = dict(events=[], outcome="error", error="model_init:" + model_error,
                    started_at=start, timestamp_basis=basis, processed_frames=0,
                    decoded_frames=0, detections=0, duration_seconds=0.0)
            else:
                result = scan_video(path, alpr, allowlist, info=info, detector=detector,
                    capture_factory=capture_factory, sleeper=sleeper, progress=progress)
            after = identity(path)
            if after != info or source_busy(root, path, after, clock(), quiet_seconds):
                atomic_json(manifest_path, {"identity": after, "settings": settings_key,
                    "state": "pending", "reason": "source_changed_during_scan"})
                totals["pending"] += 1
                reporter("[%d/%d] %s: changed; retry next run" % (number, len(videos), path.name))
                continue
            events = list(result["events"])
            if not events or result["outcome"] == "error":
                # A valid earlier recognition does not hide an incomplete scan.
                events.append(summary_event(path, info, result))
            written, ignored, rejected = 0, 0, 0
            for event in events:
                now = clock()
                if history_writer(root, event, now=now):
                    written += 1
                elif event["timestamp"] < now - 7 * 86400:
                    ignored += 1
                else:
                    rejected += 1
            totals["history_written"] += written
            totals["ignored_retention"] += ignored
            totals["confirmed"] += len(result["events"])
            if result["outcome"] == "error":
                totals["errors"] += 1
            else:
                totals["complete"] += 1
            atomic_json(manifest_path, {"identity": info, "settings": settings_key,
                "state": "error" if result["outcome"] == "error" else "complete",
                "completed_at": clock(), "summary": {k: v for k, v in result.items() if k != "events"},
                "confirmed_count": len(result["events"]), "history_written": written,
                "ignored_retention": ignored, "history_rejected": rejected})
            reporter("[%d/%d] %s: %s; samples=%d; confirmed=%d" % (
                number, len(videos), path.name, result["outcome"],
                result["processed_frames"], len(result["events"])))
        status("COMPLETE_WITH_ERRORS" if totals["errors"] else
               "PENDING" if totals["pending"] else "COMPLETE")
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Installed SuperLive_AI folder")
    parser.add_argument("--video", type=Path, action="append", default=[], help="Explicit input; repeatable")
    parser.add_argument("--record-root", type=Path, help="Recursively scan recent MP4 recordings")
    parser.add_argument("--days", type=float, default=7, help="Automatic discovery window (default: 7)")
    parser.add_argument("--state-root", type=Path, help="Private manifest/progress folder")
    parser.add_argument("--shard-index", type=int, default=0, help="Zero-based worker shard")
    parser.add_argument("--shard-count", type=int, default=1, help="Number of CPU workers")
    args = parser.parse_args(argv)
    if not args.video and args.record_root is None:
        parser.error("provide --video and/or --record-root")
    if not math.isfinite(args.days) or not 0 < args.days <= 7:
        parser.error("--days must be greater than zero and no more than seven")
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("--shard-index must be between zero and --shard-count minus one")
    if args.record_root is not None and not args.record_root.is_dir():
        parser.error("--record-root must be an existing directory")
    isolated = args.root / ".plate-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not isolated.is_file():
        print("Plate replay requires the installed .plate-venv runtime.", flush=True)
        return 2
    if path_key(sys.executable) != path_key(isolated):
        return subprocess.call([str(isolated), "-u", str(Path(__file__).resolve()),
                                *(sys.argv[1:] if argv is None else argv)])

    def stopped_status(stage, error=""):
        state_root = args.state_root or args.root / "runtime" / "plate_replay"
        path = state_root / ("progress-%d-of-%d.json" % (args.shard_index, args.shard_count))
        state = read_json(path, {})
        state = state if isinstance(state, dict) else {}
        state.update(status=stage, error=error, updated_at=time.time())
        atomic_json(path, state)

    try:
        videos = discover_videos(args.record_root, args.video, args.days)
        totals = run_batch(args.root, videos, args.state_root,
                           shard_index=args.shard_index, shard_count=args.shard_count)
        print("Batch complete: " + json.dumps(totals), flush=True)
        return 1 if totals["errors"] else 0
    except KeyboardInterrupt:
        stopped_status("STOPPED")
        print("Batch interrupted; finished files remain resumable.", flush=True)
        return 130
    except Exception as error:
        stopped_status("ERROR", type(error).__name__)
        print("Batch stopped: " + type(error).__name__, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

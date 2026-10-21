"""Resolve private recording locations and reveal one file in Explorer."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from record_protocol import read_json


class VideoLocationError(ValueError):
    """A readable failure that the preview can show without opening a file."""


def _absolute_video(value):
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        return None
    try:
        path = Path(value)
        if not path.is_absolute() or path.suffix.lower() != ".mp4":
            return None
        return path.resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def _existing(path):
    try:
        if path.is_file():
            return path
    except OSError:
        pass
    raise VideoLocationError("找不到原始影片，可能已移動或刪除：" + path.name)


def _key(path):
    return os.path.normcase(str(path))


def _replay_digest(event):
    identifier = event.get("id", "")
    if isinstance(identifier, str) and re.fullmatch(r"replay-summary:[0-9a-f]{64}", identifier):
        return identifier.split(":", 1)[1]
    evidence = event.get("evidence", [])
    values = set()
    if isinstance(evidence, list):
        for item in evidence:
            sample = item.get("sample_id") if isinstance(item, dict) else None
            match = re.fullmatch(r"replay:([0-9a-f]{64}):\d+", sample) if isinstance(sample, str) else None
            if match:
                values.add(match.group(1))
    return next(iter(values)) if len(values) == 1 else None


def resolve_video_location(root, event, record_root=None, known_roots=()):
    """Read local evidence; never launch a process or search an entire drive.

    Absolute recorded paths are authoritative. Old replay rows can be resolved
    through their manifest fingerprint. Basename-only rows must have exactly
    one candidate; a missing original is never replaced by another namesake.
    """
    if not isinstance(event, dict):
        raise VideoLocationError("這筆紀錄沒有影片資訊")
    root = Path(root).resolve()
    source = event.get("source_video")
    name = str(source or "").replace("\\", "/").rsplit("/", 1)[-1]
    for field in ("source_video_path", "source_video"):
        explicit = _absolute_video(event.get(field))
        if explicit is not None:
            if name and explicit.name.casefold() != name.casefold():
                raise VideoLocationError("影片名稱與儲存路徑不一致，未開啟任何檔案")
            return _existing(explicit)
        if field == "source_video_path" and event.get(field):
            raise VideoLocationError("影片路徑格式無效，未開啟任何檔案")
    if not name or Path(name).suffix.lower() != ".mp4" or "\0" in name:
        raise VideoLocationError("這筆紀錄沒有有效的 MP4 影片名稱")

    digest = _replay_digest(event)
    matching, by_name, unreadable = {}, {}, False
    try:
        manifests = list((root/"runtime"/"plate_replay"/"manifest").glob("*.json"))
    except OSError:
        manifests, unreadable = [], True
    for manifest in manifests:
        value = read_json(manifest)
        info = value.get("identity") if isinstance(value, dict) else None
        if not isinstance(info, dict):
            unreadable = True
            continue
        path = _absolute_video(info.get("path"))
        if path is None:
            unreadable = True
            continue
        if path.name.casefold() != name.casefold():
            continue
        by_name[_key(path)] = path
        if digest and hashlib.sha256(json.dumps(info, sort_keys=True).encode("utf-8")).hexdigest() == digest:
            matching[_key(path)] = path
    if len(matching) == 1:
        return _existing(next(iter(matching.values())))
    if unreadable:
        raise VideoLocationError("影片索引暫時無法讀取，請稍後再試")
    if digest and by_name:
        raise VideoLocationError("影片索引已變更，無法確認這筆紀錄的原始檔案")
    if record_root is None:
        config = read_json(root/"config"/"recording_config.json", {})
        record_root = config.get("record_root", "C:/SuperLive_Recordings") if isinstance(config, dict) else "C:/SuperLive_Recordings"
    folders = [root]
    folders.extend(Path(path) for path in known_roots)
    if record_root:
        records = Path(record_root)
        records = records.resolve() if records.is_absolute() else (root/records).resolve()
        folders.append(records)
        try:
            folders.extend(path for path in records.iterdir()
                           if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name) and path.is_dir())
        except OSError:
            pass
    # A manifest path and another known file with the same basename remain
    # ambiguous without a fingerprint, even if one original is now missing.
    candidates = dict(by_name)
    for folder in folders:
        try:
            path = (folder/name).resolve()
            if path.is_file():
                candidates[_key(path)] = path
        except (OSError, ValueError, RuntimeError):
            continue
    if len(candidates) > 1:
        raise VideoLocationError("找到多個同名影片，無法確認原始檔案：" + name)
    if not candidates:
        raise VideoLocationError("找不到原始影片，可能已移動或刪除：" + name)
    return _existing(next(iter(candidates.values())))


def reveal_video(root, event, record_root=None, known_roots=(), runner=None):
    """Select the MP4 in Explorer, without executing it or invoking a shell."""
    path = resolve_video_location(root, event, record_root, known_roots)
    try:
        (runner or subprocess.Popen)(["explorer.exe", "/select,", str(path)], shell=False)
    except OSError as error:
        raise VideoLocationError("無法開啟檔案總管，請稍後再試") from error
    return path

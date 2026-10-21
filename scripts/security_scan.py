#!/usr/bin/env python3
"""Fail closed on likely credentials and private runtime files before publishing.

Only the generated demo assets and application icons have a binary-media exception. Findings
include paths and line numbers, never matched credential values. Local caches
may be skipped only when the Git index contains no files beneath them.
"""
from __future__ import annotations

import codecs
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {'.venv', '.plate-venv', 'venv', '__pycache__', '.idea', '.vscode'}
PRIVATE_CACHE_DIRS = {'.venv', '.plate-venv', 'venv', '__pycache__'}
FORBIDDEN_DIR_PARTS = {'logs', 'events', 'runtime', 'recordings', 'superlive_recordings', 'backup', 'backups'}
FORBIDDEN_SUFFIXES = {'.log', '.pyc', '.pyo', '.zip', '.7z', '.rar', '.tar', '.gz', '.pt', '.onnx',
                      '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.mp4', '.avi', '.mkv', '.mov', '.webm'}
MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_DEMO_BYTES = 50 * 1024 * 1024
DEMO_MEDIA = {
    'assets/superlive-ai.png': 'png',
    'assets/superlive-ai.ico': 'ico',
    'docs/media/polygon-selection.png': 'png',
    'docs/media/activity-recording.png': 'png',
    'docs/media/inactivity-rearm.png': 'png',
    'docs/media/superlive-demo.gif': 'gif',
    'docs/media/superlive-demo.mp4': 'mp4',
}
PATTERNS = {
    'Telegram bot token': re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b'),
    'OpenAI-style secret': re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
    'AWS access key': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    'Private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'Numeric Telegram chat id assignment': re.compile(
        r'''(?im)(?<!\w)["']?(?:telegram_)?chat_id["']?\s*[:=]\s*["']?-?\d+["']?(?=\s*(?:[,;#\r\n}]|$))'''),
}


def _forbidden_name(name):
    name = name.casefold()
    if name in {'.env.example', 'telegram.env.example'}:
        return False
    return (name == '.env' or name.startswith('.env.')
            or name == 'telegram.env' or name.startswith('telegram.env.')
            or name in {'plate_allowlist.json', 'capture_config.json'}
            or name.startswith(('telegram_profile', 'credentials', 'secrets')))


def _tracked_paths(root):
    """Read the index without inspecting Git history or credentials."""
    if not (root / '.git').exists():
        return set()
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '--cached', '-z'],
                            capture_output=True, check=True)
    return {path.casefold() for path in result.stdout.decode('utf-8', errors='surrogateescape').split('\0') if path}


def _valid_media(data, kind):
    if kind == 'ico':
        return data.startswith(b'\x00\x00\x01\x00')
    if kind == 'png':
        return data.startswith(b'\x89PNG\r\n\x1a\n')
    if kind == 'gif':
        return data.startswith((b'GIF87a', b'GIF89a'))
    return len(data) >= 12 and data[4:8] == b'ftyp'


def _decode(data):
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data.decode('utf-32', errors='ignore')
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode('utf-16', errors='ignore')
    return data.decode('utf-8-sig', errors='ignore')


def scan(root):
    root = Path(root).resolve()
    findings = []
    if not root.is_dir():
        return ['SCAN ROOT IS NOT A DIRECTORY']
    try:
        tracked = _tracked_paths(root)
    except (OSError, subprocess.SubprocessError):
        return ['UNABLE TO READ GIT INDEX: tracked files could not be verified']

    def walk_error(error):
        findings.append('UNREADABLE PATH: ' + str(Path(error.filename).relative_to(root)))

    for directory, children, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        folder = Path(directory)
        kept = []
        for name in children:
            path = folder / name
            relative = path.relative_to(root)
            normalized = relative.as_posix().casefold()
            if len(relative.parts) == 1 and name == '.git':
                continue
            if path.is_symlink():
                findings.append(f'SYMLINK NOT ALLOWED: {relative}')
                continue
            if name.casefold() in SKIP_DIRS and not any(value.startswith(normalized + '/') for value in tracked):
                continue
            kept.append(name)
        children[:] = kept
        for name in filenames:
            path = folder / name
            relative = path.relative_to(root)
            normalized = relative.as_posix().casefold()
            if relative.as_posix() == '.git':
                continue
            parts = {part.casefold() for part in relative.parts[:-1]}
            if path.is_symlink():
                findings.append(f'SYMLINK NOT ALLOWED: {relative}')
                continue
            if _forbidden_name(name):
                findings.append(f'FORBIDDEN FILE: {relative}')
                continue
            if parts & FORBIDDEN_DIR_PARTS or (normalized in tracked and parts & PRIVATE_CACHE_DIRS):
                findings.append(f'PRIVATE RUNTIME PATH: {relative}')
                continue
            media_kind = DEMO_MEDIA.get(relative.as_posix())
            if path.suffix.casefold() in FORBIDDEN_SUFFIXES and media_kind is None:
                findings.append(f'PRIVATE OR UNAPPROVED ARTIFACT: {relative}')
                continue
            try:
                maximum = MAX_DEMO_BYTES if media_kind else MAX_TEXT_BYTES
                if path.stat().st_size > maximum:
                    findings.append(f'OVERSIZED UNSCANNED FILE: {relative}')
                    continue
                data = path.read_bytes()
            except OSError:
                findings.append(f'UNREADABLE FILE: {relative}')
                continue
            if media_kind and not _valid_media(data, media_kind):
                findings.append(f'INVALID DEMO MEDIA: {relative}')
                continue
            text = _decode(data)
            for label, pattern in PATTERNS.items():
                for match in pattern.finditer(text):
                    line = text.count('\n', 0, match.start()) + 1
                    findings.append(f'{label}: {relative}:{line}')
    return findings


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    findings = scan(argv[0] if argv else '.')
    print('SECURITY_SCAN=FAIL' if findings else 'SECURITY_SCAN=PASS')
    for finding in findings:
        print(' -', finding)
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())

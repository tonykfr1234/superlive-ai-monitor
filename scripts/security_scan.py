#!/usr/bin/env python3
"""Fail closed when likely secrets/private runtime files are present."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".idea", ".vscode"}
FORBIDDEN_NAMES = {
    "telegram.env",
    ".env",
    "credentials.json",
    "secrets.json",
}
FORBIDDEN_DIR_PARTS = {"logs", "events", "runtime", "recordings", "SuperLive_Recordings"}

PATTERNS = {
    "Telegram bot token": re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Numeric Telegram chat id assignment": re.compile(r"(?im)^\s*CHAT_ID\s*=\s*-?\d{6,}\s*$"),
}

# Placeholder values that are intentionally safe in examples.
SAFE_MARKERS = (
    "YOUR_TELEGRAM_BOT_TOKEN",
    "YOUR_NUMERIC_CHAT_ID",
    "example",
    "placeholder",
)

findings: list[str] = []

for path in ROOT.rglob("*"):
    rel = path.relative_to(ROOT)
    if any(part in SKIP_DIRS for part in rel.parts):
        continue

    if path.is_dir():
        continue

    if path.name in FORBIDDEN_NAMES:
        findings.append(f"FORBIDDEN FILE: {rel}")
        continue

    if any(part in FORBIDDEN_DIR_PARTS for part in rel.parts):
        findings.append(f"PRIVATE RUNTIME PATH: {rel}")
        continue

    try:
        if path.stat().st_size > 5 * 1024 * 1024:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue

    lower = text.lower()
    safe_example = any(marker.lower() in lower for marker in SAFE_MARKERS)

    for name, rx in PATTERNS.items():
        for match in rx.finditer(text):
            # Safe example files may contain placeholder assignment names, but
            # never allow a token-shaped string even in examples.
            if safe_example and name == "Numeric Telegram chat id assignment":
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{name}: {rel}:{line}")

if findings:
    print("SECURITY_SCAN=FAIL")
    for item in findings:
        print(" -", item)
    raise SystemExit(1)

print("SECURITY_SCAN=PASS")

#!/usr/bin/env python3
"""
youtube_utils.py — Shared utilities for youtube-content scripts.

Centralizes logic that used to be duplicated across scripts:
  - YouTube URL / video ID extraction
  - .env loading (with correct path resolution)
  - Safe filename generation (Windows-compatible)
  - GPU detection for Whisper device selection
  - JSON stdout output helper
"""

import os
import re
import sys
import json
from pathlib import Path

# ── YouTube ID extraction ───────────────────────────────────────────────

_VIDEO_ID_PATTERNS = [
    r'(?:youtube\.com/watch\?.*v=)([A-Za-z0-9_-]{11})',
    r'(?:youtu\.be/)([A-Za-z0-9_-]{11})',
    r'(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})',
    r'(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})',
    r'(?:youtube\.com/v/)([A-Za-z0-9_-]{11})',
    r'(?:youtube\.com/live/)([A-Za-z0-9_-]{11})',
    r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})',
    r'^([A-Za-z0-9_-]{11})$',
]


def extract_video_id(url_or_id: str) -> str | None:
    """Extract YouTube video ID from various URL formats or a raw ID."""
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()
    for pattern in _VIDEO_ID_PATTERNS:
        m = re.search(pattern, url_or_id)
        if m:
            return m.group(1)
    return None


def safe_video_id(url_or_id: str, fallback: str = 'video') -> str:
    """Return a clean 11-char video ID safe to use in filenames."""
    vid = extract_video_id(url_or_id)
    return vid if vid else fallback


# ── .env loading ────────────────────────────────────────────────────────

def load_env():
    """
    Load .env from the skill directory (script_dir/../../.env).

    Uses python-dotenv when available; silently skips if not installed.
    Does NOT override variables already set in the environment.
    """
    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / '.env',                    # scripts/.env
        script_dir.parent / '.env',             # skill_root/.env
        script_dir.parent.parent / '.env',      # parent of skill_root
        Path.home() / '.hermes' / '.env',       # ~/.hermes/.env
    ]
    for path in candidates:
        if path.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(str(path), override=False)
            except ImportError:
                # Manual parsing fallback (no python-dotenv)
                _manual_load(path)
            return path
    return None


def _manual_load(path: Path):
    """Minimal .env parser without python-dotenv."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


# ── Safe filenames ──────────────────────────────────────────────────────

_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(title: str, max_len: int = 80) -> str:
    """Sanitize a title into a Windows-safe filename."""
    if not title:
        return 'video'
    cleaned = _ILLEGAL_FILENAME_CHARS.sub('_', title).strip(' .')
    return cleaned[:max_len] or 'video'


# ── VTT parsing ─────────────────────────────────────────────────────────

def _parse_vtt_timestamp(ts: str) -> float:
    """Parse 'HH:MM:SS.mmm' or 'MM:SS.mmm' into seconds."""
    parts = ts.strip().split(':')
    sec_ms = parts[-1].split('.')
    sec = int(sec_ms[0])
    ms = int(sec_ms[1]) if len(sec_ms) > 1 else 0
    total = sec + ms / 1000.0
    if len(parts) > 1:
        total += int(parts[-2]) * 60
    if len(parts) > 2:
        total += int(parts[-3]) * 3600
    return total


def format_vtt(vtt_content: str, include_timestamps: bool = False) -> list:
    """
    Parse VTT subtitle content into clean lines.

    Returns a list of strings. When include_timestamps is True, each line
    is prefixed with [MM:SS].
    """
    lines = []
    cue_re = re.compile(r'(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3})\s*-->')
    current_time = None
    current_text = []

    for raw in vtt_content.split('\n'):
        line = raw.strip()
        if '-->' in line:
            # Flush previous cue
            if current_time is not None and current_text:
                text = ' '.join(current_text)
                if include_timestamps:
                    lines.append(f'[{int(current_time//60):02d}:{int(current_time%60):02d}] {text}')
                else:
                    lines.append(text)
            m = cue_re.search(line)
            if m:
                h, mnt, sec, ms = m.groups()
                current_time = (int(h or 0) * 3600 + int(mnt) * 60
                                + int(sec) + int(ms) / 1000.0)
            else:
                current_time = None
            current_text = []
        elif line and not line.startswith(('WEBVTT', 'Kind:', 'Language:', 'NOTE', 'STYLE')):
            cleaned = re.sub(r'<[^>]+>', '', line)
            if cleaned:
                current_text.append(cleaned)

    # Flush last cue
    if current_time is not None and current_text:
        text = ' '.join(current_text)
        if include_timestamps:
            lines.append(f'[{int(current_time//60):02d}:{int(current_time%60):02d}] {text}')
        else:
            lines.append(text)

    return lines


# ── GPU detection ───────────────────────────────────────────────────────

def detect_device(preferred: str = None) -> str:
    """
    Return the best torch device for Whisper.

    - 'auto' / None: cuda if available, else cpu
    - 'cuda' / 'cpu': forced as-is
    """
    if preferred and preferred != 'auto':
        return preferred
    try:
        import torch
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    except ImportError:
        return 'cpu'


# ── JSON output helpers ─────────────────────────────────────────────────

def emit_json(data: dict, exit_code: int = 0):
    """Print JSON to stdout and exit. Keeps stdout clean from progress bars."""
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(exit_code)

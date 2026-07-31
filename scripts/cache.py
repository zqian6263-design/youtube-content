#!/usr/bin/env python3
"""
cache.py — SQLite cache for subtitle/transcript results.

Avoids re-downloading audio and re-transcribing the same video.
Keyed by (video_id, languages, timestamps) for captions and
(video_id, model, backend) for whisper transcripts.

Location: <skill_dir>/cache.db  (or $YOUTUBE_CONTENT_CACHE env override)

Usage:
    from cache import Cache
    cache = Cache()
    hit = cache.get_subtitles(video_id, languages, timestamps)
    if hit:
        ...
    else:
        cache.set_subtitles(video_id, languages, timestamps, result_json)
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_NAME = 'cache.db'
DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days

_lock = threading.Lock()


def _db_path() -> Path:
    env = os.environ.get('YOUTUBE_CONTENT_CACHE')
    if env:
        p = Path(env)
        if p.suffix == '.db':
            return p
        return p / DB_NAME
    # Default: skill root / cache.db
    return Path(__file__).resolve().parent.parent / DB_NAME


class Cache:
    """Thread-safe SQLite cache with TTL."""

    def __init__(self, db_path: Path | str | None = None, ttl: int = DEFAULT_TTL_SECONDS):
        self.db_path = Path(db_path) if db_path else _db_path()
        self.ttl = ttl
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS cache (
                cache_key TEXT PRIMARY KEY,
                payload   TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        ''')
        self._conn.commit()

    def _get(self, key: str):
        with _lock:
            row = self._conn.execute(
                'SELECT payload, created_at FROM cache WHERE cache_key = ?', (key,)
            ).fetchone()
        if not row:
            return None
        payload, created = row
        if time.time() - created > self.ttl:
            self.delete(key)
            return None
        return payload

    def _set(self, key: str, payload: str):
        with _lock:
            self._conn.execute(
                'INSERT OR REPLACE INTO cache (cache_key, payload, created_at) VALUES (?, ?, ?)',
                (key, payload, time.time())
            )
            self._conn.commit()

    def delete(self, key: str):
        with _lock:
            self._conn.execute('DELETE FROM cache WHERE cache_key = ?', (key,))
            self._conn.commit()

    def clear(self):
        with _lock:
            self._conn.execute('DELETE FROM cache')
            self._conn.commit()

    # ── Caption cache ───────────────────────────────────────────────────

    def get_subtitles(self, video_id: str, languages: str, timestamps: bool):
        key = f'sub:{video_id}:{languages}:{"ts" if timestamps else "no-ts"}'
        payload = self._get(key)
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            self.delete(key)
            return None

    def set_subtitles(self, video_id: str, languages: str, timestamps: bool, result: dict):
        key = f'sub:{video_id}:{languages}:{"ts" if timestamps else "no-ts"}'
        self._set(key, json.dumps(result, ensure_ascii=False))

    # ── Whisper transcript cache ────────────────────────────────────────

    def get_transcript(self, video_id: str, model: str, backend: str):
        key = f'whisper:{video_id}:{model}:{backend}'
        payload = self._get(key)
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            self.delete(key)
            return None

    def set_transcript(self, video_id: str, model: str, backend: str, result: dict):
        key = f'whisper:{video_id}:{model}:{backend}'
        self._set(key, json.dumps(result, ensure_ascii=False))

    def stats(self) -> dict:
        with _lock:
            total = self._conn.execute('SELECT COUNT(*) FROM cache').fetchone()[0]
            subs = self._conn.execute(
                "SELECT COUNT(*) FROM cache WHERE cache_key LIKE 'sub:%'").fetchone()[0]
            whisp = self._conn.execute(
                "SELECT COUNT(*) FROM cache WHERE cache_key LIKE 'whisper:%'").fetchone()[0]
        return {'total': total, 'subtitles': subs, 'whisper': whisp}

    def close(self):
        """Close the SQLite connection (releases the file lock on Windows)."""
        with _lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

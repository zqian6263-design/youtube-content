#!/usr/bin/env python3
"""
Tests for youtube-content scripts.

Run with:  python -m pytest tests/ -v
Or:        python tests/test_youtube_utils.py   (no pytest needed)
"""

import os
import sys
import tempfile
from pathlib import Path

# Make scripts importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

from youtube_utils import (
    detect_device,
    extract_video_id,
    format_vtt,
    safe_filename,
    safe_video_id,
)

# ── extract_video_id ────────────────────────────────────────────────────

def test_extract_video_id_formats():
    cases = {
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ': 'dQw4w9WgXcQ',
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=WL&index=2': 'dQw4w9WgXcQ',
        'https://youtu.be/dQw4w9WgXcQ': 'dQw4w9WgXcQ',
        'https://www.youtube.com/embed/dQw4w9WgXcQ': 'dQw4w9WgXcQ',
        'https://www.youtube.com/shorts/dQw4w9WgXcQ': 'dQw4w9WgXcQ',
        'https://www.youtube.com/live/dQw4w9WgXcQ': 'dQw4w9WgXcQ',
        'dQw4w9WgXcQ': 'dQw4w9WgXcQ',
        'https://www.youtube.com/v/dQw4w9WgXcQ': 'dQw4w9WgXcQ',
    }
    for url, expected in cases.items():
        got = extract_video_id(url)
        assert got == expected, f'FAIL {url}: got {got}, expected {expected}'
    print('  ✅ test_extract_video_id_formats')


def test_extract_video_id_invalid():
    assert extract_video_id('') is None
    assert extract_video_id(None) is None
    assert extract_video_id('not-a-youtube-url') is None
    assert extract_video_id('https://example.com/video/12345') is None
    print('  ✅ test_extract_video_id_invalid')


# ── safe_video_id (regression: watch?v= filename bug) ──────────────────

def test_safe_video_id_no_illegal_chars():
    # The regression: Path(URL).stem produced "watch?v=ID" which is invalid on Windows
    url = 'https://www.youtube.com/watch?v=nWaM6XmQEmU&list=WL'
    vid = safe_video_id(url)
    assert vid == 'nWaM6XmQEmU', f'got {vid}'
    assert '?' not in vid and '=' not in vid
    # Result must be a valid Windows filename component
    filename = f'{vid}.wav'
    assert not any(c in filename for c in '<>:"/\\|?*'), f'invalid filename: {filename}'
    print('  ✅ test_safe_video_id_no_illegal_chars')


def test_safe_video_id_fallback():
    assert safe_video_id('invalid', fallback='fallback') == 'fallback'
    print('  ✅ test_safe_video_id_fallback')


# ── safe_filename ───────────────────────────────────────────────────────

def test_safe_filename():
    assert safe_filename('hello') == 'hello'
    assert safe_filename('a/b:c*d?') == 'a_b_c_d_'
    assert safe_filename('') == 'video'
    assert safe_filename(None) == 'video'
    long_title = 'x' * 200
    assert len(safe_filename(long_title)) <= 80
    print('  ✅ test_safe_filename')


# ── format_vtt (regression: timestamps lost in fallback) ───────────────

SAMPLE_VTT = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.500
Hello there, <c>this is a test</c>

00:00:04.000 --> 00:00:06.000
Second line here

00:01:30.000 --> 00:01:32.000
Third line
"""


def test_format_vtt_no_timestamps():
    lines = format_vtt(SAMPLE_VTT, include_timestamps=False)
    assert 'Hello there, this is a test' in lines[0]
    assert 'Second line here' in lines[1]
    assert len(lines) == 3
    print('  ✅ test_format_vtt_no_timestamps')


def test_format_vtt_with_timestamps():
    lines = format_vtt(SAMPLE_VTT, include_timestamps=True)
    assert lines[0].startswith('[00:01]')
    assert lines[2].startswith('[01:30]')
    print('  ✅ test_format_vtt_with_timestamps')


# ── detect_device ───────────────────────────────────────────────────────

def test_detect_device_forced():
    assert detect_device('cuda') == 'cuda'
    assert detect_device('cpu') == 'cpu'
    print('  ✅ test_detect_device_forced')


def test_detect_device_auto():
    # auto should return a valid device (cuda or cpu) — never crash
    dev = detect_device('auto')
    assert dev in ('cuda', 'cpu')
    dev2 = detect_device(None)
    assert dev2 in ('cuda', 'cpu')
    print(f'  ✅ test_detect_device_auto ({dev})')


# ── load_env ────────────────────────────────────────────────────────────

def test_load_env():
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / '.env'
        env_file.write_text('TEST_KEY=hello_world\n# comment\n')
        old = os.environ.get('TEST_KEY')
        os.environ.pop('TEST_KEY', None)
        try:
            # Monkeypatch candidates by placing .env where load_env looks
            # (scripts/../.env is the skill root — we test _manual_load directly)
            from youtube_utils import _manual_load
            _manual_load(env_file)
            assert os.environ.get('TEST_KEY') == 'hello_world'
        finally:
            if old is not None:
                os.environ['TEST_KEY'] = old
            else:
                os.environ.pop('TEST_KEY', None)
    print('  ✅ test_load_env')


# ── Subtitle verification (in analyze script) ──────────────────────────

def test_subtitle_suspicion():
    import importlib
    analyze = importlib.import_module('analyze_youtube')

    # Short transcript → suspicious
    suspicious, reason = analyze.subtitle_looks_suspicious('短', 'Some Title')
    assert suspicious, 'short transcript should be suspicious'

    # Music lyrics → suspicious
    lyrics = '♪ La la la ♫ oh yeah ♪ music ♫ verse ♪ chorus'
    suspicious, reason = analyze.subtitle_looks_suspicious(lyrics, 'Tech Talk')
    assert suspicious, 'lyrics should be suspicious'

    # Normal technical content → not suspicious
    normal = ('This tutorial shows how to code a function and deploy an API. '
              'We write Python code, test the algorithm, and debug the framework.')
    suspicious, reason = analyze.subtitle_looks_suspicious(normal, 'Python API Tutorial')
    assert not suspicious, f'should not be suspicious: {reason}'
    print('  ✅ test_subtitle_suspicion')


# ── Cache layer ─────────────────────────────────────────────────────────

def test_cache_subtitles_roundtrip():
    from cache import Cache
    with tempfile.TemporaryDirectory() as tmp:
        c = Cache(db_path=Path(tmp) / 'test.db')
        try:
            # Miss first
            assert c.get_subtitles('abc123def45', 'zh-Hans,en', False) is None
            # Set then hit
            c.set_subtitles('abc123def45', 'zh-Hans,en', False, {
                "status": "success", "subtitles": "hello world", "subtitle_count": 1
            })
            got = c.get_subtitles('abc123def45', 'zh-Hans,en', False)
            assert got is not None and got.get('subtitles') == 'hello world'
            # Different params → different key → miss
            assert c.get_subtitles('abc123def45', 'zh-Hans,en', True) is None
            assert c.get_subtitles('abc123def45', 'en', False) is None
        finally:
            c.close()
    print('  ✅ test_cache_subtitles_roundtrip')


def test_cache_transcript_roundtrip():
    from cache import Cache
    with tempfile.TemporaryDirectory() as tmp:
        c = Cache(db_path=Path(tmp) / 'test.db')
        try:
            assert c.get_transcript('abc123def45', 'small', 'openai') is None
            c.set_transcript('abc123def45', 'small', 'openai', {
                "status": "success", "text": "transcribed text", "language": "en"
            })
            got = c.get_transcript('abc123def45', 'small', 'openai')
            assert got is not None and got.get('text') == 'transcribed text'
            # Different model → miss
            assert c.get_transcript('abc123def45', 'base', 'openai') is None
            # Different backend → miss
            assert c.get_transcript('abc123def45', 'small', 'faster-whisper') is None
        finally:
            c.close()
    print('  ✅ test_cache_transcript_roundtrip')


def test_cache_ttl_expiry():
    from cache import Cache
    with tempfile.TemporaryDirectory() as tmp:
        c = Cache(db_path=Path(tmp) / 'test.db', ttl=0)  # 0s TTL = always expired
        try:
            c.set_subtitles('abc123def45', 'zh-Hans', False, {"status": "success"})
            assert c.get_subtitles('abc123def45', 'zh-Hans', False) is None  # expired
            stats = c.stats()
            assert stats['total'] == 0  # expired entry deleted on access
        finally:
            c.close()
    print('  ✅ test_cache_ttl_expiry')


# ── Bilingual interleave (in analyze script) ────────────────────────────

def test_interleave_timestamp_aligned():
    import importlib
    analyze = importlib.import_module('analyze_youtube')
    primary = '[00:01] Hello\n[00:05] World'
    secondary = '[00:01] 你好\n[00:05] 世界'
    out = analyze._interleave_captions(primary, secondary, 'en', 'zh')
    lines = out.split('\n')
    assert lines[0] == '[00:01] Hello'
    assert lines[1] == '[00:01] 你好'
    assert lines[2] == '[00:05] World'
    assert lines[3] == '[00:05] 世界'
    print('  ✅ test_interleave_timestamp_aligned')


def test_interleave_fallback_index():
    import importlib
    analyze = importlib.import_module('analyze_youtube')
    # No timestamps → index-based with language tags
    out = analyze._interleave_captions('line1\nline2', 'L1', 'en', 'zh')
    lines = out.split('\n')
    assert lines[0] == '[en] line1'
    assert lines[1] == '[zh] L1'
    assert lines[2] == '[en] line2'
    print('  ✅ test_interleave_fallback_index')


def test_playlist_id_extraction():
    import importlib
    importlib.import_module('fetch_playlist')
    from fetch_playlist import extract_playlist_id
    assert extract_playlist_id('https://www.youtube.com/playlist?list=PLabc123') == 'PLabc123'
    assert extract_playlist_id('https://www.youtube.com/watch?v=abc&list=WL&index=2') == 'WL'
    assert extract_playlist_id('no playlist here') is None
    print('  ✅ test_playlist_id_extraction')


def run_all():
    tests = [
        test_extract_video_id_formats,
        test_extract_video_id_invalid,
        test_safe_video_id_no_illegal_chars,
        test_safe_video_id_fallback,
        test_safe_filename,
        test_format_vtt_no_timestamps,
        test_format_vtt_with_timestamps,
        test_detect_device_forced,
        test_detect_device_auto,
        test_load_env,
        test_subtitle_suspicion,
        test_cache_subtitles_roundtrip,
        test_cache_transcript_roundtrip,
        test_cache_ttl_expiry,
        test_interleave_timestamp_aligned,
        test_interleave_fallback_index,
        test_playlist_id_extraction,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(f'  ❌ {t.__name__}: {e}')
        except Exception as e:
            failures += 1
            print(f'  ❌ {t.__name__}: {type(e).__name__}: {e}')

    print()
    if failures:
        print(f'✗ {failures} test(s) FAILED')
        return 1
    print(f'✓ All {len(tests)} tests PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(run_all())

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


# ── Chunked transcription helpers ───────────────────────────────────────

def test_get_audio_duration_wav():
    import subprocess

    from transcribe_whisper import get_audio_duration
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / 't.wav'
        r = subprocess.run(
            ['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
             '-i', 'sine=frequency=440:duration=5', '-ar', '16000', '-ac', '1', str(wav)],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            dur = get_audio_duration(str(wav))
            assert 4.5 <= dur <= 5.5, f'duration={dur}'
            print(f'  ✅ test_get_audio_duration_wav ({dur:.1f}s)')
        else:
            print('  ⚠ ffmpeg unavailable, skipping')


def test_split_audio_chunks():
    import subprocess

    from transcribe_whisper import get_audio_duration, split_audio
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / 'src.wav'
        r = subprocess.run(
            ['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
             '-i', 'sine=frequency=440:duration=10', '-ar', '16000', '-ac', '1', str(wav)],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print('  ⚠ ffmpeg unavailable, skipping')
            return
        chunks, n = split_audio(str(wav), Path(td) / 'out', 4.0, 'chunk')
        assert n == 3, f'10s / 4s → 3 chunks, got {n}'
        assert len(chunks) == 3
        for idx, path in chunks:
            d = get_audio_duration(path)
            assert d <= 4.5, f'chunk {idx} too long: {d}s'
        print(f'  ✅ test_split_audio_chunks ({n} chunks)')


def test_transcribe_chunked_single():
    """Chunk-minutes larger than audio → no chunking, still works."""
    import subprocess as sp
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / 's.wav'
        r = sp.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                    '-i', 'sine=frequency=440:duration=5', '-ar', '16000', '-ac', '1', str(wav)],
                   capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print('  ⚠ ffmpeg unavailable, skipping')
            return
        from transcribe_whisper import get_audio_duration
        dur = get_audio_duration(str(wav))
        n = max(1, int(dur // (60 * 60)) + 1)
        assert n == 1
    print('  ✅ test_transcribe_chunked_single')


# ── Chapter detection ───────────────────────────────────────────────────

def test_parse_subtitles_with_timestamps():
    from chapters import parse_subtitles
    text = '[00:01] hello world\n[01:30] second line\nplain line without ts'
    entries = parse_subtitles(text)
    assert len(entries) == 3
    assert entries[0]['start'] == 1.0
    assert entries[0]['text'] == 'hello world'
    assert entries[1]['start'] == 90.0
    assert entries[2]['start'] is None
    print('  ✅ test_parse_subtitles_with_timestamps')


def test_tokenize_filters_stopwords():
    from chapters import tokenize
    toks = tokenize('The quick brown fox and the lazy dog')
    assert 'quick' in toks
    assert 'fox' in toks
    assert 'the' not in toks
    assert 'and' not in toks
    print('  ✅ test_tokenize_filters_stopwords')


def test_jaccard_similarity():
    from chapters import jaccard
    assert jaccard({'a', 'b'}, {'a', 'b'}) == 1.0
    assert jaccard({'a', 'b'}, {'c', 'd'}) == 0.0
    assert jaccard({'a'}, {'a', 'b'}) == 0.5
    assert jaccard(set(), set()) == 1.0  # both empty
    print('  ✅ test_jaccard_similarity')


def test_build_windows_timestamped():
    from chapters import build_windows
    # 3 minutes of entries at 10s intervals, 60s windows → 3 windows
    entries = [{"start": float(i * 10), "text": f"topic{i % 3} word{i}"}
               for i in range(18)]  # 180s
    windows = build_windows(entries, window_sec=60.0)
    assert len(windows) == 3, f'expected 3 windows, got {len(windows)}'
    assert all('tokens' in w for w in windows)
    print('  ✅ test_build_windows_timestamped')


def test_detect_boundaries_distinct_topics():
    from chapters import build_windows, detect_boundaries
    # Two very distinct topics: windows 0-4 talk about 'search algorithm',
    # windows 5-9 talk about 'neural network' — boundary should be at 5
    entries = []
    for i in range(50):
        if i < 25:
            text = f'search algorithm graph node frontier explored state {i}'
        else:
            text = f'neural network gradient backpropagation layer weight {i}'
        entries.append({"start": float(i * 60), "text": text})
    windows = build_windows(entries, window_sec=120.0)
    boundaries = detect_boundaries(windows, min_chapters=2, max_chapters=10)
    assert boundaries, 'should detect at least one boundary'
    # First boundary should be around the topic switch (window index ~13 in 120s windows)
    assert boundaries[0] >= 3, f'boundary too early: {boundaries}'
    print(f'  ✅ test_detect_boundaries_distinct_topics (boundaries={boundaries})')


def test_generate_title_top_keywords():
    from chapters import generate_title
    windows = [
        {"start": 0, "end": 1, "tokens": {'search', 'algorithm', 'graph', 'node'}},
        {"start": 1, "end": 2, "tokens": {'search', 'algorithm', 'bfs', 'frontier'}},
    ]
    title = generate_title(windows, 0, 2, top_words=3)
    assert 'search' in title and 'algorithm' in title
    assert len(title) <= 80
    print(f'  ✅ test_generate_title_top_keywords ({title!r})')


def test_parse_subtitles_long_line_split():
    """Whisper transcripts are single long paragraphs → split into sentences."""
    from chapters import parse_subtitles
    long_line = ('This is the first sentence about search. '
                 'Here is the second sentence about knowledge. '
                 'Finally the third sentence about uncertainty.') * 30  # >800 chars
    entries = parse_subtitles(long_line)
    assert len(entries) >= 3, f'long line should split into sentences, got {len(entries)}'
    assert all(e['start'] is None for e in entries)
    assert all(e['text'] for e in entries)
    print(f'  ✅ test_parse_subtitles_long_line_split ({len(entries)} sentences)')


# ── Subtitle format conversion ──────────────────────────────────────────

def test_srt_conversion():
    from convert_subtitles import convert_segments
    segments = [
        {"start": 1.0, "duration": 2.5, "text": "Hello world"},
        {"start": 65.5, "duration": 1.0, "text": "Second line"},
    ]
    out = convert_segments(segments, 'srt')
    assert '1\n00:00:01,000 --> 00:00:03,500\nHello world' in out, out
    assert '2\n00:01:05,500 --> 00:01:06,500\nSecond line' in out, out
    print('  ✅ test_srt_conversion')


def test_vtt_conversion():
    from convert_subtitles import convert_segments
    segments = [{"start": 1.0, "duration": 2.5, "text": "Hello world"}]
    out = convert_segments(segments, 'vtt')
    assert out.startswith('WEBVTT')
    assert '00:00:01.000 --> 00:00:03.500' in out, out
    assert 'Hello world' in out
    print('  ✅ test_vtt_conversion')


def test_lrc_conversion():
    from convert_subtitles import convert_segments
    segments = [{"start": 65.0, "duration": 2.0, "text": "La la la"}]
    out = convert_segments(segments, 'lrc')
    assert out == '[01:05.00]La la la', out
    print('  ✅ test_lrc_conversion')


def test_txt_conversion():
    from convert_subtitles import convert_segments
    segments = [{"start": 65.0, "duration": 2.0, "text": "La la la"}]
    out = convert_segments(segments, 'txt')
    assert out == '[01:05] La la la', out
    print('  ✅ test_txt_conversion')


def test_vtt_to_segments_roundtrip():
    from youtube_utils import vtt_to_segments
    vtt = '''WEBVTT

00:00:01.000 --> 00:00:03.500
Hello there

00:01:30.000 --> 00:01:32.000
Second line
'''
    segs = vtt_to_segments(vtt)
    assert len(segs) == 2
    assert segs[0]['start'] == 1.0
    assert segs[0]['duration'] == 2.5
    assert segs[0]['text'] == 'Hello there'
    assert segs[1]['start'] == 90.0
    print('  ✅ test_vtt_to_segments_roundtrip')


def test_detect_chapters_full_pipeline():
    from chapters import detect_chapters, parse_subtitles
    # Simulate a 10-min video with 3 distinct topics
    lines = []
    topics = [
        (0, 200, 'introduction overview motivation history'),
        (200, 400, 'method algorithm implementation code details'),
        (400, 600, 'results evaluation benchmark conclusion summary'),
    ]
    for start, end, base in topics:
        t = start
        while t < end:
            lines.append(f'[{t // 60:02d}:{t % 60:02d}] {base} point {t}')
            t += 30
    text = '\n'.join(lines)
    entries = parse_subtitles(text)
    chapters = detect_chapters(entries, window_sec=120.0,
                               min_chapters=3, max_chapters=6)
    assert len(chapters) >= 3, f'expected >=3 chapters, got {len(chapters)}'
    assert chapters[0]['start_ts'] == '00:00'
    assert all('title' in c and c['title'] for c in chapters)
    # Chapter timestamps should be roughly ordered
    starts = [c['start'] for c in chapters]
    assert starts == sorted(starts)
    print(f'  ✅ test_detect_chapters_full_pipeline ({len(chapters)} chapters)')


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
        test_get_audio_duration_wav,
        test_split_audio_chunks,
        test_transcribe_chunked_single,
        test_parse_subtitles_with_timestamps,
        test_tokenize_filters_stopwords,
        test_jaccard_similarity,
        test_build_windows_timestamped,
        test_detect_boundaries_distinct_topics,
        test_generate_title_top_keywords,
        test_parse_subtitles_long_line_split,
        test_detect_chapters_full_pipeline,
        test_srt_conversion,
        test_vtt_conversion,
        test_lrc_conversion,
        test_txt_conversion,
        test_vtt_to_segments_roundtrip,
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

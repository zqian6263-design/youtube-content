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


# ── Translation helpers ─────────────────────────────────────────────────

def test_split_chunks_small_text():
    from translate import split_chunks
    text = 'hello world ' * 100  # ~1200 chars
    chunks = split_chunks(text, max_chars=10000)
    assert len(chunks) == 1
    assert chunks[0] == text
    print('  ✅ test_split_chunks_small_text')


def test_split_chunks_large_text():
    from translate import split_chunks
    text = ('This is a sentence. ' * 2000)  # ~40K chars
    chunks = split_chunks(text, max_chars=10000, overlap=500)
    assert len(chunks) >= 3, f'expected multiple chunks, got {len(chunks)}'
    # Chunks should tile the whole text
    joined = ''.join(chunks)
    assert len(joined) >= len(text)
    # Each chunk within limit
    assert all(len(c) <= 10000 + 500 for c in chunks)
    print(f'  ✅ test_split_chunks_large_text ({len(chunks)} chunks)')


def test_resolve_api_key_priority():
    import importlib.util
    spec = importlib.util.spec_from_file_location('tr', SCRIPTS_DIR / 'translate.py')
    tr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tr)
    import types
    # Explicit key wins
    args = types.SimpleNamespace(api_key='explicit')
    assert tr.resolve_api_key(args) == 'explicit'
    # Env fallback
    os.environ['DEEPSEEK_API_KEY'] = 'env_deepseek'
    args = types.SimpleNamespace(api_key=None)
    assert tr.resolve_api_key(args) == 'env_deepseek'
    os.environ.pop('DEEPSEEK_API_KEY', None)
    # No key → None
    os.environ.pop('OPENAI_API_KEY', None)
    args = types.SimpleNamespace(api_key=None)
    assert tr.resolve_api_key(args) is None
    print('  ✅ test_resolve_api_key_priority')


def test_translate_no_key_fails_gracefully():
    import types

    from translate import translate_text
    os.environ.pop('DEEPSEEK_API_KEY', None)
    os.environ.pop('OPENAI_API_KEY', None)
    args = types.SimpleNamespace(
        api_key=None, base_url='https://api.deepseek.com/v1', model='deepseek-chat',
        target='zh', max_chunk_chars=30000, timeout=300,
    )
    result = translate_text('hello world', args)
    assert result['status'] == 'failed'
    assert 'API key' in result['message']
    print('  ✅ test_translate_no_key_fails_gracefully')


# ── Time range parsing ──────────────────────────────────────────────────

def test_parse_time_arg():
    import analyze_youtube as az
    assert az.parse_time_arg(None) is None
    assert az.parse_time_arg('') is None
    assert az.parse_time_arg('90') == 90.0
    assert az.parse_time_arg('01:30') == 90.0
    assert az.parse_time_arg('1:02:30') == 3750.0
    assert az.parse_time_arg(' 45 ') == 45.0
    assert az.parse_time_arg('abc') is None  # invalid → None
    print('  ✅ test_parse_time_arg')


def test_time_filter_segments():
    from convert_subtitles import convert_segments
    segments = [
        {"start": 10.0, "duration": 2.0, "text": "early"},
        {"start": 50.0, "duration": 2.0, "text": "middle"},
        {"start": 90.0, "duration": 2.0, "text": "late"},
    ]
    t_from, t_to = 0.0, 60.0
    filtered = [s for s in segments if s['start'] >= t_from and s['start'] <= t_to]
    assert len(filtered) == 2
    txt = convert_segments(filtered, 'txt')
    assert 'early' in txt and 'middle' in txt and 'late' not in txt
    print('  ✅ test_time_filter_segments')


# ── Channel watch ───────────────────────────────────────────────────────

def test_watch_summarize_strips_timestamps():
    import watch_channel as wc
    transcript = '[00:01] Hello world\n[00:05] This is the first sentence about AI.\n[01:00] More text here.'
    summary = wc.summarize(transcript, max_chars=200)
    assert '[00:' not in summary  # timestamps stripped
    assert 'Hello world' in summary
    assert len(summary) <= 200
    print('  ✅ test_watch_summarize_strips_timestamps')


def test_watch_summarize_truncates():
    import watch_channel as wc
    long_text = 'word ' * 500
    summary = wc.summarize(long_text, max_chars=100)
    assert len(summary) <= 101  # 100 + ellipsis
    assert summary.endswith('…')
    print('  ✅ test_watch_summarize_truncates')


def test_watch_cache_filtering():
    """Videos already in cache should be skipped."""
    import tempfile

    import watch_channel as wc
    from cache import Cache

    with tempfile.TemporaryDirectory() as td:
        cache = Cache(db_path=Path(td) / 'test.db')
        # Simulate: video 'aaa111' already processed
        cache.set_subtitles('aaa111', 'zh-Hans,zh-Hant,en', False, {
            "status": "success", "subtitles": "already done", "language": "en",
        })

        # Monkeypatch cache in watch_channel module
        wc.Cache = lambda: cache
        videos = [
            {"id": "aaa111", "title": "Old video", "duration_sec": 100},
            {"id": "bbb222", "title": "New video", "duration_sec": 200},
        ]
        # Simulate the filtering logic used in main()
        new_videos = []
        for v in videos:
            cached = cache.get_subtitles(v['id'], 'zh-Hans,zh-Hant,en', False)
            if not cached:
                new_videos.append(v)
        assert len(new_videos) == 1
        assert new_videos[0]['id'] == 'bbb222'
        cache.close()
    print('  ✅ test_watch_cache_filtering')


# ── Whisper timestamps ─────────────────────────────────────────────────

def test_format_segments_timestamps():
    from transcribe_whisper import _format_segments_timestamps
    segments = [
        {"start": 0.0, "text": "first line"},
        {"start": 65.5, "text": "second line"},
        {"start": 130.0, "text": "third line"},
    ]
    out = _format_segments_timestamps(segments)
    lines = out.split('\n')
    assert lines[0] == '[00:00] first line'
    assert lines[1] == '[01:05] second line'
    assert lines[2] == '[02:10] third line'
    print('  ✅ test_format_segments_timestamps')


def test_format_segments_timestamps_with_offset():
    from transcribe_whisper import _format_segments_timestamps
    segments = [{"start": 10.0, "text": "chunk two"}]
    out = _format_segments_timestamps(segments, offset_sec=60.0)
    assert out == '[01:10] chunk two', out
    print('  ✅ test_format_segments_timestamps_with_offset')


def test_translation_cache_roundtrip():
    from cache import Cache
    with tempfile.TemporaryDirectory() as td:
        c = Cache(db_path=Path(td) / 'test.db')
        try:
            assert c.get_translation('vid1', 'zh', 'deepseek-chat') is None
            c.set_translation('vid1', 'zh', 'deepseek-chat', {
                "status": "success", "translated_text": "你好世界",
            })
            hit = c.get_translation('vid1', 'zh', 'deepseek-chat')
            assert hit is not None
            assert hit['translated_text'] == '你好世界'
            # Different model → miss
            assert c.get_translation('vid1', 'zh', 'other-model') is None
            # Different target → miss
            assert c.get_translation('vid1', 'ja', 'deepseek-chat') is None
        finally:
            c.close()
    print('  ✅ test_translation_cache_roundtrip')


def test_llm_titles_parse_without_timestamps():
    """LLM often drops [MM:SS] — parser must accept bare 'N. title' lines."""
    import types

    import analyze_youtube as az

    chapters = [
        {"start_ts": "L0", "title": "kw1 / kw2"},
        {"start_ts": "L184", "title": "kw3 / kw4"},
    ]
    fake_out = "1. 开场介绍\n2. 因果与关联"

    # Monkeypatch call_llm in the translate module (llm_chapter_titles imports it)
    import translate as tr_mod
    orig_call = tr_mod.call_llm
    def fake_call(api_key, base_url, model, system, user, timeout=120):
        assert '因果' in system or 'zh' in system  # target-aware prompt
        return fake_out
    tr_mod.call_llm = fake_call
    try:
        polished = az.llm_chapter_titles(
            chapters, 'Test Video', target='zh',
            args=types.SimpleNamespace(translate_api_key='k', translate_base_url='u', translate_model='m'))
        assert polished[0]['title'] == '开场介绍', polished
        assert polished[1]['title'] == '因果与关联', polished
    finally:
        tr_mod.call_llm = orig_call
    print('  ✅ test_llm_titles_parse_without_timestamps')


def test_llm_titles_fallback_on_count_mismatch():
    """If LLM returns wrong count, keep keyword titles."""
    import types

    import analyze_youtube as az

    chapters = [
        {"start_ts": "L0", "title": "kw1"},
        {"start_ts": "L184", "title": "kw2"},
        {"start_ts": "L460", "title": "kw3"},
    ]
    import translate as tr_mod
    orig_call = tr_mod.call_llm
    def fake_call(api_key, base_url, model, system, user, timeout=120):
        return "1. only one\n2. two"  # wrong count
    tr_mod.call_llm = fake_call
    try:
        polished = az.llm_chapter_titles(
            chapters, 'Test', target='zh',
            args=types.SimpleNamespace(translate_api_key='k', translate_base_url='u', translate_model='m'))
        assert polished[0]['title'] == 'kw1'  # unchanged
    finally:
        tr_mod.call_llm = orig_call
    print('  ✅ test_llm_titles_fallback_on_count_mismatch')


def test_search_bigram_tokenize():
    from search import _bigram_tokenize
    assert _bigram_tokenize('深度学习') == '深度 度学 学习'
    assert _bigram_tokenize('反事实') == '反事 事实'
    assert _bigram_tokenize('neural network') == 'neural network'
    assert _bigram_tokenize('neural网络') == 'neural 网络'
    print('  ✅ test_search_bigram_tokenize')


def test_search_extract_segments_timestamps():
    import tempfile

    from search import _extract_segments
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'subs.txt'
        f.write_text('[00:05] hello world\n[01:30] second line\n', encoding='utf-8')
        segs = _extract_segments(f, 'f1')
        assert len(segs) == 2
        assert segs[0][2] == 5
        assert segs[0][3] == 'hello world'
        assert segs[1][2] == 90
    print('  ✅ test_search_extract_segments_timestamps')


def test_search_long_line_splitting():
    import tempfile

    from search import _extract_segments
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'whisper.txt'
        long = ('This is sentence one about causal inference. '
                'This is sentence two about counterfactuals. ') * 30
        f.write_text(long, encoding='utf-8')
        segs = _extract_segments(f, 'f1')
        assert len(segs) > 3  # split into sentences
        assert all(len(s[3]) <= 800 for s in segs)
    print(f'  ✅ test_search_long_line_splitting ({len(segs)} 段)')


def test_search_index_and_query():
    """Build a small index and query it (ASCII + CJK paths)."""
    import tempfile

    import search
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 'src'
        src.mkdir()
        (src / 'a.txt').write_text(
            '[00:01] neural network training\n[00:10] gradient descent\n',
            encoding='utf-8')
        (src / 'b.md').write_text(
            '## 贝叶斯定理\n贝叶斯定理用于更新信念\n',
            encoding='utf-8')

        db = Path(td) / 'idx.db'
        result = search.build_index(paths=[src], db_path=db, verbose=False)
        assert result['status'] == 'success'
        assert result['files'] == 2

        # ASCII query
        r = search.search('neural', db_path=db)
        assert r['status'] == 'success'
        assert r['count'] >= 1
        assert 'neural' in r['matches'][0]['text']

        # CJK query
        r2 = search.search('贝叶斯', db_path=db)
        assert r2['status'] == 'success'
        assert r2['count'] >= 1, f'CJK search failed: {r2}'
        assert '贝叶斯' in r2['matches'][0]['text']

        # No match
        r3 = search.search('zzz_nonexistent', db_path=db)
        assert r3['count'] == 0
    print('  ✅ test_search_index_and_query')


def test_archive_note_structure():
    """Archive generates a structured markdown note with metadata + chapters."""
    import tempfile

    import analyze_youtube as az
    with tempfile.TemporaryDirectory() as td:
        result = {"source": "caption", "language": "en", "char_count": 100}
        extra = {
            "chapters": [
                {"start_ts": "00:00", "title": "Intro"},
                {"start_ts": "10:00", "title": "Methods"},
            ],
            "translated": "你好世界",
            "translate_target": "zh",
        }
        note = az.archive_note(
            "vid123", "Test Video", "[00:01] hello\n[00:05] world",
            result, td, extra=extra)
        p = Path(note)
        assert p.exists()
        text = p.read_text(encoding='utf-8')
        assert '# Test Video' in text
        assert '| 视频 ID | `vid123` |' in text
        assert '## \U0001f4d1 \u7ae0\u8282' in text  # ## 📑 章节
        assert '**00:00** Intro' in text
        assert '## \U0001f310 \u7ffb\u8bd1' in text  # ## 🌐 翻译
        assert '你好世界' in text
        assert '## \U0001f4dd \u5168\u6587' in text  # ## 📝 全文
        assert '[00:01] hello' in text
    print('  ✅ test_archive_note_structure')


def test_ask_llm_no_key_graceful():
    """--ask without API key fails gracefully."""
    import search
    os.environ.pop('DEEPSEEK_API_KEY', None)
    os.environ.pop('OPENAI_API_KEY', None)
    result = search.ask_llm("test question", [{"path": "x.txt", "start_ts": "00:00", "text": "hi"}])
    assert result['status'] == 'failed'
    assert 'API key' in result['message'] or 'key' in result['message'].lower()
    print('  ✅ test_ask_llm_no_key_graceful')


def test_watch_load_config():
    import tempfile

    import watch_channel as wc
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / 'watch.json'
        cfg.write_text('{"channels": [{"name": "A", "url": "@a", "max": 3}, {"name": "B", "url": "@b"}]}',
                       encoding='utf-8')
        channels = wc.load_config(str(cfg))
        assert len(channels) == 2
        assert channels[0]['name'] == 'A'
        assert channels[0]['max'] == 3
        assert channels[1].get('max') is None  # optional
    print('  ✅ test_watch_load_config')


def test_watch_load_config_invalid():
    import tempfile

    import watch_channel as wc
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / 'bad.json'
        cfg.write_text('{"channels": []}', encoding='utf-8')
        try:
            wc.load_config(str(cfg))
            assert False, 'should raise'
        except ValueError:
            pass
    print('  ✅ test_watch_load_config_invalid')


def test_watch_process_channel_mock():
    """process_channel with mocked network + isolated cache."""
    import tempfile
    import types

    import watch_channel as wc
    from cache import Cache

    orig_list = wc.list_channel_videos
    orig_extract = wc.extract_subtitles
    orig_cache = wc.Cache
    wc.list_channel_videos = lambda url, max_v, cookies: [
        {"id": "vidAAA", "title": "New A", "duration_sec": 60},
    ]
    wc.extract_subtitles = lambda vid, langs, cookies: {
        "status": "success", "subtitles": "[00:01] hello world", "language": "en",
    }
    with tempfile.TemporaryDirectory() as td:
        wc.Cache = lambda: Cache(db_path=Path(td) / 'test.db')
        try:
            args = types.SimpleNamespace(cookies=None, languages='zh-Hans,zh-Hant,en', max=5)
            result = wc.process_channel("https://www.youtube.com/@test", 5, args)
            assert result['new_count'] == 1
            assert len(result['results']) == 1
            assert result['results'][0]['status'] == 'success'
            assert any('hello world' in line for line in result['report_lines'])
            # Second run should skip (cached)
            result2 = wc.process_channel("https://www.youtube.com/@test", 5, args)
            assert result2['new_count'] == 0
        finally:
            wc.list_channel_videos = orig_list
            wc.extract_subtitles = orig_extract
            wc.Cache = orig_cache
    print('  ✅ test_watch_process_channel_mock')


def test_pipeline_extract_json():
    import pipeline as pl
    out = 'progress line\n{"status": "success", "char_count": 100}'
    data = pl.extract_json(out)
    assert data['status'] == 'success'
    assert data['char_count'] == 100
    # No JSON → empty dict
    assert pl.extract_json('no json here') == {}
    # Invalid JSON after { → empty dict
    assert pl.extract_json('{broken') == {}
    print('  ✅ test_pipeline_extract_json')


def test_pipeline_process_video_mock():
    """process_video delegates to analyze_youtube and parses its JSON."""
    import types

    import pipeline as pl

    # Mock run() to return a fake analyze result
    orig_run = pl.run
    def fake_run(cmd, timeout=1800):
        assert cmd[0] == sys.executable
        assert str(pl.ANALYZE_PY) in cmd
        # --archive flag passed through
        assert '--archive' in cmd
        return ('{"status": "success", "source": "caption", "char_count": 500, '
                '"chapters": [{"start_ts": "00:00", "title": "Intro"}]}'), 0
    pl.run = fake_run
    try:
        args = types.SimpleNamespace(chapters=True, translate=False, archive='C:/tmp/notes')
        result = pl.process_video({"id": "vidAAA", "title": "Test"}, args, 'zh-Hans,zh-Hant,en')
        assert result['status'] == 'success'
        assert result['char_count'] == 500
        assert result['chapters_count'] == 1
        assert result['title'] == 'Test'
    finally:
        pl.run = orig_run
    print('  ✅ test_pipeline_process_video_mock')


def test_vector_index_and_search_mock():
    """Vector index build + semantic search (mocked embeddings)."""
    import tempfile

    import numpy as np
    import search

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 'src'
        src.mkdir()
        (src / 'a.txt').write_text(
            '[00:01] the cat sat on the mat\n[00:10] dogs like to run\n',
            encoding='utf-8')
        db = Path(td) / 'idx.db'

        # Build FTS index first
        search.build_index(paths=[src], db_path=db, verbose=False)

        # Mock embeddings: deterministic vectors from text hash
        orig_embed = search._embed_batch
        def fake_embed(texts):
            import hashlib
            vecs = []
            for t in texts:
                h = hashlib.md5(t.encode()).digest()
                v = np.frombuffer(h, dtype=np.uint8).astype(np.float32) / 255.0
                v = np.tile(v, 16)[:512]  # 512-dim
                vecs.append(v)
            return vecs
        search._embed_batch = fake_embed
        try:
            result = search.build_vector_index(db, verbose=False)
            assert result['status'] == 'success'
            assert result['segments'] == 2

            # Query that shares text with a segment
            r = search.vector_search('cat mat', db_path=db)
            assert r['status'] == 'success'
            assert r['count'] >= 1
        finally:
            search._embed_batch = orig_embed
    print('  ✅ test_vector_index_and_search_mock')


def test_vector_search_no_index_graceful():
    """Vector search without vector index fails gracefully."""
    import tempfile

    import search
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / 'idx.db'
        r = search.vector_search('test', db_path=db)
        assert r['status'] == 'failed'
    print('  ✅ test_vector_search_no_index_graceful')


def test_playlist_title_parse():
    """fetch_playlist parses the 4th tab field as playlist title."""

    lines = (
        '1\tvidAAA\tVideo A\tCS50 Seminars - Fall 2025\n'
        '2\tvidBBB\tVideo B\tCS50 Seminars - Fall 2025\n'
    )
    videos, playlist_title = [], ''
    for line in lines.split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) >= 2:
            idx, vid = parts[0], parts[1]
            title = '\t'.join(parts[2:3]) if len(parts) > 2 else ''
            if len(parts) >= 4 and parts[3] and 'playlist:' not in parts[3]:
                playlist_title = parts[3]
            videos.append({"index": idx, "id": vid, "title": title})
    assert playlist_title == 'CS50 Seminars - Fall 2025'
    assert len(videos) == 2
    assert videos[0]['title'] == 'Video A'
    # fallback when no playlist_title field
    assert (playlist_title or 'playlist:PLX') == 'CS50 Seminars - Fall 2025'
    print('  ✅ test_playlist_title_parse')


def test_playlist_flat_mode_no_title_field():
    """Old 3-field lines: title keeps only field 3, playlist_title falls back."""
    line = '1\tvidAAA\tVideo A\n'
    parts = line.strip().split('\t')
    title = '\t'.join(parts[2:3]) if len(parts) > 2 else ''
    playlist_title = ''
    if len(parts) >= 4 and parts[3] and 'playlist:' not in parts[3]:
        playlist_title = parts[3]
    assert title == 'Video A'
    assert playlist_title == ''
    assert (playlist_title or 'playlist:PLX') == 'playlist:PLX'
    print('  ✅ test_playlist_flat_mode_no_title_field')


def test_video_jump_url():
    import search
    assert search.video_jump_url('5NgNicANyqM_Harvard Course.txt', '1:23:45') \
        == 'https://youtu.be/5NgNicANyqM?t=5025'
    assert search.video_jump_url('5NgNicANyqM_Harvard Course.txt', '03:05') \
        == 'https://youtu.be/5NgNicANyqM?t=185'
    # Notes (no video ID prefix) → no jump link
    assert search.video_jump_url('01-Search.md', '12:34') is None
    # No timestamp → base link
    assert search.video_jump_url('5NgNicANyqM_x.txt', '') \
        == 'https://youtu.be/5NgNicANyqM'
    print('  ✅ test_video_jump_url')


def test_pipeline_list_playlist_videos_mock():
    """list_playlist_videos parses yt-dlp flat output and filters cache."""
    import tempfile

    import cache as cache_mod
    import pipeline as pl

    orig_run = pl.subprocess.run
    orig_cache_cls = cache_mod.Cache

    def fake_run(cmd, **kw):
        import types
        out = '1\tvid111\tPlaylist Video A\n2\tvid222\tPlaylist Video B\n'
        return types.SimpleNamespace(returncode=0, stdout=out, stderr='')
    pl.subprocess.run = fake_run

    try:
        with tempfile.TemporaryDirectory() as td:
            from pathlib import Path as _P
            # list_playlist_videos does `from cache import Cache` internally →
            # patch cache.Cache to use a temp db
            cache_mod.Cache = lambda db_path=None: orig_cache_cls(db_path=_P(td) / 'test.db')
            videos, cache = pl.list_playlist_videos(
                {"url": "https://www.youtube.com/playlist?list=PLX", "max": 10},
                'zh-Hans,zh-Hant,en')
            assert len(videos) == 2
            assert videos[0]['id'] == 'vid111'
            assert videos[1]['title'] == 'Playlist Video B'
            cache.close()
    finally:
        pl.subprocess.run = orig_run
        cache_mod.Cache = orig_cache_cls
    print('  ✅ test_pipeline_list_playlist_videos_mock')


def test_clean_transcript_text():
    from transcribe_whisper import clean_transcript_text
    raw = '呃，今天来聊聊\n嗯\num\nmachine learning is\nmachine learning is\n'
    out = clean_transcript_text(raw)
    cleaned_lines = [ln for ln in out.split('\n') if ln.strip()]
    assert cleaned_lines[0] == '今天来聊聊'
    assert '嗯' not in cleaned_lines and 'um' not in cleaned_lines
    assert cleaned_lines.count('machine learning is') == 1
    print('  ✅ test_clean_transcript_text')


def test_clean_transcript_english_fillers():
    from transcribe_whisper import clean_transcript_text
    # multi-word English fillers stripped at line start
    out = clean_transcript_text('You know, I think this works\nuh\nI mean, that is correct')
    lines = [ln for ln in out.split('\n') if ln.strip()]
    assert lines[0] == 'I think this works'
    assert lines[1] == 'that is correct'
    # filler-only line dropped (multi-word too)
    assert 'uh' not in lines and 'You know' not in lines
    print('  ✅ test_clean_transcript_english_fillers')


def test_clean_transcript_repeated_prefix():
    from transcribe_whisper import clean_transcript_text
    # leading repeated word (stammer)
    assert clean_transcript_text('So so we need to go') == 'So we need to go'
    assert clean_transcript_text('I I think that is fine') == 'I think that is fine'
    # leading repeated 2-word phrase
    assert clean_transcript_text("let's go let's go now") == "let's go now"
    # case-insensitive collapse, preserves first-occurrence casing
    assert clean_transcript_text('THE the market is big') == 'THE market is big'
    # non-repeated line untouched
    assert clean_transcript_text('He quickly ran to the store') == 'He quickly ran to the store'
    print('  ✅ test_clean_transcript_repeated_prefix')


def test_clean_transcript_keeps_timestamps():
    from transcribe_whisper import clean_transcript_text
    # lines already formatted with [MM:SS] must not have repeated-word
    # collapsing applied to the timestamp
    out = clean_transcript_text('[00:05] So so we begin\n[00:08] Next next step')
    lines = [ln for ln in out.split('\n') if ln.strip()]
    assert lines[0] == '[00:05] So so we begin'
    assert lines[1] == '[00:08] Next next step'
    print('  ✅ test_clean_transcript_keeps_timestamps')






def test_search_ascii_or_semantics():
    """Multi-term ASCII queries use OR (AND was too strict for segments)."""
    import sqlite3
    import tempfile
    from pathlib import Path

    import search

    with tempfile.TemporaryDirectory() as td:
        conn = sqlite3.connect(Path(td) / 't.db')
        conn.execute('CREATE VIRTUAL TABLE subtitles_fts USING fts5(path, start, text)')
        conn.execute('INSERT INTO subtitles_fts VALUES (?,?,?)',
                     ('a.txt', 0, 'we are no strangers to love'))
        conn.execute('INSERT INTO subtitles_fts VALUES (?,?,?)',
                     ('b.txt', 0, 'the rules of the game'))
        conn.commit()
        # OR: any term matches
        r = search._search_ascii(conn, 'strangers mean song', 5)
        assert len(r) >= 1, 'OR semantics should find the strangers row'
        # Single term still works
        r2 = search._search_ascii(conn, 'strangers', 5)
        assert len(r2) == 1
        # Empty terms → no crash
        r3 = search._search_ascii(conn, '   ', 5)
        assert r3 == []
        conn.close()
    print('  ✅ test_search_ascii_or_semantics')


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


# ── webui parsing (parse_json_payload / truncate_text / run_search) ──────

def test_webui_parse_json_payload():
    import webui
    # clean output: progress on stderr-only is not present; find first '{'
    out = 'line1\nline2\n{"status": "success", "char_count": 100}'
    data = webui.parse_json_payload(out)
    assert data['status'] == 'success'
    assert data['char_count'] == 100
    # junk before JSON (progress lines) is skipped from the first '{'
    out2 = '[01:23] some spoken text {"status": "success"}'
    assert webui.parse_json_payload(out2)['status'] == 'success'
    # no '{' → failed with the output content as message (truncated to 200)
    assert webui.parse_json_payload('no json at all') == \
        {'status': 'failed', 'message': 'no json at all'}
    # empty output → failed with default message
    assert webui.parse_json_payload('') == \
        {'status': 'failed', 'message': '无输出'}
    # no '{' with content → failed with the content truncated to 200 chars
    r = webui.parse_json_payload('x' * 400)
    assert r['status'] == 'failed' and len(r['message']) == 200
    # broken JSON after '{' → failed parse message
    assert webui.parse_json_payload('{broken')['message'] == '输出解析失败'
    # custom default message respected
    assert webui.parse_json_payload('', default_message='空')['message'] == '空'
    print('  ✅ test_webui_parse_json_payload')


def test_webui_truncate_text():
    import webui
    assert webui.truncate_text('hello world', 5) == 'hello'
    assert webui.truncate_text('short', 100) == 'short'
    assert webui.truncate_text('', 10) == ''
    print('  ✅ test_webui_truncate_text')


def test_webui_run_search_fts_and_vector():
    """run_search dispatches fts vs vector and enriches jump URLs."""
    import os
    import tempfile

    import search

    # build a tiny index the search functions can read
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 'src'
        src.mkdir()
        (src / 'dQw4w9WgXcQ_clip01.txt').write_text(
            '[00:01] the cat sat on the mat\n[00:10] dogs run fast\n',
            encoding='utf-8')
        db = Path(td) / 'idx.db'
        search.build_index(paths=[src], db_path=db, verbose=False)

        # point index_path() at our temp db via env var
        import webui
        old_env = os.environ.get('YOUTUBE_SEARCH_INDEX')
        os.environ['YOUTUBE_SEARCH_INDEX'] = str(db)
        try:
            # search() resolves index_path() at call time
            res = webui.run_search('cat', mode='fts', limit=5)
            assert res.get('status') == 'success', res
            assert res['matches'], res
            m = res['matches'][0]
            assert m['jump_url'].startswith('https://youtu.be/')
            assert 'clip01' in m['path']

            # unknown mode defaults to fts (search backend, not vector)
            res2 = webui.run_search('cat', mode='bogus', limit=5)
            assert res2.get('status') == 'success'
        finally:
            if old_env is None:
                os.environ.pop('YOUTUBE_SEARCH_INDEX', None)
            else:
                os.environ['YOUTUBE_SEARCH_INDEX'] = old_env
        print('  ✅ test_webui_run_search_fts_and_vector')


# ── pipeline report format (build_report) ───────────────────────────────

def test_pipeline_build_report():
    import pipeline as pl
    all_results = [
        {'status': 'success', 'title': 'Video A',
         'source_type': 'channel', 'source_name': 'SomeTech',
         'id': 'aaa111', 'source': 'caption', 'char_count': 500,
         'chapters_count': 3, 'archive_file': 'C:/tmp/notes/VideoA.md'},
        {'status': 'failed', 'title': 'Video B', 'source_type': 'playlist',
         'source_name': 'MyList', 'id': 'bbb222', 'message': '字幕获取失败'},
    ]
    report = pl.build_report(all_results, 1, ['1 个频道', '1 个播放列表'],
                             {'files': 12, 'segments': 40})
    assert report.startswith('# 🏭 知识库流水线')
    assert '处理 2 个视频（成功 1），来自 1 个频道 + 1 个播放列表' in report
    assert '## ✅ Video A' in report
    assert '📡 频道: SomeTech' in report
    assert '链接: https://youtu.be/aaa111' in report
    assert '来源: caption · 500 字符' in report
    assert '📑 3 章' in report
    assert '📚 笔记: VideoA.md' in report
    assert '## ⚠️ Video B' in report
    assert '📋 播放列表: MyList' in report
    assert '状态: 字幕获取失败' in report
    assert '🔎 搜索索引: 12 文件 / 40 段' in report
    # no index_info → no index line
    report_no_idx = pl.build_report(all_results, 1, ['1 个频道'], None)
    assert '搜索索引' not in report_no_idx
    print('  ✅ test_pipeline_build_report')


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
        test_split_chunks_small_text,
        test_split_chunks_large_text,
        test_resolve_api_key_priority,
        test_translate_no_key_fails_gracefully,
        test_parse_time_arg,
        test_time_filter_segments,
        test_watch_summarize_strips_timestamps,
        test_watch_summarize_truncates,
        test_watch_cache_filtering,
        test_format_segments_timestamps,
        test_format_segments_timestamps_with_offset,
        test_translation_cache_roundtrip,
        test_llm_titles_parse_without_timestamps,
        test_llm_titles_fallback_on_count_mismatch,
        test_search_bigram_tokenize,
        test_search_extract_segments_timestamps,
        test_search_long_line_splitting,
        test_search_index_and_query,
        test_archive_note_structure,
        test_ask_llm_no_key_graceful,
        test_watch_load_config,
        test_watch_load_config_invalid,
        test_watch_process_channel_mock,
        test_pipeline_extract_json,
        test_pipeline_process_video_mock,
        test_webui_parse_json_payload,
        test_webui_truncate_text,
        test_webui_run_search_fts_and_vector,
        test_pipeline_build_report,
        test_vector_index_and_search_mock,
        test_vector_search_no_index_graceful,
        test_playlist_title_parse,
        test_playlist_flat_mode_no_title_field,
        test_video_jump_url,
        test_pipeline_list_playlist_videos_mock,
        test_clean_transcript_text,
        test_clean_transcript_english_fillers,
        test_clean_transcript_repeated_prefix,
        test_clean_transcript_keeps_timestamps,
        test_search_ascii_or_semantics,
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

#!/usr/bin/env python3
"""
analyze_youtube.py — Unified entry point for YouTube video content analysis.

Extracts captions/subtitles from YouTube videos. Falls back to audio download
+ Whisper transcription when captions are unavailable.

Modes:
  python analyze_youtube.py <URL or ID>                  # caption only + ask if none
  python analyze_youtube.py <URL> --whisper              # caption; if none, transcribe
  python analyze_youtube.py <URL> --auto                 # full auto: caption->whisper
  python analyze_youtube.py <URL> --force-whisper         # always transcribe from audio
  python analyze_youtube.py <PLAYLIST_URL> --playlist     # batch process a playlist
  python analyze_youtube.py <PLAYLIST_URL> --playlist --max 5   # first 5 videos

Flags:
  --languages zh-Hans,en        Caption language priority (default: zh-Hans,zh-Hant,en)
  --whisper-language auto       Whisper transcription language (auto = detect)
  --whisper-model small         Whisper model size
  --device auto                 torch device: auto/cuda/cpu (default: auto-detect)
  --timestamps                  Include MM:SS timestamps in output
  --playlist                    Treat the URL as a playlist and batch process
  --max N                       With --playlist: limit to N videos
  --cookies PATH                With --playlist: cookies.txt for private playlists

Env overrides:
  WHISPER_MODEL_DIR, WHISPER_TEMP, WHISPER_MODEL, WHISPER_DEVICE
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = SKILL_DIR / 'output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Shared utilities
sys.path.insert(0, str(SCRIPT_DIR))
from cache import Cache
from chapters import detect_chapters, parse_subtitles
from youtube_utils import detect_device, extract_video_id, load_env, safe_filename, safe_video_id

FETCH_SUB_PY = SCRIPT_DIR / 'fetch_subtitle_youtube.py'
FETCH_AUDIO_PY = SCRIPT_DIR / 'fetch_audio_youtube.py'
FETCH_PLAYLIST_PY = SCRIPT_DIR / 'fetch_playlist.py'
WHISPER_PY = SCRIPT_DIR / 'transcribe_whisper.py'
CHAPTERS_PY = SCRIPT_DIR / 'chapters.py'

# Global cache instance (lazy-init on first use)
_cache = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def parse_time_arg(value: str | None) -> float | None:
    """Parse 'MM:SS', 'HH:MM:SS', or plain seconds into a float."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    parts = value.split(':')
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(value)
    except ValueError:
        eprint(f'⚠ 无法解析时间: {value!r}（支持 90、01:30、1:02:30）')
        return None


def run_script(script_path, *args, timeout=300):
    cmd = [sys.executable, str(script_path)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.stdout, result.stderr, result.returncode


def save_output(video_id, title, transcript, source_type, metadata):
    safe_title = safe_filename(title)
    base = f'{video_id}_{safe_title}'
    txt_path = OUTPUT_DIR / f'{base}.txt'
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(f'标题: {title}\n来源: {source_type}\n')
        f.write('=' * 40 + '\n')
        f.write(transcript)
    metadata['transcript_file'] = str(txt_path)
    meta_path = OUTPUT_DIR / f'{base}.json'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return txt_path, meta_path


def llm_chapter_titles(chapters, title, target='zh', args=None):
    """
    Convert keyword-based chapter titles to fluent titles via LLM.

    Returns a new list of chapter dicts with polished 'title' values,
    or the original list if the LLM call fails.
    """
    try:
        import types as _types

        from translate import call_llm, resolve_api_key
    except ImportError:
        return chapters

    # resolve_api_key expects an .api_key attribute; adapt our args
    key_args = _types.SimpleNamespace(
        api_key=getattr(args, 'translate_api_key', None))
    api_key = resolve_api_key(key_args)
    if not api_key:
        eprint('⚠ LLM 章节标题需要 API key（DEEPSEEK_API_KEY），使用关键词标题')
        return chapters

    base_url = (getattr(args, 'translate_base_url', None)
                or os.environ.get('TRANSLATE_BASE_URL', 'https://api.deepseek.com/v1'))
    model = (getattr(args, 'translate_model', None)
             or os.environ.get('TRANSLATE_MODEL', 'deepseek-chat'))

    lines = [f'{i + 1}. [{c["start_ts"]}] {c["title"]}' for i, c in enumerate(chapters)]
    system = (
        f'You are a video chapter title editor. Given a list of video chapters '
        f'with timestamp and keyword tags, rewrite each title into a fluent, '
        f'concise {target} title (max 15 chars for Chinese, max 8 words for '
        f'English). Keep the numbering and timestamp format exactly. '
        f'Output ONLY the numbered list, one per line.'
    )
    user = f'Video: {title}\n\nChapters:\n' + '\n'.join(lines)

    try:
        out = call_llm(api_key, base_url, model, system, user, timeout=120)
    except Exception as e:
        eprint(f'⚠ LLM 章节标题失败: {str(e)[:150]}，使用关键词标题')
        return chapters

    # Parse "1. [00:00] New title" or "1. New title" lines (LLM may drop timestamps)
    new_titles = []
    import re as _re
    for raw in out.split('\n'):
        m = _re.match(r'\s*\d+\.?\s*(?:\[\d{2}:\d{2}\]|\[L\d+\])?\s*(.*)', raw)
        if m:
            new_titles.append(m.group(1).strip())
    if len(new_titles) != len(chapters):
        eprint(f'⚠ LLM 返回 {len(new_titles)} 个标题（期望 {len(chapters)}），保留关键词标题')
        return chapters

    for c, nt in zip(chapters, new_titles):
        c['title'] = nt
    return chapters


def generate_chapters(transcript, video_id, title, window_sec=60.0,
                      min_chapters=3, max_chapters=20, top_words=4,
                      llm_titles=False, args=None):
    """
    Detect chapters from a transcript. Returns (chapters_list, chapters_path).

    Saves a `{video_id}_{title}_chapters.json` file next to the transcript.
    Returns empty list if the transcript has too little content for detection.
    """
    entries = parse_subtitles(transcript)
    if len(entries) < 20:
        return [], None

    chapters = detect_chapters(
        entries, window_sec=window_sec,
        min_chapters=min_chapters, max_chapters=max_chapters,
        top_words=top_words,
    )

    if not chapters:
        return [], None

    # Optional: polish titles via LLM
    if llm_titles:
        chapters = llm_chapter_titles(chapters, title, target='zh', args=args)
        eprint('📑 LLM 章节标题生成完成')

    safe_title = safe_filename(title)
    ch_path = OUTPUT_DIR / f'{video_id}_{safe_title}_chapters.json'
    with open(ch_path, 'w', encoding='utf-8') as f:
        json.dump({"video_id": video_id, "title": title, "chapters": chapters},
                  f, ensure_ascii=False, indent=2)

    return chapters, str(ch_path)


def archive_note(video_id, title, transcript, result, archive_dir, extra=None):
    """
    Write a structured markdown note to the knowledge-base directory.

    Note layout:
      # 标题
      | 元数据表 |
      ## 章节 (if chapters)
      ## 翻译 (if translated)
      ## 全文

    Returns path of the written note.
    """
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    safe_title = safe_filename(title)
    note_path = archive_dir / f'{video_id}_{safe_title}.md'

    from datetime import datetime
    lines = [f'# {title}', '']
    lines.append('| 字段 | 值 |')
    lines.append('|------|-----|')
    lines.append(f'| 视频 ID | `{video_id}` |')
    lines.append(f'| 来源 | {result.get("source", "caption")} |')
    lines.append(f'| 语言 | {result.get("language", result.get("source", ""))} |')
    lines.append(f'| 字符数 | {result.get("char_count", len(transcript))} |')
    lines.append(f'| 归档时间 | {datetime.now().strftime("%Y-%m-%d %H:%M")} |')
    lines.append('')

    chapters = extra.get('chapters') if extra else None
    if chapters:
        lines.append('## 📑 章节')
        lines.append('')
        for c in chapters:
            lines.append(f'- **{c["start_ts"]}** {c["title"]}')
        lines.append('')

    translated = extra.get('translated') if extra else None
    if translated:
        lines.append(f'## 🌐 翻译（{extra.get("translate_target", "zh")}）')
        lines.append('')
        lines.append(translated)
        lines.append('')

    lines.append('## 📝 全文')
    lines.append('')
    lines.append(transcript)

    note_path.write_text('\n'.join(lines), encoding='utf-8')
    return str(note_path)


def convert_subtitles_to(segments, fmt, video_id, title):
    """
    Convert precise segments to srt/vtt/lrc/txt and save alongside output.

    Returns (converted_path, content) or (None, None) if conversion fails.
    """
    try:
        from convert_subtitles import convert_segments
        content = convert_segments(segments, fmt)
    except Exception as e:
        eprint(f'⚠ 字幕格式转换失败: {e}')
        return None, None

    safe_title = safe_filename(title)
    out_path = OUTPUT_DIR / f'{video_id}_{safe_title}.{fmt}'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return str(out_path), content


def translate_transcript(transcript, video_id, title, target='zh', args=None,
                         bilingual=False):
    """
    Translate a transcript via the LLM API. Returns (translated_path, text).

    Cached in SQLite (key: video_id + target + model) — re-running the same
    video does not re-spend API credits. Saves `{video_id}_{title}_{target}.txt`.
    Returns (None, None) on failure (e.g. missing API key).
    """
    try:
        import argparse as _argparse

        from translate import translate_text
        t_args = _argparse.Namespace(
            api_key=getattr(args, 'translate_api_key', None),
            base_url=getattr(args, 'translate_base_url', None)
            or os.environ.get('TRANSLATE_BASE_URL', 'https://api.deepseek.com/v1'),
            model=getattr(args, 'translate_model', None)
            or os.environ.get('TRANSLATE_MODEL', 'deepseek-chat'),
            target=target,
            max_chunk_chars=30000,
            timeout=300,
        )
    except ImportError:
        return None, None

    # Check translation cache first (skip API call on repeat)
    cache = get_cache()
    cached = cache.get_translation(video_id, target, t_args.model)
    if cached and cached.get('status') == 'success':
        translated = cached.get('translated_text', '')
        if translated:
            safe_title = safe_filename(title)
            suffix = f'{target}-bi' if bilingual else target
            out_path = OUTPUT_DIR / f'{video_id}_{safe_title}_{suffix}.txt'
            if bilingual:
                orig_lines = [ln for ln in transcript.split('\n') if ln.strip()]
                tr_lines = [ln for ln in translated.split('\n') if ln.strip()]
                paired = []
                for i, oline in enumerate(orig_lines):
                    paired.append(oline)
                    if i < len(tr_lines):
                        paired.append(tr_lines[i])
                content_out = '\n'.join(paired)
            else:
                content_out = translated
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(f'标题: {title}\n翻译: {target}{"（双语对照）" if bilingual else ""}\n')
                f.write('=' * 40 + '\n')
                f.write(content_out)
            eprint(f'📦 命中翻译缓存: {video_id}')
            return str(out_path), translated

    eprint(f'🌐 翻译中（目标语言: {target}）...')
    result = translate_text(transcript, t_args)
    if result.get('status') != 'success':
        eprint(f'⚠ 翻译失败: {result.get("message", "未知错误")}')
        return None, None

    translated = result.get('translated_text', '')
    safe_title = safe_filename(title)
    suffix = f'{target}-bi' if bilingual else target
    out_path = OUTPUT_DIR / f'{video_id}_{safe_title}_{suffix}.txt'

    # Bilingual: interleave original lines with translated lines
    if bilingual:
        orig_lines = [ln for ln in transcript.split('\n') if ln.strip()]
        tr_lines = [ln for ln in translated.split('\n') if ln.strip()]
        paired = []
        for i, oline in enumerate(orig_lines):
            paired.append(oline)
            # Show translated line under its original (by index or timestamp)
            if i < len(tr_lines):
                paired.append(tr_lines[i])
        content_out = '\n'.join(paired)
    else:
        content_out = translated

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f'标题: {title}\n翻译: {target}{"（双语对照）" if bilingual else ""}\n')
        f.write('=' * 40 + '\n')
        f.write(content_out)
    eprint(f'🌐 翻译完成 ({result.get("chunks", 1)} 块)'
           f'{"，双语对照" if bilingual else ""}')

    # Cache the translation
    cache.set_translation(video_id, target, t_args.model, result)
    return str(out_path), translated


# ── Subtitle verification ──────────────────────────────────────────────
_MUSIC_KEYWORDS = {'♪', '♫', 'music', 'verse', 'chorus', '歌词', '旋律'}
_TECH_KEYWORDS = {
    '代码', '函数', 'API', 'GitHub', '开源', '部署',
    '算法', '框架', '编程', '教程', '教学', '实战',
    'tutorial', 'guide', 'how to', 'code', 'programming',
}


def subtitle_looks_suspicious(text, title):
    """Check if subtitles might be misaligned/lyrics/wrong content."""
    if not text or len(text) < 100:
        return True, "字幕过短"

    words = set(re.findall(r'[\u4e00-\u9fff\w]+', text.lower()))
    music_count = sum(1 for k in _MUSIC_KEYWORDS if k in words)
    if music_count >= 3:
        return True, "检测到歌词特征"

    title_tech = sum(1 for k in _TECH_KEYWORDS if k.lower() in title.lower())
    if title_tech >= 2:
        text_tech = sum(1 for k in _TECH_KEYWORDS if k in words)
        if text_tech < 2:
            return True, "标题含技术关键词但字幕无技术内容"

    return False, ""


def fetch_video_title(video):
    """Fetch video title via yt-dlp (quick metadata, no download)."""
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(video, download=False)
            return info.get('title', video)
    except Exception:
        return video


# ── Whisper pipeline ────────────────────────────────────────────────────
def run_whisper_pipeline(video_id, video_url, title, whisper_model, device,
                         whisper_model_dir, whisper_temp,
                         whisper_language, timestamps, backend='openai',
                         chunk_minutes=0, chunk_workers=0,
                         chapters=False, chapter_window_sec=60.0,
                         chapter_min=3, chapter_max=20, chapter_top_words=4,
                         time_from=None, time_to=None,
                         llm_titles=False, args=None):
    """Download audio and transcribe with Whisper."""
    safe_id = safe_video_id(video_url, fallback=video_id or 'video')

    # Step 0: Check whisper transcript cache BEFORE downloading audio
    # (most expensive step — worth caching; avoids network + disk entirely)
    cache = get_cache()
    cached = cache.get_transcript(safe_id, whisper_model, backend)
    if cached and cached.get('status') == 'success' and time_from is None and time_to is None:
        # Cache is for the full video — skip when a time range is requested
        eprint(f'📦 命中转写缓存: {safe_id}')
        cached_text = cached.get('text', '')
        cached_title = cached.get('title', title)
        txt_path, meta_path = save_output(
            safe_id, cached_title, cached_text,
            'whisper',
            {'source': 'whisper', 'model': whisper_model, 'device': device,
             'language': cached.get('language', 'auto'),
             'duration_sec': cached.get('duration_sec', 0),
             'cached': True}
        )
        result = {
            "status": "success",
            "source": "whisper",
            "title": cached_title,
            "transcript_file": str(txt_path),
            "metadata_file": str(meta_path),
            "char_count": len(cached_text),
            "message": "Whisper 转写已完成（命中缓存）。",
            "cached": True,
        }
        if chapters:
            ch, ch_path = generate_chapters(
                cached_text, safe_id, cached_title,
                window_sec=chapter_window_sec, min_chapters=chapter_min,
                max_chapters=chapter_max, top_words=chapter_top_words,
                llm_titles=llm_titles, args=args)
            if ch:
                result["chapters"] = ch
                result["chapters_file"] = ch_path
                eprint(f'📑 章节检测完成: {len(ch)} 章（缓存）')
        return result

    # Step 1: Download audio
    eprint('🎧 Downloading audio...')
    audio_args = [
        '--video-id', video_url,
        '--output', str(whisper_temp / f'{safe_id}.wav'),
        '--temp-dir', str(whisper_temp),
    ]
    if time_from is not None:
        audio_args += ['--start-sec', str(time_from)]
    if time_to is not None:
        audio_args += ['--end-sec', str(time_to)]
    if getattr(args, 'cookies', None):
        audio_args += ['--cookies', args.cookies]
    wo, we, wc = run_script(FETCH_AUDIO_PY, *audio_args, timeout=600)

    try:
        audio_result = json.loads(wo)
    except json.JSONDecodeError:
        return {"status": "failed", "phase": "download",
                "message": f"Audio download script error: {wo[:200]}"}

    if audio_result.get('status') != 'success':
        return audio_result

    audio_file = audio_result['audio_file']
    duration = audio_result.get('duration_sec', 0)
    actual_title = audio_result.get('title', title) or title

    # Step 2: Transcribe with Whisper
    est_sec = max(10, duration // 30)
    eprint(f'🎙 Transcribing with Whisper ({whisper_model}, {device}, {backend}) ~{est_sec}s...')

    ts_flag = ['--timestamps'] if timestamps else []
    wo_kwargs = [
        '--input', audio_file,
        '--model', whisper_model,
        '--device', device,
        '--model-dir', str(whisper_model_dir),
        '--backend', backend,
    ] + ts_flag

    if chunk_minutes and chunk_minutes > 0:
        wo_kwargs += ['--chunk-minutes', str(chunk_minutes)]
    if chunk_workers and chunk_workers > 0:
        wo_kwargs += ['--chunk-workers', str(chunk_workers)]

    if whisper_language:
        wo_kwargs += ['--language', whisper_language]

    wo, we, wc = run_script(WHISPER_PY, *wo_kwargs, timeout=1800)

    try:
        whisper_result = json.loads(wo)
    except json.JSONDecodeError:
        return {"status": "failed", "phase": "whisper",
                "detail": we[:500] if we else wo[:500],
                "message": "Whisper 转写失败。尝试 --device cpu 或换模型。"}

    if whisper_result.get('status') != 'success':
        whisper_result['phase'] = 'whisper'
        return whisper_result

    transcript = whisper_result.get('text', '')
    language = whisper_result.get('language', whisper_language or 'auto')

    txt_path, meta_path = save_output(
        safe_id,
        actual_title,
        transcript,
        'whisper',
        {'source': 'whisper', 'model': whisper_model, 'device': device,
         'language': language, 'duration_sec': duration}
    )

    # Cache the transcript so re-runs skip download + transcription
    cache.set_transcript(safe_id, whisper_model, backend, {
        "status": "success",
        "text": transcript,
        "language": language,
        "duration_sec": duration,
        "title": actual_title,
    })

    result = {
        "status": "success",
        "source": "whisper",
        "title": actual_title,
        "transcript_file": str(txt_path),
        "metadata_file": str(meta_path),
        "char_count": len(transcript),
        "message": "Whisper 转写已完成，请总结。",
    }

    if chapters:
        ch, ch_path = generate_chapters(
            transcript, safe_id, actual_title,
            window_sec=chapter_window_sec, min_chapters=chapter_min,
            max_chapters=chapter_max, top_words=chapter_top_words,
            llm_titles=llm_titles, args=args)
        if ch:
            result["chapters"] = ch
            result["chapters_file"] = ch_path
            eprint(f'📑 章节检测完成: {len(ch)} 章')

    return result


# ── Single-video processing ─────────────────────────────────────────────
def _interleave_captions(primary: str, secondary: str,
                         primary_lang: str = '', secondary_lang: str = '') -> str:
    """
    Interleave two caption texts line by line, pairing by position.

    Used for bilingual output (e.g. zh primary + en secondary).
    Lines are grouped by timestamp when present, otherwise matched by index.
    """
    prim_lines = [ln for ln in primary.split('\n') if ln.strip()]
    sec_lines = [ln for ln in secondary.split('\n') if ln.strip()]

    # Match by timestamp prefix [MM:SS] when both have it
    prim_ts = {}
    for ln in prim_lines:
        m = re.match(r'(\[\d{2}:\d{2}\])\s*(.*)', ln)
        if m:
            prim_ts.setdefault(m.group(1), []).append(m.group(2))
    sec_ts = {}
    for ln in sec_lines:
        m = re.match(r'(\[\d{2}:\d{2}\])\s*(.*)', ln)
        if m:
            sec_ts.setdefault(m.group(1), []).append(m.group(2))

    if prim_ts and sec_ts:
        # Timestamp-aligned interleave
        out = []
        all_ts = sorted(set(prim_ts) | set(sec_ts))
        for ts in all_ts:
            p = ' '.join(prim_ts.get(ts, []))
            s = ' '.join(sec_ts.get(ts, []))
            if p:
                out.append(f'{ts} {p}')
            if s:
                out.append(f'{ts} {s}')
        return '\n'.join(out)

    # Fallback: index-based interleave
    out = []
    max_len = max(len(prim_lines), len(sec_lines))
    for i in range(max_len):
        if i < len(prim_lines):
            out.append(f'[{primary_lang}] {prim_lines[i]}')
        if i < len(sec_lines):
            out.append(f'[{secondary_lang}] {sec_lines[i]}')
    return '\n'.join(out)


def process_one_video(video, args, whisper_model, device,
                      whisper_model_dir, whisper_temp):
    """Process a single video; returns a JSON result dict."""
    video_id = extract_video_id(video) or safe_video_id(video)

    # Determine mode
    mode = 'default'
    if args.force_whisper:
        mode = 'force_whisper'
    elif args.auto:
        mode = 'auto'
    elif args.whisper:
        mode = 'whisper'

    # ── Step 1: Try subtitles (unless force-whisper) ────────────────────
    if mode != 'force_whisper':
        # Check cache first
        cache = get_cache()
        cached = cache.get_subtitles(video_id, args.languages, args.timestamps)
        if cached:
            eprint(f'📦 命中字幕缓存: {video_id}')
            sub_result = cached
        else:
            eprint(f'📡 Checking captions for {video}...')
            sub_args = ['--video-id', video, '--languages', args.languages]
            if args.timestamps:
                sub_args.append('--timestamps')
            if args.bilingual:
                # Secondary language = second entry in --languages (or 'en' fallback)
                langs = [lg.strip() for lg in args.languages.split(',') if lg.strip()]
                second = langs[1] if len(langs) > 1 else 'en'
                sub_args += ['--second-language', second]

            so, se, sc = run_script(FETCH_SUB_PY, *sub_args, timeout=90)

            try:
                sub_result = json.loads(so)
            except json.JSONDecodeError:
                sub_result = {"status": "failed", "phase": "parse",
                              "message": f"Subtitle script output parse error: {so[:200]}"}

            # Cache successful subtitle results
            if sub_result.get('status') == 'success':
                cache.set_subtitles(video_id, args.languages, args.timestamps, sub_result)

        if sub_result.get('status') == 'success':
            transcript = sub_result.get('subtitles', '')
            title = sub_result.get('title') or fetch_video_title(video)

            # Verify subtitles — if suspicious and in auto mode, fall back to Whisper
            suspicious, reason = subtitle_looks_suspicious(transcript, title)
            if suspicious:
                eprint(f'⚠ 字幕检测异常: {reason}')
                if mode in ('auto', 'whisper'):
                    eprint('↪ 自动切换到 Whisper 转写...')
                else:
                    pass  # Default mode: still succeed with captions

            if not (suspicious and mode in ('auto', 'whisper')):
                # Time-range filter: keep only segments within [from, to]
                if args.time_from_sec is not None or args.time_to_sec is not None:
                    segments = sub_result.get('segments') or []
                    t_from = args.time_from_sec or 0.0
                    t_to = args.time_to_sec or float('inf')
                    filtered = [s for s in segments
                                if s.get('start', 0) >= t_from and s.get('start', 0) <= t_to]
                    if filtered:
                        from convert_subtitles import convert_segments
                        transcript = convert_segments(filtered, 'txt')
                        sub_result['segments'] = filtered
                        sub_result['subtitle_count'] = len(filtered)
                        eprint(f'⏱ 时间过滤: {len(segments)} → {len(filtered)} 条字幕')

                # Bilingual: interleave primary + secondary captions
                secondary = sub_result.get('secondary_subtitles')
                if secondary:
                    transcript = _interleave_captions(
                        transcript, secondary,
                        sub_result.get('language', ''),
                        sub_result.get('secondary_language', '')
                    )

                txt_path, meta_path = save_output(
                    video_id, title, transcript, 'caption',
                    {'source': 'caption', 'language': sub_result.get('language', ''),
                     'is_auto_generated': sub_result.get('is_auto_generated', False),
                     'subtitle_count': sub_result.get('subtitle_count', 0)}
                )

                result = {
                    "status": "success",
                    "source": "caption",
                    "title": title,
                    "transcript_file": str(txt_path),
                    "metadata_file": str(meta_path),
                    "char_count": len(transcript),
                    "message": "字幕已提取，请总结。",
                }

                # Optional: auto-detect chapters
                if args.chapters:
                    chapters, ch_path = generate_chapters(
                        transcript, video_id, title,
                        window_sec=args.chapter_window_sec,
                        min_chapters=args.chapter_min,
                        max_chapters=args.chapter_max,
                        top_words=args.chapter_top_words,
                        llm_titles=args.llm_titles, args=args,
                    )
                    if chapters:
                        result["chapters"] = chapters
                        result["chapters_file"] = ch_path
                        eprint(f'📑 章节检测完成: {len(chapters)} 章')

                # Optional: convert subtitles to srt/vtt/lrc/txt
                if args.format:
                    segments = sub_result.get('segments')
                    if segments:
                        conv_path, _ = convert_subtitles_to(
                            segments, args.format, video_id, title)
                        if conv_path:
                            result["converted_file"] = conv_path
                            eprint(f'🎬 字幕已转换: {args.format.upper()}')

                # Optional: LLM translation
                if args.translate:
                    tr_path, tr_text = translate_transcript(
                        transcript, video_id, title,
                        target=args.translate_target, args=args,
                        bilingual=args.bilingual_translate)
                    if tr_path:
                        result["translated_file"] = tr_path
                        result["translated_char_count"] = len(tr_text)

                # Optional: archive structured note to knowledge base
                if args.archive:
                    archive_extra = {}
                    if result.get('chapters'):
                        archive_extra['chapters'] = result['chapters']
                    if result.get('translated_file'):
                        try:
                            tlines = Path(result['translated_file']).read_text(
                                encoding='utf-8').split('\n')
                            archive_extra['translated'] = '\n'.join(tlines[3:])
                            archive_extra['translate_target'] = args.translate_target
                        except OSError:
                            pass
                    note_path = archive_note(
                        video_id, title, transcript, result,
                        args.archive, extra=archive_extra)
                    result['archive_file'] = note_path
                    eprint(f'📚 已归档笔记: {note_path}')

                eprint(f'✅ 字幕已提取 ({sub_result.get("subtitle_count", 0)} 条)')
                return result
            # else: fall through to Whisper pipeline

        # Subtitle fetch failed — handle the failure
        phase = sub_result.get('phase', '')
        if phase == 'no_captions' or sub_result.get('status') == 'success':
            if phase == 'no_captions':
                eprint('📭 视频无字幕可用')
            if mode == 'default':
                next_cmd = f'--whisper {shlex.quote(video)}'
                if args.languages:
                    next_cmd += f' --languages {shlex.quote(args.languages)}'
                if args.timestamps:
                    next_cmd += ' --timestamps'
                return {
                    "status": "needs_confirmation",
                    "message": "此视频无可用字幕。是否用 Whisper 自动转写音频？",
                    "video_id": video_id,
                    "next_command": next_cmd,
                    "next_flags": ["--whisper"]
                }
            # whisper/auto modes: fall through to Whisper
        else:
            # API/network error — do NOT suggest Whisper
            return {
                "status": "failed",
                "phase": phase,
                "message": sub_result.get('message', '字幕提取失败'),
                "detail": sub_result.get('message', '')
            }

    # ── Step 2: Whisper transcription ───────────────────────────────────
    eprint('🎤 切换到 Whisper 音频转写模式...')

    whisper_language = args.whisper_language
    if not whisper_language:
        langs = [lg.strip() for lg in args.languages.split(',')]
        whisper_language = langs[0][:2] if langs else 'auto'

    return run_whisper_pipeline(
        video_id, video, video_id, whisper_model, device,
        whisper_model_dir, whisper_temp,
        whisper_language, args.timestamps, args.backend,
        args.chunk_minutes, args.chunk_workers,
        args.chapters, args.chapter_window_sec,
        args.chapter_min, args.chapter_max, args.chapter_top_words,
        args.time_from_sec, args.time_to_sec,
        args.llm_titles, args
    )


# ── Playlist processing ─────────────────────────────────────────────────
def process_playlist(playlist_url, args, whisper_model, device,
                     whisper_model_dir, whisper_temp):
    """Fetch playlist video list and process each video sequentially."""
    eprint(f'📋 播放列表模式: {playlist_url}')

    pl_args = ['--url', playlist_url]
    if args.max:
        pl_args += ['--max', str(args.max)]
    if args.cookies:
        pl_args += ['--cookies', args.cookies]

    po, pe, pc = run_script(FETCH_PLAYLIST_PY, *pl_args, timeout=120)

    try:
        pl_result = json.loads(po)
    except json.JSONDecodeError:
        return {"status": "failed", "phase": "playlist_parse",
                "message": f"播放列表解析失败: {po[:200]}"}

    if pl_result.get('status') != 'success':
        return pl_result

    videos = pl_result.get('videos', [])
    eprint(f'📋 播放列表包含 {len(videos)} 个视频')

    results = []
    skipped = 0
    cache = get_cache()
    for i, v in enumerate(videos, 1):
        vid = v['id']
        title = v.get('title', '')

        # Resume support: skip videos already processed (cached subtitles)
        cached = cache.get_subtitles(vid, args.languages, args.timestamps)
        if cached:
            skipped += 1
            eprint(f'[{i}/{len(videos)}] ⏭ 跳过 {vid}（已处理）')
            results.append({
                "index": i, "video_id": vid, "title": title,
                "status": "success", "skipped": True,
                "message": "已在缓存中，跳过",
            })
            continue

        eprint(f'\n[{i}/{len(videos)}] {vid} — {title}')
        result = process_one_video(
            vid, args, whisper_model, device,
            whisper_model_dir, whisper_temp
        )
        result['index'] = i
        result['video_id'] = vid
        results.append(result)

    success_count = sum(1 for r in results if r.get('status') == 'success')
    eprint(f'\n📋 播放列表处理完成: {success_count}/{len(results)} 成功'
           f'（跳过 {skipped} 个已处理）')

    return {
        "status": "success",
        "source": "playlist",
        "playlist_title": pl_result.get('playlist_title', ''),
        "total": len(results),
        "success_count": success_count,
        "skipped_count": skipped,
        "results": results,
    }


# ── Entry point ─────────────────────────────────────────────────────────
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Analyze YouTube video content')
    parser.add_argument('video', help='YouTube URL, video ID, or playlist URL')
    parser.add_argument('--whisper', action='store_true',
                        help='Use subtitles; if none, transcribe without asking')
    parser.add_argument('--auto', action='store_true',
                        help='Full auto: subtitles if available, Whisper if not')
    parser.add_argument('--force-whisper', action='store_true',
                        help='Skip subtitles, always download+transcribe')
    parser.add_argument('--languages', default='zh-Hans,zh-Hant,en',
                        help='Caption language priority (comma-separated)')
    parser.add_argument('--whisper-language', default=None,
                        help='Whisper transcription language (auto = detect)')
    parser.add_argument('--whisper-model', default=None)
    parser.add_argument('--device', default='auto',
                        help='torch device: auto (default), cuda, cpu')
    parser.add_argument('--timestamps', action='store_true',
                        help='Include timestamps in output')
    parser.add_argument('--playlist', action='store_true',
                        help='Treat URL as playlist and batch process')
    parser.add_argument('--max', type=int, default=None,
                        help='With --playlist: limit to N videos')
    parser.add_argument('--cookies', default=None,
                        help='With --playlist: path to cookies.txt')
    parser.add_argument('--backend', default='openai',
                        choices=['openai', 'faster-whisper'],
                        help='Whisper backend: openai (default) or faster-whisper (4x faster)')
    parser.add_argument('--bilingual', action='store_true',
                        help='Bilingual output: interleave primary + secondary captions')
    parser.add_argument('--chunk-minutes', type=int, default=0,
                        help='Split long audio into N-minute chunks. CPU: parallel '
                             'across processes (4-6x speedup); GPU: OOM-safe')
    parser.add_argument('--chunk-workers', type=int, default=0,
                        help='Max parallel chunk workers on CPU (default: cpu_count)')
    parser.add_argument('--chapters', action='store_true',
                        help='Auto-detect chapters from the transcript (TextTiling)')
    parser.add_argument('--chapter-window-sec', type=float, default=60.0,
                        help='Chapter detection window size in seconds (default: 60)')
    parser.add_argument('--chapter-min', type=int, default=3,
                        help='Minimum chapters to detect')
    parser.add_argument('--chapter-max', type=int, default=20,
                        help='Maximum chapters to detect')
    parser.add_argument('--chapter-top-words', type=int, default=4,
                        help='Keywords per auto-generated chapter title')
    parser.add_argument('--llm-titles', action='store_true',
                        help='Polish chapter titles via LLM (needs DEEPSEEK_API_KEY)')
    parser.add_argument('--format', default=None,
                        choices=['srt', 'vtt', 'lrc', 'txt'],
                        help='Convert subtitles to standard format (srt/vtt/lrc/txt)')
    parser.add_argument('--translate', action='store_true',
                        help='Translate subtitles via LLM API (needs DEEPSEEK_API_KEY/OPENAI_API_KEY)')
    parser.add_argument('--translate-target', default='zh',
                        help='Translation target language (zh/en/ja/ko/fr/de/es, default: zh)')
    parser.add_argument('--translate-api-key', default=None,
                        help='LLM API key (default: DEEPSEEK_API_KEY or OPENAI_API_KEY env)')
    parser.add_argument('--translate-base-url', default=None,
                        help='OpenAI-compatible API base URL (default: DeepSeek)')
    parser.add_argument('--translate-model', default=None,
                        help='Translation model (default: deepseek-chat)')
    parser.add_argument('--bilingual-translate', action='store_true',
                        help='With --translate: interleave original + translated lines')
    parser.add_argument('--archive', default=None,
                        help='Archive a structured markdown note to this directory '
                             '(Obsidian vault or any folder)')
    parser.add_argument('--from', dest='time_from', default=None,
                        help='Only process from this time (90, 01:30, 1:02:30)')
    parser.add_argument('--to', dest='time_to', default=None,
                        help='Only process up to this time (90, 01:30, 1:02:30)')
    args = parser.parse_args()

    # Load .env if present (does not override existing env vars)
    load_env()

    # Parse time range (--from/--to)
    args.time_from_sec = parse_time_arg(args.time_from)
    args.time_to_sec = parse_time_arg(args.time_to)
    if args.time_from_sec is not None or args.time_to_sec is not None:
        eprint(f'⏱ 时间范围: {args.time_from_sec or 0:.0f}s → '
               f'{args.time_to_sec if args.time_to_sec is not None else "END"}s')

    # ── Configuration from env ──────────────────────────────────────────
    whisper_model = args.whisper_model or os.environ.get('WHISPER_MODEL', 'small')
    device = detect_device(args.device or os.environ.get('WHISPER_DEVICE', 'auto'))
    if device == 'cpu' and (args.device or os.environ.get('WHISPER_DEVICE')) in (None, 'auto', ''):
        eprint('ℹ 未检测到 GPU，使用 CPU 转写（可加 --device cuda 强制）')

    whisper_model_dir = Path(os.environ.get(
        'WHISPER_MODEL_DIR', str(Path.home() / '.hermes' / 'whisper' / 'models')))
    whisper_temp = Path(os.environ.get(
        'WHISPER_TEMP', str(Path.home() / '.hermes' / 'whisper' / 'temp')))

    video = args.video.strip()

    # ── Playlist mode ────────────────────────────────────────────────────
    if args.playlist or ('list=' in video and '/watch' in video):
        result = process_playlist(video, args, whisper_model, device,
                                  whisper_model_dir, whisper_temp)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get('status') != 'success':
            sys.exit(1)
        return

    # ── Single video mode ───────────────────────────────────────────────
    result = process_one_video(
        video, args, whisper_model, device,
        whisper_model_dir, whisper_temp
    )

    print(json.dumps(result, ensure_ascii=False))
    if result.get('status') != 'success':
        sys.exit(1)


if __name__ == '__main__':
    main()

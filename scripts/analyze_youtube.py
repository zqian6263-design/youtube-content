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

Flags:
  --languages zh-Hans,en        Caption language priority (default: zh-Hans,zh-Hant,en)
  --whisper-language auto       Whisper transcription language (auto = detect)
  --whisper-model small         Whisper model size
  --device auto                 torch device: auto/cuda/cpu (default: auto-detect)
  --timestamps                  Include MM:SS timestamps in output

Env overrides:
  WHISPER_MODEL_DIR, WHISPER_TEMP, WHISPER_MODEL, WHISPER_DEVICE
"""

import os, sys, json, re, subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = SKILL_DIR / 'output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Shared utilities
sys.path.insert(0, str(SCRIPT_DIR))
from youtube_utils import (
    extract_video_id, safe_video_id, safe_filename, detect_device, load_env
)

FETCH_SUB_PY = SCRIPT_DIR / 'fetch_subtitle_youtube.py'
FETCH_AUDIO_PY = SCRIPT_DIR / 'fetch_audio_youtube.py'
WHISPER_PY = SCRIPT_DIR / 'transcribe_whisper.py'


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


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
                         whisper_language, timestamps):
    """Download audio and transcribe with Whisper."""
    safe_id = safe_video_id(video_url, fallback=video_id or 'video')

    # Step 1: Download audio
    eprint('🎧 Downloading audio...')
    audio_args = [
        '--video-id', video_url,
        '--output', str(whisper_temp / f'{safe_id}.wav'),
        '--temp-dir', str(whisper_temp),
    ]
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
    eprint(f'🎙 Transcribing with Whisper ({whisper_model}, {device}) ~{est_sec}s...')

    ts_flag = ['--timestamps'] if timestamps else []
    wo_kwargs = [
        '--input', audio_file,
        '--model', whisper_model,
        '--device', device,
        '--model-dir', str(whisper_model_dir),
    ] + ts_flag

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

    return {
        "status": "success",
        "source": "whisper",
        "title": actual_title,
        "transcript_file": str(txt_path),
        "metadata_file": str(meta_path),
        "char_count": len(transcript),
        "message": "Whisper 转写已完成，请总结。",
    }


# ── Entry point ─────────────────────────────────────────────────────────
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Analyze YouTube video content')
    parser.add_argument('video', help='YouTube URL or video ID')
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
    args = parser.parse_args()

    # Load .env if present (does not override existing env vars)
    load_env()

    # ── Configuration from env ──────────────────────────────────────────
    whisper_model = args.whisper_model or os.environ.get('WHISPER_MODEL', 'small')
    # device=auto by default: use GPU if available, else CPU
    device = detect_device(args.device or os.environ.get('WHISPER_DEVICE', 'auto'))
    if device == 'cpu' and (args.device or os.environ.get('WHISPER_DEVICE')) in (None, 'auto', ''):
        eprint(f'ℹ 未检测到 GPU，使用 CPU 转写（可加 --device cuda 强制）')

    whisper_model_dir = Path(os.environ.get(
        'WHISPER_MODEL_DIR', str(Path.home() / '.hermes' / 'whisper' / 'models')))
    whisper_temp = Path(os.environ.get(
        'WHISPER_TEMP', str(Path.home() / '.hermes' / 'whisper' / 'temp')))

    # Determine mode
    mode = 'default'
    if args.force_whisper:
        mode = 'force_whisper'
    elif args.auto:
        mode = 'auto'
    elif args.whisper:
        mode = 'whisper'

    video = args.video.strip()
    video_id = extract_video_id(video) or safe_video_id(video)

    # ── Step 1: Try subtitles (unless force-whisper) ────────────────────
    if mode != 'force_whisper':
        eprint(f'📡 Checking captions for {video}...')
        sub_args = ['--video-id', video, '--languages', args.languages]
        if args.timestamps:
            sub_args.append('--timestamps')

        so, se, sc = run_script(FETCH_SUB_PY, *sub_args, timeout=90)

        try:
            sub_result = json.loads(so)
        except json.JSONDecodeError:
            sub_result = {"status": "failed", "phase": "parse",
                          "message": f"Subtitle script output parse error: {so[:200]}"}

        if sub_result.get('status') == 'success':
            transcript = sub_result.get('subtitles', '')
            title = sub_result.get('title') or fetch_video_title(video)

            # Verify subtitles — if suspicious and in auto mode, fall back to Whisper
            suspicious, reason = subtitle_looks_suspicious(transcript, title)
            if suspicious:
                eprint(f'⚠ 字幕检测异常: {reason}')
                if mode in ('auto', 'whisper'):
                    eprint(f'↪ 自动切换到 Whisper 转写...')
                else:
                    # Default mode: still succeed with the captions but note the issue
                    pass

            if not (suspicious and mode in ('auto', 'whisper')):
                txt_path, meta_path = save_output(
                    video_id, title, transcript, 'caption',
                    {'source': 'caption', 'language': sub_result.get('language', ''),
                     'is_auto_generated': sub_result.get('is_auto_generated', False),
                     'subtitle_count': sub_result.get('subtitle_count', 0)}
                )

                eprint(f'✅ 字幕已提取 ({sub_result.get("subtitle_count", 0)} 条)')
                print(json.dumps({
                    "status": "success",
                    "source": "caption",
                    "title": title,
                    "transcript_file": str(txt_path),
                    "metadata_file": str(meta_path),
                    "char_count": len(transcript),
                    "message": "字幕已提取，请总结。",
                }, ensure_ascii=False))
                return
            # else: fall through to Whisper pipeline

        # Subtitle fetch failed — handle the failure
        phase = sub_result.get('phase', '')
        if phase == 'no_captions' or sub_result.get('status') == 'success':
            if phase == 'no_captions':
                eprint('📭 视频无字幕可用')
            if mode == 'default':
                import shlex
                next_cmd = f'--whisper {shlex.quote(video)}'
                if args.languages:
                    next_cmd += f' --languages {shlex.quote(args.languages)}'
                if args.timestamps:
                    next_cmd += ' --timestamps'
                print(json.dumps({
                    "status": "needs_confirmation",
                    "message": "此视频无可用字幕。是否用 Whisper 自动转写音频？",
                    "video_id": video_id,
                    "next_command": next_cmd,
                    "next_flags": ["--whisper"]
                }, ensure_ascii=False))
                return
            # whisper/auto modes: fall through to Whisper
        else:
            # API/network error — do NOT suggest Whisper (root cause isn't missing captions)
            print(json.dumps({
                "status": "failed",
                "phase": phase,
                "message": sub_result.get('message', '字幕提取失败'),
                "detail": sub_result.get('message', '')
            }))
            return

    # ── Step 2: Whisper transcription ───────────────────────────────────
    eprint('🎤 切换到 Whisper 音频转写模式...')

    whisper_language = args.whisper_language
    if not whisper_language:
        langs = [l.strip() for l in args.languages.split(',')]
        whisper_language = langs[0][:2] if langs else 'auto'

    result = run_whisper_pipeline(
        video_id, video, video_id, whisper_model, device,
        whisper_model_dir, whisper_temp,
        whisper_language, args.timestamps
    )

    print(json.dumps(result, ensure_ascii=False))
    if result.get('status') != 'success':
        sys.exit(1)


if __name__ == '__main__':
    main()

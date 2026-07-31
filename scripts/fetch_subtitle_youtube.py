#!/usr/bin/env python3
"""
fetch_subtitle_youtube.py — Fetch YouTube captions/subtitles.

Strategy:
  1. Try youtube-transcript-api (fast, direct)
  2. If blocked, fall back to yt-dlp subtitle extraction (slower but works
     when transcript API is blocked)

Usage:
  python fetch_subtitle_youtube.py --video-id <VIDEO_ID> [--languages zh-Hans,en] [--timestamps]

Output (JSON to stdout):
  success: {"status":"success","video_id":"...","title":"...","language":"...",
            "subtitles":"...","subtitle_count":N,"is_auto_generated":bool}
  failed:  {"status":"failed","phase":"...","message":"..."}
"""

import sys
import tempfile
from pathlib import Path

# Shared utilities (same directory as this script)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_utils import emit_json, extract_video_id, format_vtt


def format_segments(segments, include_timestamps: bool = False) -> str:
    """Format transcript snippets (dicts with text/start) into readable text."""
    lines = []
    for seg in segments:
        text = seg.get('text', '').strip() if isinstance(seg, dict) else str(seg).strip()
        if not text:
            continue
        if include_timestamps:
            start = seg.get('start', 0) if isinstance(seg, dict) else 0
            lines.append(f'[{int(start//60):02d}:{int(start%60):02d}] {text}')
        else:
            lines.append(text)
    return '\n'.join(lines)


def try_transcript_api(video_id: str, languages: list, timestamps: bool,
                       second_lang: str = None):
    """Try fetching captions via youtube-transcript-api (v1.x compatible)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    try:
        api = YouTubeTranscriptApi()

        def fetch_lang(lang_code):
            result = api.fetch(video_id, languages=[lang_code])
            segments = []
            for seg in result:
                segments.append({
                    "text": seg.text if hasattr(seg, 'text') else str(seg),
                    "start": seg.start if hasattr(seg, 'start') else 0,
                    "duration": seg.duration if hasattr(seg, 'duration') else 0,
                })
            return segments

        # Primary language
        primary_segments = None
        used_lang = None
        for lang in languages:
            try:
                primary_segments = fetch_lang(lang)
                used_lang = lang
                break
            except Exception:
                continue

        if not primary_segments:
            # Try any available transcript
            result = api.fetch(video_id)
            primary_segments = [
                {"text": seg.text if hasattr(seg, 'text') else str(seg),
                 "start": seg.start if hasattr(seg, 'start') else 0,
                 "duration": seg.duration if hasattr(seg, 'duration') else 0}
                for seg in result
            ]
            used_lang = "unknown"

        if primary_segments:
            text = format_segments(primary_segments, include_timestamps=timestamps)
            result = {
                "status": "success",
                "video_id": video_id,
                "language": used_lang or (languages[0] if languages else "unknown"),
                "subtitles": text,
                "subtitle_count": len(primary_segments),
                "is_auto_generated": False,
            }
            # Bilingual: fetch secondary language too
            if second_lang:
                try:
                    sec_segments = fetch_lang(second_lang)
                    result["secondary_language"] = second_lang
                    result["secondary_subtitles"] = format_segments(
                        sec_segments, include_timestamps=timestamps)
                    result["secondary_count"] = len(sec_segments)
                except Exception:
                    pass
            return result
    except Exception as e:
        err = str(e)
        if 'blocked' in err.lower() or 'requestblocked' in err.lower() or 'ipblocked' in err.lower():
            return {"status": "blocked", "message": err[:200]}
        if 'No transcripts' in err or 'TranscriptsDisabled' in err:
            return {"status": "no_captions", "video_id": video_id}
        return {"status": "error", "message": err[:200]}

    return None


def try_ytdlp_subtitles(video_id: str, languages: list, timestamps: bool):
    """Fallback: download subtitles via yt-dlp, preserving timestamps."""
    try:
        import yt_dlp
    except ImportError:
        return None

    with tempfile.TemporaryDirectory(prefix='yt_sub_') as tmpdir:
        # Step 1: List available subtitles
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(video_id, download=False)
                title = info.get('title', video_id)
                available_subs = info.get('subtitles', {}) or {}
                auto_subs = info.get('automatic_captions', {}) or {}
        except Exception:
            return None

        # Find best matching subtitle language
        selected_lang = None
        is_auto = False
        for lang in languages + ['en', 'zh-Hans', 'zh-Hant']:
            if lang in available_subs:
                selected_lang = lang
                break
        if not selected_lang:
            for lang in languages + ['en']:
                if lang in auto_subs:
                    selected_lang = lang
                    is_auto = True
                    break
        if not selected_lang:
            return None

        # Step 2: Download the subtitle
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [selected_lang],
            'subtitlesformat': 'vtt',
            'skip_download': True,
            'outtmpl': str(Path(tmpdir) / '%(id)s'),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_id])
        except Exception:
            return None

        # Step 3: Parse the subtitle file
        sub_files = sorted(Path(tmpdir).glob(f'{video_id}.*'))
        if not sub_files:
            return None

        with open(sub_files[0], encoding='utf-8', errors='replace') as f:
            vtt_content = f.read()

        # Parse VTT preserving timestamps
        lines = format_vtt(vtt_content, include_timestamps=timestamps)
        count = len(lines)

        text = '\n'.join(lines)

        if text.strip():
            return {
                "status": "success",
                "video_id": video_id,
                "language": selected_lang,
                "subtitles": text,
                "subtitle_count": count,
                "is_auto_generated": is_auto,
                "title": title,
            }

    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Fetch YouTube captions')
    parser.add_argument('--video-id', required=True)
    parser.add_argument('--languages', default='zh-Hans,zh-Hant,en')
    parser.add_argument('--timestamps', action='store_true')
    parser.add_argument('--second-language', default=None,
                        help='Bilingual: also fetch this language as secondary')
    args = parser.parse_args()

    video_id = extract_video_id(args.video_id)
    if not video_id:
        emit_json({"status": "failed", "phase": "extract",
                   "message": f"Could not extract video ID from: {args.video_id}"},
                  exit_code=1)

    languages = [lg.strip() for lg in args.languages.split(',') if lg.strip()]

    # Strategy 1: youtube-transcript-api (fast)
    result = try_transcript_api(video_id, languages, args.timestamps,
                                second_lang=args.second_language)
    if result:
        if result.get('status') == 'success':
            emit_json(result)
        elif result.get('status') not in ('blocked', 'no_captions'):
            emit_json(result, exit_code=1)

    # Strategy 2: yt-dlp subtitle extraction (fallback)
    result = try_ytdlp_subtitles(video_id, languages, args.timestamps)
    if result and result.get('status') == 'success':
        emit_json(result)

    # Both failed
    emit_json({
        "status": "failed", "phase": "no_captions",
        "message": f"No captions available for video {video_id} (tried API + yt-dlp fallback)",
        "video_id": video_id
    }, exit_code=1)


if __name__ == '__main__':
    main()

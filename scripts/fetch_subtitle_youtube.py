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
  success: {"status":"success","video_id":"...","language":"...","subtitles":"...","subtitle_count":N}
  failed:  {"status":"failed","phase":"...","message":"..."}
"""

import os, sys, json, re, subprocess, tempfile
from pathlib import Path


def extract_video_id(url_or_id: str) -> str | None:
    if re.match(r'^[A-Za-z0-9_-]{11}$', url_or_id):
        return url_or_id
    patterns = [
        r'(?:youtube\.com/watch\?.*v=)([A-Za-z0-9_-]{11})',
        r'(?:youtu\.be/)([A-Za-z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})',
        r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url_or_id)
        if m:
            return m.group(1)
    return None


def format_segments(segments, include_timestamps: bool = False) -> str:
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


def try_transcript_api(video_id: str, languages: list, timestamps: bool):
    """Try fetching captions via youtube-transcript-api."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    try:
        api = YouTubeTranscriptApi()
        result = api.fetch(video_id, languages=languages) if languages else api.fetch(video_id)

        segments = []
        for seg in result:
            segments.append({
                "text": seg.text if hasattr(seg, 'text') else str(seg),
                "start": seg.start if hasattr(seg, 'start') else 0,
                "duration": seg.duration if hasattr(seg, 'duration') else 0,
            })

        if segments:
            text = format_segments(segments, include_timestamps=timestamps)
            return {
                "status": "success",
                "video_id": video_id,
                "language": languages[0] if languages else "unknown",
                "subtitles": text,
                "subtitle_count": len(segments),
                "is_auto_generated": False,
            }
    except Exception as e:
        err = str(e)
        if 'blocked' in err.lower() or 'requestblocked' in err.lower() or 'ipblocked' in err.lower():
            return {"status": "blocked", "message": err[:200]}
        if 'No transcripts' in err or 'TranscriptsDisabled' in err:
            return {"status": "no_captions", "video_id": video_id}
        # Try yt-dlp for title on error
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(video_id, download=False)
                return {"status": "error", "message": err[:200], "title": info.get('title', video_id)}
        except:
            pass
        return {"status": "error", "message": err[:200]}

    return None


def try_ytdlp_subtitles(video_id: str, languages: list, timestamps: bool):
    """Fallback: download subtitles via yt-dlp."""
    with tempfile.TemporaryDirectory(prefix='yt_sub_') as tmpdir:
        # Try to get available subtitles first
        try:
            import yt_dlp
        except ImportError:
            return None

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
        for lang in languages + ['en', 'zh-Hans', 'zh-Hant']:
            if lang in available_subs:
                selected_lang = lang
                break
        if not selected_lang:
            for lang in languages + ['en']:
                if lang in auto_subs:
                    selected_lang = lang
                    break

        if not selected_lang:
            return None

        # Step 2: Download the subtitle
        sub_format = 'vtt'  # VTT is most reliable
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [selected_lang],
            'subtitlesformat': sub_format,
            'skip_download': True,
            'outtmpl': str(Path(tmpdir) / '%(id)s'),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_id])
        except Exception:
            return None

        # Step 3: Parse the subtitle file
        sub_file = Path(tmpdir) / f'{video_id}.{selected_lang}.vtt'
        if not sub_file.exists():
            # Try alternative extensions
            candidates = list(Path(tmpdir).glob(f'{video_id}.*'))
            sub_file = candidates[0] if candidates else None

        if not sub_file or not sub_file.exists():
            return None

        with open(sub_file, 'r', encoding='utf-8', errors='replace') as f:
            vtt_content = f.read()

        # Parse VTT to plain text
        lines = []
        for line in vtt_content.split('\n'):
            line = line.strip()
            # Skip VTT headers, timestamps, and metadata
            if (not line or line.startswith('WEBVTT') or line.startswith('Kind:')
                or line.startswith('Language:') or '-->' in line
                or line.startswith('[') or line.startswith('</')):
                continue
            # Clean VTT tags
            line = re.sub(r'<[^>]+>', '', line)
            if line:
                lines.append(line)

        text = '\n'.join(lines)

        if text.strip():
            return {
                "status": "success",
                "video_id": video_id,
                "language": selected_lang,
                "subtitles": text,
                "subtitle_count": len(lines),
                "is_auto_generated": selected_lang in auto_subs,
                "title": info.get('title', video_id),
            }

    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Fetch YouTube captions')
    parser.add_argument('--video-id', required=True)
    parser.add_argument('--languages', default='zh-Hans,zh-Hant,en')
    parser.add_argument('--timestamps', action='store_true')
    args = parser.parse_args()

    video_id = extract_video_id(args.video_id)
    if not video_id:
        print(json.dumps({"status": "failed", "phase": "extract",
                          "message": f"Could not extract video ID from: {args.video_id}"}))
        sys.exit(1)

    languages = [l.strip() for l in args.languages.split(',') if l.strip()]

    # Strategy 1: youtube-transcript-api (fast)
    result = try_transcript_api(video_id, languages, args.timestamps)
    if result:
        if result.get('status') == 'success':
            print(json.dumps(result, ensure_ascii=False))
            return
        elif result.get('status') != 'blocked' and result.get('status') != 'no_captions':
            print(json.dumps(result, ensure_ascii=False))
            return

    # Strategy 2: yt-dlp subtitle extraction (fallback)
    result = try_ytdlp_subtitles(video_id, languages, args.timestamps)
    if result and result.get('status') == 'success':
        print(json.dumps(result, ensure_ascii=False))
        return

    # Both failed
    print(json.dumps({
        "status": "failed", "phase": "no_captions",
        "message": f"No captions available for video {video_id} (tried API + yt-dlp fallback)",
        "video_id": video_id
    }))
    sys.exit(1)


if __name__ == '__main__':
    main()

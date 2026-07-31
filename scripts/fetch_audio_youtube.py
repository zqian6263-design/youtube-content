#!/usr/bin/env python3
"""
fetch_audio_youtube.py — Download audio from YouTube video using yt-dlp,
then convert to 16kHz mono WAV via ffmpeg.

Usage:
  python fetch_audio_youtube.py --video-id <VIDEO_ID> --output <output.wav> [--temp-dir DIR]

Output (JSON to stdout):
  On success: {"status": "success", "audio_file": "...", "duration_sec": N, "title": "..."}
  On failure: {"status": "failed", "phase": "...", "message": "..."}

Phases: extract, download, ffmpeg
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Shared utilities (same directory as this script)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_utils import emit_json, extract_video_id


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Download YouTube audio')
    parser.add_argument('--video-id', required=True, help='YouTube video ID or URL')
    parser.add_argument('--output', required=True, help='Output .wav file path')
    parser.add_argument('--temp-dir', default=None, help='Temp directory for downloads')
    parser.add_argument('--start-sec', type=float, default=None,
                        help='Only download audio starting at this second')
    parser.add_argument('--end-sec', type=float, default=None,
                        help='Only download audio up to this second')
    parser.add_argument('--cookies', default=None,
                        help='Path to cookies.txt (for bot/age-restricted videos)')
    args = parser.parse_args()

    video_id = extract_video_id(args.video_id)
    if not video_id:
        emit_json({
            "status": "failed", "phase": "extract",
            "message": f"Could not extract video ID from: {args.video_id}"
        }, exit_code=1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = Path(args.temp_dir) if args.temp_dir else Path(tempfile.mkdtemp(prefix='yt_audio_'))
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 1: Download best audio stream via yt-dlp CLI ───────────
        # Using CLI subprocess keeps progress bars out of our stdout,
        # so the JSON result stays parseable.
        yt_dlp_cmd = [
            'yt-dlp', '--quiet', '--no-progress', '--no-warnings',
            '-f', 'bestaudio/best',
            '-o', str(temp_dir / '%(id)s.%(ext)s'),
            video_id
        ]
        # Optional time range: only download a section
        if args.start_sec is not None or args.end_sec is not None:
            start = args.start_sec or 0
            end = args.end_sec if args.end_sec is not None else ''
            yt_dlp_cmd += ['--download-sections', f'*{start}-{end}']
        # Optional cookies (bot / age-restricted bypass)
        if args.cookies:
            yt_dlp_cmd += ['--cookies', args.cookies]
        # Retry once on transient failures
        dl_result = None
        for attempt in range(2):
            dl_result = subprocess.run(
                yt_dlp_cmd, capture_output=True, text=True, timeout=600
            )
            if dl_result.returncode == 0:
                break
            if attempt == 0 and not (args.cookies or args.start_sec):
                import time as _t
                _t.sleep(3)  # brief backoff before retry
        assert dl_result is not None
        if dl_result.returncode != 0:
            stderr = dl_result.stderr or ''
            # Detect bot/age/auth blocks and give actionable guidance
            if 'Sign in to confirm' in stderr or 'bot' in stderr.lower():
                emit_json({
                    "status": "failed", "phase": "auth",
                    "message": ("YouTube 风控拦截（Sign in to confirm you're not a bot）。"
                                "解决办法：\n"
                                "1. 导出浏览器 cookies：yt-dlp --cookies-from-browser chrome \"URL\"\n"
                                "2. 或提供 cookies 文件：--cookies cookies.txt\n"
                                "3. 或稍后重试（临时风控通常几分钟到几小时）"),
                    "detail": stderr[:300]
                }, exit_code=1)
            if 'Video unavailable' in stderr or 'Private video' in stderr:
                emit_json({
                    "status": "failed", "phase": "unavailable",
                    "message": f"视频不可用（可能已删除/私密/地区限制）: {stderr[:200]}"
                }, exit_code=1)
            if 'age' in stderr.lower() or '18' in stderr:
                emit_json({
                    "status": "failed", "phase": "age_restricted",
                    "message": "视频年龄受限，需要登录 cookies 才能下载。"
                                "请提供 --cookies cookies.txt",
                    "detail": stderr[:200]
                }, exit_code=1)
            emit_json({
                "status": "failed", "phase": "download",
                "message": f"yt-dlp download failed: {stderr[:200]}"
            }, exit_code=1)

        # Get video info via yt-dlp JSON output
        info_cmd = ['yt-dlp', '--quiet', '--no-warnings', '--dump-json', video_id]
        info_result = subprocess.run(
            info_cmd, capture_output=True, text=True, timeout=60
        )
        if info_result.returncode == 0:
            try:
                info = json.loads(info_result.stdout)
            except json.JSONDecodeError:
                info = {}
        else:
            info = {}
        duration = info.get('duration', 0)
        title = info.get('title', video_id)

        # Find the downloaded file (yt-dlp may use different extension)
        candidates = list(temp_dir.glob(f'{video_id}.*'))
        audio_src = None
        for c in candidates:
            if c.suffix.lower() in ('.m4a', '.webm', '.mp3', '.opus', '.aac', '.ogg', '.wav'):
                audio_src = c
                break
        if not audio_src and candidates:
            audio_src = candidates[0]

        if not audio_src or not audio_src.exists():
            emit_json({
                "status": "failed", "phase": "download",
                "message": f"Audio download produced no output file for {video_id}"
            }, exit_code=1)

        # ── Step 2: Convert to 16kHz mono WAV via ffmpeg ────────────────
        # Use a distinct temp path to avoid "same as input" errors when
        # yt-dlp already produced a WAV (it sometimes does for 16000Hz).
        conv_output = temp_dir / f'{video_id}_conv.wav'
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', str(audio_src),
            '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le',
            str(conv_output)
        ]
        result = subprocess.run(
            ffmpeg_cmd, capture_output=True, text=True, timeout=600
        )

        if result.returncode != 0:
            # If input is already a 16kHz mono WAV, ffmpeg refuses to
            # overwrite in place; just copy it to the output path.
            if 'same as Input' in result.stderr:
                shutil.copy2(str(audio_src), str(output_path))
            else:
                emit_json({
                    "status": "failed", "phase": "ffmpeg",
                    "message": f"ffmpeg conversion failed: {result.stderr[:200]}"
                }, exit_code=1)
        else:
            shutil.move(str(conv_output), str(output_path))

        if not output_path.exists():
            emit_json({
                "status": "failed", "phase": "ffmpeg",
                "message": "ffmpeg created no output file"
            }, exit_code=1)

        # Cleanup temp: remove only the downloaded source files, NOT the
        # whole temp_dir — it may be a shared directory (output wav lives
        # there when --temp-dir is passed by analyze_youtube).
        try:
            if audio_src and audio_src.exists():
                audio_src.unlink()
            if conv_output.exists():
                conv_output.unlink()
        except OSError:
            pass

        emit_json({
            "status": "success",
            "audio_file": str(output_path),
            "duration_sec": duration,
            "title": title,
            "video_id": video_id,
        })

    except Exception as e:
        # On failure, clean only our own artifacts (never rmtree a shared dir)
        try:
            if 'audio_src' in dir() and audio_src and audio_src.exists():
                audio_src.unlink()
            if 'conv_output' in dir() and conv_output.exists():
                conv_output.unlink()
        except OSError:
            pass
        emit_json({
            "status": "failed", "phase": "download",
            "message": f"Audio download error: {str(e)}",
            "video_id": video_id
        }, exit_code=1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
fetch_audio_youtube.py — Download audio from YouTube video using yt-dlp,
then convert to 16kHz mono WAV via ffmpeg.

Usage:
  python fetch_audio_youtube.py --video-id <VIDEO_ID> --output <output.wav> [--temp-dir DIR]

Output (JSON to stdout):
  On success: {"status": "success", "audio_file": "...", "duration_sec": N}
  On failure: {"status": "failed", "phase": "...", "message": "..."}

Phases: extract, download, ffmpeg
"""

import os, sys, json, re, subprocess, tempfile, shutil, time
from pathlib import Path


def extract_video_id(url_or_id: str) -> str | None:
    """Extract YouTube video ID from various URL formats or raw ID."""
    if re.match(r'^[A-Za-z0-9_-]{11}$', url_or_id):
        return url_or_id
    patterns = [
        r'(?:youtube\.com/watch\?.*v=)([A-Za-z0-9_-]{11})',
        r'(?:youtu\.be/)([A-Za-z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})',
        r'(?:youtube\.com/v/)([A-Za-z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url_or_id)
        if m:
            return m.group(1)
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Download YouTube audio')
    parser.add_argument('--video-id', required=True, help='YouTube video ID or URL')
    parser.add_argument('--output', required=True, help='Output .wav file path')
    parser.add_argument('--temp-dir', default=None, help='Temp directory for downloads')
    args = parser.parse_args()

    video_id = extract_video_id(args.video_id)
    if not video_id:
        print(json.dumps({
            "status": "failed", "phase": "extract",
            "message": f"Could not extract video ID from: {args.video_id}"
        }))
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = Path(args.temp_dir) if args.temp_dir else Path(tempfile.mkdtemp(prefix='yt_audio_'))
    temp_dir.mkdir(parents=True, exist_ok=True)

    temp_audio = temp_dir / f'{video_id}.m4a'

    try:
        import subprocess
        # Use yt-dlp as CLI subprocess for cleaner output control
        yt_dlp_cmd = [
            'yt-dlp', '--quiet', '--no-progress', '--no-warnings',
            '-f', 'bestaudio/best',
            '-o', str(temp_dir / '%(id)s.%(ext)s'),
            video_id
        ]
        dl_result = subprocess.run(
            yt_dlp_cmd, capture_output=True, text=True, timeout=300
        )
        if dl_result.returncode != 0:
            print(json.dumps({
                "status": "failed", "phase": "download",
                "message": f"yt-dlp download failed: {dl_result.stderr[:200]}"
            }))
            sys.exit(1)

        # Get video info via yt-dlp JSON output
        info_cmd = ['yt-dlp', '--quiet', '--no-warnings', '--dump-json', video_id]
        info_result = subprocess.run(
            info_cmd, capture_output=True, text=True, timeout=60
        )
        if info_result.returncode == 0:
            try:
                info = json.loads(info_result.stdout)
            except:
                info = {}
        else:
            info = {}
        duration = info.get('duration', 0)
        title = info.get('title', video_id)

        # Find the downloaded file (yt-dlp may use different extension)
        candidates = list(temp_dir.glob(f'{video_id}.*'))
        audio_src = None
        for c in candidates:
            if c.suffix.lower() in ('.m4a', '.webm', '.mp3', '.opus', '.aac', '.ogg'):
                audio_src = c
                break
        if not audio_src and candidates:
            audio_src = candidates[0]  # fallback to any file

        if not audio_src or not audio_src.exists():
            print(json.dumps({
                "status": "failed", "phase": "download",
                "message": f"Audio download produced no output file for {video_id}"
            }))
            sys.exit(1)

        # ── Step 2: Convert to 16kHz mono WAV via ffmpeg (if needed) ────
        # Use a temp conversion path to avoid "same as input" error
        conv_output = temp_dir / f'{video_id}_conv.wav'
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', str(audio_src),
            '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le',
            str(conv_output)
        ]

        result = subprocess.run(
            ffmpeg_cmd, capture_output=True, text=True, timeout=300
        )

        if result.returncode != 0:
            # Check if input is already the right format
            if result.returncode == 4294967274 and 'same as Input' in result.stderr:
                # Input file is already a 16kHz mono WAV — just copy/move it
                import shutil
                shutil.copy2(str(audio_src), str(output_path))
            else:
                print(json.dumps({
                    "status": "failed", "phase": "ffmpeg",
                    "message": f"ffmpeg conversion failed: {result.stderr[:200]}"
                }))
                sys.exit(1)
        else:
            # Move converted file to final output
            import shutil
            shutil.move(str(conv_output), str(output_path))

        if not output_path.exists():
            print(json.dumps({
                "status": "failed", "phase": "ffmpeg",
                "message": "ffmpeg created no output file"
            }))
            sys.exit(1)

        # Cleanup temp (only if auto-generated, not user-provided)
        if not args.temp_dir:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass

        print(json.dumps({
            "status": "success",
            "video_id": video_id,
            "audio_file": str(output_path),
            "duration_sec": duration,
            "title": title,
        }, ensure_ascii=False))

    except Exception as e:
        # Cleanup on error (only if auto-generated)
        if not args.temp_dir:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass
        print(json.dumps({
            "status": "failed", "phase": "download",
            "message": f"Audio download error: {str(e)}",
            "video_id": video_id
        }))
        sys.exit(1)


if __name__ == '__main__':
    main()

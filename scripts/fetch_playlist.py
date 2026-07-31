#!/usr/bin/env python3
"""
fetch_playlist.py — List video IDs from a YouTube playlist.

Uses yt-dlp with --flat-playlist (fast, metadata only, no downloads).
Requires cookies for private playlists (like "Watch Later" / WL).

Usage:
  python fetch_playlist.py --url <PLAYLIST_URL> [--max N] [--cookies PATH]

Output (JSON to stdout):
  success: {"status":"success","playlist_title":"...","videos":[{"id":"...","title":"...","index":N},...]}
  failed:  {"status":"failed","phase":"...","message":"..."}
"""

import subprocess
import sys
from pathlib import Path

# Shared utilities
sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_utils import emit_json


def extract_playlist_id(url: str) -> str | None:
    """Extract playlist ID from a URL (list= parameter or /playlist?list=)."""
    if not url:
        return None
    url = url.strip()
    if 'list=' in url:
        for part in url.split('&'):
            if part.startswith('list='):
                return part.split('=', 1)[1]
    if '/playlist?list=' in url:
        return url.split('list=', 1)[1].split('&')[0]
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='List YouTube playlist videos')
    parser.add_argument('--url', required=True, help='Playlist URL')
    parser.add_argument('--max', type=int, default=None,
                        help='Maximum number of videos to list')
    parser.add_argument('--cookies', default=None,
                        help='Path to cookies.txt (required for private playlists)')
    args = parser.parse_args()

    playlist_id = extract_playlist_id(args.url)
    if not playlist_id:
        emit_json({
            "status": "failed", "phase": "extract",
            "message": f"Could not extract playlist ID from: {args.url}"
        }, exit_code=1)

    # Build yt-dlp command
    cmd = [
        'yt-dlp', '--quiet', '--no-warnings', '--flat-playlist',
        '--print', '%(playlist_index)s\t%(id)s\t%(title)s',
    ]
    if args.cookies:
        cmd += ['--cookies', args.cookies]
    cmd.append(args.url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        err = result.stderr
        if 'Sign in to confirm' in err or 'cookies' in err.lower():
            emit_json({
                "status": "failed", "phase": "auth",
                "message": ("播放列表需要登录（可能是私密列表如 WL 稍后再看）。"
                            "请提供 cookies：--cookies cookies.txt 或从浏览器导出。"),
                "detail": err[:200],
                "playlist_id": playlist_id
            }, exit_code=1)
        emit_json({
            "status": "failed", "phase": "playlist_api",
            "message": f"yt-dlp 无法获取播放列表: {err[:200]}",
            "playlist_id": playlist_id
        }, exit_code=1)

    # Parse output lines: index \t id \t title
    videos = []
    for line in result.stdout.split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) >= 2:
            idx = parts[0]
            vid = parts[1]
            title = '\t'.join(parts[2:]) if len(parts) > 2 else ''
            videos.append({"index": idx, "id": vid, "title": title})
        if args.max and len(videos) >= args.max:
            break

    if not videos:
        emit_json({
            "status": "failed", "phase": "empty",
            "message": "播放列表为空或无法解析",
            "playlist_id": playlist_id
        }, exit_code=1)

    emit_json({
        "status": "success",
        "playlist_id": playlist_id,
        "playlist_title": f"playlist:{playlist_id}",
        "videos": videos,
        "count": len(videos),
    })


if __name__ == '__main__':
    main()

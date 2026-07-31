#!/usr/bin/env python3
"""
watch_channel.py — Watch a YouTube channel/playlist for new videos.

Checks the SQLite cache to find videos not yet processed, extracts their
subtitles, and outputs a Markdown report (cron-friendly: empty output
when nothing new).

Usage:
  python watch_channel.py --channel "@handle" --max 5
  python watch_channel.py --url "https://www.youtube.com/@channel/videos" --days 7
  python watch_channel.py --channel "@handle" --cookies cookies.txt --translate

Cron integration (Hermes):
  - Script output is the report; empty output = nothing new (silent).
  - Exit 0 with output → push report to Feishu/Telegram via Hermes cron.

Output (stdout):
  Markdown report of NEW videos (not in cache), or empty string.
  Also writes a machine-readable JSON to stderr-adjacent file when --json.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from cache import Cache


def list_channel_videos(url: str, max_videos: int, cookies: str | None) -> list:
    """List recent videos from a channel/playlist URL via yt-dlp."""
    cmd = [
        'yt-dlp', '--quiet', '--no-warnings', '--flat-playlist',
        '--print', '%(id)s\t%(title)s\t%(duration)s',
    ]
    if cookies:
        cmd += ['--cookies', cookies]
    if max_videos:
        cmd += ['--playlist-end', str(max_videos)]
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        sys.stderr.write(f'yt-dlp 失败: {result.stderr[:300]}\n')
        return []

    # Parse output lines: id \t title \t duration
    videos = []
    for line in result.stdout.split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) >= 2:
            vid, title = parts[0], parts[1]
            duration = parts[2] if len(parts) > 2 and parts[2] != 'NA' else 0
            videos.append({
                "id": vid,
                "title": title,
                "duration_sec": int(duration) if str(duration).isdigit() else 0,
            })
        if max_videos and len(videos) >= max_videos:
            break
    return videos


def get_video_duration(video_id: str) -> int:
    """Get video duration via yt-dlp (used when flat-playlist lacks it)."""
    try:
        r = subprocess.run(
            ['yt-dlp', '--quiet', '--no-warnings', '--skip-download',
             '--print', '%(duration)s', video_id],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    except Exception:
        pass
    return 0


def extract_subtitles(video_id: str, languages: str = 'zh-Hans,zh-Hant,en',
                      cookies: str | None = None) -> dict:
    """Extract subtitles for a video. Returns result dict or failure dict."""
    cmd = [sys.executable, str(SCRIPT_DIR / 'fetch_subtitle_youtube.py'),
           '--video-id', video_id, '--languages', languages]
    if cookies:
        cmd += ['--cookies', cookies]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return json.loads(r.stdout or '{"status": "failed"}')
    except Exception as e:
        return {"status": "failed", "message": str(e)}


def summarize(transcript: str, max_chars: int = 600) -> str:
    """First meaningful sentences as a crude summary."""
    lines = [ln.strip() for ln in transcript.split('\n') if ln.strip()]
    if not lines:
        return ''
    # Strip timestamps
    import re
    cleaned = [re.sub(r'^\[\d{2}:\d{2}\]\s*', '', ln) for ln in lines]
    text = ' '.join(cleaned)
    return text[:max_chars] + ('…' if len(text) > max_chars else '')


def main():
    parser = argparse.ArgumentParser(description='Watch channel for new videos')
    parser.add_argument('--channel', default=None,
                        help='Channel handle/URL (@handle, /channel/UCxxx)')
    parser.add_argument('--url', default=None,
                        help='Full URL (overrides --channel)')
    parser.add_argument('--max', type=int, default=5,
                        help='Check the N most recent videos (default: 5)')
    parser.add_argument('--cookies', default=None, help='cookies.txt path')
    parser.add_argument('--languages', default='zh-Hans,zh-Hant,en')
    parser.add_argument('--translate', action='store_true',
                        help='Translate subtitles (needs DEEPSEEK_API_KEY)')
    parser.add_argument('--json', action='store_true',
                        help='Also write machine-readable report to watch_report.json')
    args = parser.parse_args()

    url = args.url or args.channel
    if not url:
        print('需要 --channel 或 --url')
        sys.exit(1)
    if not url.startswith('http'):
        url = f'https://www.youtube.com/{url.lstrip("/")}'

    videos = list_channel_videos(url, args.max, args.cookies)
    if not videos:
        sys.stderr.write('无法获取视频列表（网络/风控？）\n')
        sys.exit(1)

    cache = Cache()
    new_videos = []
    for v in videos:
        vid = v['id']
        if not vid:
            continue
        cached = cache.get_subtitles(vid, args.languages, False)
        if cached:
            continue  # already processed
        new_videos.append(v)

    # Nothing new → empty stdout (cron silent mode)
    if not new_videos:
        cache.close()
        return

    # Process new videos (subtitle extraction only — fast path)
    report_lines = ['# 🔔 频道新视频', '', f'共 {len(new_videos)} 个新视频', '']
    results = []
    for v in new_videos:
        vid = v['id']
        sys.stderr.write(f'📥 提取 {vid} ({v["title"][:40]})...\n')
        sub = extract_subtitles(vid, args.languages, args.cookies)
        if sub.get('status') == 'success':
            transcript = sub.get('subtitles', '')
            duration = v['duration_sec'] or get_video_duration(vid)
            summary = summarize(transcript)
            report_lines.append(f'## 🎬 {v["title"]}')
            report_lines.append(f'- 链接: https://youtu.be/{vid}')
            report_lines.append(f'- 时长: {duration // 60} 分 {duration % 60} 秒')
            report_lines.append(f'- 字幕: {len(transcript)} 字符')
            if summary:
                report_lines.append(f'- 摘要: {summary}')
            report_lines.append('')
            results.append({
                "id": vid, "title": v["title"], "status": "success",
                "char_count": len(transcript), "duration_sec": duration,
            })
            # Cache the result so next run skips this video
            cache.set_subtitles(vid, args.languages, False, sub)
        else:
            report_lines.append(f'## ⚠️ {v["title"]}')
            report_lines.append(f'- 链接: https://youtu.be/{vid}')
            report_lines.append(f'- 状态: 字幕提取失败（{sub.get("message", "未知")[:100]}）')
            report_lines.append('')
            results.append({
                "id": vid, "title": v["title"], "status": "failed",
                "message": sub.get("message", "unknown")[:200],
            })

    cache.close()

    report = '\n'.join(report_lines).strip()
    if args.json:
        with open(SCRIPT_DIR / 'watch_report.json', 'w', encoding='utf-8') as f:
            json.dump({
                "generated_at": datetime.now().isoformat(timespec='seconds'),
                "new_count": len(results),
                "videos": results,
            }, f, ensure_ascii=False, indent=2)

    print(report)


if __name__ == '__main__':
    main()

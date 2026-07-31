#!/usr/bin/env python3
"""
pipeline.py — One-command automated knowledge-base pipeline.

Orchestrates the full workflow:
  watch (discover new videos on channels) → extract (captions/transcripts)
  → enhance (chapters / translation) → archive (structured notes)
  → reindex (rebuild search index) → report (Markdown summary)

This is the cron-friendly entry point: empty stdout when nothing new.

Usage:
  python pipeline.py --config watch_config.json --archive "C:/Obsidian/视频笔记"
  python pipeline.py --config watch_config.json --archive DIR --chapters --translate
  python pipeline.py --config watch_config.json --archive DIR --reindex

Flags passed to analyze: --chapters, --translate, --timestamps (see below).
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ANALYZE_PY = SCRIPT_DIR / 'analyze_youtube.py'
SEARCH_PY = SCRIPT_DIR / 'search.py'
DEFAULT_CONFIG = SCRIPT_DIR.parent / 'watch_config.json'
DEFAULT_OUTPUT = SCRIPT_DIR.parent / 'output'


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def run(cmd, timeout=1800):
    """Run a subprocess, return (stdout, returncode)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        return '', 124


def extract_json(stdout: str) -> dict:
    """Pull the JSON payload out of analyze output (progress on stderr, JSON on stdout)."""
    idx = stdout.find('{')
    if idx < 0:
        return {}
    try:
        return json.loads(stdout[idx:])
    except json.JSONDecodeError:
        return {}


def list_new_videos(channel_cfg: dict, languages: str) -> tuple:
    """
    List videos for a channel, filtering out ones already in the subtitle cache.

    Returns (videos, cache) — cache object for the caller to close.
    """
    from cache import Cache
    from watch_channel import list_channel_videos

    ch_url = channel_cfg.get('url', '')
    ch_max = channel_cfg.get('max', 5)
    if not ch_url.startswith('http'):
        ch_url = f'https://www.youtube.com/{ch_url.lstrip("/")}'

    videos = list_channel_videos(ch_url, ch_max, None)
    cache = Cache()
    new_videos = []
    for v in videos:
        vid = v.get('id', '')
        if not vid:
            continue
        if cache.get_subtitles(vid, languages, False):
            continue
        new_videos.append(v)
    return new_videos, cache


def process_video(video: dict, args, languages: str) -> dict:
    """Extract + enhance + archive a single video via analyze_youtube.py."""
    vid = video['id']
    analyze_args = [sys.executable, str(ANALYZE_PY), vid]
    if args.chapters:
        analyze_args.append('--chapters')
    if args.translate:
        analyze_args.append('--translate')
    if args.archive:
        analyze_args += ['--archive', args.archive]

    start = time.time()
    stdout, rc = run(analyze_args, timeout=1800)
    data = extract_json(stdout)
    elapsed = round(time.time() - start, 1)

    result = {
        "id": vid,
        "title": video.get('title', vid),
        "status": data.get('status', 'failed'),
        "elapsed_sec": elapsed,
    }
    if data.get('status') == 'success':
        result['source'] = data.get('source', 'caption')
        result['char_count'] = data.get('char_count', 0)
        result['archive_file'] = data.get('archive_file')
        result['transcript_file'] = data.get('transcript_file')
        if data.get('chapters'):
            result['chapters_count'] = len(data['chapters'])
        if data.get('translated_file'):
            result['translated_file'] = data['translated_file']
    else:
        result['message'] = data.get('message', f'exit={rc}')[:200]
    return result


def reindex(archive_dir: str | None, output_dir: Path) -> dict:
    """Rebuild the search index over output/ + archive dir."""
    cmd = [sys.executable, str(SEARCH_PY), '--index', '--path', str(output_dir)]
    if archive_dir:
        cmd += ['--path', archive_dir]
    stdout, rc = run(cmd, timeout=600)
    try:
        data = json.loads(stdout.strip().split('\n')[-1])
        return {"status": "ok", **data}
    except (json.JSONDecodeError, IndexError):
        return {"status": "failed", "message": stdout[:200]}


def main():
    parser = argparse.ArgumentParser(description='Automated knowledge-base pipeline')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG),
                        help='Multi-channel JSON config (default: watch_config.json)')
    parser.add_argument('--archive', default=None,
                        help='Archive structured notes to this directory')
    parser.add_argument('--chapters', action='store_true',
                        help='Auto-detect chapters for each video')
    parser.add_argument('--translate', action='store_true',
                        help='Translate subtitles (needs DEEPSEEK_API_KEY)')
    parser.add_argument('--reindex', action='store_true',
                        help='Rebuild the search index after processing')
    parser.add_argument('--vector', action='store_true',
                        help='With --reindex: also build vector embeddings '
                             '(needs: pip install fastembed)')
    parser.add_argument('--languages', default='zh-Hans,zh-Hant,en')
    args = parser.parse_args()

    # Load .env (API keys)
    try:
        from youtube_utils import load_env
        load_env()
    except ImportError:
        pass

    # Load config
    try:
        with open(args.config, encoding='utf-8') as f:
            config = json.load(f)
        channels = config.get('channels', [])
    except (OSError, json.JSONDecodeError) as e:
        eprint(f'❌ 配置加载失败: {e}')
        sys.exit(1)
    if not channels:
        eprint('❌ 配置中没有频道')
        sys.exit(1)

    all_results = []
    processed = 0
    for ch in channels:
        ch_name = ch.get('name', ch.get('url', ''))
        eprint(f'📡 [{ch_name}] 检查新视频...')
        new_videos, cache = list_new_videos(ch, args.languages)
        cache.close()
        if not new_videos:
            eprint('  ⏭ 无新视频')
            continue
        eprint(f'  🔎 {len(new_videos)} 个新视频')
        for v in new_videos:
            eprint(f'  📥 处理 {v["id"]} ({v.get("title", "")[:40]})...')
            result = process_video(v, args, args.languages)
            result['channel'] = ch_name
            all_results.append(result)
            processed += 1
            if result['status'] == 'success':
                eprint(f'    ✅ {result.get("char_count", 0)} 字符'
                       f' ({result["elapsed_sec"]}s)')
            else:
                eprint(f'    ⚠ {result.get("message", "失败")}')

    # Nothing new → silent (cron)
    if not all_results:
        return

    # Rebuild search index
    index_info = None
    if args.reindex:
        eprint('🔎 重建搜索索引...')
        index_info = reindex(args.archive, DEFAULT_OUTPUT)
        eprint(f'  ✅ {index_info.get("segments", 0)} 段索引')
        if args.vector:
            eprint('🧠 构建向量索引（可能需要几分钟）...')
            from search import build_vector_index, index_path
            vec_result = build_vector_index(index_path(), verbose=True)
            if vec_result.get('status') == 'success':
                index_info['vector_segments'] = vec_result.get('segments', 0)
                eprint(f'  ✅ 向量索引: {vec_result.get("segments", 0)} 段')
            else:
                eprint(f'  ⚠ 向量索引失败: {vec_result.get("message", "")}')

    # Report
    success = sum(1 for r in all_results if r['status'] == 'success')
    lines = ['# 🏭 知识库流水线', '',
             f'处理 {len(all_results)} 个视频（成功 {success}），'
             f'来自 {len(channels)} 个频道', '']
    for r in all_results:
        status_icon = '✅' if r['status'] == 'success' else '⚠️'
        lines.append(f'## {status_icon} {r["title"]}')
        lines.append(f'- 频道: {r.get("channel", "")}')
        lines.append(f'- 链接: https://youtu.be/{r["id"]}')
        if r['status'] == 'success':
            lines.append(f'- 来源: {r.get("source", "")} · {r.get("char_count", 0)} 字符')
            if r.get('chapters_count'):
                lines.append(f'- 📑 {r["chapters_count"]} 章')
            if r.get('translated_file'):
                lines.append('- 🌐 已翻译')
            if r.get('archive_file'):
                lines.append(f'- 📚 笔记: {Path(r["archive_file"]).name}')
        else:
            lines.append(f'- 状态: {r.get("message", "未知")}')
        lines.append('')
    if index_info:
        lines.append(f'🔎 搜索索引: {index_info.get("files", 0)} 文件 / '
                     f'{index_info.get("segments", 0)} 段')
        lines.append('')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()

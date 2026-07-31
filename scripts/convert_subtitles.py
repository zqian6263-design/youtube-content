#!/usr/bin/env python3
"""
convert_subtitles.py — Convert subtitle segments to standard formats.

Input: JSON list of segments on stdin, or --segments-file.
Each segment: {"start": float (seconds), "duration": float, "text": str}

Formats:
  srt — SubRip (players, editors: 剪映/Premiere/ffmpeg)
  vtt — WebVTT (browsers, YouTube-style)
  lrc — LRC lyrics (music players)
  txt — Plain text with [MM:SS] timestamps

Usage:
  python convert_subtitles.py --format srt < segments.json
  python convert_subtitles.py --format srt --segments-file segments.json

Output (JSON to stdout):
  {"status":"success","format":"srt","content":"...","segment_count":N}
"""

import argparse
import json
import sys


def format_srt_time(seconds: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm"""
    seconds = max(0.0, seconds)
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def format_vtt_time(seconds: float) -> str:
    """WebVTT timestamp: HH:MM:SS.mmm"""
    seconds = max(0.0, seconds)
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


def format_lrc_time(seconds: float) -> str:
    """LRC timestamp: [MM:SS.xx]"""
    seconds = max(0.0, seconds)
    cs = int(round((seconds - int(seconds)) * 100))
    total = int(seconds)
    m, s = divmod(total, 60)
    return f'[{m:02d}:{s:02d}.{cs:02d}]'


def to_srt(segments: list) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        text = seg.get('text', '').strip()
        if not text:
            continue
        start = seg.get('start', 0.0)
        duration = seg.get('duration', 2.0)
        end = start + max(0.1, duration)
        lines.append(f'{i}')
        lines.append(f'{format_srt_time(start)} --> {format_srt_time(end)}')
        lines.append(text)
        lines.append('')
    return '\n'.join(lines)


def to_vtt(segments: list) -> str:
    lines = ['WEBVTT', '']
    for seg in segments:
        text = seg.get('text', '').strip()
        if not text:
            continue
        start = seg.get('start', 0.0)
        duration = seg.get('duration', 2.0)
        end = start + max(0.1, duration)
        lines.append(f'{format_vtt_time(start)} --> {format_vtt_time(end)}')
        lines.append(text)
        lines.append('')
    return '\n'.join(lines)


def to_lrc(segments: list) -> str:
    lines = []
    for seg in segments:
        text = seg.get('text', '').strip()
        if not text:
            continue
        start = seg.get('start', 0.0)
        lines.append(f'{format_lrc_time(start)}{text}')
    return '\n'.join(lines)


def to_txt(segments: list) -> str:
    lines = []
    for seg in segments:
        text = seg.get('text', '').strip()
        if not text:
            continue
        start = seg.get('start', 0.0)
        m, s = divmod(max(0, int(start)), 60)
        lines.append(f'[{m:02d}:{s:02d}] {text}')
    return '\n'.join(lines)


CONVERTERS = {
    'srt': to_srt,
    'vtt': to_vtt,
    'lrc': to_lrc,
    'txt': to_txt,
}


def convert_segments(segments: list, fmt: str) -> str:
    """Convert segments to the requested format."""
    converter = CONVERTERS.get(fmt.lower())
    if converter is None:
        raise ValueError(f'Unsupported format: {fmt} (choose from {", ".join(CONVERTERS)})')
    return converter(segments)


def main():
    parser = argparse.ArgumentParser(description='Convert subtitle segments')
    parser.add_argument('--format', required=True, choices=list(CONVERTERS),
                        help='Output format')
    parser.add_argument('--segments-file', default=None,
                        help='JSON file with segments list (default: read stdin)')
    args = parser.parse_args()

    if args.segments_file:
        try:
            with open(args.segments_file, encoding='utf-8') as f:
                segments = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(json.dumps({"status": "failed", "message": f"无法读取 segments: {e}"}))
            sys.exit(1)
    else:
        try:
            segments = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "failed", "message": f"stdin 不是有效 JSON: {e}"}))
            sys.exit(1)

    if not isinstance(segments, list):
        print(json.dumps({"status": "failed", "message": "segments 必须是列表"}))
        sys.exit(1)

    try:
        content = convert_segments(segments, args.format)
    except ValueError as e:
        print(json.dumps({"status": "failed", "message": str(e)}))
        sys.exit(1)

    print(json.dumps({
        "status": "success",
        "format": args.format,
        "content": content,
        "segment_count": len(segments),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()

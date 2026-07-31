#!/usr/bin/env python3
"""
chapters.py — Automatic chapter detection from timestamped subtitles.

Implements a TextTiling-style algorithm:
  1. Split subtitle lines into fixed-size time windows
  2. Compute content similarity between adjacent windows (token Jaccard)
  3. Local minima of similarity = topic boundary candidates
  4. Generate a chapter title from each segment's most frequent keywords

Usage (standalone):
  python chapters.py --input subtitles.txt [--window-sec 60] [--min-chapters 3]
      [--max-chapters 20] [--top-words 4]

Output (JSON):
  {"status":"success","chapters":[{"start":0.0,"title":"...","start_ts":"00:00"},...]}

Input formats accepted:
  - "[MM:SS] text" lines (as produced by --timestamps)
  - Plain text lines without timestamps (chapters then have no timestamps;
    boundaries are still detected by content shifts)
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_utils import emit_json

TS_RE = re.compile(r'^\[(\d{1,2}):(\d{2})\]\s*(.*)$')
WORD_RE = re.compile(r'[a-zA-Z\u4e00-\u9fff][a-zA-Z0-9\u4e00-\u9fff]*')

# Stopwords for title/keyword generation (EN + ZH, technical talk bias)
STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'if', 'then', 'so', 'we', 'you',
    'they', 'this', 'that', 'these', 'those', 'it', 'is', 'are', 'was',
    'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does',
    'did', 'will', 'would', 'can', 'could', 'should', 'may', 'might',
    'of', 'in', 'on', 'at', 'to', 'for', 'from', 'with', 'by', 'about',
    'as', 'into', 'like', 'just', 'also', 'very', 'really', 'get', 'got',
    'one', 'two', 'go', 'going', 'know', 'think', 'say', 'said', 'thing',
    'things', 'way', 'make', 'made', 'take', 'took', 'see', 'want', 'let',
    'us', 'okay', 'right', 'well', 'now', 'good', 'great', 'actually',
    'basically', 'sort', 'kind', 'something', 'everything', 'everyone',
    'someone', 'people', 'thing', 'stuff', 'etc', 'e.g', 'ie', 'vs',
    'um', 'uh', 'hmm', 'oh', 'ah', 'yeah', 'yes', 'no', 'ok', 'okay',
    'again', 'put', 'different', 'equal', 'ever', 'much', 'out', 've',
    'don', 'didn', 'doesn', 'can', 'won', 'll', 're', 's', 't', 'm',
    'get', 'got', 'go', 'going', 'know', 'think', 'say', 'said', 'thing',
    'things', 'way', 'make', 'made', 'take', 'took', 'see', 'want', 'let',
    'us', 'right', 'well', 'now', 'good', 'great', 'actually', 'basically',
    'sort', 'kind', 'something', 'everything', 'everyone', 'someone',
    'people', 'stuff', 'etc', 'e.g', 'ie', 'vs', 'lot', 'lots', 'really',
    'still', 'even', 'maybe', 'perhaps', 'probably', 'usually', 'always',
    'never', 'sometimes', 'little', 'big', 'new', 'old', 'first', 'last',
    'next', 'other', 'another', 'same', 'more', 'most', 'less', 'least',
    'many', 'much', 'few', 'some', 'any', 'all', 'each', 'every',
    '的', '了', '是', '在', '我', '你', '他', '她', '它', '们', '这', '那',
    '就', '都', '也', '很', '有', '和', '与', '及', '一个', '我们', '你们',
    '他们', '这个', '那个', '什么', '怎么', '为什么', '可以', '可能', '应该',
    '然后', '所以', '但是', '因为', '如果', '就是', '不是', '没有',
}


def parse_subtitles(text: str) -> list:
    """
    Parse subtitle text into (start_sec, text) entries.

    Accepts "[MM:SS] text" lines (timestamps) or plain lines (start=None).
    Very long lines (e.g. whisper transcripts dumped as one paragraph) are
    split at sentence boundaries so chapter detection has enough units.

    Returns list of dicts: {"start": float|None, "text": str}
    """
    entries = []
    SENT_SPLIT = re.compile(r'(?<=[.!?。！？])\s+')

    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue
        m = TS_RE.match(line)
        if m:
            minutes, seconds, content = m.groups()
            start = int(minutes) * 60 + int(seconds)
            entries.append({"start": float(start), "text": content.strip()})
        else:
            if len(line) > 800:
                # Long paragraph → split into sentences
                for sent in SENT_SPLIT.split(line):
                    sent = sent.strip()
                    if sent:
                        entries.append({"start": None, "text": sent})
            else:
                entries.append({"start": None, "text": line})
    return entries


def tokenize(text: str) -> set:
    """Lowercased token set for similarity (filters stopwords)."""
    tokens = [t.lower() for t in WORD_RE.findall(text)]
    return {t for t in tokens if t not in STOPWORDS and len(t) > 1}


def jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def build_windows(entries: list, window_sec: float = 60.0) -> list:
    """
    Group entries into fixed-size time windows.

    Returns list of {"start": float, "end": float, "tokens": set}
    For entries without timestamps, falls back to fixed line-count windows.
    """
    if not entries:
        return []

    has_ts = any(e["start"] is not None for e in entries)

    if not has_ts:
        # No timestamps: window = fixed number of lines (heuristic)
        per_window = max(10, len(entries) // 30)
        windows = []
        for i in range(0, len(entries), per_window):
            chunk = entries[i:i + per_window]
            tokens = set()
            for e in chunk:
                tokens |= tokenize(e["text"])
            windows.append({
                "start": float(i),
                "end": float(i + len(chunk)),
                "tokens": tokens,
                "row_mode": True,  # start is a line index, not seconds
            })
        return windows

    # Timestamp-based windows
    windows = []
    current_start = entries[0]["start"]
    current_end = current_start + window_sec
    current_tokens = set()

    for e in entries:
        start = e["start"]
        if start is not None and start >= current_end:
            windows.append({
                "start": current_start,
                "end": current_end,
                "tokens": current_tokens,
            })
            current_start = start
            current_end = start + window_sec
            current_tokens = set()
        current_tokens |= tokenize(e["text"])

    if current_tokens:
        windows.append({
            "start": current_start,
            "end": current_end,
            "tokens": current_tokens,
        })

    return windows


def detect_boundaries(windows: list, min_chapters: int = 3,
                      max_chapters: int = 20) -> list:
    """
    Find topic boundaries = local minima of similarity between adjacent windows.

    Returns list of boundary indices (index into `windows` where a new
    chapter starts). Ensures boundaries are spaced out (min gap) and the
    count is within [min_chapters, max_chapters].
    """
    n = len(windows)
    if n < 2:
        return []

    # Similarity between adjacent windows
    sims = [jaccard(windows[i]["tokens"], windows[i + 1]["tokens"])
            for i in range(n - 1)]

    # Local minima (lower than both neighbors); smooth with small window
    boundaries = []
    for i in range(1, len(sims) - 1):
        if sims[i] < sims[i - 1] and sims[i] <= sims[i + 1]:
            boundaries.append(i + 1)  # boundary → new chapter at window i+1

    # Fallback: if no local minima, take the lowest similarity points
    if not boundaries and len(sims) >= 2:
        ranked = sorted(range(len(sims)), key=lambda i: sims[i])
        boundaries = sorted(ranked[: min(5, len(ranked))])
        boundaries = [b + 1 for b in boundaries]

    # Enforce minimum spacing (at least ~3 windows apart)
    spaced = []
    for b in boundaries:
        if not spaced or b - spaced[-1] >= 3:
            spaced.append(b)
    boundaries = spaced

    # Enforce chapter count limits
    if len(boundaries) + 1 > max_chapters:
        # Keep the strongest boundaries (highest drop)
        drops = {}
        for b in boundaries:
            if 0 <= b - 1 < len(sims):
                drops[b] = 1.0 - sims[b - 1]
        keep = sorted(drops, key=drops.get, reverse=True)[:max_chapters - 1]
        boundaries = sorted(keep)

    if len(boundaries) + 1 < min_chapters and boundaries:
        # Too few: relax by dropping spacing requirement, keep top (min_chapters-1)
        if len(boundaries) + 1 < min_chapters and len(sims) >= min_chapters:
            ranked = sorted(range(len(sims)), key=lambda i: sims[i])
            boundaries = sorted(ranked[: min_chapters - 1])
            boundaries = [b + 1 for b in boundaries]

    return boundaries


def generate_title(windows, start_idx: int, end_idx: int, top_words: int = 4) -> str:
    """Generate a chapter title from the segment's most frequent keywords."""
    counter = Counter()
    for w in windows[start_idx:end_idx]:
        for t in w["tokens"]:
            counter[t] += 1

    if not counter:
        return f"Chapter {start_idx + 1}"

    words = [w for w, _ in counter.most_common(top_words)]
    title = ' / '.join(words)
    return title[:80]


def format_ts(seconds: float) -> str:
    """Format seconds as MM:SS."""
    seconds = max(0, int(seconds))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def detect_chapters(entries: list, window_sec: float = 60.0,
                    min_chapters: int = 3, max_chapters: int = 20,
                    top_words: int = 4) -> list:
    """
    Full pipeline: windows → boundaries → chapter list.

    Returns list of {"start": float, "start_ts": str, "title": str}
    """
    windows = build_windows(entries, window_sec)
    boundaries = detect_boundaries(windows, min_chapters, max_chapters)

    # Row mode (no timestamps): show line index instead of MM:SS
    row_mode = bool(windows) and windows[0].get('row_mode', False)

    # Chapter boundaries: [0] + boundaries (+ implicit end)
    bounds = [0] + boundaries
    chapters = []
    for i, b in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else len(windows)
        title = generate_title(windows, b, end, top_words)
        start = windows[b]["start"]
        if row_mode:
            start_ts = f'L{int(start)}'
        else:
            start_ts = format_ts(start)
        chapters.append({
            "start": round(start, 1),
            "start_ts": start_ts,
            "title": title,
        })
    return chapters


def main():
    parser = argparse.ArgumentParser(description='Detect chapters from subtitles')
    parser.add_argument('--input', required=True, help='Subtitle text file')
    parser.add_argument('--window-sec', type=float, default=60.0,
                        help='Similarity window size in seconds (default: 60)')
    parser.add_argument('--min-chapters', type=int, default=3)
    parser.add_argument('--max-chapters', type=int, default=20)
    parser.add_argument('--top-words', type=int, default=4,
                        help='Keywords per chapter title')
    args = parser.parse_args()

    try:
        with open(args.input, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError as e:
        emit_json({"status": "failed", "message": f"无法读取文件: {e}"}, exit_code=1)

    entries = parse_subtitles(text)
    if not entries:
        emit_json({"status": "failed", "message": "字幕为空"}, exit_code=1)

    chapters = detect_chapters(
        entries, window_sec=args.window_sec,
        min_chapters=args.min_chapters, max_chapters=args.max_chapters,
        top_words=args.top_words,
    )

    emit_json({
        "status": "success",
        "chapters": chapters,
        "chapter_count": len(chapters),
    })


if __name__ == '__main__':
    main()

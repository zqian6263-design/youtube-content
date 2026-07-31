#!/usr/bin/env python3
"""
search.py — Full-text search over extracted subtitles/transcripts.

Builds a SQLite FTS5 index over the output/ directory (or custom paths),
with a bigram tokenizer so Chinese text is searchable. Queries return
matching segments with timestamps and surrounding context.

Usage:
  # Build / update the index
  python search.py --index [--path DIR] [--path FILE ...]

  # Search
  python search.py --query "A* search heuristic"
  python search.py --query "反事实" --context 3 --limit 10
  python search.py --query "neural network" --file "CS50*" --json

Index location: <script_dir>/search_index.db (or $YOUTUBE_SEARCH_INDEX env)

Output (stdout, plain or --json):
  Matches with: file, timestamp, score, text (with context)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INDEX = SCRIPT_DIR.parent / 'search_index.db'
DEFAULT_SOURCES = SCRIPT_DIR.parent / 'output'

TS_RE = re.compile(r'^\[(\d{2}):(\d{2})\]\s*(.*)$')
LONG_LINE_CUT = 800


def index_path() -> Path:
    env = os.environ.get('YOUTUBE_SEARCH_INDEX')
    return Path(env) if env else DEFAULT_INDEX


def _connect(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def _bigram_tokenize(text: str) -> str:
    """
    Tokenize text for FTS: ASCII words as-is, CJK split into bigrams.
    'neural网络' → 'neural 网络 网络' (bigram pairs joined by spaces)
    """
    tokens = []
    # ASCII word runs
    for m in re.finditer(r'[A-Za-z0-9_]+', text):
        tokens.append(m.group(0).lower())
    # CJK runs → bigrams
    for m in re.finditer(r'[\u4e00-\u9fff]+', text):
        run = m.group(0)
        if len(run) == 1:
            tokens.append(run)
        else:
            for i in range(len(run) - 1):
                tokens.append(run[i:i + 2])
    return ' '.join(tokens)


def _extract_segments(path: Path, file_id: str) -> list:
    """Extract (file_id, file_path, start_sec, text) from a subtitle file."""
    segments = []
    try:
        content = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return segments

    for raw in content.split('\n'):
        line = raw.strip()
        if not line:
            continue
        m = TS_RE.match(line)
        if m:
            mm, ss, text = m.groups()
            start = int(mm) * 60 + int(ss)
        else:
            start = None
            text = line
        if not text.strip():
            continue
        # Very long whisper lines → split into sentences
        if len(text) > LONG_LINE_CUT:
            for sent in re.split(r'(?<=[.!?。！？])\s+', text):
                sent = sent.strip()
                if sent:
                    segments.append((file_id, str(path), start, sent))
        else:
            segments.append((file_id, str(path), start, text))
    return segments


def build_index(paths=None, db_path=None, verbose=True):
    """Scan paths (dirs or files) and rebuild the FTS index."""
    db_path = Path(db_path) if db_path else index_path()
    sources = paths or [DEFAULT_SOURCES]
    if isinstance(sources, (str, Path)):
        sources = [sources]

    # Collect files
    files = []
    SUPPORTED = ('.txt', '.srt', '.vtt', '.md')
    for src in sources:
        p = Path(src)
        if p.is_dir():
            for ext in SUPPORTED:
                files.extend(sorted(p.glob(f'*{ext}')))
        elif p.is_file() and p.suffix in SUPPORTED:
            files.append(p)
        elif p.is_file():
            if verbose:
                print(f'⚠ 跳过非字幕文件: {p.name}', file=sys.stderr)

    if verbose:
        print(f'📇 扫描到 {len(files)} 个字幕文件', file=sys.stderr)

    # Rebuild index
    conn = _connect(db_path)
    conn.execute('DROP TABLE IF EXISTS subtitles_fts')
    conn.execute('''
        CREATE VIRTUAL TABLE subtitles_fts USING fts5(
            file_id, path, start, text,
            tokenize = 'unicode61'
        )
    ''')
    conn.execute('CREATE TABLE IF NOT EXISTS subtitles_meta (key TEXT PRIMARY KEY, value TEXT)')

    count = 0
    for fi, path in enumerate(files, 1):
        file_id = f'f{fi}'
        for seg in _extract_segments(path, file_id):
            f_id, f_path, start, text = seg
            conn.execute(
                'INSERT INTO subtitles_fts (file_id, path, start, text) VALUES (?,?,?,?)',
                (f_id, f_path, start if start is not None else -1, text)
            )
            count += 1
        if verbose:
            print(f'  [{fi}/{len(files)}] {path.name} ({count} 段)', file=sys.stderr)

    conn.execute(
        'INSERT OR REPLACE INTO subtitles_meta (key, value) VALUES (?,?)',
        ('file_count', str(len(files)))
    )
    conn.execute(
        'INSERT OR REPLACE INTO subtitles_meta (key, value) VALUES (?,?)',
        ('segment_count', str(count))
    )
    conn.commit()
    conn.close()
    if verbose:
        print(f'✅ 索引完成: {len(files)} 文件 / {count} 段 → {db_path}', file=sys.stderr)
    return {"status": "success", "files": len(files), "segments": count}


def _search_ascii(conn, query: str, limit: int):
    """FTS5 MATCH for ASCII queries; returns rows."""
    try:
        q = ' '.join(query.split())
        rows = conn.execute(
            '''SELECT path, start, text, bm25(subtitles_fts) AS score
               FROM subtitles_fts WHERE subtitles_fts MATCH ? ORDER BY score
               LIMIT ?''', (q, limit)
        ).fetchall()
        return rows
    except sqlite3.OperationalError:
        return []


def _search_cjk(conn, query: str, limit: int, context: int):
    """LIKE-based fallback for CJK queries (FTS unicode61 can't match bigrams)."""
    terms = [t for t in _bigram_tokenize(query).split() if len(t) >= 2]
    # Also include the raw query for single CJK chars
    raw_cjk = ''.join(re.findall(r'[\u4e00-\u9fff]+', query))
    if len(raw_cjk) == 1:
        terms.append(raw_cjk)

    rows = []
    for term in terms:
        hits = conn.execute(
            'SELECT path, start, text FROM subtitles_fts WHERE text LIKE ? LIMIT ?',
            (f'%{term}%', limit)
        ).fetchall()
        rows.extend(hits)
    return rows


def search(query: str, limit: int = 10, context: int = 0,
           file_filter: str | None = None, db_path=None, verbose=True):
    """Search the index. Returns list of match dicts."""
    db_path = Path(db_path) if db_path else index_path()
    if not db_path.exists():
        return {"status": "failed", "message": f"索引不存在: {db_path}，请先运行 --index"}

    conn = _connect(db_path)
    try:
        has_cjk = bool(re.search(r'[\u4e00-\u9fff]', query))

        if has_cjk:
            raw = _search_cjk(conn, query, limit, context)
            results = []
            seen = set()
            for path, start, text in raw:
                key = (path, start, text)
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "path": path,
                    "start": start if start >= 0 else None,
                    "start_ts": f'{start // 60:02d}:{start % 60:02d}' if start >= 0 else '',
                    "text": text,
                    "score": 0,
                })
        else:
            raw = _search_ascii(conn, query, limit)
            results = []
            for path, start, text, score in raw:
                results.append({
                    "path": path,
                    "start": start if start >= 0 else None,
                    "start_ts": f'{start // 60:02d}:{start % 60:02d}' if start >= 0 else '',
                    "text": text,
                    "score": round(-score, 2),
                })
    finally:
        conn.close()

    # File filter
    if file_filter:
        results = [r for r in results if file_filter.lower() in Path(r['path']).name.lower()]

    # Context: fetch neighbors from the same file around the match
    if context > 0 and results:
        results = _add_context(results, context, db_path)

    return {"status": "success", "query": query, "count": len(results),
            "matches": results[:limit]}


def _add_context(results, context, db_path):
    """Attach surrounding lines from the same file (best-effort)."""
    conn = _connect(db_path)
    try:
        for r in results:
            path = r['path']
            start = r['start']
            if start is None:
                continue
            neighbors = conn.execute(
                '''SELECT start, text FROM subtitles_fts
                   WHERE path = ? AND start >= ? AND start <= ? + 120
                   ORDER BY start LIMIT ?''',
                (path, max(0, start - 120), start, context * 2 + 1)
            ).fetchall()
            ctx_lines = []
            for s, t in neighbors:
                ctx_lines.append(
                    f'[{s // 60:02d}:{s % 60:02d}] {t}' if s >= 0 else t)
            r['context'] = '\n'.join(ctx_lines)
    finally:
        conn.close()
    return results


def format_results(result: dict) -> str:
    """Human-readable output for a search result."""
    if result.get('status') != 'success':
        return f"❌ {result.get('message', '搜索失败')}"
    if not result.get('matches'):
        return f"🔍 未找到匹配: {result.get('query', '')}"

    out = [f"🔍 找到 {result['count']} 个匹配: {result['query']}", '']
    for m in result['matches']:
        name = Path(m['path']).name
        out.append(f"📄 {name}  @ {m['start_ts']}")
        out.append(f"   {m['text'][:200]}")
        if m.get('context'):
            out.append("   ── 上下文 ──")
            for line in m['context'].split('\n'):
                out.append(f"   {line[:120]}")
        out.append('')
    return '\n'.join(out)


def main():
    parser = argparse.ArgumentParser(description='Search subtitles')
    parser.add_argument('--index', action='store_true', help='Build/rebuild index')
    parser.add_argument('--path', action='append', default=None,
                        help='Index source dir/file (repeatable; default: output/)')
    parser.add_argument('--query', default=None, help='Search query')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--context', type=int, default=0,
                        help='Show N surrounding lines')
    parser.add_argument('--file', default=None, help='Filter by filename substring')
    parser.add_argument('--json', action='store_true', help='JSON output')
    args = parser.parse_args()

    if args.index:
        result = build_index(paths=args.path, verbose=True)
        print(json.dumps(result, ensure_ascii=False))
        return

    if not args.query:
        parser.print_help()
        sys.exit(1)

    result = search(args.query, limit=args.limit, context=args.context,
                    file_filter=args.file)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_results(result))


if __name__ == '__main__':
    main()

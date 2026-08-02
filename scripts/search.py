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
    """FTS5 MATCH for ASCII queries; returns rows.

    Multi-term queries use OR semantics (AND is too strict for short
    subtitle segments); BM25 ranking keeps multi-hit rows on top.
    """
    try:
        terms = [t.replace('"', '') for t in query.split() if t.strip()]
        if not terms:
            return []
        if len(terms) == 1:
            q = f'"{terms[0]}"'
        else:
            q = ' OR '.join(f'"{t}"' for t in terms)
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


# ── Vector (semantic) search ────────────────────────────────────────────

_embed_model = None
VEC_MODEL_NAME = 'BAAI/bge-small-zh-v1.5'


def _get_embedder():
    """Lazy-load the embedding model (cached across calls)."""
    global _embed_model
    if _embed_model is None:
        try:
            from fastembed import TextEmbedding
            _embed_model = TextEmbedding(model_name=VEC_MODEL_NAME)
        except ImportError:
            raise RuntimeError('需要 fastembed：pip install fastembed')
    return _embed_model


def _embed_batch(texts: list) -> list:
    """Embed a batch of texts → list of float32 numpy arrays."""
    model = _get_embedder()
    return list(model.embed(texts, batch_size=64))


def build_vector_index(db_path: Path, sources=None, verbose=True):
    """
    Build vector embeddings for all segments (reuses the FTS text rows).

    Stores into table segments_vec (id, path, start, text, embedding BLOB).
    Returns {"status", "segments"}.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            'SELECT rowid, path, start, text FROM subtitles_fts').fetchall()
    except sqlite3.OperationalError as e:
        conn.close()
        return {"status": "failed", "message": f"索引不存在，先运行 --index: {e}"}

    if not rows:
        conn.close()
        return {"status": "failed", "message": "FTS 索引为空"}

    # Drop old vector table and rebuild
    conn.execute('DROP TABLE IF EXISTS segments_vec')
    conn.execute('''
        CREATE TABLE segments_vec (
            id INTEGER PRIMARY KEY,
            path TEXT,
            start REAL,
            text TEXT,
            embedding BLOB
        )
    ''')

    texts = [r[3] for r in rows]
    if verbose:
        print(f'🧠 生成 {len(texts)} 段向量嵌入（{VEC_MODEL_NAME}）...', file=sys.stderr)

    import numpy as np
    try:
        vecs = _embed_batch(texts)
    except RuntimeError as e:
        conn.close()
        return {"status": "failed", "message": str(e)}

    batch = []
    for (rowid, path, start, text), vec in zip(rows, vecs):
        blob = np.asarray(vec, dtype=np.float32).tobytes()
        batch.append((rowid, path, start if start is not None else -1, text, blob))
        if len(batch) >= 500:
            conn.executemany(
                'INSERT INTO segments_vec (id, path, start, text, embedding) '
                'VALUES (?,?,?,?,?)', batch)
            batch = []
    if batch:
        conn.executemany(
            'INSERT INTO segments_vec (id, path, start, text, embedding) '
            'VALUES (?,?,?,?,?)', batch)
    conn.commit()
    conn.close()

    if verbose:
        print(f'✅ 向量索引完成: {len(vecs)} 段', file=sys.stderr)
    return {"status": "success", "segments": len(vecs)}


def vector_search(query: str, limit: int = 10, context: int = 0,
                  file_filter: str | None = None, db_path=None, verbose=True):
    """Semantic search: embed query → cosine similarity over segments_vec."""
    db_path = Path(db_path) if db_path else index_path()
    if not db_path.exists():
        return {"status": "failed", "message": f"索引不存在: {db_path}，请先运行 --index"}

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            'SELECT id, path, start, text, embedding FROM segments_vec').fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return {"status": "failed",
                "message": "向量索引不存在，请运行：python search.py --index --vector"}

    if not rows:
        conn.close()
        return {"status": "failed", "message": "向量索引为空"}

    import numpy as np
    qvec = np.asarray(_embed_batch([query])[0], dtype=np.float32)
    qnorm = np.linalg.norm(qvec)
    if qnorm == 0:
        conn.close()
        return {"status": "failed", "message": "查询向量为空"}

    scored = []
    for rid, path, start, text, blob in rows:
        vec = np.frombuffer(blob, dtype=np.float32)
        n = np.linalg.norm(vec)
        if n == 0:
            continue
        sim = float(np.dot(qvec, vec) / (qnorm * n))
        scored.append((sim, rid, path, start, text))
    conn.close()

    scored.sort(key=lambda x: -x[0])
    results = []
    for sim, rid, path, start, text in scored[:limit]:
        results.append({
            "path": path,
            "start": start if start >= 0 else None,
            "start_ts": f'{int(start) // 60:02d}:{int(start) % 60:02d}' if start >= 0 else '',
            "text": text,
            "score": round(sim, 4),
        })

    if file_filter:
        results = [r for r in results if file_filter.lower() in Path(r['path']).name.lower()]

    if context > 0 and results:
        results = _add_context(results, context, db_path)

    return {"status": "success", "query": query, "count": len(results),
            "matches": results, "mode": "vector"}


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


def video_jump_url(file_name: str, start_ts: str) -> str | None:
    """
    Build a YouTube jump link for a reference.

    File names look like '<video_id>_<title>...' — extract the 11-char
    video ID and convert MM:SS to seconds. Returns None if not applicable.
    """
    import re as _re
    m = _re.match(r'([\w-]{11})_', file_name)
    if not m:
        return None
    vid = m.group(1)
    ts = start_ts or ''
    if not ts or not ts.replace(':', '').isdigit():
        return f'https://youtu.be/{vid}'
    parts = ts.split(':')
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return f'https://youtu.be/{vid}?t={seconds}'


def ask_llm(question: str, matches: list, db_path=None, verbose=True):
    """
    RAG-style Q&A: use search matches as context, answer via LLM.

    Returns {"status", "answer", "references"}
    """
    try:
        import types as _types

        from translate import call_llm, resolve_api_key
    except ImportError:
        return {"status": "failed", "message": "translate.py 不可用"}

    key_args = _types.SimpleNamespace(api_key=None)
    api_key = resolve_api_key(key_args)
    if not api_key:
        return {"status": "failed",
                "message": "需要 DEEPSEEK_API_KEY / OPENAI_API_KEY 环境变量"}

    base_url = os.environ.get('TRANSLATE_BASE_URL', 'https://api.deepseek.com/v1')
    model = os.environ.get('TRANSLATE_MODEL', 'deepseek-chat')

    # Build context from matches
    ctx_parts = []
    for m in matches:
        name = Path(m['path']).name
        ts = m.get('start_ts', '')
        ctx_parts.append(f'[{name} @ {ts}] {m["text"]}')
    context = '\n'.join(ctx_parts)

    system = (
        'You are a study assistant answering questions based ONLY on the '
        'provided video subtitle excerpts. Answer in Chinese (简体中文) unless '
        'the question is in another language. Be concise (3-8 sentences), '
        'use bullet points when helpful. If the excerpts do not contain the '
        'answer, say so directly. Cite the source file and timestamp '
        'when you reference specific content.'
    )
    user = f'问题: {question}\n\n相关字幕片段:\n{context}'

    try:
        answer = call_llm(api_key, base_url, model, system, user, timeout=180)
    except Exception as e:
        return {"status": "failed", "message": f"LLM 调用失败: {str(e)[:200]}"}

    references = [
        {"file": Path(m['path']).name, "start_ts": m.get('start_ts', ''),
         "jump_url": video_jump_url(Path(m['path']).name, m.get('start_ts', '')),
         "text": m['text'][:150]}
        for m in matches[:5]
    ]
    return {"status": "success", "answer": answer, "references": references}


def main():
    # Load .env so DEEPSEEK_API_KEY works for --ask
    try:
        from youtube_utils import load_env
        load_env()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description='Search subtitles')
    parser.add_argument('--index', action='store_true', help='Build/rebuild index')
    parser.add_argument('--path', action='append', default=None,
                        help='Index source dir/file (repeatable; default: output/)')
    parser.add_argument('--query', default=None, help='Search query')
    parser.add_argument('--ask', default=None,
                        help='Ask a question (RAG: search + LLM answer)')
    parser.add_argument('--vector', action='store_true',
                        help='Use vector (semantic) search instead of FTS')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--context', type=int, default=0,
                        help='Show N surrounding lines')
    parser.add_argument('--file', default=None, help='Filter by filename substring')
    parser.add_argument('--json', action='store_true', help='JSON output')
    args = parser.parse_args()

    if args.index:
        result = build_index(paths=args.path, verbose=True)
        if args.vector:
            vec_result = build_vector_index(index_path(), sources=args.path, verbose=True)
            if vec_result.get('status') != 'success':
                print(json.dumps(vec_result, ensure_ascii=False))
                sys.exit(1)
            result['vector_segments'] = vec_result.get('segments', 0)
        print(json.dumps(result, ensure_ascii=False))
        return

    if not args.query and not args.ask:
        parser.print_help()
        sys.exit(1)

    # RAG question answering
    if args.ask:
        # Extract meaningful keywords from the question (strip question words)
        import re as _re
        stop_zh = {'什么', '怎么', '如何', '为什么', '区别', '是否', '吗', '呢',
                   '的', '了', '是', '在', '和', '与', '及', '一个', '可以',
                   '能', '要', '会', '请', '介绍', '讲', '说', '解释', '比较'}
        stop_en = {'what', 'how', 'why', 'is', 'are', 'the', 'a', 'an', 'of',
                   'and', 'or', 'in', 'on', 'to', 'for', 'with', 'vs', 'difference',
                   'between', 'explain', 'compare', 'tell', 'about', 'please'}
        # ASCII keywords
        ascii_kw = [w.lower() for w in _re.findall(r'[A-Za-z][A-Za-z0-9_*+-]*', args.ask)
                    if w.lower() not in stop_en]
        # CJK bigrams from the question
        cjk_bigrams = []
        cjk_run = ''.join(_re.findall(r'[\u4e00-\u9fff]+', args.ask))
        for i in range(len(cjk_run) - 1):
            pair = cjk_run[i:i + 2]
            if pair not in cjk_bigrams:
                cjk_bigrams.append(pair)

        # Prefer ASCII keywords; fall back to CJK bigrams
        # Filter: skip FTS wildcard chars and single letters
        ascii_clean = [w for w in ascii_kw
                       if len(w) >= 2 and not w.endswith('*') and '*' not in w]
        search_parts = ascii_clean[:3]
        if not search_parts:
            # CJK bigrams, skipping overly-generic ones
            generic = {'搜索', '什么', '区别', '是否', '这个', '那个', '一个'}
            cjk_clean = [b for b in cjk_bigrams if b not in generic and b not in stop_zh]
            search_parts = cjk_clean[:3]
        search_query = ' '.join(search_parts) if search_parts else args.ask

        if args.vector:
            result = vector_search(search_query, limit=6, context=1,
                                   file_filter=args.file)
        else:
            result = search(search_query, limit=6, context=1, file_filter=args.file)
        if result.get('status') != 'success' or not result.get('matches'):
            print(json.dumps({
                "status": "failed",
                "message": f"未找到相关片段: {result.get('message', '')}"
            }, ensure_ascii=False))
            sys.exit(1)
        answer = ask_llm(args.ask, result['matches'])
        if args.json:
            print(json.dumps(answer, ensure_ascii=False, indent=2))
        else:
            if answer.get('status') != 'success':
                print(f"❌ {answer.get('message', '问答失败')}")
            else:
                print(f"❓ {args.ask}\n")
                print(answer['answer'])
                print('\n📎 参考片段:')
                for ref in answer['references']:
                    jump = ref.get('jump_url') or ''
                    if jump:
                        print(f"  [{ref['start_ts']}] {ref['file']}")
                        print(f"    ⏩ {jump}")
                    else:
                        print(f"  [{ref['start_ts']}] {ref['file']}")
        return

    if args.vector:
        result = vector_search(args.query, limit=args.limit, context=args.context,
                               file_filter=args.file)
    else:
        result = search(args.query, limit=args.limit, context=args.context,
                        file_filter=args.file)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_results(result))


if __name__ == '__main__':
    main()

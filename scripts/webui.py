#!/usr/bin/env python3
"""
webui.py — Simple web UI for youtube-content.

Paste a YouTube URL → choose mode → view captions/transcript/chapters,
download SRT files, and browse previously processed outputs.

Usage:
  python webui.py [--port 8080] [--host 127.0.0.1]

Requires: pip install flask
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ANALYZE_PY = SCRIPT_DIR / 'analyze_youtube.py'
OUTPUT_DIR = SCRIPT_DIR.parent / 'output'  # analyze writes to project root /output

try:
    from flask import Flask, jsonify, render_template_string, request, send_from_directory
except ImportError:
    print('需要 Flask：pip install flask')
    sys.exit(1)

app = Flask(__name__)

HTML = '''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🎬 YouTube Content Tool</title>
<style>
  body { font-family: -apple-system, "Microsoft YaHei", sans-serif; max-width: 900px;
         margin: 0 auto; padding: 20px; background: #f7f8fa; color: #24292f; }
  h1 { text-align: center; color: #1f6feb; }
  .card { background: #fff; border-radius: 10px; padding: 20px; margin: 16px 0;
          box-shadow: 0 1px 3px rgba(0,0,0,.1); }
  input[type=text] { width: 100%; padding: 10px; font-size: 15px; border: 1px solid #d0d7de;
                     border-radius: 6px; box-sizing: border-box; }
  select, button { padding: 10px 14px; font-size: 15px; border-radius: 6px;
                   border: 1px solid #d0d7de; background: #fff; cursor: pointer; }
  button { background: #1f6feb; color: #fff; border: none; margin-left: 8px; }
  button:hover { background: #1857c1; }
  button:disabled { background: #8bb3f0; cursor: wait; }
  label { font-weight: 600; margin-right: 10px; }
  .row { margin: 10px 0; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  pre { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 14px;
        white-space: pre-wrap; word-break: break-word; max-height: 500px; overflow-y: auto;
        font-size: 14px; line-height: 1.6; }
  .status { padding: 10px 14px; border-radius: 6px; margin: 10px 0; }
  .ok { background: #dafbe1; color: #116329; }
  .err { background: #ffebe9; color: #cf222e; }
  .warn { background: #fff8c5; color: #9a6700; }
  .file { padding: 8px 0; border-bottom: 1px solid #eee; display: flex;
          justify-content: space-between; align-items: center; }
  .file a { color: #1f6feb; text-decoration: none; }
  a.dl { color: #fff !important; background: #1f6feb; padding: 4px 10px; border-radius: 4px;
         font-size: 13px; }
  .small { color: #57606a; font-size: 13px; }
  #spinner { display: none; text-align: center; padding: 20px; color: #57606a; }
</style>
</head>
<body>
<h1>🎬 YouTube Content Tool</h1>

<div class="card">
  <div class="row">
    <input type="text" id="url" placeholder="粘贴 YouTube 链接 / 视频 ID / 播放列表...">
  </div>
  <div class="row">
    <label>模式</label>
    <select id="mode">
      <option value="">提取字幕（最快）</option>
      <option value="--auto">自动（有字幕→字幕，无字幕→转写）</option>
      <option value="--force-whisper">强制 Whisper 转写</option>
      <option value="--playlist">播放列表批量</option>
    </select>
    <label>附加</label>
    <input type="checkbox" id="chapters"> 章节
    <input type="checkbox" id="translate"> 翻译成中文
    <input type="checkbox" id="srt"> SRT 下载
  </div>
  <div class="row">
    <button id="run" onclick="run()">🚀 开始处理</button>
    <span class="small" id="hint"></span>
  </div>
  <div id="spinner">⏳ 处理中，请稍候（长视频转写可能需要几分钟）...</div>
  <div id="result"></div>
</div>

<div class="card">
  <h3>📁 历史输出</h3>
  <div id="files"></div>
</div>

<script>
async function run() {
  const url = document.getElementById('url').value.trim();
  if (!url) { alert('请输入链接'); return; }
  const mode = document.getElementById('mode').value;
  const args = [];
  if (mode) args.push(mode);
  if (document.getElementById('chapters').checked) args.push('--chapters');
  if (document.getElementById('translate').checked) args.push('--translate');
  if (document.getElementById('srt').checked) args.push('--format', 'srt');
  const btn = document.getElementById('run');
  btn.disabled = true;
  document.getElementById('spinner').style.display = 'block';
  document.getElementById('result').innerHTML = '';
  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, args})
    });
    const data = await resp.json();
    renderResult(data);
  } catch (e) {
    document.getElementById('result').innerHTML =
      '<div class="status err">请求失败: ' + e + '</div>';
  }
  btn.disabled = false;
  document.getElementById('spinner').style.display = 'none';
  loadFiles();
}

function renderResult(d) {
  const r = document.getElementById('result');
  if (d.status === 'success') {
    let html = '<div class="status ok">✅ ' + d.message + '</div>';
    if (d.title) html += '<p><b>标题:</b> ' + escapeHtml(d.title) + '</p>';
    if (d.chapters && d.chapters.length) {
      html += '<p><b>📑 章节:</b></p><ul>';
      d.chapters.slice(0, 20).forEach(c => {
        html += '<li>' + escapeHtml(c.start_ts) + ' — ' + escapeHtml(c.title) + '</li>';
      });
      html += '</ul>';
    }
    if (d.transcript) {
      html += '<p><b>📝 内容:</b></p><pre>' + escapeHtml(d.transcript.slice(0, 8000)) + '</pre>';
      if (d.transcript.length > 8000) html += '<p class="small">…（截断，完整见文件）</p>';
    }
    html += '<p class="small">文件: ' +
      [d.transcript_file, d.converted_file, d.translated_file, d.chapters_file]
        .filter(Boolean).map(f => escapeHtml(f.split('/').pop())).join(' · ') + '</p>';
    r.innerHTML = html;
  } else if (d.status === 'needs_confirmation') {
    r.innerHTML = '<div class="status warn">⚠ ' + escapeHtml(d.message) + '</div>' +
      '<div class="row"><button onclick="runWhisper()">🎙 用 Whisper 转写</button></div>';
    window._pending = d;
  } else {
    r.innerHTML = '<div class="status err">❌ ' + escapeHtml(d.message || '处理失败') + '</div>' +
      (d.detail ? '<pre>' + escapeHtml(d.detail) + '</pre>' : '');
  }
}

async function runWhisper() {
  const d = window._pending;
  if (!d) return;
  const btn = document.getElementById('run');
  btn.disabled = true;
  document.getElementById('spinner').style.display = 'block';
  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: d.video_id, args: ['--whisper']})
    });
    renderResult(await resp.json());
  } catch (e) {
    document.getElementById('result').innerHTML =
      '<div class="status err">请求失败: ' + e + '</div>';
  }
  btn.disabled = false;
  document.getElementById('spinner').style.display = 'none';
  loadFiles();
}

async function loadFiles() {
  try {
    const resp = await fetch('/api/files');
    const files = await resp.json();
    const box = document.getElementById('files');
    if (!files.length) { box.innerHTML = '<span class="small">暂无输出</span>'; return; }
    box.innerHTML = files.map(f =>
      '<div class="file"><span>' + escapeHtml(f.name) + ' <span class="small">' +
      f.size + '</span></span><a class="dl" href="' + f.download + '">下载</a></div>'
    ).join('');
  } catch (e) { /* ignore */ }
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

loadFiles();
</script>
</body>
</html>'''


@app.route('/')
def index():
    return render_template_string(HTML)


KB_HTML = '''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>📚 知识库</title>
<style>
  body { font-family: -apple-system, "Microsoft YaHei", sans-serif; max-width: 900px;
         margin: 0 auto; padding: 20px; color: #24292f; }
  h1 { font-size: 22px; }
  .tabs { display: flex; gap: 8px; margin: 16px 0; }
  .tab { padding: 8px 18px; border: 1px solid #d0d7de; border-radius: 8px;
         cursor: pointer; background: #f6f8fa; font-size: 14px; }
  .tab.active { background: #1f6feb; color: #fff; border-color: #1f6feb; }
  .panel { display: none; }
  .panel.active { display: block; }
  input[type=text] { width: calc(100% - 110px); padding: 9px; border: 1px solid #d0d7de;
                     border-radius: 6px; font-size: 14px; }
  button { padding: 9px 18px; background: #1f6feb; color: #fff; border: none;
           border-radius: 6px; font-size: 14px; cursor: pointer; margin-left: 6px; }
  button:hover { background: #1857c1; }
  .result { margin-top: 14px; }
  .item { border: 1px solid #d0d7de; border-radius: 8px; padding: 10px 14px;
          margin-bottom: 8px; font-size: 13px; }
  .item .meta { color: #57606a; font-size: 12px; margin-bottom: 4px; }
  .item a { color: #1f6feb; text-decoration: none; }
  .item pre { white-space: pre-wrap; word-break: break-word; margin: 4px 0;
              font-family: inherit; font-size: 13px; }
  .err { color: #cf222e; }
  .small { color: #57606a; font-size: 12px; }
</style>
</head>
<body>
<h1>📚 视频知识库</h1>
<div class="tabs">
  <div class="tab active" data-tab="search">🔍 搜索</div>
  <div class="tab" data-tab="ask">❓ 问答</div>
  <div class="tab" data-tab="notes">📄 笔记</div>
</div>

<div id="p-search" class="panel active">
  <input type="text" id="sq" placeholder="输入关键词（中文/英文）...">
  <button onclick="doSearch()">搜索</button>
  <label class="small"><input type="checkbox" id="svec"> 语义搜索（慢）</label>
  <div id="sres" class="result"></div>
</div>

<div id="p-ask" class="panel">
  <input type="text" id="aq" placeholder="输入问题，如：CS50 里 A* 和贪心搜索的区别？">
  <button onclick="doAsk()">提问</button>
  <label class="small"><input type="checkbox" id="avec"> 语义检索</label>
  <div id="ares" class="result"></div>
</div>

<div id="p-notes" class="panel">
  <div class="small">归档目录: <span id="notes-dir"></span></div>
  <div id="nres" class="result"></div>
</div>

<script>
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById('p-' + t.dataset.tab).classList.add('active');
  if (t.dataset.tab === 'notes') loadNotes();
}));

function esc(s) { return String(s||'').replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

async function doSearch() {
  const q = document.getElementById('sq').value.trim();
  const mode = document.getElementById('svec').checked ? 'vector' : 'fts';
  const el = document.getElementById('sres');
  if (!q) return;
  el.innerHTML = '⏳ 搜索中...';
  try {
    const r = await fetch('/api/search?q=' + encodeURIComponent(q) +
      '&mode=' + mode + '&limit=10');
    const d = await r.json();
    if (d.status !== 'success' || !d.matches || !d.matches.length) {
      el.innerHTML = '<div class="err">未找到匹配（' + esc(d.message||'') + '）</div>';
      return;
    }
    el.innerHTML = '<div class="small">找到 ' + d.count + ' 个匹配</div>' + d.matches.map(m => {
      const j = m.jump_url ? ' · <a href="' + esc(m.jump_url) + '" target="_blank">⏩ 跳转</a>' : '';
      return '<div class="item"><div class="meta">[' + esc(m.start_ts||'--:--') + '] ' +
        esc(m.path.split(/[\\\\\\/]/).pop()) + ' · 得分 ' + (m.score||'') + j + '</div>' +
        '<pre>' + esc(m.text) + '</pre></div>';
    }).join('');
  } catch (e) { el.innerHTML = '<div class="err">❌ ' + esc(e) + '</div>'; }
}

async function doAsk() {
  const q = document.getElementById('aq').value.trim();
  const mode = document.getElementById('avec').checked ? 'vector' : 'fts';
  const el = document.getElementById('ares');
  if (!q) return;
  el.innerHTML = '⏳ 思考中（DeepSeek 回答，约 10-30 秒）...';
  try {
    const r = await fetch('/api/ask', { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({question: q, mode: mode}) });
    const d = await r.json();
    if (d.status !== 'success') { el.innerHTML = '<div class="err">❌ ' + esc(d.message||'') + '</div>'; return; }
    let html = '<div class="item"><pre>' + esc(d.answer) + '</pre></div>';
    if (d.references && d.references.length) {
      html += '<div class="small">📎 参考片段:</div>' + d.references.map(ref => {
        const j = ref.jump_url ? ' · <a href="' + esc(ref.jump_url) + '" target="_blank">⏩ 跳转</a>' : '';
        return '<div class="item"><div class="meta">[' + esc(ref.start_ts||'') + '] ' +
          esc(ref.file) + j + '</div><pre>' + esc(ref.text) + '</pre></div>';
      }).join('');
    }
    el.innerHTML = html;
  } catch (e) { el.innerHTML = '<div class="err">❌ ' + esc(e) + '</div>'; }
}

async function loadNotes() {
  const el = document.getElementById('nres');
  try {
    const r = await fetch('/api/notes');
    const d = await r.json();
    document.getElementById('notes-dir').textContent = d.dir;
    if (!d.notes || !d.notes.length) { el.innerHTML = '<div class="small">暂无归档笔记</div>'; return; }
    el.innerHTML = d.notes.map(n => '<div class="item"><div class="meta">📄 ' +
      esc(n.name) + ' · ' + n.size + '</div><pre>' + esc(n.content) + '</pre></div>').join('');
  } catch (e) { el.innerHTML = '<div class="err">❌ ' + esc(e) + '</div>'; }
}

loadNotes();
</script>
</body>
</html>
'''


@app.route('/kb')
def kb():
    return render_template_string(KB_HTML)


@app.after_request
def add_cors_headers(resp):
    """Allow browser extensions to call the API from any origin."""
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp


@app.route('/api/quick')
def api_quick():
    """Quick API for the browser extension.

    GET /api/quick?url=<video>&max=<chars>&summarize=<0|1>&whisper=<0|1>
    whisper=1: if no captions, transcribe audio with Whisper (slow).
    Returns: {status, title, summary, text, chapters}
    """
    url = request.args.get('url', '').strip()
    max_chars = int(request.args.get('max', '3000'))
    do_summarize = request.args.get('summarize', '1') != '0'
    do_whisper = request.args.get('whisper', '0') == '1'
    if not url:
        return jsonify({"status": "failed", "message": "缺少 url 参数"}), 400

    cmd = [sys.executable, str(ANALYZE_PY), url, '--chapters']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        out = result.stdout.strip()
        idx = out.find('{')
        if idx < 0:
            return jsonify({"status": "failed", "message": out[:200] or "无输出"})
        data = json.loads(out[idx:])
    except subprocess.TimeoutExpired:
        return jsonify({"status": "failed", "message": "处理超时"})
    except json.JSONDecodeError:
        return jsonify({"status": "failed", "message": "输出解析失败"})

    # No captions → optional Whisper fallback (slow: minutes)
    if data.get('status') == 'needs_confirmation' and do_whisper:
        try:
            wcmd = [sys.executable, str(ANALYZE_PY), url, '--whisper', '--chapters']
            wresult = subprocess.run(wcmd, capture_output=True, text=True, timeout=3600)
            wout = wresult.stdout.strip()
            widx = wout.find('{')
            if widx >= 0:
                data = json.loads(wout[widx:])
        except subprocess.TimeoutExpired:
            return jsonify({"status": "failed", "message": "Whisper 转写超时（视频过长）"})
        except json.JSONDecodeError:
            pass

    if data.get('status') != 'success':
        return jsonify(data)

    # Attach transcript text (skip header)
    text = ''
    try:
        p = Path(data.get('transcript_file', ''))
        lines = p.read_text(encoding='utf-8').split('\n')
        text = '\n'.join(lines[3:])[:max_chars]
    except OSError:
        pass

    # LLM summary (Simplified Chinese, cached) when requested
    summary = None
    if do_summarize and text:
        try:
            sys.path.insert(0, str(SCRIPT_DIR))
            import argparse as _ap

            from analyze_youtube import summarize_transcript
            s_args = _ap.Namespace(
                translate_api_key=None, translate_base_url=None, translate_model=None)
            summary, _used_cache = summarize_transcript(
                text, data.get('video_id') or url, data.get('title', ''),
                args=s_args)
        except Exception as e:
            print(f'⚠ summarize failed: {e}', file=sys.stderr)

    return jsonify({
        "status": "success",
        "title": data.get('title', ''),
        "summary": summary,
        "text": text,
        "chapters": data.get('chapters', []),
        "transcript_file": data.get('transcript_file'),
        "converted_file": data.get('converted_file'),
        "translated_file": data.get('translated_file'),
    })


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    data = request.get_json(force=True)
    url = data.get('url', '').strip()
    args = data.get('args', [])
    if not url:
        return jsonify({"status": "failed", "message": "请输入链接"}), 400

    cmd = [sys.executable, str(ANALYZE_PY), url] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        out = result.stdout.strip()
        # The script prints progress to stderr and JSON to stdout
        idx = out.find('{')
        if idx >= 0:
            payload = json.loads(out[idx:])
        else:
            payload = {"status": "failed", "message": out[:300] or "无输出"}
    except subprocess.TimeoutExpired:
        payload = {"status": "failed", "message": "处理超时（>1小时）"}
    except json.JSONDecodeError:
        payload = {"status": "failed", "message": f"输出解析失败: {out[:200]}"}

    # Attach transcript text for display
    if payload.get('status') == 'success' and payload.get('transcript_file'):
        try:
            p = Path(payload['transcript_file'])
            lines = p.read_text(encoding='utf-8').split('\n')
            payload['transcript'] = '\n'.join(lines[3:])  # skip header
        except OSError:
            pass
    return jsonify(payload)


@app.route('/api/files')
def api_files():
    files = []
    if OUTPUT_DIR.exists():
        for f in sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
            if f.is_file() and f.suffix in ('.txt', '.json', '.srt', '.vtt', '.lrc'):
                size = f.stat().st_size
                files.append({
                    "name": f.name,
                    "size": f"{size / 1024:.1f} KB" if size > 1024 else f"{size} B",
                    "download": f"/api/download/{f.name}",
                })
    return jsonify(files)


@app.route('/api/download/<path:filename>')
def api_download(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


# ── Knowledge base: search / ask / notes ────────────────────────────────

def _search_index_path():
    """Locate search_index.db (project output dir parent or cwd)."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from search import index_path
    return str(index_path())


@app.route('/api/search')
def api_search():
    """GET /api/search?q=...&mode=fts|vector&limit=N&file=..."""
    q = request.args.get('q', '').strip()
    mode = request.args.get('mode', 'fts')
    limit = int(request.args.get('limit', '10'))
    file_filter = request.args.get('file') or None
    if not q:
        return jsonify({"status": "failed", "message": "缺少 q 参数"}), 400

    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from search import search, vector_search
        if mode == 'vector':
            result = vector_search(q, limit=limit, context=1, file_filter=file_filter)
        else:
            result = search(q, limit=limit, context=1, file_filter=file_filter)
        # Add jump URLs
        from pathlib import Path as _P

        from search import video_jump_url
        for m in result.get('matches', []):
            fname = _P(m['path']).name
            m['jump_url'] = video_jump_url(fname, m.get('start_ts', ''))
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "failed", "message": str(e)[:200]}), 500


@app.route('/api/ask', methods=['POST'])
def api_ask():
    """POST /api/ask {question, mode: fts|vector} → RAG answer + references."""
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get('question') or '').strip()
    mode = data.get('mode', 'fts')
    if not question:
        return jsonify({"status": "failed", "message": "缺少 question"}), 400

    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        # Keyword extraction (mirror search.py --ask)
        import re as _re

        from search import ask_llm, search, vector_search, video_jump_url
        stop_zh = {'什么', '怎么', '如何', '为什么', '区别', '是否', '吗', '呢',
                   '的', '了', '是', '在', '和', '与', '及', '一个', '可以',
                   '能', '要', '会', '请', '介绍', '讲', '说', '解释', '一下',
                   '这个', '那个', '哪些', '哪个', '多少', '里面', '中'}
        stop_en = {'what', 'how', 'why', 'is', 'are', 'the', 'a', 'an', 'of',
                   'in', 'and', 'to', 'for', 'with', 'on', 'at', 'do', 'does'}
        ascii_kw = [w.lower() for w in _re.findall(r'[A-Za-z][A-Za-z0-9_*+-]*', question)
                    if w.lower() not in stop_en]
        cjk_bigrams = [question[i:i+2] for i in range(len(question) - 1)
                       if '\u4e00' <= question[i] <= '\u9fff'
                       and '\u4e00' <= question[i+1] <= '\u9fff']
        search_query = ' '.join(ascii_kw[:3]) if ascii_kw else \
            ' '.join(b for b in cjk_bigrams[:3] if b not in stop_zh)
        if not search_query:
            search_query = question

        if mode == 'vector':
            result = vector_search(search_query, limit=6, context=1)
        else:
            result = search(search_query, limit=6, context=1)
        if result.get('status') != 'success' or not result.get('matches'):
            return jsonify({"status": "failed",
                            "message": "没有找到相关内容，换个问法试试"})

        answer = ask_llm(question, result['matches'])
        # Add jump URLs to references
        for ref in answer.get('references', []):
            ref['jump_url'] = video_jump_url(ref.get('file', ''),
                                             ref.get('start_ts', ''))
        return jsonify(answer)
    except Exception as e:
        return jsonify({"status": "failed", "message": str(e)[:200]}), 500


@app.route('/api/notes')
def api_notes():
    """GET /api/notes?dir=<archive dir> — list markdown notes + content."""
    dir_arg = request.args.get('dir', '').strip()
    if not dir_arg:
        dir_arg = str(Path.home() / 'Desktop' / '视频知识库')
    notes_dir = Path(dir_arg)
    notes = []
    if notes_dir.exists():
        for f in sorted(notes_dir.glob('*.md'),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
            notes.append({
                "name": f.name,
                "size": f"{f.stat().st_size / 1024:.1f} KB",
                "content": f.read_text(encoding='utf-8', errors='replace')[:4000],
                "mtime": f.stat().st_mtime,
            })
    return jsonify({"status": "success", "dir": str(notes_dir), "notes": notes})


def main():
    parser = argparse.ArgumentParser(description='youtube-content web UI')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()

    # Load .env so DEEPSEEK_API_KEY works for /api/quick summarize
    try:
        from youtube_utils import load_env
        load_env()
    except ImportError:
        pass

    print(f'🌐 Web UI 启动: http://{args.host}:{args.port}')
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()

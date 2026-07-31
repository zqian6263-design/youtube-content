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

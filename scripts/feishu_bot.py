#!/usr/bin/env python3
"""
feishu_bot.py — Feishu (Lark) bot that extracts YouTube content on demand.

Deploy as a webhook service on the Feishu Open Platform (企业自建应用):
  1. 飞书开放平台 → 创建企业自建应用
  2. 事件订阅 → 请求地址: https://your-host/webhook (POST)
  3. 权限: im:message, im:message:send_as_bot
  4. 添加事件: im.message.receive_v1

User sends a YouTube link to the bot → bot replies with the caption
summary / first N chars / transcript file info.

Config (env or .env):
  FEISHU_APP_ID=cli_xxx
  FEISHU_APP_SECRET=xxx
  BOT_REPLY_CHARS=2000   (max chars of transcript in reply)
  BOT_FLAGS=--timestamps (extra analyze flags, optional)

Run: python feishu_bot.py [--port 8081]

Requires: pip install flask requests
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ANALYZE_PY = SCRIPT_DIR / 'analyze_youtube.py'

try:
    import requests
    from flask import Flask, jsonify, request
except ImportError:
    print('需要依赖：pip install flask requests')
    sys.exit(1)

app = Flask(__name__)

FEISHU_OPEN = 'https://open.feishu.cn/open-apis'
URL_RE = re.compile(
    r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s，。！？、]+)'
)


def get_tenant_token():
    """Fetch tenant_access_token from Feishu API."""
    resp = requests.post(
        f'{FEISHU_OPEN}/auth/v3/tenant_access_token/internal',
        json={
            'app_id': os.environ.get('FEISHU_APP_ID', ''),
            'app_secret': os.environ.get('FEISHU_APP_SECRET', ''),
        },
        timeout=15,
    )
    data = resp.json()
    if data.get('code') != 0:
        raise RuntimeError(f'获取 token 失败: {data}')
    return data['tenant_access_token']


def reply_message(chat_id: str, text: str):
    """Send a text message to a chat as the bot."""
    token = get_tenant_token()
    resp = requests.post(
        f'{FEISHU_OPEN}/im/v1/messages?receive_id_type=chat_id',
        headers={'Authorization': f'Bearer {token}'},
        json={
            'receive_id': chat_id,
            'msg_type': 'text',
            'content': json.dumps({'text': text}, ensure_ascii=False),
        },
        timeout=15,
    )
    data = resp.json()
    if data.get('code') != 0:
        print(f'⚠ 回复失败: {data}', file=sys.stderr)


def extract_youtube_url(text: str) -> str | None:
    m = URL_RE.search(text)
    return m.group(1) if m else None


def process_video(url: str) -> str:
    """Run analyze_youtube.py (subtitle fast path) and build a reply."""
    flags = os.environ.get('BOT_FLAGS', '').split()
    max_chars = int(os.environ.get('BOT_REPLY_CHARS', '2000'))
    try:
        result = subprocess.run(
            [sys.executable, str(ANALYZE_PY), url] + flags,
            capture_output=True, text=True, timeout=900,
        )
        out = result.stdout.strip()
        idx = out.find('{')
        if idx < 0:
            return f'⚠ 处理失败（无输出）\n{out[:200]}'
        data = json.loads(out[idx:])
    except subprocess.TimeoutExpired:
        return '⏳ 处理超时（>15分钟），请稍后重试'
    except Exception as e:
        return f'⚠ 处理出错: {str(e)[:200]}'

    if data.get('status') == 'success':
        title = data.get('title', '')
        transcript_file = data.get('transcript_file', '')
        # Read transcript for reply
        try:
            lines = Path(transcript_file).read_text(encoding='utf-8').split('\n')
            content = '\n'.join(lines[3:])  # skip header
        except OSError:
            content = ''
        truncated = content[:max_chars] + ('…' if len(content) > max_chars else '')
        return f'🎬 {title}\n\n{truncated}'
    if data.get('status') == 'needs_confirmation':
        return ('📭 该视频无字幕。\n'
                '要我用 Whisper 转写音频吗？回复：\n'
                f'`whisper {data.get("video_id", "")}`')
    return f"⚠ 提取失败: {data.get('message', '未知错误')}"


@app.route('/webhook', methods=['POST'])
def webhook():
    body = request.get_json(force=True, silent=True) or {}

    # URL verification handshake
    if 'challenge' in body:
        return jsonify({'challenge': body['challenge']})

    # Message event
    event = body.get('event', {})
    message = event.get('message', {})
    msg_type = message.get('message_type', message.get('msg_type', ''))
    chat_id = message.get('chat_id', '')

    if msg_type == 'text' or msg_type == 'post':
        content = message.get('content', '')
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            parsed = {'text': content}
        text = parsed.get('text', '')

        # Whisper command support: "whisper <id>"
        if text.strip().startswith('whisper '):
            vid = text.strip().split(' ', 1)[1].strip()
            try:
                result = subprocess.run(
                    [sys.executable, str(ANALYZE_PY), vid, '--whisper'],
                    capture_output=True, text=True, timeout=3600,
                )
                out = result.stdout.strip()
                idx = out.find('{')
                data = json.loads(out[idx:]) if idx >= 0 else {}
                if data.get('status') == 'success':
                    reply_message(chat_id, f'🎙 转写完成: {data.get("title", "")}')
                else:
                    reply_message(chat_id, f"⚠ 转写失败: {data.get('message', '未知')}")
            except Exception as e:
                reply_message(chat_id, f'⚠ 转写出错: {str(e)[:200]}')
            return jsonify({'code': 0})

        url = extract_youtube_url(text)
        if url:
            reply_message(chat_id, '⏳ 正在提取字幕，请稍候...')
            reply = process_video(url)
            reply_message(chat_id, reply)
        else:
            reply_message(chat_id, '🔗 请发送 YouTube 视频链接，例如：\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ')

    return jsonify({'code': 0})


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


def main():
    parser = argparse.ArgumentParser(description='Feishu bot for youtube-content')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8081)
    args = parser.parse_args()

    # Load .env
    try:
        from youtube_utils import load_env
        load_env()
    except ImportError:
        pass

    if not os.environ.get('FEISHU_APP_ID') or not os.environ.get('FEISHU_APP_SECRET'):
        print('⚠ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET（在 .env 或环境变量中）')
        print('   bot 可启动，但回复消息会失败。')

    print(f'🤖 Feishu bot 启动: http://{args.host}:{args.port}/webhook')
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()

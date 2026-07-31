#!/usr/bin/env python3
"""
translate.py — Translate subtitle text via an OpenAI-compatible chat API.

Uses any OpenAI-compatible endpoint (DeepSeek / OpenAI / local LLM).
Key resolution order: --api-key > DEEPSEEK_API_KEY > OPENAI_API_KEY.

Input: subtitle text (with or without [MM:SS] timestamps) via --input file
       or --text argument.

Output (JSON to stdout):
  {"status":"success","translated_text":"...","chunks":N,"model":"..."}

Long inputs are split into overlapping chunks (~30K chars) and translated
sequentially, then merged — avoids context window overflow.

Usage:
  python translate.py --input subs.txt --target zh
  python translate.py --text "hello world" --target zh
  DEEPSEEK_API_KEY=sk-xxx python translate.py --input subs.txt
"""

import argparse
import json
import os
import sys
import time
import urllib.request

DEFAULT_BASE_URL = 'https://api.deepseek.com/v1'
DEFAULT_MODEL = 'deepseek-chat'
MAX_CHUNK_CHARS = 30000
OVERLAP_CHARS = 1000

SYSTEM_PROMPT = (
    "You are a professional subtitle translator. Translate the given subtitle "
    "text into {target} ({target_name}). Rules:\n"
    "1. Keep the meaning accurate and natural; prefer colloquial spoken tone.\n"
    "2. Preserve [MM:SS] timestamp prefixes exactly as-is at the start of each line.\n"
    "3. Keep technical terms, proper nouns, and code snippets in original form "
    "(optionally with a Chinese gloss in parentheses).\n"
    "4. Do NOT add explanations, notes, or commentary. Output only the translation.\n"
    "5. Keep line breaks matching the input structure.\n"
)

TARGET_NAMES = {
    'zh': 'Simplified Chinese (简体中文)',
    'en': 'English',
    'ja': 'Japanese (日本語)',
    'ko': 'Korean (한국어)',
    'fr': 'French',
    'de': 'German',
    'es': 'Spanish',
}


def resolve_api_key(args) -> str | None:
    """Key resolution: --api-key > DEEPSEEK_API_KEY > OPENAI_API_KEY."""
    if args.api_key:
        return args.api_key
    for var in ('DEEPSEEK_API_KEY', 'OPENAI_API_KEY'):
        v = os.environ.get(var, '').strip()
        if v:
            return v
    return None


def call_llm(api_key: str, base_url: str, model: str, system: str, user: str,
             timeout: int = 300) -> str:
    """Call OpenAI-compatible chat completions API. Returns assistant text."""
    url = f'{base_url.rstrip("/")}/chat/completions'
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 8192,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data['choices'][0]['message']['content'].strip()


def split_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS,
                 overlap: int = OVERLAP_CHARS) -> list:
    """Split text into overlapping chunks at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            # Back off to the last sentence boundary within the chunk
            window = text[start:end]
            boundary = max(window.rfind('. '), window.rfind('。'),
                           window.rfind('! '), window.rfind('？'), window.rfind('\n'))
            if boundary > max_chars // 2:
                end = start + boundary + 1
        chunks.append(text[start:end])
        start = max(end - overlap, start + max_chars // 2)
        if start >= len(text):
            break
    return chunks


def translate_text(text: str, args) -> dict:
    """Translate subtitle text. Returns result dict."""
    api_key = resolve_api_key(args)
    if not api_key:
        return {
            "status": "failed",
            "message": ("未配置 API key。请设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY "
                        "环境变量，或用 --api-key 参数。"),
        }

    target = args.target.lower()
    target_name = TARGET_NAMES.get(target, target)
    system = SYSTEM_PROMPT.format(target=target, target_name=target_name)

    chunks = split_chunks(text, args.max_chunk_chars or MAX_CHUNK_CHARS)
    translated_parts = []
    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            sys.stderr.write(f'  ⏳ 翻译块 {i}/{len(chunks)} ({len(chunk)} 字符)...\n')
            sys.stderr.flush()
        user = (f'Translate the following subtitle text into {target_name}. '
                f'Preserve [MM:SS] prefixes. Output ONLY the translation:\n\n{chunk}')
        try:
            translated = call_llm(
                api_key, args.base_url, args.model, system, user,
                timeout=args.timeout,
            )
        except Exception as e:
            return {
                "status": "failed",
                "message": f"API 调用失败（块 {i}）: {str(e)[:200]}",
                "detail": str(e)[:500],
            }
        translated_parts.append(translated)
        if len(chunks) > 1 and i < len(chunks):
            time.sleep(0.5)  # polite rate limiting between chunks

    return {
        "status": "success",
        "translated_text": '\n'.join(translated_parts),
        "chunks": len(chunks),
        "model": args.model,
        "target": target,
    }


def main():
    parser = argparse.ArgumentParser(description='Translate subtitle text')
    parser.add_argument('--input', default=None, help='Input subtitle file')
    parser.add_argument('--text', default=None, help='Input text directly')
    parser.add_argument('--target', default='zh',
                        help='Target language code (zh/en/ja/ko/fr/de/es, default: zh)')
    parser.add_argument('--api-key', default=None, help='API key (or env var)')
    parser.add_argument('--base-url', default=os.environ.get(
        'TRANSLATE_BASE_URL', DEFAULT_BASE_URL), help='OpenAI-compatible base URL')
    parser.add_argument('--model', default=os.environ.get(
        'TRANSLATE_MODEL', DEFAULT_MODEL), help='Model name')
    parser.add_argument('--max-chunk-chars', type=int, default=MAX_CHUNK_CHARS)
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()

    if args.input:
        try:
            with open(args.input, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError as e:
            print(json.dumps({"status": "failed", "message": f"无法读取文件: {e}"}))
            sys.exit(1)
    elif args.text:
        text = args.text
    else:
        print(json.dumps({"status": "failed", "message": "需要 --input 或 --text"}))
        sys.exit(1)

    if not text.strip():
        print(json.dumps({"status": "failed", "message": "输入文本为空"}))
        sys.exit(1)

    result = translate_text(text, args)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get('status') == 'success' else 1)


if __name__ == '__main__':
    main()

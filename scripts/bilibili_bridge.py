#!/usr/bin/env python3
"""
bilibili_bridge.py — Bilibili adapter for youtube-content.

Bridges Bilibili videos into the same pipeline as YouTube:
  1. CC subtitles via bilibili-content skill's fetch_subtitle.py
     (needs BILIBILI_SESSDATA / BILIBILI_BILI_JCT cookies)
  2. Audio via yt-dlp (bilibili is supported natively) → Whisper
     transcription when no subtitles (--whisper)

Output JSON matches the youtube path: {status, transcript, title, ...}

Usage:
  python bilibili_bridge.py "BV1xx..." [--whisper] [--chapters]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WHISPER_PY = SCRIPT_DIR / 'transcribe_whisper.py'

# bilibili-content skill location (installed skill or repo checkout)
BILI_SKILL_CANDIDATES = [
    Path(os.environ.get('BILIBILI_SKILL_DIR', '')),
    Path.home() / 'AppData' / 'Local' / 'hermes' / 'skills' / 'media' / 'bilibili-content',
    Path.home() / '.hermes' / 'skills' / 'media' / 'bilibili-content',
]
BILI_SKILL_DIR = next((p for p in BILI_SKILL_CANDIDATES if (p / 'scripts' / 'fetch_subtitle.py').exists()), None)


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_bili_env():
    """Inject Bilibili cookies from the skill .env into os.environ."""
    if not BILI_SKILL_DIR:
        return
    env_file = BILI_SKILL_DIR / '.env'
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())


def extract_bvid(input_str: str) -> str | None:
    """Extract BV number from URL or raw string."""
    s = input_str.strip()
    if re.match(r'^BV[a-zA-Z0-9]+$', s):
        return s
    m = re.search(r'BV[a-zA-Z0-9]+', s)
    return m.group(0) if m else None


def run(cmd, timeout=600):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result


def subtitle_mismatch(title: str, transcript: str) -> str | None:
    """
    Detect Bilibili AI-subtitle mismatch (a known Bilibili data bug where
    the returned subtitle belongs to a DIFFERENT video).

    Extracts meaningful keywords from the title (ASCII words + 2+ char
    Chinese tokens) and checks whether any appear in the transcript.
    Returns a reason string if mismatch is likely, else None.
    """
    import re as _re

    if not title or not transcript:
        return None
    stop = {'什么', '如何', '一个', '背后', '教程', '视频', '这个', '那个',
            '这些', '那些', '为什么', '怎么', '里面', '之间'}
    text_lower = transcript.lower()
    # ASCII keywords (RAG, API, LLM, ...)
    ascii_kws = [w.lower() for w in _re.findall(r'[A-Za-z]{2,}', title)
                 if w.lower() not in {'the', 'and', 'for', 'with'}]
    # Chinese keywords: sliding-window bigrams (catches 模型 from 大模型)
    zh_kws = []
    for m in _re.findall(r'[\u4e00-\u9fff]{2,}', title):
        for i in range(len(m) - 1):
            tok = m[i:i+2]
            if tok not in stop and len(tok) == 2:
                zh_kws.append(tok)
    if not ascii_kws and not zh_kws:
        return None
    # Hits per category (Bilibili's AI subs are randomly mismatched, so a
    # single generic hit is not enough — ASCII title terms MUST appear)
    ascii_hits = [kw for kw in ascii_kws if kw in text_lower]
    zh_hits = [kw for kw in zh_kws if kw in text_lower]
    if ascii_kws:
        if ascii_hits and zh_hits:
            return None
        if ascii_hits and not zh_kws:
            return None
    elif zh_kws:
        if len(zh_hits) >= 2:
            return None
    return (f'字幕与标题不匹配（标题关键词 {" ".join((ascii_kws + zh_kws)[:3])} '
            f'未在字幕中出现），疑似 B站 AI 字幕错配')


def fetch_subtitles(bvid: str) -> dict:
    """Try CC subtitles via the bilibili-content skill, with retries.

    Bilibili's player API intermittently fails (empty_subtitle_url etc.);
    retrying usually recovers.
    """
    if not BILI_SKILL_DIR:
        return {"status": "failed", "phase": "skill",
                "message": "bilibili-content skill 未找到"}
    fetch_py = BILI_SKILL_DIR / 'scripts' / 'fetch_subtitle.py'
    last = {"status": "failed", "phase": "retry", "message": "所有重试均失败"}
    for attempt in range(3):
        result = run([sys.executable, str(fetch_py), bvid, '--json'], timeout=120)
        try:
            idx = result.stdout.find('{')
            data = json.loads(result.stdout[idx:]) if idx >= 0 else {}
        except json.JSONDecodeError:
            last = {"status": "failed", "phase": "parse",
                    "message": result.stdout[:200]}
            continue
        if data.get('has_subtitles') is True or data.get('status') == 'success':
            return data
        last = data
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    # Normalize to our JSON shape
    return {
        "status": "failed",
        "phase": last.get('phase', 'subtitle'),
        "message": last.get('message') or last.get('error') or '字幕获取失败',
        "video_id": bvid,
    }


def download_audio(bvid: str, out_wav: Path) -> tuple:
    """Download Bilibili audio via yt-dlp and convert to 16kHz mono WAV.

    Returns (wav_path, duration_sec) or raises on failure.
    """
    url = f'https://www.bilibili.com/video/{bvid}'
    tmp = out_wav.parent / f'{bvid}_src'
    tmp.mkdir(parents=True, exist_ok=True)

    # 1. Download bestaudio
    dl = run([
        'yt-dlp', '--quiet', '--no-warnings', '--no-progress',
        '-f', 'bestaudio/best', '-o', str(tmp / '%(id)s.%(ext)s'), url,
    ], timeout=600)
    if dl.returncode != 0:
        raise RuntimeError(f'yt-dlp download failed: {dl.stderr[:200]}')

    src = None
    for f in tmp.iterdir():
        if f.suffix.lower() in ('.m4a', '.webm', '.mp3', '.aac', '.ogg', '.wav'):
            src = f
            break
    if not src:
        raise RuntimeError('no audio file produced')

    # 2. Convert to 16kHz mono WAV
    conv = tmp / f'{bvid}_conv.wav'
    ff = run([
        'ffmpeg', '-y', '-i', str(src), '-ar', '16000', '-ac', '1',
        '-c:a', 'pcm_s16le', str(conv),
    ], timeout=600)
    if ff.returncode != 0:
        raise RuntimeError(f'ffmpeg failed: {ff.stderr[:200]}')
    if not conv.exists():
        raise RuntimeError('ffmpeg produced no output')

    # Cleanup source (keep only the wav)
    src.unlink(missing_ok=True)
    conv.replace(out_wav)
    return out_wav, 0


def transcribe_audio(wav_path: Path, model: str, device: str,
                     language: str, model_dir: Path, timestamps: bool) -> dict:
    cmd = [
        sys.executable, str(WHISPER_PY),
        '--input', str(wav_path),
        '--model', model,
        '--device', device,
        '--model-dir', str(model_dir),
        '--language', language,
    ]
    if timestamps:
        cmd.append('--timestamps')
    result = run(cmd, timeout=3600)
    try:
        idx = result.stdout.find('{')
        return json.loads(result.stdout[idx:]) if idx >= 0 else {
            "status": "failed", "message": result.stdout[:200]}
    except json.JSONDecodeError:
        return {"status": "failed", "message": result.stdout[:200]}


def verify_subtitle_match(title: str, transcript: str) -> tuple:
    """
    LLM check: does the subtitle content actually belong to this video?

    Bilibili AI subtitles intermittently return mismatched content
    (wrong video's subtitles). Returns (matched: bool, reason: str).
    Skips (True, '') when no API key is available.
    """
    try:
        from youtube_utils import load_env
        load_env()
        import types as _types

        from translate import call_llm, resolve_api_key
    except ImportError:
        return True, ''

    key = resolve_api_key(_types.SimpleNamespace(api_key=None))
    if not key:
        return True, ''

    system = (
        '你是视频字幕质检员。判断给定的"字幕开头"是否与"视频标题"主题一致。'
        '只回答 JSON：{"match": true/false, "reason": "简短中文原因"}。'
    )
    user = (f'视频标题: {title}\n\n'
            f'字幕开头（前 400 字符）:\n{transcript[:400]}')
    try:
        out = call_llm(key, 'https://api.deepseek.com/v1', 'deepseek-chat',
                       system, user, timeout=60)
        import json as _json
        import re as _re
        m = _re.search(r'\{.*\}', out, _re.S)
        if m:
            d = _json.loads(m.group(0))
            return bool(d.get('match', True)), str(d.get('reason', ''))[:100]
    except Exception:
        pass
    return True, ''


def main():
    parser = argparse.ArgumentParser(description='Bilibili adapter')
    parser.add_argument('video', help='BV号 or bilibili URL')
    parser.add_argument('--whisper', action='store_true',
                        help='Transcribe with Whisper when no subtitles')
    parser.add_argument('--force-whisper', action='store_true',
                        help='Always transcribe (skip subtitle check)')
    parser.add_argument('--timestamps', action='store_true')
    parser.add_argument('--no-verify', action='store_true',
                        help='Skip subtitle-vs-title consistency check')
    parser.add_argument('--model', default=os.environ.get('WHISPER_MODEL', 'small'))
    parser.add_argument('--device', default=os.environ.get('WHISPER_DEVICE', 'cpu'))
    parser.add_argument('--language', default='zh')
    parser.add_argument('--model-dir', default='~/.hermes/whisper/models')
    parser.add_argument('--temp-dir', default='~/.hermes/whisper/temp')
    args = parser.parse_args()

    load_bili_env()

    bvid = extract_bvid(args.video)
    if not bvid:
        print(json.dumps({"status": "failed", "phase": "extract",
                          "message": f"无法从输入提取 BV 号: {args.video}"}))
        sys.exit(1)

    # Step 1: CC subtitles (skip if --force-whisper)
    # Step 1: CC subtitles (skip if --force-whisper)
    if not args.force_whisper:
        sub = fetch_subtitles(bvid)
        has_subs = (sub.get('has_subtitles') is True
                    or sub.get('status') == 'success')
        transcript = (sub.get('transcript') or sub.get('subtitles')
                      or sub.get('text') or '') if has_subs else ''
        if has_subs and transcript:
            # Guard against Bilibili AI-subtitle mismatch (a known
            # Bilibili data bug: returned subs belong to another video).
            mismatch = subtitle_mismatch(sub.get('title') or bvid, transcript)
            if mismatch:
                if not args.whisper:
                    print(json.dumps({
                        "status": "needs_confirmation",
                        "video_id": bvid,
                        "message": "⚠ " + mismatch + "。建议用 Whisper 转写。",
                        "detail": mismatch[:150],
                        "next_command": f"--whisper {bvid}",
                        "next_flags": ["--whisper"],
                    }, ensure_ascii=False))
                    return
                transcript = ''  # fall through to Whisper
            else:
                result = {
                    "status": "success",
                    "source": "bilibili_caption",
                    "video_id": bvid,
                    "title": sub.get('title') or bvid,
                    "transcript": transcript,
                    "char_count": len(transcript),
                    "message": "B站字幕已提取",
                }
                print(json.dumps(result, ensure_ascii=False))
                return
        # No subtitles (or API failure): fall through to whisper when requested
        if not args.whisper:
            print(json.dumps({
                "status": "needs_confirmation",
                "video_id": bvid,
                "message": "此 B站视频无可用字幕（或获取失败）。"
                           "是否用 Whisper 转写音频？",
                "detail": sub.get('message', '')[:100],
                "next_command": f"--whisper {bvid}",
                "next_flags": ["--whisper"],
            }))
            return

    # Step 2: Whisper transcription
    # Step 2: Whisper transcription
    model_dir = Path(os.path.expanduser(args.model_dir))
    model_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(os.path.expanduser(args.temp_dir))
    temp_dir.mkdir(parents=True, exist_ok=True)
    wav_path = temp_dir / f'{bvid}.wav'

    eprint('🎧 下载 B站音频...')
    t0 = time.time()
    try:
        download_audio(bvid, wav_path)
    except RuntimeError as e:
        print(json.dumps({"status": "failed", "phase": "download",
                          "message": f"音频下载失败: {str(e)[:200]}"}))
        sys.exit(1)

    eprint('🎙 Whisper 转写中...')
    tr = transcribe_audio(wav_path, args.model, args.device, args.language,
                          model_dir, args.timestamps)
    if tr.get('status') != 'success':
        print(json.dumps({"status": "failed", "phase": "whisper",
                          "message": tr.get('detail') or tr.get('message', '转写失败')[:200]}))
        sys.exit(1)

    text = tr.get('text', '')
    print(json.dumps({
        "status": "success",
        "source": "whisper",
        "video_id": bvid,
        "title": bvid,
        "transcript": text,
        "char_count": len(text),
        "elapsed_sec": round(time.time() - t0, 1),
        "message": "B站 Whisper 转写完成",
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()

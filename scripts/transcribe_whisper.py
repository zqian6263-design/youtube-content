#!/usr/bin/env python3
"""
transcribe_whisper.py — Transcribe audio file using Whisper.

Supports two backends:
  --backend openai        openai-whisper (default, widely compatible)
  --backend faster-whisper  faster-whisper (CTranslate2, ~4x faster, lower VRAM)

Chunked parallel transcription (long videos):
  --chunk-minutes N       Split audio into N-minute chunks. On CPU, chunks are
                          transcribed in parallel across processes (~4-6x speedup
                          on multi-core). On GPU, chunks run sequentially (avoids
                          OOM on very long audio).

Usage:
  python transcribe_whisper.py --input <audio.wav> [--model small] [--device cuda]
      [--language zh] [--model-dir PATH] [--backend openai|faster-whisper]
      [--chunk-minutes 10] [--chunk-workers 4]

Output (JSON to stdout):
  success: {"status":"success","text":"...","language":"...","backend":"...",
            "chunks":N,"elapsed_sec":...}
  failed:  {"status":"failed","detail":"..."}
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# ── Helpers ─────────────────────────────────────────────────────────────

def get_audio_duration(path: str) -> float:
    """Get WAV duration in seconds."""
    try:
        with wave.open(path, 'rb') as w:
            return w.getnframes() / w.getframerate()
    except wave.Error:
        # Fallback: try ffprobe for non-WAV formats
        r = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', path],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
        return 0.0


def split_audio(input_path: str, out_dir: Path, chunk_sec: float, prefix: str):
    """Split audio into chunks with ffmpeg. Returns list of chunk paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = get_audio_duration(input_path)
    n_chunks = max(1, math.ceil(duration / chunk_sec))
    chunks = []
    for i in range(n_chunks):
        start = i * chunk_sec
        out_path = out_dir / f'{prefix}_chunk{i:03d}.wav'
        cmd = [
            'ffmpeg', '-y', '-v', 'error',
            '-i', input_path,
            '-ss', f'{start:.1f}',
            '-t', f'{chunk_sec:.1f}',
            '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le',
            str(out_path)
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not out_path.exists():
            raise RuntimeError(f'ffmpeg chunk {i} failed: {r.stderr[:200]}')
        chunks.append((i, str(out_path)))
    return chunks, n_chunks


# ── Single-chunk transcription (runs in worker process) ────────────────

def _format_segments_timestamps(segments, offset_sec: float = 0.0) -> str:
    """Format segments as [MM:SS] text lines, with optional time offset."""
    lines = []
    for seg in segments:
        text = (seg.get('text') or '').strip()
        if not text:
            continue
        start = offset_sec + float(seg.get('start', 0) or 0)
        m, s = divmod(max(0, int(start)), 60)
        lines.append(f'[{m:02d}:{s:02d}] {text}')
    return '\n'.join(lines)


def _transcribe_file(input_path: str, model_name: str, device: str,
                     language: str, model_dir: str, backend: str,
                     include_timestamps: bool = False) -> dict:
    """Transcribe one audio file (used directly and as process-pool worker)."""
    if backend == 'faster-whisper':
        from faster_whisper import WhisperModel
        model = WhisperModel(
            model_name, device=device, download_root=model_dir, compute_type='int8'
        )
        segments_iter, info = model.transcribe(
            input_path,
            language=(language if language != 'auto' else None),
        )
        segments = list(segments_iter)
        if include_timestamps:
            text = _format_segments_timestamps(segments)
        else:
            text = ' '.join(seg.text.strip() for seg in segments)
        lang = getattr(info, 'language', language or 'auto')
        return {"text": text, "language": lang}

    import whisper
    model = whisper.load_model(model_name, device=device, download_root=model_dir)
    kwargs = {}
    if language and language != 'auto':
        kwargs['language'] = language
    result = model.transcribe(input_path, **kwargs)
    if include_timestamps:
        text = _format_segments_timestamps(result.get('segments') or [])
    else:
        text = result.get('text', '').strip()
    return {
        "text": text,
        "language": result.get('language', language or 'auto'),
    }


def _worker(args):
    """Top-level worker for ProcessPoolExecutor (picklable)."""
    return _transcribe_file(*args)


# ── Chunked transcription ───────────────────────────────────────────────

def transcribe_chunked(args, input_path: str):
    """Split audio and transcribe chunks (parallel on CPU, sequential on GPU)."""
    chunk_sec = max(1.0, float(args.chunk_minutes) * 60)
    duration = get_audio_duration(input_path)
    n_chunks = max(1, math.ceil(duration / chunk_sec))

    include_ts = bool(getattr(args, 'timestamps', False))

    if n_chunks <= 1:
        return _transcribe_file(
            input_path, args.model, args.device, args.language,
            str(args.model_dir), args.backend, include_ts
        ), 1

    eprint(f'🔪 音频 {duration:.0f}s → {n_chunks} 个分块（每个 {args.chunk_minutes} 分钟）')

    tmp = Path(tempfile.mkdtemp(prefix='whisper_chunks_'))
    try:
        chunks, _ = split_audio(input_path, tmp, chunk_sec, 'chunk')

        results = {}
        use_parallel = (args.device == 'cpu' and n_chunks > 1
                        and args.chunk_workers != 1)

        if use_parallel:
            workers = min(args.chunk_workers or os.cpu_count() or 2, n_chunks)
            eprint(f'⚡ 并行转写 ({workers} 进程)...')
            tasks = [
                (path, args.model, args.device, args.language,
                 str(args.model_dir), args.backend, include_ts)
                for _, path in chunks
            ]
            with ProcessPoolExecutor(max_workers=workers) as pool:
                for (idx, _), result in zip(chunks, pool.map(_worker, tasks)):
                    results[idx] = result
        else:
            eprint('🔁 顺序转写分块...')
            for idx, path in chunks:
                results[idx] = _transcribe_file(
                    path, args.model, args.device, args.language,
                    str(args.model_dir), args.backend, include_ts
                )

        # Merge chunks in order
        text_parts = []
        languages = set()
        for idx in sorted(results):
            r = results[idx]
            if r.get('language'):
                languages.add(r['language'])
            text_parts.append(r.get('text', ''))

        if include_ts:
            # Re-stamp each chunk's [MM:SS] with the chunk offset
            merged = []
            for idx in sorted(results):
                r = results[idx]
                for line in r.get('text', '').split('\n'):
                    if not line.strip():
                        continue
                    m = re.match(r'^\[(\d{2}):(\d{2})\]\s*(.*)$', line)
                    if m:
                        mm, ss, content = m.groups()
                        abs_sec = idx * chunk_sec + int(mm) * 60 + int(ss)
                        am, as_ = divmod(int(abs_sec), 60)
                        merged.append(f'[{am:02d}:{as_:02d}] {content}')
                    else:
                        merged.append(line)
            text = '\n'.join(merged)
        else:
            text = '\n\n'.join(p for p in text_parts if p)
        language = languages.pop() if len(languages) == 1 else (args.language or 'auto')

        return {"text": text, "language": language}, n_chunks
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def clean_transcript_text(text: str) -> str:
    """
    Clean up raw Whisper output:
    - drop filler words (呃/嗯/um/uh/you know/i mean) at line starts
    - collapse leading repeated words / short phrases (stammer artifacts)
    - merge consecutive duplicate lines
    - collapse >2 blank lines
    Returns cleaned text.
    """

    fillers = (
        '呃', '嗯', '啊', 'um', 'uh', 'hmm', 'erm',
        'you know', 'i mean', 'sort of', 'kind of',
    )
    out_lines = []
    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            if out_lines and out_lines[-1] != '':
                out_lines.append('')
            continue
        # Strip filler prefixes (e.g. "呃，然后" → "然后"; "Um, so" → "so";
        # "You know, I think" → "I think")
        lower = line.lower()
        # Drop a line that is ONLY filler words (e.g. "呃", "um", "you know")
        if lower in fillers:
            continue
        changed = True
        while changed:
            changed = False
            for f in fillers:
                if lower.startswith(f) and len(line) > len(f):
                    rest = line[len(f):].lstrip(' ，,。.！!？?')
                    if rest:
                        line = rest
                        lower = line.lower()
                        changed = True
        if not line:
            continue
        # Collapse a leading repeated word (stammer): "So so we need" → "So we need";
        # "I I think" → "I think". Word repeated 2+ times at line start.
        lower = line.lower()
        if not _starts_with_timestamp(line):
            line = _collapse_repeated_prefix(line)
        # Merge consecutive duplicates (Whisper often repeats a line)
        if out_lines and out_lines[-1].lower() == line.lower():
            continue
        out_lines.append(line)
    return '\n'.join(out_lines)


def _collapse_repeated_prefix(line: str) -> str:
    """Collapse a leading word or 2-word phrase repeated 2+ times.

    "So so we need"    -> "So we need"
    "I I think that"   -> "I think that"
    "let's go let's go now" -> "let's go now"
    Word counts are compared case-insensitively; the first occurrence's
    original casing is preserved. Returns the line unchanged if no
    repetition is found.
    """
    words = line.split()
    if len(words) < 3:
        return line
    # --- leading repeated single word ---
    w0 = words[0].lower()
    j = 1
    while j < len(words) and words[j].lower() == w0:
        j += 1
    if j >= 2:
        # keep words[j:] but re-join with original spacing style
        return ' '.join(words[:1] + words[j:])
    # --- leading repeated 2-word phrase ---
    if len(words) >= 4 and words[0].lower() == words[2].lower() \
            and words[1].lower() == words[3].lower():
        return ' '.join(words[:2] + words[4:])
    return line


def _starts_with_timestamp(line: str) -> bool:
    """True if the line begins with a [MM:SS] timestamp (already formatted)."""
    return bool(re.match(r'^\s*\[\d{1,2}:\d{2}\]', line))



def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


# ── Entry point ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--model', default='small')
    parser.add_argument('--device', default='auto',
                        help='torch device: auto/cuda/cpu')
    parser.add_argument('--language', default='zh',
                        help='Language code (zh, en, ja, auto). auto = detect')
    parser.add_argument('--model-dir', default='~/.hermes/whisper/models')
    parser.add_argument('--backend', default='openai',
                        choices=['openai', 'faster-whisper'],
                        help='Transcription backend (default: openai)')
    parser.add_argument('--chunk-minutes', type=int, default=0,
                        help='Split audio into N-minute chunks. 0 = no chunking. '
                             'CPU: parallel across processes; GPU: sequential (OOM-safe)')
    parser.add_argument('--chunk-workers', type=int, default=0,
                        help='Max parallel chunk workers on CPU (default: cpu_count)')
    parser.add_argument('--timestamps', action='store_true',
                        help='Prefix each line with [MM:SS] timestamps')
    parser.add_argument('--clean', action='store_true',
                        help='Clean transcript: drop fillers, merge duplicates')
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(json.dumps({"status": "error", "detail": f"File not found: {args.input}"}))
        sys.exit(1)

    model_dir = Path(os.path.expanduser(args.model_dir))
    model_dir.mkdir(parents=True, exist_ok=True)
    args.model_dir = model_dir

    # Resolve device: auto -> cuda if available else cpu
    if args.device == 'auto':
        try:
            import torch
            args.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        except ImportError:
            args.device = 'cpu'

    try:
        t0 = time.time()
        if args.chunk_minutes and args.chunk_minutes > 0:
            result, n_chunks = transcribe_chunked(args, args.input)
        else:
            result = _transcribe_file(
                args.input, args.model, args.device, args.language,
                str(args.model_dir), args.backend
            )
            n_chunks = 1
        if args.clean and isinstance(result, dict) and result.get('text'):
            result['text'] = clean_transcript_text(result['text'])
            result['cleaned'] = True
        result['status'] = 'success'
        result['backend'] = args.backend
        result['chunks'] = n_chunks
        result['elapsed_sec'] = round(time.time() - t0, 1)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({
            "status": "failed",
            "detail": f"Whisper error: {str(e)}",
            "message": "Whisper 转写失败。尝试 --device cpu 或换模型。"
        }))
        sys.exit(1)


if __name__ == '__main__':
    main()

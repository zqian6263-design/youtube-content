#!/usr/bin/env python3
"""
transcribe_whisper.py — Transcribe audio file using Whisper.

Supports two backends:
  --backend openai        openai-whisper (default, widely compatible)
  --backend faster-whisper  faster-whisper (CTranslate2, ~4x faster, lower VRAM)

Usage:
  python transcribe_whisper.py --input <audio.wav> [--model small] [--device cuda]
      [--language zh] [--model-dir PATH] [--backend openai|faster-whisper]

Output (JSON to stdout):
  success: {"status":"success","text":"...","language":"...","backend":"..."}
  failed:  {"status":"failed","detail":"..."}
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def transcribe_openai(args):
    """openai-whisper backend."""
    try:
        import whisper
    except ImportError:
        return {"status": "failed", "detail": "openai-whisper not installed. Run: pip install openai-whisper"}

    model = whisper.load_model(
        args.model,
        device=args.device,
        download_root=str(args.model_dir)
    )

    transcribe_kwargs = {}
    if args.language and args.language != 'auto':
        transcribe_kwargs['language'] = args.language

    result = model.transcribe(str(args.input), **transcribe_kwargs)
    return {
        "status": "success",
        "text": result.get('text', '').strip(),
        "language": result.get('language', args.language or 'auto'),
        "backend": "openai",
    }


def transcribe_faster(args):
    """faster-whisper backend (CTranslate2)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"status": "failed", "detail": "faster-whisper not installed. Run: pip install faster-whisper"}

    # faster-whisper uses int8 compute by default (fast + low VRAM)
    compute_type = "int8"
    model = WhisperModel(
        args.model,
        device=args.device,
        download_root=str(args.model_dir),
        compute_type=compute_type,
    )

    segments_iter, info = model.transcribe(
        str(args.input),
        language=(args.language if args.language != 'auto' else None),
    )

    text_parts = []
    for segment in segments_iter:
        text_parts.append(segment.text.strip())
    text = ' '.join(text_parts)

    return {
        "status": "success",
        "text": text,
        "language": getattr(info, 'language', args.language or 'auto'),
        "backend": "faster-whisper",
    }


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
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(json.dumps({"status": "error", "detail": f"File not found: {args.input}"}))
        sys.exit(1)

    model_dir = Path(os.path.expanduser(args.model_dir))
    model_dir.mkdir(parents=True, exist_ok=True)

    # Resolve device: auto -> cuda if available else cpu
    if args.device == 'auto':
        try:
            import torch
            args.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        except ImportError:
            args.device = 'cpu'

    try:
        t0 = time.time()
        if args.backend == 'faster-whisper':
            result = transcribe_faster(args)
        else:
            result = transcribe_openai(args)
        result['elapsed_sec'] = round(time.time() - t0, 1)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result.get('status') == 'success' else 1)
    except Exception as e:
        print(json.dumps({
            "status": "failed",
            "detail": f"Whisper error: {str(e)}",
            "message": "Whisper 转写失败。尝试 --device cpu 或换模型。"
        }))
        sys.exit(1)


if __name__ == '__main__':
    main()

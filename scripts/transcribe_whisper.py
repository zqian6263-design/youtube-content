#!/usr/bin/env python3
"""
transcribe_whisper.py — Transcribe audio file using Whisper.

Usage:
  python transcribe_whisper.py --input <audio.wav> [--model small] [--device cuda] [--language zh] [--model-dir PATH]
"""

import os, sys, json, time, argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--model', default='small')
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--language', default='zh',
                        help='Language code (zh, en, ja, auto). auto = detect')
    parser.add_argument('--model-dir', default='~/.hermes/whisper/models')
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(json.dumps({"status": "error", "detail": f"File not found: {args.input}"}))
        sys.exit(1)

    model_dir = Path(args.model_dir)
    os.makedirs(model_dir, exist_ok=True)

    try:
        import whisper
    except ImportError:
        print(json.dumps({"status": "error", "detail": "Whisper not installed."}))
        sys.exit(1)

    try:
        t0 = time.time()
        model = whisper.load_model(
            args.model,
            device=args.device,
            download_root=str(model_dir)
        )
        load_time = time.time() - t0

        transcribe_kwargs = {}
        if args.language and args.language != 'auto':
            transcribe_kwargs['language'] = args.language

        t0 = time.time()
        result = model.transcribe(args.input, **transcribe_kwargs)
        transcribe_time = time.time() - t0

        text = result.get('text', '').strip()

        print(json.dumps({
            "status": "success",
            "text": text,
            "duration": result.get('duration', 0),
            "segments_count": len(result.get('segments', [])),
            "char_count": len(text),
            "detected_language": result.get('language', args.language),
            "model": args.model,
            "device": args.device,
            "load_time_seconds": round(load_time, 1),
            "transcribe_time_seconds": round(transcribe_time, 1),
        }, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({"status": "error", "detail": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()

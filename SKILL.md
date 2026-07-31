---
name: youtube-content
description: extract captions/subtitles and transcribe audio from youtube videos for structured summarization. use when the user provides a youtube.com url, youtu.be link, video id, or asks to summarize, extract, organize, outline, or analyze a youtube video. prefer official captions first, then fall back to audio download and whisper transcription when captions are unavailable.
---

# YouTube Content Tool

Extracts content from YouTube videos: official/auto-generated captions first,
Whisper transcription as fallback. Output is a transcript file + metadata JSON.
**Summarization is done by Hermes/AI**, not by the scripts.

## When to use

Use when the user shares a YouTube video URL, a `youtu.be` short link,
a video ID, or asks to summarize/extract/analyze YouTube content.

## Pipeline

Always run `analyze_youtube.py` as the single entry point.

```
analyze_youtube.py
  ├─ 1. fetch_subtitle_youtube.py
  │   ├─ success → transcript.txt → Hermes summarizes
  │   ├─ no captions → status: needs_confirmation → ask user
  │   │   └─ with --whisper/--auto → continue to 2
  │   │   └─ with --force-whisper → skip to 2
  │   └─ API error → status: failed (do NOT suggest Whisper)
  ├─ 2. fetch_audio_youtube.py
  │   ├─ success → continue to 3
  │   └─ fail → report phase (download/ffmpeg)
  └─ 3. transcribe_whisper.py
      success → transcript.txt → Hermes summarizes
```

## Error distinction (important)

The script distinguishes three kinds of failures — do NOT confuse them:

| Caption result | Script outputs | Hermes should |
|---------------|---------------|---------------|
| `status: "error"` | ID/API/network failure | Report `phase` + `message`, do NOT suggest Whisper |
| `status: "no_captions"` | Genuinely no captions | Ask user if they want Whisper (default) or auto-transcribe (`--whisper`/`--auto`) |

## Prerequisites

### Dependencies
```bash
pip install -r requirements.txt
```

Whisper model downloaded on first use (~466MB for small) to
`~/.hermes/whisper/models/`.

### ffmpeg (required for Whisper fallback)
Required for audio conversion to 16kHz mono WAV.
```bash
# Windows
winget install ffmpeg
choco install ffmpeg

# macOS
brew install ffmpeg

# Linux
apt install ffmpeg      # Debian/Ubuntu
dnf install ffmpeg      # Fedora
```

### No API key needed
Unlike the YouTube Data API v3, `youtube-transcript-api` works without
any authentication. Captions are fetched via YouTube's public endpoints.

## Scripts

| Script | Role |
|--------|------|
| `analyze_youtube.py` | **Single entry point.** Modes: default (ask), `--whisper`, `--auto`, `--force-whisper`, `--playlist`. Also `--timestamps`, `--languages`, `--bilingual`, `--whisper-language`, `--device auto`, `--backend`. |
| `fetch_subtitle_youtube.py` | Extract captions via youtube-transcript-api + yt-dlp fallback. Supports bilingual (`--second-language`). |
| `fetch_audio_youtube.py` | Download audio stream via yt-dlp, convert to WAV. |
| `fetch_playlist.py` | List playlist video IDs (for `--playlist` batch mode). |
| `transcribe_whisper.py` | Transcribe with Whisper. Backends: `openai`, `faster-whisper`. |
| `youtube_utils.py` | Shared utilities: ID parsing, .env loading, VTT parsing, GPU detection. |
| `cache.py` | SQLite cache (subtitles + transcripts, 7-day TTL). |
| `tests/test_youtube_utils.py` | 17 unit tests. Run: `python tests/test_youtube_utils.py` |

## Usage Strategy

| Scenario | Recommended Mode | Rationale |
|----------|-----------------|-----------|
| 普通短视频、快速浏览 | `默认` (无 flag) | 字幕优先，最快最省 |
| 教程、讲座、内容重要 | `--auto` | 字幕→Whisper 自动兜底 |
| 字幕明显错位/内容重要 | `--force-whisper` | 跳过字幕，直接从音频转写 |
| 想完全省心 | `--auto` | 字幕→Whisper 自动兜底 |

### Subtitle verification

When subtitles are extracted, the script checks for:
1. **歌词特征** — ♪/♫ 符号或大量短重复句 (auto-generated timestamps often
   cause lyric captions to be misaligned)
2. **内容-标题不匹配** — 技术类标题但字幕无任何技术关键词
3. **字幕过短** — 明显少于正常内容量

If suspicious, the script still returns the transcript but flags the issue.
In `--auto` mode, suspicious subtitles trigger Whisper fallback.

## Usage

```bash
SKILL_DIR = <skill_dir>

# Default: caption only, ask if none (safe)
python SKILL_DIR/scripts/analyze_youtube.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Short link
python SKILL_DIR/scripts/analyze_youtube.py "https://youtu.be/dQw4w9WgXcQ"

# Just video ID
python SKILL_DIR/scripts/analyze_youtube.py "dQw4w9WgXcQ"

# Whisper: use captions; if none, transcribe without asking
python SKILL_DIR/scripts/analyze_youtube.py "URL" --whisper

# Auto: full automatic — captions if available, Whisper if not
python SKILL_DIR/scripts/analyze_youtube.py "URL" --auto

# Force Whisper: skip captions, always download+transcribe
python SKILL_DIR/scripts/analyze_youtube.py "URL" --force-whisper

# With timestamps
python SKILL_DIR/scripts/analyze_youtube.py "URL" --timestamps --auto

# Caption language fallback (try zh, fall back to en)
python SKILL_DIR/scripts/analyze_youtube.py "URL" --languages zh-Hans,zh-Hant,en --auto

# Whisper language override (auto = detect, zh/en/ja = force)
python SKILL_DIR/scripts/analyze_youtube.py "URL" --auto --whisper-language auto

# Use CPU for Whisper (no CUDA)
python SKILL_DIR/scripts/analyze_youtube.py "URL" --force-whisper --device cpu

# Device auto-detection (default): GPU if available, else CPU
python SKILL_DIR/scripts/analyze_youtube.py "URL" --force-whisper --device auto

# Full auto with custom model
python SKILL_DIR/scripts/analyze_youtube.py "URL" --auto --whisper-model base --device auto

# faster-whisper backend (~4x faster): pip install faster-whisper
python SKILL_DIR/scripts/analyze_youtube.py "URL" --auto --backend faster-whisper

# Bilingual output (zh primary + en secondary, timestamp-aligned)
python SKILL_DIR/scripts/analyze_youtube.py "URL" --languages zh-Hans,en --bilingual --timestamps

# Batch process a playlist (first 5 videos)
python SKILL_DIR/scripts/analyze_youtube.py "PLAYLIST_URL" --playlist --max 5

# Private playlist (e.g. Watch Later) needs cookies.txt
python SKILL_DIR/scripts/analyze_youtube.py "PLAYLIST_URL" --playlist --cookies cookies.txt

# Run tests
python SKILL_DIR/tests/test_youtube_utils.py
```

> **缓存**: 字幕和 Whisper 转写结果自动缓存在 `cache.db`（7 天 TTL）。
> 重复分析同一视频会秒回，跳过网络请求和转写。

Output files are saved to `SKILL_DIR/output/`:
- `{video_id}_{title}.txt` — transcript
- `{video_id}_{title}.json` — metadata

## Workflow for Hermes

1. Run `analyze_youtube.py` with the user's URL or video ID (default: conservative mode).
2. Read the JSON output:

   | `status` | Meaning | Hermes action |
   |----------|---------|---------------|
   | `success` | Transcript ready | `read_file` the `transcript_file`, then summarize |
   | `needs_confirmation` | No captions, user needs to decide | Tell user, ask permission, re-run with `--whisper` |
   | `failed` | Error occurred (API/network/whisper) | Report the `phase` and `message` to user |

### Handling "no captions"

**Default behavior** (no special flag):
- Extract captions. If none found, `status: "needs_confirmation"` → ask the user.
- Do NOT auto-start Whisper without user consent. The `next_command` preserves all flags.

**User says yes** — re-run with `--whisper`:
```bash
python SKILL_DIR/scripts/analyze_youtube.py "VIDEO_ID" --whisper
```

**User explicitly wants full automation** (e.g. "完整分析这个视频"):
```bash
python SKILL_DIR/scripts/analyze_youtube.py "VIDEO_ID" --auto
```

**User wants to bypass captions entirely** (e.g. "不要字幕直接转写"):
```bash
python SKILL_DIR/scripts/analyze_youtube.py "VIDEO_ID" --force-whisper
```

### Language handling

Two independent language settings:

| Parameter | Purpose | Example |
|-----------|---------|---------|
| `--languages zh-Hans,en` | Caption language priority (YouTube) | `--languages zh-Hans,en` |
| `--whisper-language auto` | Whisper transcription language | `--whisper-language auto` |

For non-English videos, use `--whisper-language auto` so Whisper auto-detects
the spoken language.

### Chunking (for long transcripts)
If the transcript exceeds ~50K characters, split into overlapping chunks of
~40K chars with ~2K overlap. Summarize each chunk separately, then merge the
summaries into a final result. This prevents context window overflow.

### Default summary format
```
📺 视频标题
━━━━━━━━━━━━━━━━━━
• 要点1
• 要点2
• 要点3
💡 结论
```

### Output formats (user can request)
Key points, quotes with context, chapter summaries, full transcript, blog post.

## Error Handling

| Phase | Meaning | Action |
|-------|---------|--------|
| `extract` | Could not parse video ID from URL | Verify URL format |
| `import` | youtube-transcript-api not installed | Run `pip install -r requirements.txt` |
| `transcript_api` | Transcript API error | Video may be private/deleted/region-locked |
| `no_captions` | No captions available for any language | Suggest Whisper fallback |
| `download` | Audio download failed (yt-dlp error) | Network issue; check URL validity |
| `ffmpeg` | Audio conversion failed | Install ffmpeg |
| `whisper` | Transcription failed (OOM, audio issue) | Try `--device cpu` or smaller model (tiny/base) |

## Env variable overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `WHISPER_MODEL_DIR` | `~/.hermes/whisper/models` | Whisper model storage location |
| `WHISPER_TEMP` | `~/.hermes/whisper/temp` | Audio download temp directory |
| `WHISPER_MODEL` | `small` | Default Whisper model name |
| `WHISPER_DEVICE` | `auto` | torch device: auto/cuda/cpu (auto = GPU if available) |

> `WHISPER_DEVICE` defaults to `auto` — the script detects GPU automatically
> and falls back to CPU when no CUDA is available. No more first-run crashes.

## Notes

- ~90% of YouTube videos have auto-generated captions (instant, zero cost)
- ~10% need Whisper fallback (~4 min GPU time per 42 min video)
- Whisper small model (466MB) stored at `~/.hermes/whisper/models/small.pt`
- Audio download uses yt-dlp (no authentication needed)
- Does NOT handle: age-restricted, private, deleted, or live-stream videos
- Does NOT require YouTube Data API key or OAuth
- Caption quality varies greatly — auto-generated captions for music
  content are often unusable
- Run `python tests/test_youtube_utils.py` after changes to catch regressions

## Comparison with bilibili-content

| Feature | youtube-content | bilibili-content |
|---------|---------------|-----------------|
| Auth needed | No | Bilibili cookies |
| Caption source | youtube-transcript-api + yt-dlp | Bilibili API |
| Audio download | yt-dlp | Bilibili playurl API |
| GPU detection | auto (cuda/cpu) | manual --device |
| Cookie config | Not needed | Required |
| Unit tests | Yes | No |

<div align="center">

# 🎬 YouTube Content Tool

**一键提取 YouTube 字幕 / Whisper 音频转写 / 章节导出 / 双语对照 / 知识库问答 全家桶**

[![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/zqian6263-design/youtube-content/pulls)
[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-blue)](https://github.com/zqian6263-design/youtube-content/actions)
[![Tests](https://img.shields.io/badge/tests-39%20passed-brightgreen)](tests/test_youtube_utils.py)

<p align="center">
  <img src="https://img.shields.io/badge/字幕-✅%20自动提取-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Whisper-🎙%20音频转写-orange?style=for-the-badge" />
  <img src="https://img.shields.io/badge/章节-📑%20自动检测-green?style=for-the-badge" />
  <img src="https://img.shields.io/badge/翻译-🌐%20LLM%20支持-purple?style=for-the-badge" />
</p>

</div>

---

## ✨ 功能特性（定位：视频文本资产层——拿 → 存 → 用）

> YouTube 提供"看"（视频/章节/字幕/机翻）；本工具提供 YouTube **没有**的：
> **导出**（字幕/章节/对照）→ **沉淀**（笔记/归档）→ **使用**（搜索/问答/引用）

| 特性 | 说明 | 差异化 |
|------|------|--------|
| 🎯 **一键提取** | 支持链接 / 短链接 / 视频 ID / 播放列表，自动提取字幕 | 导出为干净文本 |
| 📡 **双引擎兜底** | youtube-transcript-api 优先 → yt-dlp 兜底，云 IP 被封也能用 | 稳定性 |
| 🎙 **Whisper 转写** | 无字幕视频自动下载音频转写；GPU 加速（GTX 1080 实测 12x 实时） | YouTube 不提供 |
| 📑 **原生章节导出** | `--yt-chapters` 导出 YouTube 自带章节为 JSON（YouTube 只能看） | **差异化核心** |
| 📑 **章节检测** | `--chapters` TextTiling 自动分割话题（可选增强，非默认） | 无原生章节时兜底 |
| 🎬 **格式转换** | SRT / VTT / LRC / TXT 一键互转（剪映、Premiere、播放器、歌词） | 导出生态 |
| 🌐 **高质量翻译** | LLM 翻译（DeepSeek/OpenAI），非 YouTube 机翻 | 质量+可导出 |
| 👥 **双语对照** | `--bilingual-translate` 中英双语时间轴对齐交错输出（YouTube 只能看不能导出） | **差异化核心** |
| 🔍 **知识库** | FTS 全文 + 向量语义搜索 + RAG 问答（跨视频，答案带时间戳跳转） | **差异化核心** |
| ⏱ **时间范围** | `--from 10:00 --to 20:00` 只处理指定片段 | 灵活 |
| 💾 **智能缓存** | SQLite 缓存字幕和转写结果，重复分析秒回 | 效率 |
| 🆓 **零配置** | 基础功能无需 API Key、无需 Cookie | 门槛 |

---

## 🚀 快速开始

### 安装

```bash
# 方式 1：克隆仓库
git clone https://github.com/zqian6263-design/youtube-content.git
cd youtube-content
pip install -r requirements.txt

# 方式 2：pip 直接安装（v0.8.0 起支持）
pip install .

# 需要 ffmpeg（Whisper 转写）：
#   Windows: winget install ffmpeg    macOS: brew install ffmpeg    Linux: sudo apt install ffmpeg
```

### 基础用法

```bash
# 📝 提取字幕（最快，推荐）
python scripts/analyze_youtube.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 支持短链接 / 纯 ID / 播放列表
python scripts/analyze_youtube.py "https://youtu.be/dQw4w9WgXcQ"
python scripts/analyze_youtube.py "dQw4w9WgXcQ"
python scripts/analyze_youtube.py "https://www.youtube.com/playlist?list=PLxxx" --playlist --max 10
```

输出文件保存在 `output/`：
- `{视频ID}_{标题}.txt` — 纯文本字幕
- `{视频ID}_{标题}.json` — 元数据（含精确 segments）

---

## 📋 命令参考

| 场景 | 命令 |
|------|------|
| 提取字幕 | `python scripts/analyze_youtube.py "URL"` |
| 无字幕→Whisper 转写 | `python scripts/analyze_youtube.py "URL" --auto` |
| 强制音频转写 | `python scripts/analyze_youtube.py "URL" --force-whisper` |
| 长视频并行加速 | `python scripts/analyze_youtube.py "URL" --force-whisper --chunk-minutes 10 --chunk-workers 4` |
| faster-whisper（4倍速） | `python scripts/analyze_youtube.py "URL" --auto --backend faster-whisper` |
| 章节自动检测 | `python scripts/analyze_youtube.py "URL" --chapters` |
| 转 SRT / VTT / LRC | `python scripts/analyze_youtube.py "URL" --format srt` |
| 翻译成中文 | `export DEEPSEEK_API_KEY=sk-xxx && python scripts/analyze_youtube.py "URL" --translate` |
| 只处理某段 | `python scripts/analyze_youtube.py "URL" --from 10:00 --to 20:00` |
| 双语字幕 | `python scripts/analyze_youtube.py "URL" --languages zh-Hans,en --bilingual --timestamps` |
| 播放列表批量 | `python scripts/analyze_youtube.py "URL" --playlist --max 5` |

### 常用参数

| 参数 | 说明 |
|------|------|
| `--auto` | 有字幕→提取，无字幕→自动转写 |
| `--force-whisper` | 强制走音频转写 |
| `--device auto/cpu/cuda` | Whisper 设备（默认 auto 自动检测） |
| `--backend openai/faster-whisper` | 转写后端 |
| `--chunk-minutes N` | 长视频分块大小（分钟） |
| `--chapters` | 章节检测 |
| `--format srt/vtt/lrc/txt` | 字幕格式转换 |
| `--translate` / `--translate-target` | LLM 翻译及目标语言 |
| `--from/--to` | 时间范围（90、01:30、1:02:30） |
| `--playlist --max N` | 播放列表批量处理 |

---

## 🏗 架构

```
┌─────────────────────────────────────────────────────┐
│                 analyze_youtube.py                  │
│             （统一入口，模式分发）                      │
└──────────────┬──────────────────────────┬───────────┘
               │ 字幕路径                   │ 转写路径
               ▼                           ▼
┌──────────────────────────┐   ┌──────────────────────────┐
│  fetch_subtitle_youtube  │   │  fetch_audio_youtube     │
│  transcript-api → yt-dlp │   │  yt-dlp 下载 + ffmpeg    │
│  双引擎兜底 + 双语        │   │  时间范围下载              │
└────────────┬─────────────┘   └────────────┬─────────────┘
             ▼                              ▼
   ┌──────────────────┐          ┌──────────────────┐
   │  convert_subtitles│          │ transcribe_whisper│
   │  SRT/VTT/LRC/TXT │          │ openai/faster     │
   └────────┬─────────┘          │ 分块并行           │
            ▼                    └────────┬─────────┘
   ┌──────────────────┐                   ▼
   │   translate.py    │◄───────── 字幕/转写结果
   │   LLM 翻译         │
   └──────────────────┘
            ▼
   ┌──────────────────────────────────────────────┐
   │  chapters.py（章节检测）· cache.py（SQLite 缓存） │
   └──────────────────────────────────────────────┘
```

---

## 📝 示例输出

### 章节检测（Judea Pearl 因果推断演讲，2 小时）

```json
{
  "chapters": [
    {"start": 0.0,  "start_ts": "00:00", "title": "graph / model / estimate"},
    {"start": 736.0,"start_ts": "12:16", "title": "effect / data / probability"},
    {"start": 920.0,"start_ts": "15:20", "title": "counterfactual / given / when"}
  ]
}
```

### 翻译（Rick Astley 歌词 → 中文）

```
[00:01] ♪ 我们对爱并不陌生 ♪
[00:18] ♪ 你知道规则，我也一样 ♪
[00:22] ♪ 我想到的是全心全意的承诺 ♪
```

---

## 🧪 测试

```bash
python -m pytest tests/ -v    # 39 个单元测试
python tests/test_youtube_utils.py   # 无需 pytest 也可运行
```

覆盖：URL 解析、安全文件名、VTT 解析、GPU 检测、缓存往返、章节检测、格式转换、翻译分块、时间范围解析。

---

## 🤝 致谢

- 灵感与结构参考 [Air000000/bilibili-content](https://github.com/Air000000/bilibili-content)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — 音频/字幕下载
- [openai-whisper](https://github.com/openai/whisper) — 语音转写
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — 加速转写后端

## 📄 License

[MIT](LICENSE)

<div align="center">

# 🎬 YouTube Content Tool

**一键提取 YouTube 视频字幕 / 音频转写工具**  
无需 API Key · 无需 Cookie · 开箱即用

[![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/zqian6263-design/youtube-content/pulls)
[![Stars](https://img.shields.io/github/stars/zqian6263-design/youtube-content?style=social)](https://github.com/zqian6263-design/youtube-content)

<p align="center">
  <img src="https://img.shields.io/badge/字幕-✅%20自动提取-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Whisper-🎙%20音频转写-orange?style=for-the-badge" />
  <img src="https://img.shields.io/badge/yt--dlp-⬇️%20兜底下载-red?style=for-the-badge" />
</p>

---

**🌏 [English](README.md) | [中文](README.md)**

</div>

---

## ✨ 功能特性

| 特性 | 说明 |
|------|------|
| 🎯 **一键提取** | 支持 YouTube 链接 / youtu.be / 视频 ID，自动提取字幕 |
| 📡 **双引擎兜底** | youtube-transcript-api 优先，yt-dlp 字幕提取作为 fallback |
| 🎙 **Whisper 转写** | 无字幕视频自动下载音频并用 OpenAI Whisper 转写 |
| 🌐 **多语言** | 支持中/英/日等多语言字幕优先级选择 |
| ⏱ **时间戳** | 支持输出带时间轴的全文 |
| 🆓 **零配置** | 无需 API Key、无需 Cookie、无需注册 |
| 🚀 **即装即用** | `pip install` + 一条命令搞定 |

---

## 🚀 快速开始

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/zqian6263-design/youtube-content.git
cd youtube-content

# 2. 安装依赖
pip install -r requirements.txt

# 3. （可选）安装 ffmpeg — Whisper 音频转写需要
# Windows: winget install ffmpeg
# macOS:   brew install ffmpeg
# Linux:   sudo apt install ffmpeg
```

### 基础用法

```bash
# 📝 提取字幕（最快，推荐）
python scripts/analyze_youtube.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 也支持短链接和纯 ID
python scripts/analyze_youtube.py "https://youtu.be/dQw4w9WgXcQ"
python scripts/analyze_youtube.py "dQw4w9WgXcQ"
```

输出文件保存在 `output/` 目录：
- `{视频ID}_{标题}.txt` — 纯文本字幕
- `{视频ID}_{标题}.json` — 元数据

### 🎙 自动转写（无字幕视频）

```bash
# 有字幕→提取，无字幕→Whisper 转写
python scripts/analyze_youtube.py "https://youtu.be/xxx" --auto

# 或者强制从音频转写
python scripts/analyze_youtube.py "https://youtu.be/xxx" --force-whisper
```

---

## 📋 完整命令参考

| 命令 | 说明 |
|------|------|
| `analyze_youtube.py <URL>` | 默认模式：有字幕就提取，没有就询问 |
| `--whisper` | 有字幕→提取，无字幕→自动转写（不询问） |
| `--auto` | 全自动模式：字幕优先，Whisper 兜底 |
| `--force-whisper` | 跳过字幕，直接从音频转写 |
| `--timestamps` | 输出带时间轴的字幕 |
| `--languages zh-Hans,en` | 字幕语言优先级（默认: 中文→英文） |
| `--whisper-language en` | Whisper 转写语言（auto=自动检测） |
| `--device cpu` | 用 CPU 运行 Whisper（无 GPU 时必选） |
| `--whisper-model base` | Whisper 模型大小（tiny/base/small/medium/large） |

### 实用组合

```bash
# 💡 推荐：字幕 + Whisper 自动兜底 + CPU
python scripts/analyze_youtube.py "URL" --auto --device cpu

# 💡 英文视频最佳实践
python scripts/analyze_youtube.py "URL" --auto --languages en --whisper-language auto

# 💡 重要内容：跳过字幕，直接从音频转写
python scripts/analyze_youtube.py "URL" --force-whisper --device cpu
```

---

## 🏗 架构

```
analyze_youtube.py (入口)
  ├─ 1. fetch_subtitle_youtube.py
  │   ├─ 优先: youtube-transcript-api
  │   ├─ 兜底: yt-dlp 字幕提取
  │   └─ 失败 → 进入 Whisper 模式
  ├─ 2. fetch_audio_youtube.py
  │   ├─ yt-dlp 下载最佳音频流
  │   └─ ffmpeg 转 16kHz mono WAV
  └─ 3. transcribe_whisper.py
      └─ Whisper 模型转写 → 输出字幕
```

### 错误处理速查

| 错误阶段 | 含义 | 解决 |
|---------|------|------|
| `no_captions` | 无字幕可用 | 加 `--auto` 用 Whisper |
| `download` | 音频下载失败 | 检查网络/URL |
| `ffmpeg` | 格式转换失败 | `brew install ffmpeg` / `winget install ffmpeg` |
| `whisper` | 转写失败 | 加 `--device cpu` 或用 `--whisper-model base` |

---

## 📺 示例输出

仓库 `output/` 目录包含真实示例：

| 视频 | 长度 | 方式 | 字符数 |
|------|------|------|--------|
| [Rick Astley - Never Gonna Give You Up](https://www.youtube.com/watch?v=dQw4w9WgXcQ) | 3:33 | 字幕提取 | ~2K |
| [Judea Pearl - The Foundations of Causal Inference](https://www.youtube.com/watch?v=nWaM6XmQEmU) | 2:01 | Whisper 转写 | ~79K |

---

## 🔧 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WHISPER_MODEL_DIR` | `~/.hermes/whisper/models` | Whisper 模型存储路径 |
| `WHISPER_TEMP` | `~/.hermes/whisper/temp` | 临时音频文件路径 |
| `WHISPER_MODEL` | `small` | Whisper 模型（tiny/base/small） |
| `WHISPER_DEVICE` | `cuda` | torch 设备（无 GPU 设 cpu） |

---

## 🤝 贡献指南

PR / Issue 都欢迎！提交前请确保：

1. 代码通过 `python scripts/analyze_youtube.py <test_url>` 测试
2. 更新了相关文档

---

## 🙏 致谢

本项目的架构和设计思路参考了 [Air000000/bilibili-content](https://github.com/Air000000/bilibili-content) —— 一个优秀的 B站 视频内容提取工具。感谢原作者的启发。

---

<div align="center">

**如果这个项目对你有帮助，请点个 ⭐ 支持一下！**

</div>

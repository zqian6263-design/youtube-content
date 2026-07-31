# 📦 发布指南

## 构建

```bash
pip install build
python -m build           # 生成 dist/*.whl + *.tar.gz
```

## 发布到 PyPI

需要 PyPI 账号 + token（https://pypi.org/manage/account/token/）：

```bash
pip install twine
twine upload dist/*       # 输入 token 或配置 ~/.pypirc
```

发布后：

```bash
pip install youtube-content   # 任何人可用
```

## 发布 GitHub Release

```bash
# 打标签
git tag v0.16.0
git push origin v0.16.0      # 或经 Git Data API

# 或用 GitHub API 创建 Release（含 Release notes）
# 见 releases 页面：https://github.com/zqian6263-design/youtube-content/releases
```

## 版本历史

| 版本 | 要点 |
|------|------|
| v0.15.0 | Docker + Feishu bot |
| v0.14.0 | RAG 问答 + 知识库归档 |
| v0.13.0 | 全文搜索（FTS5 + 中文 bigram） |
| v0.12.0 | 风控兜底 + Web UI |
| v0.11.0 | LLM 章节标题 + 双语翻译 |
| v0.10.0 | 翻译缓存 + 播放列表断点 + Whisper 时间戳 |
| v0.9.0 | 频道监控 |
| v0.8.0 | 时间范围截取 |
| v0.7.0 | LLM 翻译 |
| v0.6.0 | 字幕格式转换 |
| v0.5.0 | 章节检测 |
| v0.4.0 | 分块并行转写 |
| v0.3.0 | 播放列表/faster-whisper/双语/pip 打包/CI/缓存 |

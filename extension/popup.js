// popup.js — popup 页面逻辑（独立文件，避免扩展 CSP 阻止内联脚本）
const API = 'http://127.0.0.1:8080/api/quick';
const statusEl = document.getElementById('status');

document.getElementById('run').addEventListener('click', async () => {
  let url = document.getElementById('url').value.trim();
  if (!url) {
    // Try current tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const m = tab.url.match(/[?&]v=([\w-]{11})/);
    if (m) url = m[1];
    else { statusEl.textContent = '⚠ 当前页面不是 YouTube 视频页'; statusEl.className = 'err'; return; }
  }
  statusEl.textContent = '⏳ 处理中...';
  statusEl.className = '';
  try {
    const resp = await fetch(`${API}?url=${encodeURIComponent(url)}&max=2500`,
      { signal: AbortSignal.timeout(60000) });
    const data = await resp.json();
    if (data.status === 'success') {
      let out = `🎬 ${data.title || ''}\n\n`;
      if (data.chapters && data.chapters.length) {
        out += '📑 章节:\n' + data.chapters.slice(0, 10).map(c =>
          `  ${c.start_ts} — ${c.title}`).join('\n') + '\n\n';
      }
      if (data.summary) {
        out += '📝 总结:\n' + data.summary + '\n\n📄 原文（展开）:\n';
      }
      out += data.text || '';
      statusEl.textContent = out;
    } else {
      statusEl.textContent = '❌ ' + (data.message || '处理失败');
      statusEl.className = 'err';
    }
  } catch (e) {
    statusEl.textContent = '❌ 无法连接本地服务。\n请先运行 python scripts/webui.py';
    statusEl.className = 'err';
  }
});

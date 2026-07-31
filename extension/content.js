// content.js — inject a "总结此视频" button into YouTube watch pages.
// Talks to the local youtube-content Web UI (http://127.0.0.1:8080).

const API = 'http://127.0.0.1:8080/api/quick';

function getVideoId() {
  // YouTube
  if (window.location.hostname.includes('youtube.com')) {
    const m = window.location.pathname.match(/^\/watch/) && new URLSearchParams(window.location.search).get('v');
    return m || null;
  }
  // Bilibili
  if (window.location.hostname.includes('bilibili.com')) {
    const m = window.location.pathname.match(/BV[a-zA-Z0-9]+/);
    return m ? m[0] : null;
  }
  return null;
}

function makeButton() {
  const btn = document.createElement('button');
  btn.id = 'yt-summary-btn';
  btn.textContent = '📋 总结此视频';
  btn.style.cssText = `
    margin-left: 12px; padding: 8px 14px; border: none; border-radius: 18px;
    background: #1f6feb; color: #fff; font-size: 13px; cursor: pointer;
    font-family: inherit; display: inline-flex; align-items: center; gap: 6px;
  `;
  btn.addEventListener('click', onClick);
  return btn;
}

let panel = null;

function showPanel(html, ok) {
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'yt-summary-panel';
    panel.style.cssText = `
      position: fixed; right: 20px; top: 80px; width: 380px; max-height: 70vh;
      overflow-y: auto; background: #fff; border: 1px solid #d0d7de;
      border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,.2);
      z-index: 99999; padding: 16px; font-family: -apple-system, "Microsoft YaHei", sans-serif;
      font-size: 13px; line-height: 1.6; color: #24292f;
    `;
    panel.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <b>🎬 视频总结</b>
        <span id="yt-summary-close" style="cursor:pointer;font-size:16px">✕</span>
      </div>
      <div id="yt-summary-body"></div>`;
    panel.querySelector('#yt-summary-close').addEventListener('click', () => panel.remove());
    document.body.appendChild(panel);
  }
  const body = panel.querySelector('#yt-summary-body');
  body.innerHTML = html;
  body.style.color = ok ? '#24292f' : '#cf222e';
}

async function onClick() {
  const vid = getVideoId();
  if (!vid) { showPanel('⚠ 无法识别视频 ID', false); return; }

  showPanel('⏳ 正在提取内容，请稍候...<br><small>（需要本地运行: python scripts/webui.py）</small>', true);

  try {
    const resp = await fetch(`${API}?url=${encodeURIComponent(vid)}&max=6000`, { signal: AbortSignal.timeout(120000) });
    const data = await resp.json();
    if (data.status === 'success') {
      let html = `<b>${escapeHtml(data.title || '')}</b><br>`;
      if (data.chapters && data.chapters.length) {
        html += '<hr><b>📑 章节</b><ul style="margin:6px 0;padding-left:20px">';
        data.chapters.slice(0, 10).forEach(c => {
          html += `<li>${escapeHtml(c.start_ts)} — ${escapeHtml(c.title)}</li>`;
        });
        html += '</ul>';
      }
      // LLM summary first (Simplified Chinese), then collapsible raw text
      if (data.summary) {
        html += '<hr><b>📝 总结</b><div style="white-space:pre-wrap;word-break:break-word;margin-top:6px">' +
          escapeHtml(data.summary) + '</div>';
        if (data.text) {
          html += '<details style="margin-top:8px"><summary style="cursor:pointer;color:#57606a">📄 查看字幕原文</summary>' +
            '<div style="white-space:pre-wrap;word-break:break-word;margin-top:6px;color:#57606a">' +
            escapeHtml(data.text) + '</div></details>';
        }
      } else {
        html += '<hr><div style="white-space:pre-wrap;word-break:break-word">' +
          escapeHtml(data.text || '') + '</div>';
      }
      showPanel(html, true);
    } else if (data.status === 'needs_confirmation') {
      showPanel('📭 该视频无可用字幕。', true);
      // Whisper fallback button
      const body = document.querySelector('#yt-summary-body');
      const btn = document.createElement('button');
      btn.textContent = '🎙 用 Whisper 转写（较慢，几分钟）';
      btn.style.cssText = 'margin-top:10px;padding:8px 12px;border:none;border-radius:8px;' +
        'background:#9a6700;color:#fff;font-size:13px;cursor:pointer;width:100%';
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = '⏳ 转写中，请耐心等待（长视频需 10-30 分钟）...';
        try {
          const resp = await fetch(`${API}?url=${encodeURIComponent(vid)}&max=6000&whisper=1`,
            { signal: AbortSignal.timeout(3600000) });
          const wdata = await resp.json();
          if (wdata.status === 'success') {
            let html = `<b>${escapeHtml(wdata.title || '')}</b><br>`;
            if (wdata.chapters && wdata.chapters.length) {
              html += '<hr><b>📑 章节</b><ul style="margin:6px 0;padding-left:20px">';
              wdata.chapters.slice(0, 10).forEach(c => {
                html += `<li>${escapeHtml(c.start_ts)} — ${escapeHtml(c.title)}</li>`;
              });
              html += '</ul>';
            }
            if (wdata.summary) {
              html += '<hr><b>📝 总结</b><div style="white-space:pre-wrap;word-break:break-word;margin-top:6px">' +
                escapeHtml(wdata.summary) + '</div>';
            }
            html += '<hr><div style="white-space:pre-wrap;word-break:break-word">' +
              escapeHtml((wdata.text || '').slice(0, 4000)) + '</div>';
            showPanel(html, true);
          } else {
            showPanel('❌ ' + escapeHtml(wdata.message || '转写失败'), false);
          }
        } catch (e) {
          showPanel('❌ 转写请求失败: ' + escapeHtml(String(e)), false);
        }
      });
      body.appendChild(btn);
      body.appendChild(document.createElement('br'));
      body.appendChild(Object.assign(document.createElement('small'), {
        textContent: '也可在本地运行: analyze_youtube.py "URL" --whisper'
      }));
    } else {
      showPanel('❌ ' + escapeHtml(data.message || '处理失败'), false);
    }
  } catch (e) {
    showPanel('❌ 无法连接本地服务。<br>请先运行 <code>python scripts/webui.py</code> 并确认 8080 端口可用。<br><small>' + escapeHtml(String(e)) + '</small>', false);
  }
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// Inject button near the title (re-check on navigation)
function inject() {
  if (!getVideoId()) return;
  if (document.getElementById('yt-summary-btn')) return;
  let target = null;
  if (window.location.hostname.includes('youtube.com')) {
    const h1 = document.querySelector('h1.ytd-watch-metadata');
    target = h1 ? h1.parentElement : document.querySelector('#title h1');
  } else if (window.location.hostname.includes('bilibili.com')) {
    // Bilibili: title bar next to the video title
    const h1 = document.querySelector('h1.video-title') ||
               document.querySelector('.video-info-title');
    target = h1 ? h1.parentElement : document.querySelector('#viewbox_report');
  }
  if (target) {
    target.appendChild(makeButton());
  }
}

// YouTube is an SPA — observe URL changes
let lastUrl = location.href;
new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    setTimeout(inject, 1200);
  }
}).observe(document, { subtree: true, childList: true });

setTimeout(inject, 1500);

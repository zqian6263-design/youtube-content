@echo off
REM start_webui.bat — 一键启动 youtube-content Web UI（含知识库 /kb）
REM 用法：双击运行，或注册为开机自启（见下方注释）
REM
REM 开机自启（任务计划程序，管理员 PowerShell）：
REM   schtasks /Create /TN "youtube-content-webui" /TR "D:\win\youtube-content\start_webui.bat" /SC ONLOGON /RL LIMITED
REM 删除自启：
REM   schtasks /Delete /TN "youtube-content-webui" /F

cd /d D:\win\youtube-content

echo ============================================
echo   youtube-content Web UI
echo   - 提取/转写:  http://127.0.0.1:8080/
echo   - 知识库:     http://127.0.0.1:8080/kb
echo   Ctrl+C 停止
echo ============================================

python scripts\webui.py --port 8080

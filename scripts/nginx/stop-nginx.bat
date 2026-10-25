@echo off
rem ============================================================
rem  stop-nginx.bat — 停止 nginx（配套 start-nginx.bat；拷到 nginx 安装根后运行）
rem  ⚠️ 本机未安装 nginx → 本脚本【未实测】，交付待部署方验证（与配置/start 一致）。
rem ============================================================
setlocal
cd /d "%~dp0"

nginx.exe -p "%CD%\" -s quit
echo [R5] nginx 已请求平滑退出（-s quit）。若未退出：taskkill /IM nginx.exe /F
exit /b 0

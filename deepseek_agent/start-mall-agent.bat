@echo off
chcp 65001 >nul
rem ===== Mall-Agent (FastAPI :8002) 启动脚本 =====
rem 说明：前端(admin-web/app-h5)的 VITE_AGENT_BASE_URL 均指向 http://localhost:8002，
rem       因此这里用环境变量 PORT=8002 覆盖 mall_agent/config.py 的默认 8001。
cd /d "D:\复合\work\rag\mall\deepseek_agent\llm_backend"
call "..\.venv\Scripts\activate.bat"
set PORT=8002
"..\.venv\Scripts\python.exe" run_mall.py
pause

@echo off
chcp 65001 >nul
rem ===== Start legacy DeepSeek mall-agent (requires mall-portal :8085) =====
cd /d "%~dp0deepseek_agent"
call start-mall-agent.bat

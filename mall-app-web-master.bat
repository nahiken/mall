@echo off
chcp 65001 >nul
rem ===== Start mall-app-web-master (uni-app H5, port 5174) =====
cd /d "%~dp0mall-app-web-master"
if not exist node_modules call npm install
call npm run dev:h5

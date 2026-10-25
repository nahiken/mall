@echo off
chcp 65001 >nul
rem ===== Start mall-admin-web-master (Vue3 admin front-end, port 5173) =====
cd /d "%~dp0mall-admin-web-master"
if not exist node_modules call npm install
call npm run dev

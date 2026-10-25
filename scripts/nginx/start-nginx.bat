@echo off
rem ============================================================
rem  start-nginx.bat — 启动 nginx（阶段14 / R5 反代交付，配套 docs/nginx.conf）
rem
rem  使用（本机未装 nginx → 此文件为模板）：
rem    1) 安装 nginx for Windows，并把它加入 PATH 或用绝对路径；
rem    2) 把仓库 docs/nginx.conf 的 server/http 段按注释替换 nginx 安装目录的
rem       conf/nginx.conf（日志/pid 相对路径以 nginx 根为基准，勿直接覆盖全文件）；
rem    3) 把本文件与 stop-nginx.bat 拷到 nginx 安装根（与 nginx.exe 同级）后运行。
rem
rem  配置与脚本均【未实测】——nginx 未安装，交付待部署方验证。
rem ============================================================
setlocal
cd /d "%~dp0"

echo [R5] 运行目录 = %CD%
echo [R5] 前置：nginx.exe 与本脚本同级；conf/nginx.conf 已就位。

rem 语法自检（失败即退出，不启动）
nginx.exe -t -p "%CD%\" || goto :fail
echo [R5] nginx -t 通过

rem -p 以当前目录为前缀后台启动（日志/pid 落本目录 logs/）
start "mall-nginx" /b nginx.exe -p "%CD%\"
echo [R5] nginx 已启动（后台）。
echo [R5]   冒烟：curl http://localhost/agent-api/api/mall/health  应回 mall-agent ok
echo [R5]   冒烟：curl http://localhost/                应见 admin 首页
echo [R5]   停止：本目录 stop-nginx.bat
exit /b 0

:fail
echo [R5] nginx -t 未通过 —— 配置有误，未启动。
exit /b 1

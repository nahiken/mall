@echo off
rem ============================================================
rem  smoke-nginx.bat — 反代就绪后一键冒烟（阶段14 / R5）
rem  前置：nginx 已按 docs/nginx.conf 起在 :80，各上游服务在跑。
rem  用法：直接运行；全部 PASS 即反代关键点成立。
rem  注意：下方 SSE/WS 测试会真实调用 :8002 上的 mall-agent → 可能发起一次
rem    DeepSeek 调用（有/无 RAG 视服务端 env 而定）。纯连通性也可只看前三项。
rem  ⚠️ 本机未安装 nginx → 本脚本【未实测】，交付待部署方验证（与配置/start 一致）。
rem ============================================================
setlocal
set BASE=http://localhost

echo [R5] 冒烟开始：BASE=%BASE%

echo.
echo --- 1) mall-agent REST 经反代（应有 service 字段 ok）---
curl -s -o nul -w "HTTP %%{http_code}\n" %BASE%/agent-api/api/mall/health

echo.
echo --- 2) mall-agent monitor snapshot 经反代（快照应含 "queue" 键）---
rem 快照返回体键为 "queue":{...}（stats.py snapshot），非 queue_depth —— 后者只出现在
rem /api/mall/queue/status。故断言键取 "queue"。
curl -s %BASE%/agent-api/api/monitor/snapshot | findstr /C:"queue" >nul && echo PASS snapshot 含 queue 键 || echo FAIL snapshot

echo.
echo --- 3) admin dev 首页经根路径 ---
curl -s -o nul -w "HTTP %%{http_code}\n" %BASE%/

echo.
echo --- 4) SSE 响应头（Content-Type 应为 text/event-stream）---
echo 注意：nginx 会吞掉上游 X-Accel-* 控制头（X-Accel-Buffering 只作用于 nginx 本层、
echo      不回传客户端），故此处只看 Content-Type；逐帧不缓冲属传输层行为，无对外头。
curl -s -I --max-time 15 %BASE%/agent-api/api/monitor/stream | findstr /I "event-stream"

echo.
echo --- 5) SSE 单请求流（真实回合，数秒内应收 meta/stage/.../done）---
echo 注意：此步发起一次真实 Agent 调用；--max-time 90 兜底，超时自动断、不阻塞后续。
set CHAT_BODY=%TEMP%\nginx-smoke-chat.json
rem 内联 JSON 在 cmd 下引号必碎 → 写临时 ASCII body 文件，遵循仓库 curl 文件体惯例
rem （--data-binary @file.json，见 AGENT-OBS-HANDOFF / EXPERIENCE-GUIDE）。
echo {"messages":[{"role":"user","content":"hello"}],"user_id":"nginx-smoke","conversation_id":null}> "%CHAT_BODY%"
curl -s -N --max-time 90 -X POST %BASE%/agent-api/api/mall/chat/stream -H "Content-Type: application/json" --data-binary "@%CHAT_BODY%" ^
  | findstr /C:"event:" | findstr /C:"meta" /C:"stage" /C:"done" && echo PASS SSE 收到 meta/stage/done || echo FAIL SSE 事件
del "%CHAT_BODY%" >nul 2>&1

echo.
echo --- 6) WebSocket 升级（应回 HTTP/1.1 101）---
curl -s -o nul -w "HTTP %%{http_code}\n" --max-time 10 -N -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" %BASE%/agent-api/api/mall/chat/ws

echo.
echo [R5] 冒烟结束。1/3 项通过=基本连通；4/5 项通过=SSE 未缓冲逐帧转发；
echo     6 项返回 101=WS Upgrade 正常（curl 握手后立即断开属预期）。
exit /b 0

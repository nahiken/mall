"""mall-agent 容器健康检查（P2-06）。

为什么单独成文件而不是塞进 Dockerfile 的一行 `python -c`：
- 单行写不下 try/except，失败时会在 `docker inspect` 里留一大段 traceback；
- 单独的文件可以在本机直接执行来验证（见 `优化任务清单.md` P2-06 验收），
  不必先 build 镜像。

判定：GET /health 返回 200 且 body 里 status=="ok" 才退出 0；其余（连接失败、
超时、5xx、body 不含 status）退出 1。**故意不把 rag_enabled=false 判成不健康**：
RAG 是可选依赖，关掉它是正常降级状态（见 knowledge_client 的设计）。
"""
import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT_SEC = 3


def check(port: str | None = None) -> tuple[bool, str]:
    port = port or os.getenv("PORT", "8002")
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SEC) as resp:
            status = resp.status
            body = resp.read(2048)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 — 任何异常都只意味"不健康"
        return False, f"{type(exc).__name__}: {exc}"
    if status != 200:
        return False, f"HTTP {status}"
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return False, "response is not JSON"
    if not isinstance(payload, dict):
        return False, "response is not a JSON object"
    if payload.get("status") != "ok":
        return False, f"status={payload.get('status')!r}"
    return True, "ok"


if __name__ == "__main__":
    healthy, detail = check()
    print(("healthy: " if healthy else "unhealthy: ") + detail, flush=True)
    sys.exit(0 if healthy else 1)

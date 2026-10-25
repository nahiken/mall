"""Mall Agent — standalone FastAPI server entry point.

Architecture:
  HTTP / WS → [TaskQueue] → Background Workers → Deepseek API / Java Backend
                           ↓
              [Result Queue] → SSE / WS streaming to client

Workers are started on app startup and run in the background event loop.
"""
import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mall_agent.router import router as mall_router
from mall_agent.monitor import router as monitor_router
from mall_agent.queue import TaskQueue
from mall_agent.worker import start_worker_pool
from mall_agent.db_session import init_db, close_db
from mall_agent.config import HOST, PORT, WORKER_COUNT, DB_CONFIG, QUEUE_BACKEND, CORS_ORIGINS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
)
logger = logging.getLogger("mall_agent")

# The live task-queue instance, set in startup() and closed in shutdown().
_queue = None

# ── app factory ─────────────────────────────────────────────────────────

app = FastAPI(title="Mall Agent API", version="1.0.0")

# P0-02：只允许配置的 Origin 跨域；JWT 走 Authorization 头，不依赖 cookie，
# 故 allow_credentials=False（与 allow_origins=["*"] + credentials 的不安全组合解耦）。
# 生产同源部署时 CORS_ORIGINS 设空串 → 不输出任何 CORS 头。
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(mall_router, prefix="/api/mall")
app.include_router(monitor_router, prefix="/api/monitor")


@app.get("/health")
async def root_health():
    from mall_agent import knowledge_client as kb
    return {"status": "ok", "service": "mall-agent", "rag_enabled": kb.enabled()}


# ── lifecycle ───────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    # Initialize database
    await init_db()

    # P1-06: metrics persistence runs as a background flush coroutine so the
    # event loop never blocks on metrics.jsonl writes. Started before workers.
    from mall_agent.stats import stats as mall_stats
    mall_stats.start_writer()

    # #104: task-queue backend — "redis" uses the Redis Streams bus
    # (multi-process/multi-instance); "memory" (default) is the in-process queue.
    # QUEUE_BACKEND is resolved ONCE here (S6): any other value warns and falls
    # back to memory rather than silently pretending to be "memory".
    global _queue
    backend = QUEUE_BACKEND
    if backend not in ("redis", "memory"):
        logger.warning("Unknown QUEUE_BACKEND=%r; falling back to 'memory'",
                       backend)
        backend = "memory"
    if backend == "redis":
        from mall_agent.redis_queue import RedisTaskQueue

        _queue = RedisTaskQueue()
        await _queue.start()
    else:
        _queue = TaskQueue()
    logger.info("Queue backend: %s",
                "Redis Streams" if backend == "redis" else "in-memory")
    queue = _queue

    # Inject queue into router module so endpoints can use it
    import mall_agent.router as r
    r.task_queue = queue
    r.worker_count = WORKER_COUNT

    import mall_agent.monitor as m
    m.task_queue = queue
    m.worker_count = WORKER_COUNT

    # Start background workers
    worker_tasks, cleanup_task = await start_worker_pool(queue, count=WORKER_COUNT)

    logger.info("=" * 50)
    logger.info("Mall Agent started")
    logger.info("  Workers: %d", WORKER_COUNT)
    logger.info("  Queue:   %s", "Redis Streams (multi-instance)" if backend == "redis" else "in-memory (max 500 pending)")
    logger.info("  LLM:     %s", "Deepseek")
    logger.info("  DB:      %s@%s/%s",
                DB_CONFIG["user"], DB_CONFIG["host"], DB_CONFIG["database"])
    logger.info("  Port:    %d", PORT)
    logger.info("=" * 50)


@app.on_event("shutdown")
async def shutdown():
    # P1-06: stop the metrics writer and flush queued events before exiting.
    from mall_agent.stats import stats as mall_stats
    await mall_stats.aclose()
    # #104: close the queue backend (Redis: poller/reaper + connection).
    if _queue is not None and hasattr(_queue, "aclose"):
        await _queue.aclose()
    await close_db()
    # #105: close the process-lifetime shared HTTP clients (DeepSeek / RAG /
    # Java backend) so the process can exit cleanly without pending conns.
    from mall_agent import service as svc
    from mall_agent import knowledge_client as kb
    from mall_agent import product_api as pa

    await svc.aclose_client()
    await kb.aclose_client()
    await pa.aclose_client()
    logger.info("Mall Agent shutting down")


# ── entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Starting Mall Agent on {HOST}:{PORT} with {WORKER_COUNT} workers")
    uvicorn.run(
        "run_mall:app",
        host=HOST,
        port=PORT,
        reload=False,
        workers=1,
        log_level="info",
    )

"""Monitoring endpoints for mall-agent.

GET /api/monitor/snapshot   -> current process stats (rolling 60s window) +
                               queue depth + DB aggregate counts + rag_enabled
GET /api/monitor/stream     -> SSE heartbeats pushing a snapshot every 2s (used
                               by the admin monitor page as a "connected" signal;
                               the page's primary data is the snapshot poll)
GET/POST /api/monitor/rag   -> read / flip the runtime RAG enablement (see
                               knowledge_client.set_enabled)
GET /api/monitor/loadtests  -> published perf_test runs, newest first, backed by
                               loadtest_store (logs/loadtests). POST publishes a
                               perf_test run payload verbatim (scripts/perf_test.py
                               --publish).
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from .stats import clamp_history_seconds, stats
from .queue import TaskQueue
from . import knowledge_client as kb
from . import loadtest_store
from .monitor_auth import require_monitor

logger = logging.getLogger("mall_agent.monitor")

router = APIRouter()

# Set by run_mall.py on startup (same pattern as mall_agent.router).
task_queue: TaskQueue | None = None
worker_count: int = 4


def _get_queue() -> TaskQueue:
    if task_queue is None:
        raise RuntimeError("TaskQueue not initialised")
    return task_queue


# P1-05: DB aggregate counts are cached so a 2s monitor poll no longer runs two
# ``count(*)`` per snapshot. Concurrent callers share one in-flight query (no
# stampede), and failures are cached for a short while so a down DB isn't
# hammered. TTL is 10–30s per the task card; override with env if needed.
_DB_COUNTS_TTL_SEC = float(os.getenv("MONITOR_DB_COUNTS_TTL_SEC", "15"))
_DB_COUNTS_ERROR_TTL_SEC = min(_DB_COUNTS_TTL_SEC, 5.0)
_db_counts_cache: dict | None = None
_db_counts_cached_at: float = 0.0
_db_counts_error_at: float = 0.0
_db_counts_task: asyncio.Task | None = None


async def _load_db_counts() -> dict | None:
    """Run the two count(*) queries once and update the cache (success or not)."""
    global _db_counts_cache, _db_counts_cached_at, _db_counts_error_at
    try:
        from .db_session import get_session
        from .models import Conversation, Message

        async with get_session()() as session:
            conv_q = select(func.count(Conversation.id))
            msg_q = select(func.count(Message.id))
            conversations = (await session.execute(conv_q)).scalar() or 0
            messages = (await session.execute(msg_q)).scalar() or 0
        _db_counts_cache = {"conversations": conversations, "messages": messages}
        _db_counts_cached_at = time.monotonic()
        _db_counts_error_at = 0.0
        return _db_counts_cache
    except Exception as exc:  # noqa: BLE001 — DB hiccup must not kill the endpoint
        logger.warning("monitor DB counts unavailable: %s", exc)
        _db_counts_error_at = time.monotonic()
        return None


def _db_counts_are_fresh(now: float) -> bool:
    # A recent failure short-circuits retries (serve the stale cache / None)
    # instead of hammering a down database on every 2s poll.
    if _db_counts_error_at > 0 and now - _db_counts_error_at < _DB_COUNTS_ERROR_TTL_SEC:
        return True
    if _db_counts_cache is not None:
        return now - _db_counts_cached_at < _DB_COUNTS_TTL_SEC
    return False


def _clear_db_counts_task(task: asyncio.Task) -> None:
    global _db_counts_task
    if _db_counts_task is task:
        _db_counts_task = None


async def _db_counts() -> dict | None:
    """Aggregate conversation/message counts from MySQL, cached for ~15s.

    None on DB failure. Kept async-shaped: a cold/expired cache awaits the
    shared refresh task; concurrent snapshots await the same task.
    """
    global _db_counts_task
    now = time.monotonic()
    if _db_counts_are_fresh(now):
        return _db_counts_cache
    task = _db_counts_task
    if task is None:
        task = asyncio.create_task(_load_db_counts(), name="monitor-db-counts")
        task.add_done_callback(_clear_db_counts_task)
        _db_counts_task = task
    # shield: one caller disconnecting must not cancel the shared query.
    return await asyncio.shield(task)


async def _snapshot() -> dict:
    q = _get_queue()
    db_counts = await _db_counts()
    payload = stats.snapshot(queue_depth=await q.get_task_count(), workers=worker_count, db_counts=db_counts)
    payload["rag_enabled"] = kb.enabled()
    return payload


@router.get("/snapshot")
async def snapshot(_: None = Depends(require_monitor)) -> dict:
    """Return the current monitoring snapshot (rolling window + totals + DB)."""
    return await _snapshot()


@router.get("/history")
async def history(seconds: int = 300, _: None = Depends(require_monitor)) -> dict:
    """Per-second aggregates over the last N seconds for the chart page.

    Data comes from the in-memory event mirror that accumulates regardless of
    whether a monitor page is open (raw counters appended on every record_*), so
    opening the page shows traffic that happened while nobody was watching.
    ``seconds`` is clamped to METRICS_HISTORY_SECONDS via the single threshold in
    ``stats.clamp_history_seconds``; the response ``seconds`` echoes the clamped
    value actually served so it matches the points' real span.

    Response shape (each point is a 1s bucket with activity, so ``llm_per_sec``
    == that second's successful LLM calls — the instantaneous per-second rate,
    NOT snapshot's 60s-window-average ``qps``):
      {"seconds": int,
       "points":   [{ts, llm_per_sec, tokens_in, tokens_out, llm_errors,
                     tools_calls, tools_errors, tasks_ok, tasks_err,
                     p95_latency_ms}],
       "latency":  [{ts, latency_ms}]}   # worker whole-turn latency, ok+fail
    """
    effective = clamp_history_seconds(seconds)
    return {
        "seconds": effective,
        "points": stats.get_history(effective),
        "latency": stats.get_latency_history(effective),
    }


@router.get("/rag")
async def rag_state(_: None = Depends(require_monitor)) -> dict:
    """Current runtime RAG enablement (true only if a URL is configured AND on)."""
    return {"enabled": kb.enabled()}


class RagSetRequest(BaseModel):
    enabled: bool


@router.post("/rag")
async def rag_set(req: RagSetRequest, _: None = Depends(require_monitor)) -> dict:
    """Flip RAG at runtime; returns the actually-effective state (POST true with
    no RAG_HTTP_URL configured still yields enabled=false)."""
    return {"enabled": kb.set_enabled(req.enabled)}


@router.get("/stream")
async def stream(_: None = Depends(require_monitor)):
    """SSE stream that pushes a snapshot every 2 seconds (heartbeat + live data)."""
    async def event_stream():
        while True:
            try:
                payload = await _snapshot()
                yield f"event: snapshot\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001
                logger.exception("monitor stream iteration failed")
                yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/loadtests")
async def loadtests(_: None = Depends(require_monitor)) -> dict:
    """Published perf-test runs, newest first (see loadtest_store)."""
    return {"items": loadtest_store.list_runs()}


@router.post("/loadtests")
async def loadtests_publish(payload: dict, _: None = Depends(require_monitor)) -> dict:
    """Publish a perf_test run (scripts/perf_test.py --publish posts its run
    JSON here). Body = the perf_test run payload verbatim."""
    try:
        return loadtest_store.publish(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid loadtest payload: {exc}") from exc

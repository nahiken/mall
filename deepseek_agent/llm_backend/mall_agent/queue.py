"""Async in-memory task queue with per-task result streaming.

Architecture:
  submit() → [asyncio.Queue] → worker_loop() → LLM API → publish_event()
                                                          ↓
  subscribe(task_id) ← [per-task asyncio.Queue] ←──────────┘

No Redis dependency. Swappable to Redis-backed by implementing same interface.
"""
import asyncio
import uuid
import time
import logging
from typing import AsyncGenerator

from .config import DEDUP_ENABLED, DEDUP_MAX_TRACKED

logger = logging.getLogger("mall_agent.queue")

MAX_QUEUE_SIZE = 500  # reject if more than 500 tasks pending
TASK_TTL = 600        # auto-cleanup task results after 10 minutes


class TaskQueue:
    """In-memory async queue. Task results evicted after TASK_TTL seconds."""

    def __init__(self):
        self._pending: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._results: dict[str, asyncio.Queue] = {}  # task_id → event queue
        self._tasks: dict[str, dict] = {}              # task_id → metadata
        self._lock = asyncio.Lock()
        # In-flight dedup index: dedup_key → task_id, for tasks still pending or
        # running. A task's key is removed when the task is marked done/failed
        # (see mark_done/mark_failed/_cleanup) so only genuinely-active submits
        # dedup — the in-memory queue cannot replay a finished task's result.
        self._dedup_keys: dict[str, str] = {}

    # ── in-flight dedup (local, single-process; see config.DEDUP_*) ──────

    def dedup_key_for(self, user_id: str, content: str,
                      conversation_id: int | None) -> str | None:
        """Canonical dedup key if dedup is enabled, else None.

        Keyed on (user_id, request conversation_id, last user content). The
        request's conversation_id is used (not the resolved one): two rapid
        identical sends of a *new* conversation's first message both carry
        conversation_id=None and so collide correctly; on an existing
        conversation a retry carries the same id and hits precisely. Content is
        the *last user turn* (the router already isolated it). Scope is
        process-local by design (no shared store): a cross-instance dedup
        belongs to the Redis bus (#104). Returns None when MALL_AGENT_DEDUP=off
        (rollback to always-fresh submits) or content is blank.
        """
        if not DEDUP_ENABLED or not (content or "").strip():
            return None
        return f"u:{user_id}|c:{conversation_id}|{content}"

    async def find_inflight(self, dedup_key: str) -> str | None:
        """Return the active task_id for ``dedup_key``, or None.

        A key exists only while its task is pending/running (removed on
        done/failed/cleanup). Guarded by the queue lock — callers read under it
        in submit(). Eviction keeps the index bounded (LRU-ish: a hit refreshes
        nothing, and the bound clears the whole map rather than leaking).
        """
        async with self._lock:
            # Drop any key whose task is no longer active.
            stale: list[str] = []
            for key, tid in self._dedup_keys.items():
                info = self._tasks.get(tid)
                if info is None or info.get("status") not in ("pending", "running"):
                    stale.append(key)
            for key in stale:
                self._dedup_keys.pop(key, None)
            task_id = self._dedup_keys.get(dedup_key)
            if task_id is None and len(self._dedup_keys) >= DEDUP_MAX_TRACKED:
                # Bound reached without a match — reset rather than grow
                # unbounded (safe: worst case is a few duplicates resubmitted).
                logger.warning(
                    "Dedup index at %d entries; clearing (duplicates may slip through)",
                    DEDUP_MAX_TRACKED,
                )
                self._dedup_keys.clear()
            return task_id

    def _unregister_dedup(self, task_id: str) -> None:
        """Drop any dedup key pointing at ``task_id`` (task no longer active)."""
        for key, tid in list(self._dedup_keys.items()):
            if tid == task_id:
                self._dedup_keys.pop(key, None)

    # ── producer API (called by HTTP handlers) ──────────────────────────

    async def submit(self, messages: list[dict], user_id: str = "1",
                     conversation_id: int | None = None,
                     dedup_key: str | None = None) -> str:
        """Enqueue a new task. Returns immediately with task_id.

        Raises asyncio.QueueFull if queue is at capacity. When ``dedup_key`` is
        given (and dedup enabled) the key is registered against this task_id so
        a later find_inflight for the same key returns it while in flight.
        """
        task_id = uuid.uuid4().hex[:12]
        async with self._lock:
            self._results[task_id] = asyncio.Queue()
            self._tasks[task_id] = {
                "status": "pending",
                "created_at": time.time(),
                "user_id": user_id,
                "conversation_id": conversation_id,
            }
            if dedup_key is not None:
                self._dedup_keys[dedup_key] = task_id
        await self._pending.put({
            "task_id": task_id,
            "messages": messages,
            "user_id": user_id,
            "conversation_id": conversation_id,
        })
        logger.info("Task %s submitted (queue size: %d)", task_id, self._pending.qsize())
        return task_id

    async def get_task_count(self) -> int:
        return self._pending.qsize()

    async def get_task_info(self, task_id: str) -> dict | None:
        info = self._tasks.get(task_id)
        if info:
            return {"task_id": task_id, **info}
        return None

    async def get_all_pending(self) -> list[dict]:
        return [
            {"task_id": tid, **meta}
            for tid, meta in self._tasks.items()
            if meta["status"] == "pending"
        ]

    # ── consumer API (called by background workers) ─────────────────────

    async def get_task(self) -> dict | None:
        """Block until a task is available. Returns None on timeout."""
        try:
            return await asyncio.wait_for(self._pending.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return None

    async def publish_event(self, task_id: str, event: str, data: str):
        """Push a serialised SSE event chunk for this task."""
        async with self._lock:
            q = self._results.get(task_id)
        if q is not None:
            await q.put({"event": event, "data": data})

    async def mark_done(self, task_id: str):
        """Signal that no more events will be produced for this task."""
        async with self._lock:
            info = self._tasks.get(task_id)
            if info:
                info["status"] = "done"
                info["completed_at"] = time.time()
            q = self._results.get(task_id)
            self._unregister_dedup(task_id)
        if q is not None:
            await q.put(None)  # sentinel → subscriber stops

    async def mark_failed(self, task_id: str, reason: str):
        """Mark task as failed and signal subscribers."""
        async with self._lock:
            info = self._tasks.get(task_id)
            if info:
                info["status"] = "failed"
                info["error"] = reason
                info["completed_at"] = time.time()
            q = self._results.get(task_id)
            self._unregister_dedup(task_id)
        if q is not None:
            await q.put(None)

    # ── subscriber API (called by SSE / WebSocket handlers) ─────────────

    async def subscribe(self, task_id: str) -> AsyncGenerator[tuple[str, str], None]:
        """Yield (event_type, data_str) pairs until the task is done.

        Cleanup happens automatically after the last consumer disconnects
        (or after TASK_TTL seconds for abandoned tasks).
        """
        q: asyncio.Queue | None
        async with self._lock:
            q = self._results.get(task_id)

        if q is None:
            return

        try:
            while True:
                item = await asyncio.wait_for(q.get(), timeout=TASK_TTL)
                if item is None:  # sentinel → done
                    return
                yield item["event"], item["data"]
        except asyncio.TimeoutError:
            logger.warning("Task %s subscriber timed out", task_id)
        finally:
            await self._cleanup(task_id)

    async def _cleanup(self, task_id: str):
        """Remove task data to free memory."""
        async with self._lock:
            self._results.pop(task_id, None)
            self._tasks.pop(task_id, None)
            self._unregister_dedup(task_id)

    async def periodic_cleanup(self):
        """Background task to evict stale tasks."""
        while True:
            await asyncio.sleep(120)
            now = time.time()
            async with self._lock:
                stale = [
                    tid for tid, info in self._tasks.items()
                    if now - info.get("created_at", 0) > TASK_TTL
                ]
                for tid in stale:
                    self._results.pop(tid, None)
                    self._tasks.pop(tid, None)
                    self._unregister_dedup(tid)
                if stale:
                    logger.info("Cleaned up %d stale tasks", len(stale))

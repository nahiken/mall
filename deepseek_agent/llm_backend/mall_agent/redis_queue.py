"""Redis Streams task queue — multi-instance backend for mall-agent.

Implements the same public interface as the in-memory ``queue.TaskQueue``
(submit / get_task / publish_event / mark_done / mark_failed / subscribe /
get_task_count / get_task_info / get_all_pending / dedup_key_for /
find_inflight / periodic_cleanup / aclose) so workers, router and monitor stay
backend-agnostic. Select via ``QUEUE_BACKEND=redis`` (config.py); ``memory``
(default) is the rollback.

Why Streams — see PROD-HIGH-CONCURRENCY-DESIGN §4.2:
  - Tasks are one stream ``mall_agent:tasks`` read through a consumer group:
    XREADGROUP delivers each message to exactly one worker (no double
    processing across workers / instances); XACK confirms only after the worker
    finishes. A crashed worker is recovered via **XPENDING + XCLAIM** (the
    6.2+ auto-claim shortcut is unavailable on Redis 5): the reaper polls the
    group's pending list for messages
    idle past CLAIM_IDLE_MS and XCLAIMs them onto its own consumer.
  - SSE/WS events for one task live in an append-only per-task stream
    ``mall_agent:ev:{task_id}``. A subscriber replays it from the start (XRANGE)
    then follows live (XREAD BLOCK) until the terminal ``done``/``failed``
    entry (or SUBSCRIBE_MAX_WAIT seconds — never hang forever). This fixes
    pure-Pub/Sub's "join mid-stream loses events" and, unlike the in-memory
    per-task asyncio.Queue, lets several subscribers each get the full stream
    (no single-consumer race).
  - Cross-instance dedup of identical in-flight submits is an atomic SET NX EX.

In-process wiring (workers must block on both new tasks and reaped tasks):
  get_task() drains an internal asyncio.Queue fed by two background tasks:
    - a poller doing XREADGROUP '>' for brand-new tasks;
    - a reaper doing XPENDING + XCLAIM for tasks whose claiming worker died idle.
  A finished-but-unacked task must never re-run, so mark_done/mark_failed write
  the task's meta to a terminal status **first**, then XACK the task's stream
  message id (tracked in-process as _claimed[task_id]). The reaper double-checks
  terminal state from BOTH the meta hash and the per-task event stream before it
  would re-feed a claimed message.

Re-delivery guarantee is at-least-once (honest boundary). P1-07 adds a worker
lease: while a task is being processed this process writes
``mall_agent:hb:{task_id}`` every HEARTBEAT_INTERVAL_SEC (TTL HEARTBEAT_TTL_SEC).
The reaper only XCLAIMs a pending message when it has been idle past
CLAIM_IDLE_MS **and** its heartbeat key is gone, so a legitimate long task is no
longer stolen from a live worker. A crashed worker stops refreshing and the key
expires within HEARTBEAT_TTL_SEC, after which a peer takes over (still
at-least-once: a terminal state is checked before re-feeding, but a crash
*after* doing side effects can still replay them).

Redis key layout:
  mall_agent:tasks                 the task stream (one consumer group)
  mall_agent:ev:{task_id}          per-task event stream (append-only)
  mall_agent:meta:{task_id}        per-task Hash {status,user_id,
                                   conversation_id,created_at,error}
  mall_agent:dedup:{dedup_key}     SETNX idempotency key → winning task_id
  mall_agent:hb:{task_id}          worker lease heartbeat (TTL 90s, P1-07)

Failure behaviour mirrors the in-memory queue: transport errors are logged and
degrade (return []/None/no-op) so queue plumbing never takes a conversation
down.
"""
import asyncio
import json
import logging
import os
import time
import uuid

from redis.asyncio import Redis, from_url
from redis.exceptions import ResponseError

from .config import DEDUP_ENABLED, REDIS_URL

logger = logging.getLogger("mall_agent.redis_queue")

STREAM_TASKS = "mall_agent:tasks"
STREAM_GROUP = "mall_agent:workers"
EV_PREFIX = "mall_agent:ev:"
META_PREFIX = "mall_agent:meta:"
DEDUP_PREFIX = "mall_agent:dedup:"
HB_PREFIX = "mall_agent:hb:"

# Consumer names must be unique per process (not per queue instance) so the
# group's XACK / XCLAIM can tell which process holds a pending message.
CONSUMER_PREFIX = f"p{os.getpid()}"
# The reaper XCLAIMs onto its own per-process consumer so two instances in one
# host do not share a PEL slot.
REAPER_PREFIX = f"reaper-{os.getpid()}"

KEY_TTL = 600        # ev/meta keys expire after this (subscribers can replay briefly)
DEDUP_TTL = 90       # dedup key lifetime ≈ task round-trip + slack
CLAIM_IDLE_MS = 120_000  # a claim idle this long is a *candidate* for takeover
POLL_BLOCK_MS = 1000     # XREADGROUP block window per poll
LOCAL_QUEUE_MAX = 500    # cap on the in-process hand-off queue
TASK_STREAM_MAXLEN = 2000   # hard cap on the tasks stream (bounded Redis memory)
SUBSCRIBE_MAX_WAIT = 300    # hard cap (s) on subscribe's live-follow phase

# P1-07 worker lease: refresh every 30s with a 90s TTL, so a live worker keeps
# its claim (even for tasks longer than CLAIM_IDLE_MS) and a crashed one is
# take-over eligible within 90s of its last beat.
HEARTBEAT_INTERVAL_SEC = 30
HEARTBEAT_TTL_SEC = 90


class RedisTaskQueue:
    """Task queue over Redis Streams (same interface as queue.TaskQueue)."""

    def __init__(self):
        self._redis: Redis | None = None
        self._local: asyncio.Queue[dict] = asyncio.Queue(maxsize=LOCAL_QUEUE_MAX)
        self._claimed: dict[str, str] = {}   # task_id → stream msg_id (this proc)
        # msg_ids this process has fed to the local queue and not yet acked.
        # Lets the reaper skip messages its OWN worker still holds (no self
        # double-feed), while crash takeover still works across processes.
        self._recent_claimed: set[str] = set()
        self._poller_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        # task_id → heartbeat coroutine (P1-07). One per task this process runs.
        self._leases: dict[str, asyncio.Task] = {}
        self._stopping = False

    # ── claim bookkeeping ────────────────────────────────────────────────

    def _add_claim(self, task_id: str, msg_id: str) -> None:
        self._claimed[task_id] = msg_id
        self._recent_claimed.add(msg_id)

    def _drop_claim(self, task_id: str) -> str | None:
        msg_id = self._claimed.pop(task_id, None)
        if msg_id is not None:
            self._recent_claimed.discard(msg_id)
        return msg_id

    # ── worker lease / heartbeat (P1-07) ─────────────────────────────────

    @staticmethod
    def _hb_key(task_id: str) -> str:
        return f"{HB_PREFIX}{task_id}"

    async def _lease_loop(self, task_id: str) -> None:
        """Refresh this task's heartbeat until cancelled (TTL 90s)."""
        key = self._hb_key(task_id)
        while True:
            try:
                r = await self._r()
                await r.set(key, str(int(time.time())), ex=HEARTBEAT_TTL_SEC)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — heartbeat is best-effort
                logger.warning("heartbeat write failed for %s: %s", task_id, exc)
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

    def _start_lease(self, task_id: str) -> None:
        """Start the heartbeat for a task this process just handed to a worker."""
        if task_id in self._leases:
            return
        self._leases[task_id] = asyncio.create_task(
            self._lease_loop(task_id), name=f"lease-{task_id}")

    async def _stop_lease(self, task_id: str) -> None:
        """Stop the heartbeat and delete the key (terminal path / shutdown)."""
        task = self._leases.pop(task_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("lease task ended with error for %s: %s", task_id, exc)
        try:
            r = await self._r()
            await r.delete(self._hb_key(task_id))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a stale key self-expires in 90s
            pass

    async def _lease_alive(self, r: Redis, task_id: str) -> bool:
        """True while a worker still owns the task (heartbeat key present).

        On a Redis error we answer True: refusing to take over is the safe
        choice (a duplicate run is worse than a delayed one).
        """
        try:
            return bool(await r.exists(self._hb_key(task_id)))
        except Exception:  # noqa: BLE001
            return True

    @staticmethod
    def _is_stale(idle_ms: int, heartbeat_alive: bool) -> bool:
        """Take-over predicate: idle long enough AND no live heartbeat."""
        return idle_ms >= CLAIM_IDLE_MS and not heartbeat_alive

    # ── connection / lifecycle ───────────────────────────────────────────

    async def _r(self) -> Redis:
        if self._redis is None:
            # protocol=2 (RESP2): the local Redis is v5 (no HELLO/RESP3), and
            # redis-py 8 defaults to RESP3 — force RESP2 or the handshake fails.
            self._redis = from_url(
                REDIS_URL, decode_responses=True, protocol=2
            )
            try:
                await self._redis.xgroup_create(
                    STREAM_TASKS, STREAM_GROUP, id="0", mkstream=True
                )
            except ResponseError as exc:
                # "BUSYGROUP ... already exists" — another instance / prior run.
                if "BUSYGROUP" not in str(exc):
                    raise
                pass  # group already exists
        return self._redis

    async def start(self):
        """Start the poller + reaper background tasks (call once at startup)."""
        if self._poller_task is None:
            self._poller_task = asyncio.create_task(
                self._poll_loop(), name="redis-poller")
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(
                self._reap_loop(), name="redis-reaper")

    async def aclose(self) -> None:
        self._stopping = True
        # P1-07: stop heartbeats first so a long task doesn't leave leases behind.
        for task_id in list(self._leases):
            await self._stop_lease(task_id)
        # S7: gather only the tasks that were actually started (both may be
        # None if start() was never called).
        tasks = [t for t in (self._poller_task, self._reaper_task)
                 if t is not None]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ── background feed loops ────────────────────────────────────────────

    async def _poll_loop(self):
        """Feed brand-new tasks (XREADGROUP '>') into the local queue."""
        while not self._stopping:
            try:
                r = await self._r()
                res = await r.xreadgroup(
                    STREAM_GROUP, CONSUMER_PREFIX, {STREAM_TASKS: ">"},
                    count=1, block=POLL_BLOCK_MS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("redis poll failed")
                await asyncio.sleep(1)
                continue
            if not res:
                continue
            _, entries = res[0]
            for msg_id, fields in entries:
                task = self._decode_task(msg_id, fields)
                if task is None:
                    # Unreadable task — ack it so it does not poison the stream.
                    try:
                        await r.xack(STREAM_TASKS, STREAM_GROUP, msg_id)
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                try:
                    self._local.put_nowait(task)
                except asyncio.QueueFull:
                    # P3: local backlog full — do NOT XACK. XACK would remove the
                    # message from the PEL and, under the group's '>' semantics,
                    # it would never be re-delivered → silent task loss. Leaving
                    # it pending lets the reaper (idle timeout) reclaim it later.
                    logger.warning(
                        "local queue full; task %s left pending for reaper takeover",
                        task["task_id"],
                    )
                    continue
                self._add_claim(task["task_id"], msg_id)

    async def _terminal_state(self, r: Redis, task_id: str) -> bool:
        """Double check a claimed task is not already finished.

        P1: returns True if the meta hash is done/failed OR the per-task event
        stream already ends in a done/failed entry — in both cases the reaper
        must XACK (never re-run) instead of re-feeding.
        """
        if not task_id:
            return False
        try:
            meta = await r.hgetall(f"{META_PREFIX}{task_id}")
        except Exception:  # noqa: BLE001
            meta = {}
        if meta.get("status") in ("done", "failed"):
            return True
        try:
            last = await r.xrevrange(f"{EV_PREFIX}{task_id}", max="+", min="-",
                                     count=1)
        except Exception:  # noqa: BLE001
            return False
        return bool(last) and last[0][1].get("event") in ("done", "failed")

    async def _task_id_for_msg(self, r: Redis, msg_id: str) -> str:
        """Read a pending message's task_id ("" when trimmed/unreadable)."""
        try:
            found = await r.xrange(STREAM_TASKS, min=msg_id, max=msg_id, count=1)
        except Exception:  # noqa: BLE001
            return ""
        return str(found[0][1].get("task_id") or "") if found else ""

    async def _reap_loop(self):
        """Re-deliver tasks whose claiming worker died.

        Redis 5-compatible takeover: poll XPENDING for messages idle past
        CLAIM_IDLE_MS, then require the worker heartbeat (P1-07) to be gone before
        XCLAIMing them onto this process's reaper consumer. A task that already
        reached a terminal state (done/failed in its meta hash OR its event
        stream) is NOT re-delivered — its stream message must instead be XACKed so
        it is never re-run (P1 double check). Messages this process itself is
        still holding (_recent_claimed) are skipped so the local reaper never
        double-feeds its own in-flight work.
        """
        while not self._stopping:
            await asyncio.sleep(CLAIM_IDLE_MS / 1000)
            try:
                r = await self._r()
                pending = await r.xpending_range(
                    STREAM_TASKS, STREAM_GROUP, min="-", max="+", count=20
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("redis reap xpending failed")
                continue
            if not pending:
                continue
            idle_ids: list[str] = []
            for p in pending:
                msg_id = p.get("message_id")
                if not msg_id or msg_id in self._recent_claimed:
                    # Already claimed + fed by THIS process (worker may still be
                    # running a legitimate long task) — never steal our own.
                    continue
                # redis-py XPENDING detail reports idle in *milliseconds*
                # (verified: ~1310 for 1.3s), so compare directly to CLAIM_IDLE_MS.
                try:
                    idle_ms = int(p.get("time_since_delivered", 0))
                except (TypeError, ValueError):
                    idle_ms = 0
                if idle_ms < CLAIM_IDLE_MS:
                    continue
                # P1-07: idle alone is not proof of death — require the worker
                # lease (heartbeat) to be gone too, so a >120s task on a live
                # worker is never duplicated.
                task_id = await self._task_id_for_msg(r, msg_id)
                if not task_id:
                    continue
                if await self._lease_alive(r, task_id):
                    continue
                idle_ids.append(msg_id)
            if not idle_ids:
                continue
            try:
                claimed = await r.xclaim(
                    STREAM_TASKS, STREAM_GROUP, REAPER_PREFIX, CLAIM_IDLE_MS,
                    idle_ids, force=True,
                )
            except Exception:  # noqa: BLE001
                logger.exception("redis reap xclaim failed")
                continue
            for msg_id, fields in claimed:
                if not msg_id or not fields:
                    # Message was trimmed out of the stream while pending — its
                    # PEL id is an orphan with no payload to recover. Skip.
                    continue
                task_id = fields.get("task_id", "") or ""
                if await self._terminal_state(r, task_id):
                    # Already finished — ack so it is never re-run.
                    try:
                        await r.xack(STREAM_TASKS, STREAM_GROUP, msg_id)
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                task = self._decode_task(msg_id, fields)
                if task is None:
                    try:
                        await r.xack(STREAM_TASKS, STREAM_GROUP, msg_id)
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                self._add_claim(task["task_id"], msg_id)
                try:
                    self._local.put_nowait(task)
                except asyncio.QueueFull:
                    # P3: don't ack — leave pending for a later reap pass.
                    self._drop_claim(task["task_id"])
                    logger.warning(
                        "local queue full; claimed task %s left pending for a later reap",
                        task["task_id"],
                    )

    @staticmethod
    def _decode_task(msg_id: str, fields: dict) -> dict | None:
        try:
            messages = json.loads(fields.get("messages") or "[]")
        except (TypeError, ValueError):
            messages = []
        conv_raw = fields.get("conversation_id") or ""
        task_id = fields.get("task_id")
        if not task_id:
            return None
        return {
            "task_id": task_id,
            "messages": messages,
            "user_id": fields.get("user_id") or "1",
            "conversation_id": int(conv_raw) if conv_raw.isdigit() else None,
            "_msg_id": msg_id,
        }

    # ── producer API (router) ────────────────────────────────────────────

    def dedup_key_for(self, user_id: str, content: str,
                      conversation_id: int | None) -> str | None:
        """Canonical dedup key, or None when dedup off / blank content."""
        if not DEDUP_ENABLED or not (content or "").strip():
            return None
        return f"{DEDUP_PREFIX}u:{user_id}|c:{conversation_id}|{content}"

    async def find_inflight(self, dedup_key: str) -> str | None:
        """Active task_id for a dedup key (Redis GET), or None."""
        try:
            r = await self._r()
            return await r.get(dedup_key)
        except Exception:  # noqa: BLE001
            logger.exception("Redis dedup GET failed for %s", dedup_key)
            return None

    async def submit(self, messages: list[dict], user_id: str = "1",
                     conversation_id: int | None = None,
                     dedup_key: str | None = None) -> str:
        """Enqueue a task atomically; returns the task_id to run.

        With a dedup_key, submission is an atomic SET NX: whoever wins XADDs
        the task and returns its fresh id; a loser returns the winner's id
        without enqueuing (kills the concurrent double-submit race). Without a
        dedup_key (WS path / dedup off) it always enqueues.
        """
        r = await self._r()
        task_id = uuid.uuid4().hex[:12]
        claimed = True
        if dedup_key is not None:
            try:
                claimed = bool(await r.set(dedup_key, task_id, nx=True, ex=DEDUP_TTL))
            except Exception:  # noqa: BLE001
                logger.exception("Redis dedup SET failed; enqueuing fresh")
                claimed = True
            if not claimed:
                existing = await r.get(dedup_key)
                if existing:
                    logger.info("Redis dedup hit: reuse task %s", existing)
                    return existing
                # Key vanished between the failed SETNX and GET — retry once.
                try:
                    await r.set(dedup_key, task_id, nx=True, ex=DEDUP_TTL)
                except Exception:  # noqa: BLE001
                    pass
        payload = {
            "task_id": task_id,
            "user_id": user_id,
            "conversation_id": str(conversation_id) if conversation_id else "",
            "messages": json.dumps(messages, ensure_ascii=False),
        }
        try:
            # P4: exact MAXLEN bounds the tasks stream so it cannot grow without
            # limit (XACK does not delete stream entries). Exact (not ~) trim is
            # used so xlen is a hard cap; note Redis 5 may still trim an entry
            # that is pending in a consumer group (orphaned PEL id) once 2000
            # newer tasks back up — an accepted extreme-backlog tradeoff.
            await r.xadd(STREAM_TASKS, payload, maxlen=TASK_STREAM_MAXLEN,
                         approximate=False)
        except Exception:  # noqa: BLE001
            logger.exception("XADD failed for task %s", task_id)
            if dedup_key is not None:
                try:
                    await r.delete(dedup_key)
                except Exception:  # noqa: BLE001
                    pass
            raise
        await self._write_meta(task_id, {
            "status": "pending",
            "created_at": str(time.time()),
            "user_id": user_id,
            "conversation_id": str(conversation_id) if conversation_id else "",
            "error": "",
        })
        logger.info("Task %s submitted to Redis (conv=%s)", task_id, conversation_id)
        return task_id

    async def _write_meta(self, task_id: str, meta: dict) -> None:
        try:
            r = await self._r()
            key = f"{META_PREFIX}{task_id}"
            await r.hset(key, mapping=meta)
            await r.expire(key, KEY_TTL)
        except Exception:  # noqa: BLE001
            logger.exception("meta write failed for task %s", task_id)

    # ── consumer API (worker) ────────────────────────────────────────────

    async def get_task(self) -> dict | None:
        """Drain one task from the local hand-off queue, or None on timeout."""
        try:
            task = await asyncio.wait_for(self._local.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return None
        # P1-07: the worker owns the task from here on — start the lease so the
        # reaper won't steal it while the (possibly long) turn runs.
        self._start_lease(task["task_id"])
        return task

    async def publish_event(self, task_id: str, event: str, data: str) -> None:
        """Append one SSE event to the task's stream (best-effort, 3 tries)."""
        r = await self._r()
        key = f"{EV_PREFIX}{task_id}"
        for attempt in range(3):
            try:
                await r.xadd(key, {"event": event, "data": data}, maxlen=500)
                await r.expire(key, KEY_TTL)
                return
            except Exception:  # noqa: BLE001
                logger.exception("publish_event failed (try %d) for %s",
                                 attempt + 1, task_id)
                await asyncio.sleep(0.2)

    async def _terminal(self, task_id: str, event: str, data: str) -> None:
        """Write the terminal SSE event, then XACK the task's stream message.

        P2/S5: the terminal XADD is retried (3 tries, same as publish_event).
        Only if it is durably written do we XACK — otherwise we leave the task
        message in the PEL (un-acked) so the reaper can reclaim / retry it
        instead of the terminal event silently disappearing behind a XACK.
        """
        r = await self._r()
        key = f"{EV_PREFIX}{task_id}"
        wrote = False
        for attempt in range(3):
            try:
                await r.xadd(key, {"event": event, "data": data}, maxlen=500)
                await r.expire(key, KEY_TTL)
                wrote = True
                break
            except Exception:  # noqa: BLE001
                logger.exception("terminal %s event failed (try %d) for %s",
                                 event, attempt + 1, task_id)
                await asyncio.sleep(0.2)
        # P1-07: the task is terminal — stop the heartbeat before dropping the
        # claim so no reaper sees a stale lease.
        await self._stop_lease(task_id)
        # Drop our in-process claim either way (see P1 ordering: meta is already
        # terminal by now, so a reaper reclaiming will XACK, not re-run).
        msg_id = self._drop_claim(task_id)
        if not wrote:
            logger.error(
                "terminal %s event NOT written for %s after retries; leaving "
                "task un-acked so the reaper can recover it", event, task_id)
            return
        if msg_id:
            try:
                await r.xack(STREAM_TASKS, STREAM_GROUP, msg_id)
            except Exception:  # noqa: BLE001
                logger.exception("xack failed for %s", task_id)

    async def mark_done(self, task_id: str) -> None:
        # P1 ordering: set the terminal meta FIRST (so a reaper inspecting an
        # un-acked message sees a finished task), then write the done event and
        # XACK. Never the other way round — that ordering left a window where a
        # done event existed but meta was still pending and the reaper re-ran it.
        await self._write_meta(task_id, {"status": "done", "error": ""})
        await self._terminal(task_id, "done", "[DONE]")

    async def mark_failed(self, task_id: str, reason: str) -> None:
        await self._write_meta(task_id, {"status": "failed", "error": reason})
        await self._terminal(task_id, "failed",
                             json.dumps({"message": reason}, ensure_ascii=False))

    # ── subscriber API (SSE) ─────────────────────────────────────────────

    async def subscribe(self, task_id: str):
        """Yield (event_type, data_str): replay ev:{task_id}, then follow.

        Returns (no events) if the task is unknown / already expired. Stops at
        the terminal ``done``/``failed`` entry, or after SUBSCRIBE_MAX_WAIT
        seconds with no terminal event (S4 — never hang a subscriber forever).
        """
        r = await self._r()
        key = f"{EV_PREFIX}{task_id}"
        last_id = "0"
        try:
            entries = await r.xrange(key, min="-", max="+")
        except Exception:  # noqa: BLE001
            logger.exception("subscribe xrange failed for %s", task_id)
            return
        for msg_id, fields in entries:
            yield fields.get("event", ""), fields.get("data", "")
            if fields.get("event") in ("done", "failed"):
                return
            last_id = msg_id
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SUBSCRIBE_MAX_WAIT
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    "subscribe timed out after %ss without a terminal event for %s",
                    SUBSCRIBE_MAX_WAIT, task_id)
                return
            try:
                res = await r.xread({key: last_id}, count=10,
                                    block=max(1, min(1000, int(remaining * 1000))))
            except Exception:  # noqa: BLE001
                logger.exception("subscribe xread failed for %s", task_id)
                return
            if not res:
                continue
            _, new_entries = res[0]
            for msg_id, fields in new_entries:
                yield fields.get("event", ""), fields.get("data", "")
                last_id = msg_id
                if fields.get("event") in ("done", "failed"):
                    return

    # ── info API (monitor / queue-status) ────────────────────────────────

    async def get_task_count(self) -> int:
        """Real in-flight depth = un-acked (pending) messages, not stream length.

        P4/S2: XACK does not remove entries from the tasks stream, so xlen is
        history and would never drop. XPENDING's count is the group's
        claimed-but-not-finished messages — the true "in-flight / backlog" depth
        the monitor reports as queue_depth.
        """
        try:
            r = await self._r()
            res = await r.xpending(STREAM_TASKS, STREAM_GROUP)
            if isinstance(res, dict):
                # redis-py 8 returns {'pending': N, 'min':…, 'max':…, 'consumers':…}
                return int(res.get("pending", 0) or 0)
            # Older redis-py returned a sequence with the total as [0].
            if res:
                return int(res[0])
            return 0
        except Exception:  # noqa: BLE001
            return 0

    async def get_task_info(self, task_id: str) -> dict | None:
        try:
            r = await self._r()
            meta = await r.hgetall(f"{META_PREFIX}{task_id}")
        except Exception:  # noqa: BLE001
            return None
        if not meta:
            return None
        conv = meta.get("conversation_id") or ""
        return {
            "task_id": task_id,
            "status": meta.get("status", "pending"),
            "user_id": meta.get("user_id", ""),
            "conversation_id": int(conv) if conv.isdigit() else None,
            "created_at": float(meta.get("created_at") or 0),
            "error": meta.get("error", ""),
        }

    async def get_all_pending(self) -> list[dict]:
        # No cheap task-id index in Redis — approximate from the group's pending
        # (un-acked) messages. Good enough for a health/status readout.
        # S3: report the REAL task_id (read back from each message's payload,
        # when it is still in the stream) alongside the stream message_id — the
        # memory backend's shape is {task_id, status, …}.
        try:
            r = await self._r()
            pending = await r.xpending_range(
                STREAM_TASKS, STREAM_GROUP, min="-", max="+", count=20
            )
        except Exception:  # noqa: BLE001
            return []
        out: list[dict] = []
        for p in pending:
            msg_id = str(p.get("message_id", ""))
            task_id = ""
            if msg_id:
                try:
                    found = await r.xrange(STREAM_TASKS, min=msg_id,
                                           max=msg_id, count=1)
                    if found:
                        task_id = str(found[0][1].get("task_id") or "")
                except Exception:  # noqa: BLE001
                    task_id = ""
            out.append({
                "task_id": task_id,          # "" if already consumed/trimmed
                "message_id": msg_id,
                "status": "pending",
            })
        return out

    # ── cleanup ──────────────────────────────────────────────────────────

    async def periodic_cleanup(self):
        """Backward-compatible no-op — reaping runs in its own background task.

        worker.start_worker_pool launches ``queue.periodic_cleanup()`` as a
        long-lived task; for the Redis backend the reaper (started in start())
        already handles crash takeover, so this coroutine just idles.
        """
        while not self._stopping:
            await asyncio.sleep(3600)

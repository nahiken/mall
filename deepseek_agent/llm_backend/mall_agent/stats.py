"""Process-local usage statistics for mall-agent monitoring.

A rolling 60-second window (per-second buckets) plus lifetime counters. The
monitor snapshot endpoint reads this module-level singleton.

Persistence (observability that never changes behaviour):
  - Every record_* call appends one raw event to an in-memory bounded mirror
    ``_events`` (``{"ts": int_seconds, "k": kind, "v": value}``).
  - Only the kinds that ``_replay`` restores at startup — the lifetime counters
    ``llm_ok``/``llm_err``/``tok_in``/``tok_out`` — are also appended to
    ``<METRICS_DIR>/metrics.jsonl``. The other kinds (lat_ms / tool / task /
    task_lat_ms) are per-process live data held by the mirror only, so the file
    can't grow without bound and restart cost is limited to the 4 replayed kinds.
  - ``get_history(seconds)`` aggregates the in-memory mirror per second, so the
    monitor page sees live traffic even when nobody was watching / after a page
    reopen (as long as the process is up). Mirror only — the file is NOT read on
    the hot path.
  - Lifetime counters (cost / total LLM calls & errors) are replayed from the
    file at startup, so they survive a process restart instead of resetting.
  - The mirror is bounded by count (deque maxlen) AND by time: once it grows
    past a threshold it is pruned to ``HISTORY_SECONDS``, so stale events don't
    sit in RAM forever at low traffic while in-window events survive bursts.
  - Only raw counters are stored (never derived QPS/percentiles), so the口径
    stays unambiguous; failures are swallowed but surfaced via a throttled
    warning (observation must never break a conversation).

Thread-safety: mall-agent is async; record_* calls happen from worker coroutines
on the single event loop, so no locking is required. snapshot() runs on the same
loop.

P1-06 — async persistence: once ``start_writer()`` has been called on the app
event loop, ``_append`` only formats a line and hands it to an ``asyncio.Queue``;
a background coroutine batches lines (flush every ``METRICS_FLUSH_INTERVAL_SEC``
seconds or every ``METRICS_FLUSH_BATCH`` lines) and writes them with one file
handle per batch. The event loop therefore never blocks on disk. Shutdown calls
``aclose()``, which stops the writer and flushes anything still queued. If the
writer was never started (unit tests, CLI, threads) ``_append`` falls back to the
old synchronous write, so behaviour and replayability are unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections import deque
from pathlib import Path

from .config import (
    DS_PRICE_INPUT_RMB_PER_M,
    DS_PRICE_OUTPUT_RMB_PER_M,
)

logger = logging.getLogger("mall_agent.stats")

WINDOW_SECONDS = 60
# How far back /history serves. The in-memory mirror is also pruned to roughly
# this horizon (see _MIRROR_PRUNE_* below) once it grows large.
HISTORY_SECONDS = int(os.getenv("METRICS_HISTORY_SECONDS", "3600"))
METRICS_DIR = os.getenv("METRICS_DIR", "logs")

# Only these kinds are replayed from disk at startup, so only they are ever
# written to the metrics file. Everything else is per-process live data held by
# the in-memory mirror (keeps the file small; restart restores lifetime only).
_REPLAYED_KINDS = frozenset(("llm_ok", "llm_err", "tok_in", "tok_out"))

# Mirror bounds: a count cap (deque maxlen, emergency backstop under an extreme
# sustained burst) plus a time-prune (see MallStats._prune_events) that drops
# events older than HISTORY_SECONDS once the front of the mirror ages past it.
_EVENTS_MAXLEN = 1_000_000
_MIRROR_PRUNE_EVERY_SEC = 30        # at most one prune pass per 30s

# P1-06 async metrics writer: flush cadence and the bounded in-memory queue.
_FLUSH_INTERVAL_SEC = float(os.getenv("METRICS_FLUSH_INTERVAL_SEC", "1.0"))
_FLUSH_BATCH = max(1, int(os.getenv("METRICS_FLUSH_BATCH", "100")))
# Bounded so a broken (e.g. read-only) disk can't grow RAM without limit; when
# the queue is full the batch is simply written synchronously (backpressure).
_QUEUE_MAXLEN = 10_000

_last_warn_ts = 0.0


def _throttled_warn(message: str) -> None:
    """Log a warning at most ~once/30s so a broken metrics sink stays observable
    without spamming. Does NOT change the swallow semantics."""
    global _last_warn_ts
    now = time.monotonic()
    if now - _last_warn_ts > 30.0:
        _last_warn_ts = now
        logger.warning(message)


def clamp_history_seconds(seconds: int | float) -> int:
    """Clamp a /history ``seconds`` request to the supported retention window.

    Single source of truth for the retention threshold so the monitor endpoint
    can echo the clamped value it actually served.
    """
    return max(1, min(int(seconds), HISTORY_SECONDS))


class _PerSecondBucket:
    __slots__ = ("ts", "llm_calls", "llm_errors", "tokens_in", "tokens_out",
                 "tools_calls", "tools_errors", "task_ok", "task_err")

    def __init__(self, ts: float) -> None:
        self.ts = ts
        self.llm_calls = 0
        self.llm_errors = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.tools_calls = 0
        self.tools_errors = 0
        self.task_ok = 0
        self.task_err = 0


class MallStats:
    def __init__(self) -> None:
        self._buckets: deque[_PerSecondBucket] = deque()
        self._latency_samples: deque[tuple[float, float]] = deque()  # (ts, ms)
        self._lifetime_cost_rmb = 0.0
        self._lifetime_llm_calls = 0
        self._lifetime_llm_errors = 0
        self._active_trials = 0
        self._started_at = time.time()
        self._last_bucket_ts = 0.0
        self._events: deque[dict] = deque(maxlen=_EVENTS_MAXLEN)
        self._last_prune_ts = 0.0
        self._metrics_path = Path(METRICS_DIR) / "metrics.jsonl"
        # P1-06: async writer state. None until start_writer() runs on the app
        # loop; while None, _append keeps the legacy synchronous behaviour.
        self._write_queue: asyncio.Queue[str] | None = None
        self._writer_task: asyncio.Task | None = None
        self._replay()

    # ---- persistence (best-effort; observation never breaks business) ------

    def _append(self, key: str, value: int | float) -> None:
        """Record one raw event.

        Every event goes to the bounded mirror. Only kinds in ``_REPLAYED_KINDS``
        (the lifetime counters restored at startup) are also appended to
        metrics.jsonl; the rest are per-process live data and never touch disk.
        """
        line = {"ts": int(time.time()), "k": key, "v": value}
        self._events.append(line)
        self._prune_events(now=int(line["ts"]))
        if key not in _REPLAYED_KINDS:
            return
        payload = json.dumps(line, ensure_ascii=False) + "\n"
        queue = self._write_queue
        if queue is not None:
            try:
                queue.put_nowait(payload)
                return
            except asyncio.QueueFull:
                # Disk/writer is behind: fall back to a direct write for this
                # batch rather than dropping the event or blocking the loop.
                _throttled_warn("metrics write queue full; writing synchronously")
        self._write_lines([payload])

    # ---- async writer (P1-06) ---------------------------------------------

    def start_writer(self) -> None:
        """Start the background flush coroutine on the running event loop.

        Idempotent; called from the FastAPI startup hook. Must run on the same
        loop that serves conversations, because record_* uses ``put_nowait``.
        """
        if self._writer_task is not None:
            return
        self._write_queue = asyncio.Queue(maxsize=_QUEUE_MAXLEN)
        self._writer_task = asyncio.create_task(
            self._flush_loop(), name="mall-agent-metrics-writer")

    async def _flush_loop(self) -> None:
        """Drain the queue: at most one second (or ``_FLUSH_BATCH`` lines) of lag."""
        assert self._write_queue is not None
        queue = self._write_queue
        loop = asyncio.get_running_loop()
        batch: list[str] = []
        try:
            while True:
                batch.append(await queue.get())
                deadline = loop.time() + _FLUSH_INTERVAL_SEC
                while len(batch) < _FLUSH_BATCH:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        batch.append(await asyncio.wait_for(queue.get(), remaining))
                    except asyncio.TimeoutError:
                        break
                self._write_lines(batch)
                for _ in batch:
                    queue.task_done()
                batch = []
        except asyncio.CancelledError:
            # Shutdown may interrupt between a get() and the write; flush what we
            # already took so no event is lost, then re-raise.
            self._write_lines(batch)
            for _ in batch:
                queue.task_done()
            raise

    def _write_lines(self, lines: list[str]) -> None:
        """Append a batch with a single open/write; never raises at the caller."""
        if not lines:
            return
        try:
            self._metrics_path.parent.mkdir(parents=True, exist_ok=True)
            with self._metrics_path.open("a", encoding="utf-8") as handle:
                handle.write("".join(lines))
        except OSError as exc:
            # Disk trouble must never break a conversation — but stay observable.
            _throttled_warn(f"metrics file append failed: {exc}")

    async def aclose(self) -> None:
        """Stop the writer and flush queued lines so shutdown loses nothing."""
        task, self._writer_task = self._writer_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                _throttled_warn(f"metrics writer stopped with error: {exc}")
        queue, self._write_queue = self._write_queue, None
        pending: list[str] = []
        if queue is not None:
            while True:
                try:
                    pending.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
        self._write_lines(pending)

    def _prune_events(self, now: int) -> None:
        """Time-bounds the mirror so stale events don't sit in RAM at low traffic.

        Events are appended oldest-first, so dropping from the front anything
        older than HISTORY_SECONDS keeps the mirror bounded by time in addition
        to its deque maxlen. The pass runs at most every ``_MIRROR_PRUNE_EVERY_SEC``
        seconds and short-circuits on an in-window front, so the hot path stays
        O(1) (a couple of compares per append) even when there is nothing to drop.
        """
        if now - self._last_prune_ts < _MIRROR_PRUNE_EVERY_SEC:
            return
        if not self._events:
            return
        cutoff = now - HISTORY_SECONDS
        if int(self._events[0].get("ts", 0)) >= cutoff:
            return  # oldest event is still in-window; nothing stale at the front
        self._last_prune_ts = now
        while self._events and int(self._events[0].get("ts", 0)) < cutoff:
            self._events.popleft()

    def _replay(self) -> None:
        """Replay the metrics file so lifetime cost/calls survive a restart."""
        try:
            if not self._metrics_path.exists():
                return
            with self._metrics_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        line = json.loads(raw)
                        key, value = line.get("k"), line.get("v")
                        if key == "llm_ok":
                            self._lifetime_llm_calls += int(value or 0)
                        elif key == "llm_err":
                            self._lifetime_llm_errors += int(value or 0)
                        elif key == "tok_in":
                            self._lifetime_cost_rmb += (int(value or 0) / 1_000_000) * DS_PRICE_INPUT_RMB_PER_M
                        elif key == "tok_out":
                            self._lifetime_cost_rmb += (int(value or 0) / 1_000_000) * DS_PRICE_OUTPUT_RMB_PER_M
                    except (ValueError, TypeError, AttributeError):
                        # A single malformed/non-numeric/non-dict line (e.g. valid
                        # JSON but {"k":"llm_ok","v":"bad"}) must never crash
                        # replay — skip it and keep going.
                        continue
        except OSError as exc:
            _throttled_warn(f"metrics file replay failed: {exc}")

    # ---- internals --------------------------------------------------------

    def _bucket(self) -> _PerSecondBucket:
        now = time.time()
        if self._last_bucket_ts == 0 or now - self._last_bucket_ts >= 1.0:
            self._last_bucket_ts = int(now)
            self._buckets.append(_PerSecondBucket(self._last_bucket_ts))
        bucket = self._buckets[-1]
        # Evict buckets older than the window.
        cutoff = now - WINDOW_SECONDS
        while self._buckets and self._buckets[0].ts < cutoff:
            self._buckets.popleft()
        return bucket

    def _window_agg(self) -> dict:
        bucket = self._bucket()  # ensures at least one live bucket exists
        agg = {
            "llm_calls": 0, "llm_errors": 0,
            "tokens_in": 0, "tokens_out": 0,
            "tools_calls": 0, "tools_errors": 0,
            "task_ok": 0, "task_err": 0,
        }
        for b in self._buckets:
            agg["llm_calls"] += b.llm_calls
            agg["llm_errors"] += b.llm_errors
            agg["tokens_in"] += b.tokens_in
            agg["tokens_out"] += b.tokens_out
            agg["tools_calls"] += b.tools_calls
            agg["tools_errors"] += b.tools_errors
            agg["task_ok"] += b.task_ok
            agg["task_err"] += b.task_err
        return agg

    # ---- recording (called from workers / service) ------------------------

    def record_llm(self, ok: bool, tokens_in: int = 0, tokens_out: int = 0,
                   latency_ms: float | None = None) -> None:
        b = self._bucket()
        if ok:
            b.llm_calls += 1
            b.tokens_in += int(tokens_in or 0)
            b.tokens_out += int(tokens_out or 0)
            self._lifetime_llm_calls += 1
            self._lifetime_cost_rmb += (
                (int(tokens_in or 0) / 1_000_000) * DS_PRICE_INPUT_RMB_PER_M
                + (int(tokens_out or 0) / 1_000_000) * DS_PRICE_OUTPUT_RMB_PER_M
            )
            self._append("llm_ok", 1)
            self._append("tok_in", int(tokens_in or 0))
            self._append("tok_out", int(tokens_out or 0))
        else:
            b.llm_errors += 1
            self._lifetime_llm_errors += 1
            self._append("llm_err", 1)
        if latency_ms is not None:
            now = time.time()
            self._latency_samples.append((now, float(latency_ms)))
            cutoff = now - WINDOW_SECONDS
            while self._latency_samples and self._latency_samples[0][0] < cutoff:
                self._latency_samples.popleft()
            self._append("lat_ms", float(latency_ms))

    def record_tool(self, ok: bool) -> None:
        b = self._bucket()
        if ok:
            b.tools_calls += 1
            self._append("tool_ok", 1)
        else:
            b.tools_errors += 1
            self._append("tool_err", 1)

    def record_task(self, ok: bool) -> None:
        b = self._bucket()
        if ok:
            b.task_ok += 1
            self._append("task_ok", 1)
        else:
            b.task_err += 1
            self._append("task_err", 1)

    def record_task_latency(self, latency_ms: float) -> None:
        """Record a whole-turn worker latency sample.

        Recorded for both success and failure paths (worker finally block), so
        latency history is not biased to successful turns only.
        """
        self._append("task_lat_ms", round(float(latency_ms), 1))

    def inc_active(self) -> None:
        self._active_trials += 1

    def dec_active(self) -> None:
        if self._active_trials > 0:
            self._active_trials -= 1

    # ---- snapshot ---------------------------------------------------------

    def p95_total_ms(self) -> float:
        if not self._latency_samples:
            return 0.0
        vals = sorted(v for _, v in self._latency_samples)
        idx = min(len(vals) - 1, math.ceil(len(vals) * 0.95) - 1)
        return round(float(vals[idx]), 1)

    def snapshot(self, queue_depth: int, workers: int,
                 db_counts: dict | None = None) -> dict:
        agg = self._window_agg()
        return {
            "ts": int(time.time() * 1000),
            "uptime_s": round(time.time() - self._started_at, 1),
            "qps": round(agg["llm_calls"] / WINDOW_SECONDS, 4),
            "llm_calls": agg["llm_calls"],
            "llm_errors": agg["llm_errors"],
            "tokens": {"in": agg["tokens_in"], "out": agg["tokens_out"]},
            "cost_rmb": round(self._lifetime_cost_rmb, 6),
            "lifetime_llm_calls": self._lifetime_llm_calls,
            "tools": {"calls": agg["tools_calls"], "errors": agg["tools_errors"]},
            "permission": {"blocked": 0},
            "active_trials": self._active_trials,
            "latency": {"p95_total_ms": self.p95_total_ms()},
            "tasks": {"ok": agg["task_ok"], "err": agg["task_err"]},
            "queue": {"depth": queue_depth, "workers": workers},
            "db": db_counts,
        }


    # ---- history (served by GET /api/monitor/history) ----------------------

    def get_history(self, seconds: int = 300) -> list[dict]:
        """Per-second aggregates over the last ``seconds`` (bounded & pruned).

        Aggregated from the in-memory event mirror (live traffic kept even when
        no monitor page is open), NOT from the file — the file is only the
        restart-persistence layer. ``seconds`` is clamped to ``HISTORY_SECONDS``
        so an unbounded request can't exhaust memory.

        Each point is one 1-second bucket that had activity. Because a bucket is
        exactly 1s wide, ``llm_per_sec`` equals the number of successful LLM
        calls in that second (the instantaneous per-second rate) — it is NOT the
        60s-window-average ``qps`` of the snapshot endpoint, hence the distinct
        name. Final point shape:
          {ts, llm_per_sec, tokens_in, tokens_out, llm_errors, tools_calls,
           tools_errors, tasks_ok, tasks_err, p95_latency_ms}
        """
        seconds = clamp_history_seconds(seconds)
        cutoff = int(time.time()) - seconds
        # kind -> column key mapping for raw events.
        counters = {
            "llm_ok": "llm_calls",
            "llm_err": "llm_errors",
            "tok_in": "tokens_in",
            "tok_out": "tokens_out",
            "tool_ok": "tools_calls",
            "tool_err": "tools_errors",
            "task_ok": "tasks_ok",
            "task_err": "tasks_err",
        }
        buckets: dict[int, dict] = {}
        lat_by_ts: dict[int, list[float]] = {}
        for event in self._events:
            ts = int(event.get("ts", 0))
            if ts < cutoff:
                continue
            key = event.get("k")
            bucket = buckets.setdefault(ts, {"ts": ts, "llm_calls": 0,
                                             "tokens_in": 0, "tokens_out": 0,
                                             "llm_errors": 0, "tools_calls": 0,
                                             "tools_errors": 0, "tasks_ok": 0,
                                             "tasks_err": 0})
            if key == "lat_ms":
                lat_by_ts.setdefault(ts, []).append(float(event.get("v", 0)))
            elif key in counters:
                col = counters[key]
                bucket[col] += int(event.get("v", 0))
        out: list[dict] = []
        for ts in sorted(buckets):
            bucket = buckets[ts]
            lat_sample = lat_by_ts.get(ts, [])
            p95 = 0.0
            if lat_sample:
                lat_sample.sort()
                p95 = lat_sample[min(len(lat_sample) - 1,
                                     math.ceil(len(lat_sample) * 0.95) - 1)]
            out.append({
                "ts": ts,
                "llm_per_sec": bucket["llm_calls"],
                "tokens_in": bucket["tokens_in"],
                "tokens_out": bucket["tokens_out"],
                "llm_errors": bucket["llm_errors"],
                "tools_calls": bucket["tools_calls"],
                "tools_errors": bucket["tools_errors"],
                "tasks_ok": bucket["tasks_ok"],
                "tasks_err": bucket["tasks_err"],
                "p95_latency_ms": round(p95, 1),
            })
        return out

    def get_latency_history(self, seconds: int = 300) -> list[dict]:
        """Raw whole-turn latency samples (worker task, success+failure) for load tests."""
        seconds = clamp_history_seconds(seconds)
        cutoff = int(time.time()) - seconds
        out: list[dict] = []
        for event in self._events:
            ts = int(event.get("ts", 0))
            if ts < cutoff or event.get("k") != "task_lat_ms":
                continue
            out.append({"ts": ts, "latency_ms": round(float(event.get("v", 0)), 1)})
        return out


# Process-wide singleton.
stats = MallStats()

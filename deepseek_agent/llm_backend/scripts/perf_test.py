"""R6 压测脚本 —— Mall Agent 并发压测 + 有/无 RAG 对比（阶段14 / R6）

Drives ``POST /api/mall/chat/stream`` (single-request SSE) with **chat-type**
messages so each round takes the kb_search (RAG) branch when enabled, then the
DeepSeek streaming leg. Reports QPS / latency percentiles / success rate per
concurrency level, plus server-side token throughput from /api/monitor/history.

WHY chat-type only — the RAG toggle only affects the ``intent=="chat"`` leg
(``service.py``: ``kb.search`` injects snippets into the chat system prompt).
recommend (商品检索) never touches RAG, so 有/无 RAG 对比必须打在 chat 消息上。
无 RAG ≠ 无 LLM：chat 两条腿都走 DeepSeek，关 RAG 只是跳过知识库检索注入。

Server architecture being measured (grounded in the real code):
  - one uvicorn process (workers=1), a single asyncio event loop;
  - WORKER_COUNT (=4) background coroutines pull tasks from an in-memory
    TaskQueue (asyncio.Queue, max 500 pending, queue.py) and each worker awaits
    a whole DeepSeek turn before taking the next → the *upstream-LLM concurrency
    ceiling equals WORKER_COUNT*, never the client concurrency. Higher client
    concurrency shows up as queue-wait latency, not extra parallel LLM calls.
    The harness therefore keeps the target concurrency *saturated* (a request is
    dispatched the instant a slot frees) so the measurement is what the server
    actually sustains at that offered load.
  - every accepted turn also writes conversation history to MySQL
    (pool_size=5, max_overflow=10) on the same event loop → DB is in the cost.

Auto-degrade (真实压测 + 自动降级): the harness cannot flip the running server
to offline rules mid-level, so “degrade” means *guarding the sweep*: when a
whole level's success rate collapses (DeepSeek timeout/429/outage) or its p50
explodes vs. the previous level, it marks the level ``degraded``, stops
escalating concurrency and caps the remaining levels at the degraded one — so
we never burn unbounded tokens/cost on an unhealthy upstream. Every level row
carries ``mode`` = real|degraded (both are reported in the AGENT-PERF table).

Envelope probe (``--mode envelope``): GET /api/monitor/snapshot (no DeepSeek, no
DB history write). F5: it is *not* a pure HTTP/event-loop ceiling — the
monitor-handler path still runs a DB aggregate + stats rollup on the event loop
— so treat the envelope numbers as an upper-bound *proxy* for the framework
ceiling (framework vs. LLM time separation), not an exact "empty handler" bound.
See AGENT-PERF §2.1.

    # full chat sweep (real DeepSeek):
    cd llm_backend && ../.venv/Scripts/python.exe scripts/perf_test.py \
        --scenario chat_rag_on --json-out logs/perf_chat_on.json
    # envelope (no LLM):
    ../.venv/Scripts/python.exe scripts/perf_test.py --mode envelope \
        --levels 8,16,32,64,128 --duration 15 --json-out logs/perf_env.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

DEFAULT_BASE = "http://127.0.0.1:8002"
CHAT_STREAM = "/api/mall/chat/stream"
SNAPSHOT_URL = "/api/monitor/snapshot"
HISTORY_URL = "/api/monitor/history"

USER_IDS = ("perf-u1", "perf-u2", "perf-u3", "perf-u4", "perf-u5")
# RAG retrieves returns/refund knowledge; grounded chat answer expected.
# NOTE on intent: these messages are phrased to NOT hit the deterministic rule
# fallback words (推荐/想买/多少钱/…), so classify_intent makes its real LLM
# call and the measured round = intent-LLM + chat-LLM (2 sequential DeepSeek
# calls), which is the true steady-state cost of a chat turn.
CHAT_QUERIES = (
    "你们商城一般有哪些售后服务条款",
    "商品不满意的情况下通常怎么处理",
    "关于售后退换货的流程能说明一下吗",
)

REQUEST_TIMEOUT_S = 60.0
HIST_SECONDS = 3600
DEGRADE_OK_RATE = 0.6      # below this success rate ⇒ treat upstream as down
DEGRADE_P50_JUMP = 4.0     # p50 this many × previous level ⇒ back-pressure blowup


def _pct(values: list[float], p: int) -> float:
    # F1: nearest-rank percentile — index = ceil(n*p/100) - 1. `round()` was
    # banker's rounding, so an exact .5 product (n=5,9,13… at p50) landed one
    # rank BELOW the true statistic → systematic under-report of the latency
    # tail. ceil() keeps the *upper* nearest rank; clamp keeps p=100/p>0 safe.
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, math.ceil(len(values) * p / 100) - 1)
    return values[idx]


# --------------------------------------------------------------------------
# dataclasses
# --------------------------------------------------------------------------

@dataclass
class LevelResult:
    mode: str                 # "real" | "degraded"
    scenario: str
    level: int
    requests: int = 0
    ok: int = 0
    http_err: int = 0
    flags: dict = field(default_factory=dict)   # timeout/status/error_evt/incomplete
    latency_ms: list = field(default_factory=list)
    wall_s: float = 0.0
    qps: float = 0.0
    degraded_reason: str = ""

    @property
    def success_rate(self) -> float:
        return (self.ok / self.requests) if self.requests else 0.0

    def pct(self, p: int) -> float:
        return round(_pct(self.latency_ms, p), 1)


@dataclass
class _Ctx:
    got_done: bool = False
    got_error_event: bool = False
    error_msg: str = ""
    text_chars: int = 0


# --------------------------------------------------------------------------
# request bodies (pure)
# --------------------------------------------------------------------------

def chat_body(query: str, user_id: str) -> dict:
    return {
        "messages": [{"role": "user", "content": query}],
        "user_id": user_id,
        "conversation_id": None,
    }


# --------------------------------------------------------------------------
# single chat round — returns (success, flag, text_chars)
# never raises
# --------------------------------------------------------------------------

async def run_chat_once(client: httpx.AsyncClient, url: str, body: dict) -> tuple[bool, str, int]:
    # F8: wall-clock latency is NOT returned — the caller's worker measures its
    # own t0→t1 around this call (uniform with the envelope branch); the old
    # internally-computed latency_ms was recomputed/discarded by its only caller.
    ctx = _Ctx()
    try:
        async with client.stream("POST", url, json=body, timeout=REQUEST_TIMEOUT_S) as resp:
            if resp.status_code != 200:
                return False, f"status:{resp.status_code}", 0
            pending = ""
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    pending = line[6:].strip()
                elif line.startswith("data:") and pending:
                    name, data = pending, line[5:].strip()
                    pending = ""
                    try:
                        obj = json.loads(data)
                    except Exception:
                        obj = data
                    if name == "done":
                        ctx.got_done = True
                    elif name == "error":
                        ctx.got_error_event = True
                        if isinstance(obj, dict):
                            ctx.error_msg = str(obj.get("message") or "")[:80]
                    # F6: there is no `llm_error` event on the wire — the
                    # backend surfaces LLM failures as an `error` event or a
                    # non-200, both handled above. The old branch was dead code.
                    elif name == "text":
                        if isinstance(obj, dict):
                            ctx.text_chars += len(obj.get("content") or "")
    except (httpx.HTTPError, asyncio.TimeoutError):
        return False, "timeout", 0
    except Exception:
        return False, "http_eof", 0
    if ctx.got_error_event:
        return False, f"error:{ctx.error_msg or 'event'}", ctx.text_chars
    if not ctx.got_done:
        return False, "incomplete", ctx.text_chars
    return True, "", ctx.text_chars


# --------------------------------------------------------------------------
# saturated worker pool for one level
# --------------------------------------------------------------------------

async def run_level(
    *,
    mode: str,
    scenario: str,
    level: int,
    url: str,
    envelope: bool,
    duration_s: float,
    requests_cap: int,
    warmup_s: float,
    user_ids: tuple[str, ...],
    queries: tuple[str, ...],
) -> LevelResult:
    res = LevelResult(mode=mode, scenario=scenario, level=level)

    async def _warm() -> None:
        """A couple of best-effort warm requests (errors ignored)."""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
                for _ in range(min(2, level)):
                    if envelope:
                        await client.get(url, timeout=REQUEST_TIMEOUT_S)
                    else:
                        await run_chat_once(
                            client, url,
                            chat_body(queries[_ % len(queries)], user_ids[_ % len(user_ids)]),
                        )
        except Exception:
            pass

    if warmup_s:
        # F2: bound how long the warm-up may block. Each warm request can wait
        # up to REQUEST_TIMEOUT_S (=60s) serially; under an upstream outage a
        # bare `await _warm()` would stall the level ~60-120s before its
        # measurement window ever starts. wait_for caps that at warmup_s and we
        # swallow the timeout so a hung upstream just skips the warm phase —
        # warm failures stay silent either way.
        try:
            await asyncio.wait_for(_warm(), timeout=warmup_s)
        except asyncio.TimeoutError:
            pass

    loop_start = time.time()
    lock = asyncio.Lock()

    async def _worker(idx: int) -> None:
        nonlocal res
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
            while True:
                async with lock:
                    # dispatch decision + claim are ONE atomic section: the cap /
                    # duration check and the increment share one lock acquisition,
                    # so the pool can never overshoot `requests_cap` — each worker
                    # that passes the check claims exactly one request slot.
                    if res.requests >= (requests_cap if requests_cap > 0 else 1 << 62):
                        return
                    if time.time() - loop_start >= duration_s:
                        return
                    res.requests += 1
                    my_idx = res.requests
                user = user_ids[(idx + my_idx) % len(user_ids)]
                query = queries[(idx + my_idx) % len(queries)]
                t0 = time.time()
                if envelope:
                    try:
                        ok = (await client.get(url, timeout=REQUEST_TIMEOUT_S)).status_code == 200
                    except Exception:
                        ok = False
                    success, flag = ok, ("" if ok else "http")
                else:
                    success, flag, _chars = await run_chat_once(
                        client, url, chat_body(query, user))
                lat = (time.time() - t0) * 1000
                async with lock:
                    if success:
                        res.ok += 1
                    if flag:
                        res.flags[flag] = res.flags.get(flag, 0) + 1
                    res.latency_ms.append(max(0.0, lat))

    workers = [asyncio.create_task(_worker(i)) for i in range(level)]
    await asyncio.gather(*workers)
    res.wall_s = time.time() - loop_start
    res.qps = (res.requests / res.wall_s) if res.wall_s > 0 else 0.0
    return res


# --------------------------------------------------------------------------
# server-side token totals over /history (sum of 1s-bucket token events that
# occurred at-or-after a caller-supplied timestamp)
# --------------------------------------------------------------------------

async def fetch_server_tokens(client: httpx.AsyncClient, base: str,
                              since_ts: Optional[float] = None) -> dict:
    try:
        resp = await client.get(f"{base}{HISTORY_URL}", params={"seconds": HIST_SECONDS})
        if resp.status_code == 200:
            points = (resp.json() or {}).get("points", [])
            if since_ts is not None:
                points = [p for p in points if int(p.get("ts") or 0) >= int(since_ts)]
            tin = sum(int(p.get("tokens_in") or 0) for p in points)
            tout = sum(int(p.get("tokens_out") or 0) for p in points)
            llm_ok = sum(int(p.get("llm_per_sec") or 0) for p in points)
            return {"tokens_in": tin, "tokens_out": tout, "llm_calls": llm_ok}
    except Exception:
        pass
    return {"tokens_in": 0, "tokens_out": 0, "llm_calls": 0}


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _render_row(r: LevelResult) -> str:
    flags = " ".join(f"{k}={v}" for k, v in sorted(r.flags.items())) or "-"
    return (
        f"{r.mode:<8} L={r.level:<3} req={r.requests:<4} ok={r.ok:<4} "
        f"succ={r.success_rate * 100:5.1f}% QPS={r.qps:6.2f} "
        f"p50={r.pct(50):7.1f} p95={r.pct(95):7.1f} p99={r.pct(99):7.1f}ms "
        f"wall={r.wall_s:5.1f}s flags[{flags}]"
    )


def _build_payload(results: list[LevelResult], tokens: dict, tokens_s: float,
                   scenario: str, rag: Optional[bool], base: str,
                   levels_tried: list[int]) -> dict:
    """Build the run's JSON payload (shared by the local --json-out dump and the
    --publish POST to the agent's /api/monitor/loadtests endpoint)."""
    return {
        "scenario": scenario,
        "rag_enabled": rag,
        "base": base,
        "levels_tried": levels_tried,
        "server_tokens": tokens,
        # P3: spec wants tokens/s — (in+out) over the sum of per-level wall
        # clocks, same figure the console prints as "tokens/s ≈ <n>".
        "tokens_s": round(tokens_s, 2),
        "levels": [
            {
                "mode": r.mode, "level": r.level, "requests": r.requests,
                "ok": r.ok, "success_rate": round(r.success_rate, 4),
                "qps": round(r.qps, 3), "wall_s": round(r.wall_s, 2),
                "p50_ms": r.pct(50), "p95_ms": r.pct(95), "p99_ms": r.pct(99),
                "flags": r.flags,
                "degraded_reason": r.degraded_reason,
            }
            for r in results
        ],
    }


def _dump_json(path: str, payload: dict) -> None:
    dirn = os.path.dirname(path) or "."
    os.makedirs(dirn, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


async def _publish(base: str, payload: dict) -> None:
    """POST the run payload to the agent's loadtest store. Never raises.

    A publish failure is observation-only (the run is already in the local JSON
    when requested); it prints an ASCII warning and must not affect exit code.
    """
    url = f"{base.rstrip('/')}/api/monitor/loadtests"
    try:
        # Fixed 20s: publishing is post-run bookkeeping — a hung/misconfigured
        # publish base must not stall an already-finished sweep.
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                print(f"!! publish failed: HTTP {resp.status_code}: {resp.text[:160]}")
    except Exception as exc:
        print(f"!! publish failed: {exc}")


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="R6 Mall Agent perf harness")
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--mode", choices=["chat", "envelope"], default="chat")
    p.add_argument("--scenario", default="chat", help="tag stored in the report / json")
    p.add_argument("--rag", dest="rag", default=None, help="true/false/null annotation")
    p.add_argument("--levels", default="1,2,4,8,16,32")
    p.add_argument("--duration", type=float, default=40.0)
    p.add_argument("--requests", type=int, default=0, help="per-level request cap")
    p.add_argument("--warmup", type=float, default=3.0)
    p.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT_S)
    p.add_argument("--json-out", default="")
    p.add_argument("--publish", default="", help="base URL whose /api/monitor/loadtests to POST the finished run to (e.g. http://127.0.0.1:8002); empty = don't publish")
    return p.parse_args(argv)


async def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    envelope = args.mode == "envelope"
    url = f"{args.base}{SNAPSHOT_URL if envelope else CHAT_STREAM}"
    results: list[LevelResult] = []

    rag: Optional[bool] = None
    if args.rag is not None:
        rag = args.rag.lower() in ("1", "true", "yes", "on")

    # Server-side accounting window = sweep start → now. /history buckets are
    # keyed by wall-clock second and the in-memory mirror keeps active seconds,
    # so summing buckets at-or-after this stamp includes ONLY this sweep's
    # traffic — no subtract baseline, no bleed-in from prior/live traffic.
    sweep_started = time.time()
    degraded_at: Optional[int] = None
    prev: Optional[LevelResult] = None
    levels_tried: list[int] = []

    for level in levels:
        if degraded_at is not None and level > degraded_at:
            print(f"—— degraded: capping at L={degraded_at} (skip L={level}) ——", flush=True)
            continue
        # F8: `mode="degraded"` passed here was unreachable — levels are
        # strictly increasing, so once degraded_at is set every later level is
        # skipped above and the guard below flips THIS level post-hoc. Pass the
        # honest pre-flip value; the guard rewrites res.mode when it degrades.
        res = await run_level(
            mode="real", scenario=args.scenario, level=level, url=url,
            envelope=envelope, duration_s=args.duration,
            requests_cap=args.requests, warmup_s=args.warmup,
            user_ids=USER_IDS, queries=CHAT_QUERIES,
        )
        levels_tried.append(level)
        print(_render_row(res), flush=True)
        results.append(res)

        # auto-degrade guard for chat (envelope has no upstream LLM to degrade)
        if not envelope and prev is not None:
            reason = ""
            # F3: both statistical branches need a real sample on BOTH sides
            # (>=3 attempts each) — with 1-2 samples the "previous level" is
            # pure noise and could spuriously trip a 4x p50 jump.
            enough = res.requests >= 3 and prev.requests >= 3
            if enough and res.success_rate < DEGRADE_OK_RATE:
                reason = f"success_rate {res.success_rate:.2f} < {DEGRADE_OK_RATE}"
            elif enough and prev.pct(50) > 0 and \
                    res.pct(50) > DEGRADE_P50_JUMP * prev.pct(50):
                reason = (f"p50 {res.pct(50):.0f}ms > {DEGRADE_P50_JUMP}x prev "
                          f"{prev.pct(50):.0f}ms (back-pressure)")
            # F3 fallback: at L=1-2 a full-timeout outage yields <3 attempts in
            # the window, so the branches above can never fire (the guard would
            # stay starved until L>=4 / ~3min). If the level burned its whole
            # nominal wall with zero completions and every attempt read-timed
            # out, the upstream is just as down — degrade anyway.
            elif 0 < res.requests < 3 and res.ok == 0 and \
                    res.flags.get("timeout", 0) == res.requests and \
                    res.wall_s >= args.duration:
                reason = (f"0/{res.requests} completed, all read-timeout "
                          f"({res.wall_s:.0f}s wall)")
            if reason and degraded_at is None:
                degraded_at = level
                res.degraded_reason = reason
                res.mode = "degraded"
                # F7: U+26A0 (⚠) is not in GBK/cp936 — with py3.11 Windows stdout
                # redirected (.bat run) that print raised UnicodeEncodeError on
                # exactly this path, aborting the sweep. Use ASCII only here.
                print(f"!! DEGRADE at L={level}: {reason}", flush=True)
        prev = res

    # F8: token accounting is since_ts-window based (sweep_started → now) — no
    # pre/post baseline subtraction, so fetch straight into one dict (the old
    # base_client2 / tok_after aliasing was a dead end).
    async with httpx.AsyncClient() as base_client:
        tokens = await fetch_server_tokens(base_client, args.base, since_ts=sweep_started)

    total_req = sum(r.requests for r in results)
    sweep_wall = sum(r.wall_s for r in results)
    tokens_s = ((tokens["tokens_in"] + tokens["tokens_out"]) / sweep_wall
                if sweep_wall > 0 else 0.0)

    print("\n=== 服务器侧汇总（本场景窗口增量，来自 /api/monitor/history） ===")
    print(f"  llm_calls(成功) = {tokens['llm_calls']}   总回合请求 = {total_req}")
    print(f"  tokens in = {tokens['tokens_in']}   out = {tokens['tokens_out']}   "
          f"tokens/s ≈ {tokens_s:.1f}   (各档 wall 合计 {sweep_wall:.1f}s)")

    payload = _build_payload(results, tokens, tokens_s, args.scenario,
                             rag, args.base, levels_tried)
    if args.json_out:
        _dump_json(args.json_out, payload)
        print(f"\n已写 {args.json_out}")
    if args.publish:
        await _publish(args.publish, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Background workers that publish Agent events while they are generated."""
import asyncio
import json
import logging
import time

from .queue import TaskQueue
from .service import stream_messages
from . import history_service as hs
from .config import CTX_MAX_MESSAGES, CTX_SERVER
from .stats import stats

logger = logging.getLogger("mall_agent.worker")


def _as_int_ms(value: object, fallback: int | None) -> int | None:
    """Parse a millisecond timestamp defensively — never raises.

    Arbitrary stage/intent data (or ``None``) reaching ``int()`` must degrade to
    ``fallback`` instead of throwing: this is observation code and may never take
    the worker down (see the INVARIANT note on ``_build_agent_trace``).
    """
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _build_agent_trace(
    stage_rows: list[dict],
    intent_payload: dict | None,
    recorded_at_ms: int,
) -> list[dict] | None:
    """Assemble the persisted ``agent_trace`` for one assistant turn.

    ``stage_rows`` are the raw stage-event data dicts (``{node,label,at_ms,
    detail}``) forwarded by the worker — node entered ⇒ emitted, so they arrive
    in execution order; the terminal ``done`` sentinel is filtered out by the
    caller. ``intent_payload`` is the parsed ``intent`` event (optional) whose
    after-the-fact facts — the classified intent type, category/keywords and its
    reason — are the intended post-hop data for the ``intent`` step (stage
    ``detail`` only carries pre-hop facts, see flow.py). ``recorded_at_ms`` is
    the wall-clock stamp of the moment this turn is being persisted, used to
    time the final step.

    Each persisted step is ``{node, label, at_ms, duration_ms, detail}``:
    ``duration_ms`` is derived here from consecutive ``at_ms`` (final step =
    recorded_at − its at_ms) so history replay needs no re-derivation, whereas
    the R3 live card computed the same number on the client from at_ms deltas.

    INVARIANT — observation never breaks the main flow: this function must never
    raise. Every extraction from ``raw`` is defended — a non-dict/blank ``node``
    row is skipped, a bad ``at_ms`` degrades to ``duration_ms=None`` (step kept
    for label fidelity) instead of ``int()`` throwing, and a non-dict ``detail``
    degrades to an empty dict rather than ``dict(str)`` throwing.
    ``_safe_build_trace`` wraps it in ``try/except → None`` so any residual
    failure still becomes ``extra=None`` and never escapes worker_loop.

    Returns ``None`` when there is nothing to persist (no stage forwarded —
    e.g. NODE_TRACE off), so the assistant message then carries no ``extra``.
    """
    if not stage_rows:
        return None
    trace: list[dict] = []
    for index, raw in enumerate(stage_rows):
        if not isinstance(raw, dict):
            continue
        node = raw.get("node")
        if not node:
            continue
        base = _as_int_ms(raw.get("at_ms"), 0) or 0
        # Only a dict neighbour can supply a next-step stamp; a malformed or
        # non-dict neighbour is unknown → fall back to turn end (never raise).
        nxt = stage_rows[index + 1] if index + 1 < len(stage_rows) else None
        if isinstance(nxt, dict):
            end_at_ms = _as_int_ms(nxt.get("at_ms"), recorded_at_ms) or recorded_at_ms
        else:
            end_at_ms = recorded_at_ms
        # duration from neighbouring at_ms; a bad/absent base leaves the step
        # un-timed (None) rather than raising — kept for label fidelity.
        duration_ms = max(0, end_at_ms - base) if base else None
        detail = raw.get("detail")
        entry = {
            "node": node,
            "label": raw.get("label", node),
            "at_ms": base,
            "duration_ms": duration_ms,
            "detail": dict(detail) if isinstance(detail, dict) else {},
        }
        if node == "intent" and isinstance(intent_payload, dict):
            # Merge post-hop facts from the intent event into the intent step.
            for key in ("intent", "category", "keywords", "reason", "type"):
                if key in intent_payload:
                    entry["detail"][key] = intent_payload[key]
        trace.append(entry)
    return trace


def _safe_build_trace(
    stage_rows: list[dict],
    intent_payload: dict | None,
    recorded_at_ms: int,
    worker_id: int,
) -> list[dict] | None:
    """Build the persisted agent_trace, degrading to None on any failure.

    _build_agent_trace is written never-to-raise, but observation code must
    never escape worker_loop: a residual bug degrades to extra=None here.
    """
    try:
        return _build_agent_trace(stage_rows, intent_payload, recorded_at_ms)
    except Exception:
        logger.exception("Worker[%d] trace build failed; persisting without agent_trace", worker_id)
        return None


async def _load_server_context(conversation_id: int | None) -> list[dict] | None:
    """Authoritative PRIOR turns to ground this round, or None.

    When CTX_SERVER is on and a conversation_id is present, fetch the newest
    text turns from MySQL (history_service.get_message_context) so the LLM / RAG
    answer from what is actually on record, not the client's self-reported full
    history. The router saves this round's user message before the task is
    submitted, so that row is the newest — it is dropped here so the returned
    list is strictly the PRIOR turns and service.stream_messages appends the
    current turn once. Returns None (→ legacy client-fed behaviour) when
    disabled, no conversation_id, only a current turn exists, or the fetch
    fails — never raises.
    """
    if not CTX_SERVER or not conversation_id:
        return None
    try:
        history = await hs.get_message_context(conversation_id, limit=CTX_MAX_MESSAGES)
    except Exception:
        logger.exception(
            "Worker: server context load failed for conv %s; using client-fed path",
            conversation_id,
        )
        return None
    # Drop the trailing current-user row (just committed by the router) so only
    # genuine prior turns remain; an empty remainder degrades to None.
    while history and history[-1].get("role") == "user":
        history.pop()
    return history if history else None


async def worker_loop(queue: TaskQueue, worker_id: int):
    logger.info("Worker[%d] started", worker_id)
    while True:
        task = await queue.get_task()
        if task is None:
            await asyncio.sleep(0)
            continue

        task_id = task["task_id"]
        messages = task["messages"]
        conversation_id = task.get("conversation_id")
        logger.info("Worker[%d] processing task %s (conv=%s)", worker_id, task_id, conversation_id)

        # #109: server-authoritative context — rebuild the LLM/RAG context from
        # MySQL by conversation_id when enabled, instead of trusting the
        # client's self-reported full message list. Fetch is failure-soft: any
        # error falls back to the legacy client-fed path (context_msgs=None).
        context_msgs = await _load_server_context(conversation_id)

        assistant_text = ""
        assistant_products = None
        # R4: per-turn agent_trace collection. stage_rows keeps the raw stage
        # event data dicts in arrival order (the terminal "done" sentinel is
        # skipped) and intent_payload holds the parsed intent event — together
        # they become the persisted Message.extra.agent_trace (see below).
        stage_rows: list[dict] = []
        intent_payload: dict | None = None
        stats.inc_active()
        turn_started = time.monotonic()
        try:
            async for event in stream_messages(messages, context_msgs=context_msgs):
                event_type = event.get("event", "")
                data = event.get("data", "")

                if event_type == "text":
                    assistant_text += data.get("content", "") if isinstance(data, dict) else str(data)
                elif event_type == "products" and isinstance(data, dict):
                    assistant_products = data.get("products", [])
                elif event_type == "stage" and isinstance(data, dict):
                    # Keep every non-terminal step for the persisted trace. The
                    # "done" stage is the closing sentinel — filtered here so it
                    # does not become a card row.
                    if data.get("node") != "done":
                        stage_rows.append(data)
                elif event_type == "intent" and isinstance(data, dict):
                    intent_payload = data

                data_string = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data)
                await queue.publish_event(task_id, event_type, data_string)

            if conversation_id:
                recorded_at_ms = int(time.time() * 1000)
                # Isolate the trace build (observation never breaks main flow):
                # a residual bug degrades to extra=None via _safe_build_trace —
                # never escapes worker_loop.
                trace = _safe_build_trace(stage_rows, intent_payload, recorded_at_ms, worker_id)
                await hs.save_message(
                    conversation_id,
                    "assistant",
                    assistant_text or "(空)",
                    extra={"agent_trace": trace} if trace else None,
                )
                if assistant_products:
                    await hs.save_message(
                        conversation_id,
                        "assistant",
                        json.dumps(assistant_products, ensure_ascii=False),
                        msg_type="products",
                    )

            await queue.mark_done(task_id)
            stats.record_task(True)
            logger.info(
                "Worker[%d] task %s done (%d chars, %d products)",
                worker_id,
                task_id,
                len(assistant_text),
                len(assistant_products) if assistant_products else 0,
            )
        except Exception as exc:
            logger.exception("Worker[%d] task %s failed", worker_id, task_id)
            stats.record_task(False)
            if conversation_id:
                recorded_at_ms = int(time.time() * 1000)
                # Same isolation as the success path — the helper's try/except
                # degrades any residual bug to trace=None (this branch is already
                # inside the outer except, so nothing may escape worker_loop).
                trace = _safe_build_trace(stage_rows, intent_payload, recorded_at_ms, worker_id)
                await hs.save_message(
                    conversation_id,
                    "assistant",
                    f"处理失败: {exc}",
                    extra={"agent_trace": trace} if trace else None,
                )
            await queue.publish_event(
                task_id,
                "error",
                json.dumps({"message": f"处理失败: {exc}"}, ensure_ascii=False),
            )
            await queue.mark_failed(task_id, str(exc))
        finally:
            # Whole-turn latency for BOTH success and failure paths (recorded in
            # finally so failed/long-tailed turns aren't excluded from latency
            # history / load-test comparisons).
            stats.record_task_latency((time.monotonic() - turn_started) * 1000)
            stats.dec_active()


async def start_worker_pool(queue: TaskQueue, count: int = 4):
    tasks = [
        asyncio.create_task(worker_loop(queue, index), name=f"worker-{index}")
        for index in range(count)
    ]
    cleanup = asyncio.create_task(queue.periodic_cleanup(), name="queue-cleanup")
    logger.info("Worker pool started: %d workers", count)
    return tasks, cleanup
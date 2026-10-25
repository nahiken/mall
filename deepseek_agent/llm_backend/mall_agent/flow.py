"""Agent decision-flow node definitions and stage-event helpers.

Observability-only (never changes business behaviour):
  - ``NODE_TRACE`` (config) gates whether ``stream_messages`` emits per-step
    ``stage`` events. When off, the old event stream (meta/intent/text/products/
    …/done) is emitted unchanged and no stage bookkeeping runs. It is bound
    here at import time, so it is a startup-time toggle (restart to change),
    not a runtime one.
  - There is no node catalogue in this module: node ids are the hardcoded
    strings at the service.py emit call sites and are forwarded verbatim as
    ``stage.node``. The frontend renders a vertical step-card list in the order
    it actually saw those ids and lights up the node it is currently on.
    ``_LABELS`` is the only live node map — id → human-facing Chinese title,
    read by ``emit_stage`` (an unknown id falls back to the id itself).
  - ``emit_stage`` emits a stage the moment its hop *begins* (not after it
    completes), so the SSE consumer sees a node light up exactly while the agent
    is executing it — the honest "entered node X" signal the realtime card needs.
    Each stage carries ``at_ms`` (authoritative backend wall-clock); the
    frontend derives a completed step's ``duration`` as the delta to the next
    ``at_ms``, so the backend never has to resend completed stages.
    ``emit_stage`` never raises: a bug in flow tracing must not break a
    conversation.

R4's persisted ``Message.extra.agent_trace`` is not built here: the worker
relays these ``stage`` events to the SSE client and rebuilds the persisted
trace from them (see worker.py ``_build_agent_trace``), so this module needs no
writer-side accumulator.

Stage ids are matched from source-code call sites (see service.py), so they are
kept as stable English snake_case; ``label`` is the human-facing Chinese title
for the step card. Each stage dict: ``{node, label, at_ms, detail}``.
"""
import time
from typing import Any

from .config import NODE_TRACE

# Node ids come from the service.py emit call sites (no catalogue here — see
# module docstring). This map is the only id -> Chinese label table, read by
# emit_stage when it builds a stage payload (an unknown id falls back to the id).
_LABELS = {
    "intent": "意图分类",
    "kb_search": "知识库检索",
    "llm_expand": "检索词扩展",
    "tool_search": "商品检索",
    "finalize": "结果合成",
    "done": "完成",
}


def emit_stage(
    node: str,
    **detail: Any,
) -> dict[str, Any] | None:
    """Build the SSE payload for the agent about to run ``node``.

    ``detail`` carries what is known *before* the hop runs (query text, term /
    product counts already in hand) — facts that only appear after the hop (e.g.
    the classified intent) travel on the existing ``intent``/``products`` events
    instead, so a stage never has to be re-sent with late data. Persistence of
    the trace is handled by the worker (it relays these ``stage`` events to the
    SSE client and rebuilds the trace from them), so this function only returns
    the payload and never accumulates anything.
    Returns ``{"event": "stage", "data": stage}``, or ``None`` when node-tracing
    is off (callers then yield nothing). Never raises: a flow-tracing bug must
    not break a conversation.
    """
    if not NODE_TRACE:
        return None
    stage: dict[str, Any] = {
        "node": node,
        "label": _LABELS.get(node, node),
        "at_ms": int(time.time() * 1000),
        "detail": detail,
    }
    return {"event": "stage", "data": stage}

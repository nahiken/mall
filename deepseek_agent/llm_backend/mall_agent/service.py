"""Mall agent intent routing, relevant product retrieval and true LLM streaming.

P2-04：检索/提示词/LLM 客户端已拆到 retrieval.py / prompts.py / llm.py，
本文件只保留编排（意图→检索→拼 prompt→流式输出）。
"""
import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from .config import DEEPSEEK_MODEL, CTX_RAG_HISTORY, FUSED_INTENT, INTENT_RULE_SHORTCUT
from .intent_classifier import classify_fused, classify_intent, rule_short_circuit
from .flow import emit_stage
from . import llm
from .llm import client as _client
from .prompts import (
    CHAT_SYSTEM_PROMPT,
    RECOMMEND_SYSTEM_PROMPT,
    RECOMMEND_USER_PROMPT_TEMPLATE,
)
from .retrieval import (
    _add_unique,
    _assemble_base_terms,
    _format_products_for_llm,
    _llm_expand_search_terms,
    _product_to_card,
    _search_products_by_terms,
)
from .errors import log_degraded
from . import knowledge_client as kb


logger = logging.getLogger("mall_agent.service")


async def aclose_client() -> None:
    """关闭共享 LLM 客户端（run_mall.py 在 shutdown 时调 service.aclose_client()）。

    实现放在 llm.py（唯一持有单例的地方），这里保留同名函数作为对外入口，
    避免 run_mall.py 跟着改；也避免"import 了但没人用"的死导入。
    """
    await llm.aclose_client()


def _stage_event(node: str, **detail: Any) -> dict | None:
    """Build a decision-flow stage SSE payload for ``node`` (see flow.emit_stage).

    Returns ``None`` when node-tracing is off; a tracing bug must never break
    the conversation, so any error also degrades to ``None``.
    """
    try:
        return emit_stage(node, **detail)
    except Exception:
        return None


def _history_text(
    history: list[dict],
    *,
    max_turns: int,
    max_chars: int = 0,
) -> str:
    """Flatten server-side conversation history into a compact transcript.

    Takes oldest-first ``[{"role", "content"}, …]`` rows and renders the most
    recent ``max_turns`` as ``用户：… / 助手：…`` lines. Used (a) to build a RAG
    query that still carries referents from earlier turns and (b) as the
    "prior conversation" block injected into the recommend prompt — both without
    any extra LLM call. ``max_chars`` (0 = unlimited) guards against an overly
    long transcript blowing the prompt. Pure and never raises.
    """
    if not history:
        return ""
    recent = history[-max_turns:] if max_turns > 0 else history
    lines = []
    chars = 0
    for m in recent:
        role = m.get("role") or ""
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        label = "用户" if role == "user" else "助手"
        line = f"{label}：{content}"
        lines.append(line)
        chars += len(line)
        if max_chars and chars >= max_chars:
            break
    return "\n".join(lines)


async def _stream_completion(messages: list[dict], max_tokens: int) -> AsyncGenerator[str, None]:
    """Stream a chat completion, recording LLM usage stats as it runs.

    Uses stream_options.include_usage so the final chunk carries usage tokens.
    Some providers reject that param — we retry once without it (behaviour stays
    identical, stats just lose token counts).
    """
    from .stats import stats

    start = time.monotonic()
    try:
        stream = await _client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=0.65,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
    except Exception as exc:  # noqa: BLE001
        try:
            stream = await _client().chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                temperature=0.65,
                max_tokens=max_tokens,
                stream=True,
            )
        except Exception as retry_exc:  # noqa: BLE001
            stats.record_llm(False)
            log_degraded(
                logger, "llm_stream", retry_exc,
                message="LLM stream call failed (after retry without usage options)",
            )
            raise
        # 第一次失败多半是 provider 不认 stream_options；记 INFO 便于核对
        log_degraded(
            logger, "llm_stream", exc,
            message="LLM stream rejected usage options; retried without them",
            level=logging.INFO,
        )
    tokens_in = 0
    tokens_out = 0
    try:
        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                tokens_in = getattr(usage, "prompt_tokens", 0) or 0
                tokens_out = getattr(usage, "completion_tokens", 0) or 0
            content = chunk.choices[0].delta.content if chunk.choices else None
            if content:
                yield content
        stats.record_llm(
            True,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception as exc:  # noqa: BLE001
        stats.record_llm(False)
        log_degraded(
            logger, "llm_stream", exc,
            message="LLM stream broke mid-response",
        )
        raise


async def _stream_plain_text(text: str) -> AsyncGenerator[str, None]:
    """Keep non-LLM fallback messages visibly incremental too."""
    for start in range(0, len(text), 6):
        yield text[start:start + 6]
        await asyncio.sleep(0.025)


async def stream_messages(
    messages: list[dict],
    context_msgs: list[dict] | None = None,
) -> AsyncGenerator[dict, None]:
    """Yield intent, stage, text chunks, products and completion as they become available.

    Decision-flow tracing: when ``NODE_TRACE`` is on (default), each slow
    decision/search hop also yields a ``stage`` event carrying the node id/label,
    the wall-clock ``at_ms`` and a free-form ``detail`` (reason, hit counts,
    terms, …). The terminal ``done`` stage closes the trace. This generator only
    forwards the ``stage`` events — the persisted agent_trace is rebuilt by the
    worker from them (worker.py), so no write-side accumulator lives here. A
    local error in tracing must never break the message stream, so the emit
    calls go through the swallow-exceptions ``_stage_event`` helper.

    ``messages`` is the client-supplied request (its last user turn is the
    trigger for this round). ``context_msgs`` — when provided — is the
    **server-authoritative** conversation history (oldest-first ``[{"role",
    "content"}, …]`` from MySQL by conversation_id, see worker.py) used in its
    place to build the LLM context / RAG query / recommend history block, so the
    agent answers from what is actually on record rather than what a client
    self-reports. When ``context_msgs`` is empty/None the legacy behaviour is
    kept (client messages as context, single-turn RAG).
    """
    if not messages:
        yield {"event": "error", "data": {"message": "消息不能为空"}}
        yield {"event": "done", "data": "[DONE]"}
        return

    last_user_message = next(
        (str(item.get("content", "")) for item in reversed(messages) if item.get("role") == "user"),
        "",
    ).strip()
    if not last_user_message:
        yield {"event": "error", "data": {"message": "请输入消息"}}
        yield {"event": "done", "data": "[DONE]"}
        return

    # ── intent ───────────────────────────────────────────────────────────
    intent_stage = _stage_event("intent")
    if intent_stage:
        yield intent_stage
    # P1-01: 规则短路（明确短句）→ 合并意图+检索词（一次调用）→ 旧两跳（回滚路径）。
    # 三条路径产出的 intent 事件字段向后兼容，只多出 terms/terms_ready/degraded。
    shortcut = rule_short_circuit(last_user_message) if INTENT_RULE_SHORTCUT else None
    if shortcut is not None:
        intent_result = shortcut
        logger.debug("intent short-circuited by rules: %s", shortcut.get("reason"))
    elif FUSED_INTENT:
        intent_result = await classify_fused(last_user_message)
    else:
        intent_result = await classify_intent(last_user_message)
    intent = str(intent_result.get("intent") or "chat")
    intent_payload = {**intent_result, "type": intent}
    # Server-side诊断字段：规则/合并/降级走了哪条路径，前端忽略未知字段即可。
    intent_payload.setdefault("path", "rule" if shortcut is not None
                              else ("fused" if FUSED_INTENT else "legacy"))
    yield {"event": "intent", "data": intent_payload}

    if intent == "chat":
        # Ground the casual chat with knowledge-base snippets when relevant.
        # Retrieval is optional & failure-soft: empty/failed returns [] so the
        # conversation keeps working with the plain system prompt.
        kb_stage = _stage_event("kb_search")
        if kb_stage:
            yield kb_stage
        # Query RAG with the recent transcript + current query so cross-turn
        # pronouns ("它质量怎么样") still carry their referent; single-turn if
        # server history is unavailable (legacy path).
        rag_query = last_user_message
        if context_msgs:
            prior = _history_text(context_msgs, max_turns=CTX_RAG_HISTORY)
            if prior:
                rag_query = f"{prior}\n当前问题：{last_user_message}"
        snippets = await kb.search(rag_query)
        system_prompt = CHAT_SYSTEM_PROMPT
        if snippets:
            context = "\n".join(f"- {s}" for s in snippets)
            system_prompt = (
                f"{CHAT_SYSTEM_PROMPT}\n\n"
                "【商城知识库参考】以下内容仅作回答依据之一，仅当问题与之相关时使用，不得虚构或照搬：\n"
                f"{context}"
            )
        finalize_stage = _stage_event("finalize", snippets=len(snippets))
        if finalize_stage:
            yield finalize_stage
        # Authoritative server history (when available) replaces the client-fed
        # 8-message window; the current user turn is always appended last.
        if context_msgs:
            llm_messages = [
                {"role": "system", "content": system_prompt},
                *context_msgs,
                {"role": "user", "content": last_user_message},
            ]
        else:
            llm_messages = [{"role": "system", "content": system_prompt}, *messages[-8:]]
        async for content in _stream_completion(llm_messages, max_tokens=1024):
            yield {"event": "text", "data": {"content": content}}
    else:
        # ── recommend / product_query ────────────────────────────────────
        category = str(intent_result.get("category") or "").strip()
        keywords = str(intent_result.get("keywords") or last_user_message).strip()

        # Fast term assembly (no LLM, cached category tree)…
        base_terms, _category_names = await _assemble_base_terms(category, keywords)
        # …then LLM term expansion, then the concurrent product search — each
        # slow hop emits its own stage so the frontend can light it up in turn.
        llm_stage = _stage_event("llm_expand", base_terms=len(base_terms),
                                 terms_ready=bool(intent_result.get("terms_ready")))
        if llm_stage:
            yield llm_stage
        if intent_result.get("terms_ready"):
            # 合并路径/规则命中：检索词已随意图一起产出，省掉这次 LLM。
            expanded = [str(t) for t in (intent_result.get("terms") or []) if str(t).strip()]
        else:
            expanded = await _llm_expand_search_terms(
                last_user_message, category, keywords, _category_names
            )
        terms: list[str] = list(base_terms)
        for term in expanded:
            _add_unique(terms, term, limit=20)

        tool_stage = _stage_event("tool_search", terms=len(terms))
        if tool_stage:
            yield tool_stage
        products: list[dict] = []
        if terms:
            products = await _search_products_by_terms(terms)

        if not products:
            label = category or keywords or "相关"
            text = f"目前商城没有找到{label}商品，我不会用手机或电脑等无关商品凑数。你可以换个更具体的关键词，我再帮你找。"
            # fallback 文案同样属“结果合成”（与上方 products 分支 / chat 分支一致）：兜底
            # 回复生成前也下 finalize，避免期间“当前脉动步”仍停留在早已结束的 tool_search。
            no_prod_stage = _stage_event("finalize")
            if no_prod_stage:
                yield no_prod_stage
            async for content in _stream_plain_text(text):
                yield {"event": "text", "data": {"content": content}}
        else:
            prod_stage = _stage_event("finalize", products=len(products))
            if prod_stage:
                yield prod_stage
            history_text = _history_text(context_msgs, max_turns=CTX_RAG_HISTORY)
            history_block = (
                "\n本轮之前的对话摘要（仅作理解指代用，如'刚才那款/预算'，勿照抄为商品）：\n"
                f"{history_text}"
                if history_text
                else ""
            )
            prompt = RECOMMEND_USER_PROMPT_TEMPLATE.format(
                query=last_user_message,
                category=category or "未限定",
                keywords=keywords,
                search_terms="、".join(terms),
                history_block=history_block,
                products=_format_products_for_llm(products),
            )
            llm_messages = [
                {"role": "system", "content": RECOMMEND_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            async for content in _stream_completion(llm_messages, max_tokens=1800):
                yield {"event": "text", "data": {"content": content}}
            yield {
                "event": "products",
                "data": {"products": [_product_to_card(product) for product in products]},
            }

    done_stage = _stage_event("done")
    if done_stage:
        yield done_stage
    yield {"event": "done", "data": "[DONE]"}


async def process_messages(messages: list[dict]) -> list[dict]:
    """Compatibility helper used by tests and non-streaming callers."""
    return [event async for event in stream_messages(messages)]

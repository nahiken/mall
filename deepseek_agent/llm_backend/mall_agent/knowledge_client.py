"""RAG knowledge base client for mall-agent conversations.

Retrieves relevant snippets from the external RAG HTTP service (see
RAG-MCP-SERVER http_server on :8123) and returns them so service.py can inject
them as grounding context into the chat prompt.

Design rules:
- RAG_HTTP_URL empty => everything returns [] (feature off), even if enabled.
- MALL_AGENT_RAG=off (or 0/false) disables at process start regardless of URL.
- The boolean can be flipped at runtime via ``set_enabled()``; the URL (and
  therefore the "no URL = off" default) still requires a restart to change.
- Any failure (timeout / connect error / non-2xx) returns [] and logs a
  throttled warning. Retrieval must never slow down or break a conversation.
- Snippets are truncated to RAG_SNIPPET_MAX_CHARS and capped at RAG_TOP_K.
"""
import asyncio
import logging
import time

import httpx

from .config import (
    RAG_ENABLED,
    RAG_HTTP_URL,
    RAG_SNIPPET_MAX_CHARS,
    RAG_TIMEOUT_SECONDS,
    RAG_TOP_K,
)
from .errors import classify_status, log_degraded

logger = logging.getLogger("mall_agent.kb")

# Process-lifetime shared httpx client (was: a fresh AsyncClient per /query —
# a TLS handshake + connection per retrieval). mall-agent runs on a single event
# loop, so a module singleton is safe.
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=RAG_TIMEOUT_SECONDS)
    return _client


async def aclose_client() -> None:
    """Close the shared client (idempotent). Called on app shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


# URL configured at import (config values are copied into this module's
# namespace — changing config.RAG_HTTP_URL later does NOT affect these). A
# runtime flip of the boolean is possible via set_enabled(); the URL itself
# (and therefore the default) still requires a restart.
_url_configured = bool(RAG_HTTP_URL and RAG_HTTP_URL.strip())
_enabled = _url_configured and RAG_ENABLED
_last_warn = 0.0


def enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> bool:
    """Flip RAG at runtime; returns the actually-effective state.

    Disabling is unconditional; enabling still requires a configured
    RAG_HTTP_URL (an empty URL keeps RAG off — the setter cannot conjure a
    backend), mirroring the restart-with-empty-URL semantics.
    """
    global _enabled
    _enabled = bool(value) and _url_configured
    logger.info("RAG runtime set_enabled(%s) -> %s", bool(value), _enabled)
    return _enabled


def _throttled_warn(message: str) -> None:
    global _last_warn
    now = time.monotonic()
    if now - _last_warn > 30:  # at most once per 30s
        _last_warn = now
        logger.warning("RAG knowledge retrieval unavailable: %s", message)


def _warn_failure(exc: BaseException) -> None:
    """P2-07：异常降级带 category（timeout/connect/parse…），同样 30s 节流。"""
    global _last_warn
    now = time.monotonic()
    if now - _last_warn > 30:
        _last_warn = now
        log_degraded(logger, "rag", exc,
                     message="RAG knowledge retrieval unavailable")


async def search(query: str, top_k: int | None = None) -> list[str]:
    """Return up to top_k grounded snippets (raw strings) relevant to query.

    Empty string query or disabled service -> [].
    """
    if not _enabled or not (query or "").strip():
        return []
    k = top_k or RAG_TOP_K
    try:
        resp = await _http().post(
            f"{RAG_HTTP_URL.rstrip('/')}/query",
            json={"text": query, "top_k": k},
        )
        if resp.status_code != 200:
            _throttled_warn(
                f"HTTP {resp.status_code} "
                f"(category={classify_status(resp.status_code)}): "
                f"{resp.text[:120]}"
            )
            return []
        payload = resp.json()
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        _warn_failure(exc)
        return []
    except ValueError as exc:
        # 200 但不是 JSON（网关 HTML 错误页 / 截断响应）。P2-01 由
        # test_malformed_payload_degrades_to_empty 钉住：这条路径过去会抛出去，
        # 把整轮对话打断。
        _warn_failure(exc)
        return []

    snippets: list[str] = []
    try:
        for result in payload.get("results", []):
            text = (result.get("text") or "").strip()
            if text:
                snippets.append(text[:RAG_SNIPPET_MAX_CHARS])
            if len(snippets) >= k:
                break
    except Exception:  # noqa: BLE001 — malformed payload must not break chat
        _throttled_warn("malformed /query payload")
        return []
    return snippets

"""异常分类（P2-07）：把 `except Exception` 的降级变成可聚合的短标签。

问题：降级日志原先只打 ``type(exc).__name__``，于是 ``APIConnectionError`` 和
``RateLimitError`` 都是一行 WARNING，运维分不清「上游限流该降速」和「网络抖动该
重试」，也没法按类别设告警阈值（比如 rate_limit 连续 5 分钟就报警，unknown 只在
每日巡检里看）。

做法：把异常归到固定枚举，日志行统一带 ``category=``、``type=``、``hop=``，
既人能读也能被日志系统按字段切分。分类刻意**保守**：拿不准就 ``unknown``，
不猜。

边界：只分类，不重试、不改控制流 —— 降级结论仍由各调用点自己决定，这样回滚这
个模块不会改变任何行为。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

TIMEOUT = "timeout"
RATE_LIMIT = "rate_limit"
AUTH = "auth"
BAD_REQUEST = "bad_request"
SERVER_ERROR = "server_error"
CONNECT = "connect"
PARSE = "parse"
CANCELLED = "cancelled"
UNKNOWN = "unknown"

_MAX_MESSAGE_CHARS = 200


def _status_code(exc: BaseException) -> int | None:
    """Best-effort HTTP status: openai/httpx 用 status_code，有的客户端用 code。"""
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        # bool 是 int 的子类，排除掉避免 True 被当 1
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    return None


def classify_status(status: int | None) -> str:
    """Classify a bare HTTP status (for non-raising non-200 paths)."""
    if status == 429:
        return RATE_LIMIT
    if status in (401, 403):
        return AUTH
    if status is not None and 500 <= status < 600:
        return SERVER_ERROR
    if status is not None and 400 <= status < 500:
        return BAD_REQUEST
    return UNKNOWN


def classify_exception(exc: BaseException) -> str:
    """Map an exception to one of the module's category constants."""
    if isinstance(exc, asyncio.CancelledError) or type(exc).__name__ == "CancelledError":
        return CANCELLED
    name = type(exc).__name__
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in name:
        return TIMEOUT

    status = _status_code(exc)
    if status is not None:
        category = classify_status(status)
        if category != UNKNOWN:
            return category

    if isinstance(exc, ConnectionError) or "Connect" in name:
        return CONNECT
    if isinstance(exc, json.JSONDecodeError):
        return PARSE
    if isinstance(exc, (ValueError, KeyError)) or "Parse" in name or "Validation" in name:
        return PARSE
    return UNKNOWN


def exc_fields(exc: BaseException, *, hop: str = "") -> dict:
    """Structured fields for ``logger.*(..., extra=exc_fields(...))``."""
    return {
        "hop": hop,
        "exc_type": type(exc).__name__,
        "exc_category": classify_exception(exc),
        "exc_message": str(exc)[:_MAX_MESSAGE_CHARS],
    }


def log_degraded(
    logger: logging.Logger,
    hop: str,
    exc: BaseException,
    *,
    message: str = "degraded",
    level: int = logging.WARNING,
) -> str:
    """Log one classified degradation line; returns the category (for callers)."""
    category = classify_exception(exc)
    if category == CANCELLED:
        # 取消是正常停机路径，不该出现在 WARNING 里制造噪音
        level = logging.DEBUG
    logger.log(
        level,
        "%s: hop=%s category=%s type=%s msg=%s",
        message,
        hop,
        category,
        type(exc).__name__,
        str(exc)[:_MAX_MESSAGE_CHARS],
    )
    return category


_last_warn_by_hop: dict[str, float] = {}


def log_degraded_throttled(
    logger: logging.Logger,
    hop: str,
    exc: BaseException,
    *,
    message: str = "degraded",
    interval_sec: float = 30.0,
) -> str | None:
    """Same as :func:`log_degraded`, but at most once per ``interval_sec`` **per hop**.

    Per-hop (not global) so a chatty hop cannot silence a different, more serious
    one. Used by hot paths (intent / RAG / product search) that can fail on every
    turn. Returns the category when the line was actually emitted, else ``None``.
    """
    now = time.monotonic()
    if now - _last_warn_by_hop.get(hop, 0.0) <= interval_sec:
        return None
    _last_warn_by_hop[hop] = now
    return log_degraded(logger, hop, exc, message=message)

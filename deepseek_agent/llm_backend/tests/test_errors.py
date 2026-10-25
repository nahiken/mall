"""P2-07：异常分类与降级日志。

验收要求「人为制造超时/429，日志能区分」。这里不依赖真实上游：构造带
``status_code`` 的假异常即可覆盖分类逻辑；日志格式用 caplog 断言字段可解析。
"""
import asyncio
import json
import logging

import httpx
import pytest

from mall_agent import errors
from mall_agent.errors import (
    AUTH,
    BAD_REQUEST,
    CANCELLED,
    CONNECT,
    PARSE,
    RATE_LIMIT,
    SERVER_ERROR,
    TIMEOUT,
    UNKNOWN,
    classify_exception,
    classify_status,
    exc_fields,
    log_degraded,
    log_degraded_throttled,
)


class _HttpError(Exception):
    def __init__(self, status: int, message: str = "boom"):
        super().__init__(message)
        self.status_code = status


# ── 分类矩阵 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc,expected",
    [
        (asyncio.TimeoutError(), TIMEOUT),
        (TimeoutError(), TIMEOUT),
        (httpx.ReadTimeout("slow"), TIMEOUT),
        (_HttpError(429), RATE_LIMIT),
        (_HttpError(401), AUTH),
        (_HttpError(403), AUTH),
        (_HttpError(500), SERVER_ERROR),
        (_HttpError(503), SERVER_ERROR),
        (_HttpError(400), BAD_REQUEST),
        (_HttpError(404), BAD_REQUEST),
        (httpx.ConnectError("refused"), CONNECT),
        (ConnectionError("reset"), CONNECT),
        (json.JSONDecodeError("Expecting value", "x", 0), PARSE),
        (ValueError("invalid intent"), PARSE),
        (KeyError("terms"), PARSE),
        (RuntimeError("boom"), UNKNOWN),
        (asyncio.CancelledError(), CANCELLED),
    ],
)
def test_classify_exception(exc, expected):
    assert classify_exception(exc) == expected


def test_timeout_wins_over_status_code():
    """超时异常常带 status_code=0/None，不能被误判成 bad_request。"""

    class _TimeoutWithStatus(httpx.ReadTimeout):
        status_code = 408

    assert classify_exception(_TimeoutWithStatus("slow")) == TIMEOUT


def test_status_from_nested_response_object():
    class _Resp:
        status_code = 429

    class _Err(Exception):
        response = _Resp()

    assert classify_exception(_Err("limited")) == RATE_LIMIT


def test_bool_status_is_not_treated_as_int():
    class _Err(Exception):
        status_code = True

    assert classify_exception(_Err("weird")) == UNKNOWN


@pytest.mark.parametrize(
    "status,expected",
    [(429, RATE_LIMIT), (401, AUTH), (403, AUTH), (500, SERVER_ERROR),
     (502, SERVER_ERROR), (400, BAD_REQUEST), (418, BAD_REQUEST),
     (None, UNKNOWN), (302, UNKNOWN)],
)
def test_classify_status(status, expected):
    assert classify_status(status) == expected


def test_classify_does_not_import_optional_clients():
    """分类模块必须只依赖标准库：它在 import 链上，不能拖进 openai/redis。"""
    source = (errors.__file__ or "")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "import openai" not in text
    assert "import redis" not in text


# ── 结构化字段 ──────────────────────────────────────────────────────────


def test_exc_fields_shape_and_truncation():
    fields = exc_fields(_HttpError(429, "x" * 1000), hop="intent")
    assert fields["hop"] == "intent"
    assert fields["exc_type"] == "_HttpError"
    assert fields["exc_category"] == RATE_LIMIT
    assert len(fields["exc_message"]) == 200


# ── 日志输出 ────────────────────────────────────────────────────────────


def test_log_degraded_emits_category_and_type(caplog):
    logger = logging.getLogger("mall_agent.test_errors")
    with caplog.at_level(logging.WARNING, logger="mall_agent.test_errors"):
        category = log_degraded(logger, "intent", _HttpError(429), message="falling back")

    assert category == RATE_LIMIT
    line = caplog.records[0].getMessage()
    assert "falling back" in line
    assert "hop=intent" in line
    assert "category=rate_limit" in line
    assert "type=_HttpError" in line


def test_timeout_and_rate_limit_lines_are_distinguishable(caplog):
    """「日志能区分超时和 429」是 P2-07 的验收条件。"""
    logger = logging.getLogger("mall_agent.test_errors")
    with caplog.at_level(logging.WARNING, logger="mall_agent.test_errors"):
        log_degraded(logger, "intent", asyncio.TimeoutError("slow"))
        log_degraded(logger, "intent", _HttpError(429))
    first, second = (r.getMessage() for r in caplog.records)
    assert "category=timeout" in first
    assert "category=rate_limit" in second


def test_cancelled_is_logged_at_debug_not_warning(caplog):
    logger = logging.getLogger("mall_agent.test_errors")
    with caplog.at_level(logging.DEBUG, logger="mall_agent.test_errors"):
        log_degraded(logger, "shutdown", asyncio.CancelledError())
    record = caplog.records[0]
    assert record.levelno == logging.DEBUG
    assert "category=cancelled" in record.getMessage()


def test_throttled_logger_is_per_hop(caplog, monkeypatch):
    """一个 hop 刷屏不能把另一个 hop 的告警压掉。"""
    monkeypatch.setattr(errors, "_last_warn_by_hop", {})
    logger = logging.getLogger("mall_agent.test_errors")
    with caplog.at_level(logging.WARNING, logger="mall_agent.test_errors"):
        assert log_degraded_throttled(logger, "intent", RuntimeError("a")) is not None
        assert log_degraded_throttled(logger, "intent", RuntimeError("b")) is None
        assert log_degraded_throttled(logger, "rag", RuntimeError("c")) is not None
    assert len(caplog.records) == 2

"""共享 DeepSeek 客户端（P2-04 从 service.py 拆出）。

原来 service.py 既是编排层又持有客户端单例，retrieval 想调 LLM 只能反向 import
service —— 拆出来是为了让"谁都能拿 client，且全进程只有一个连接池"。
"""
import logging

import httpx
from openai import AsyncOpenAI

from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    LLM_CONNECT_TIMEOUT_SECONDS,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
)

logger = logging.getLogger("mall_agent.llm")


# Process-lifetime shared DeepSeek (OpenAI-compatible) client. Previously every
# _client() call constructed a fresh AsyncOpenAI — recreating its HTTP
# connection per LLM call (3 calls/turn). Single event loop ⇒ module singleton
# is safe. Set lazily so import never touches the network.
_llm_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _llm_client
    if _llm_client is None:
        # P1-01: bounded timeout/retries — a single stuck call must not hold a
        # worker for the SDK default (10 min) or retry forever on 429.
        _llm_client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=httpx.Timeout(LLM_TIMEOUT_SECONDS, connect=LLM_CONNECT_TIMEOUT_SECONDS),
            max_retries=LLM_MAX_RETRIES,
        )
    return _llm_client


# 兼容旧名字：拆分前各模块都是 `service._client()`，测试也按这个叫法注入假客户端。
_client = client


async def aclose_client() -> None:
    """Close the shared DeepSeek client (idempotent). Called on app shutdown."""
    global _llm_client
    if _llm_client is not None:
        try:
            await _llm_client.close()
        finally:
            _llm_client = None


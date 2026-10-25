"""P2-01：RAG 知识库客户端（失败降级 / 截断 / 运行期开关）。

knowledge_client 的契约是「检索失败绝不能拖垮对话」：任何超时、非 200、畸形
payload 都必须返回 ``[]`` 而不是抛异常。这条路径平时几乎不触发，一旦改动
（比如给 post 加了 await、改了异常类型）就会在生产的高峰期把整轮对话打断，
所以用假 HTTP 客户端把每个失败分支都钉住。
"""
import logging

import httpx
import pytest

from mall_agent import knowledge_client as kc


class _Response:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeHTTP:
    def __init__(self, response=None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.is_closed = False   # knowledge_client._http() 会读这个属性
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url, json=None):
        self.calls.append((url, json or {}))
        if self._exc is not None:
            raise self._exc
        return self._response


@pytest.fixture
def rag(monkeypatch):
    """打开 RAG，并把 httpx 客户端换成可编程假实现。"""

    def install(response=None, exc=None):
        fake = _FakeHTTP(response, exc)
        monkeypatch.setattr(kc, "_client", fake)
        monkeypatch.setattr(kc, "_enabled", True)
        monkeypatch.setattr(kc, "_url_configured", True)
        monkeypatch.setattr(kc, "RAG_HTTP_URL", "http://rag.test/")
        monkeypatch.setattr(kc, "RAG_TOP_K", 3)
        monkeypatch.setattr(kc, "RAG_SNIPPET_MAX_CHARS", 10)
        monkeypatch.setattr(kc, "_last_warn", 0.0)
        return fake

    return install


async def test_disabled_service_returns_empty_without_http(monkeypatch):
    monkeypatch.setattr(kc, "_enabled", False)
    assert await kc.search("手机") == []


async def test_blank_query_returns_empty(rag):
    fake = rag(_Response(payload={"results": []}))
    assert await kc.search("   ") == []
    assert fake.calls == []


async def test_successful_query_returns_snippets(rag):
    fake = rag(_Response(payload={"results": [{"text": "七天无理由退货"}]}))
    assert await kc.search("退货政策") == ["七天无理由退货"]
    url, payload = fake.calls[0]
    assert url == "http://rag.test/query"     # 末尾斜杠不应产生双斜杠
    assert payload == {"text": "退货政策", "top_k": 3}


async def test_snippets_are_truncated_and_capped(rag):
    results = [{"text": f"片段{i}" * 20} for i in range(5)]
    rag(_Response(payload={"results": results}))
    snippets = await kc.search("手机", top_k=2)
    assert len(snippets) == 2
    assert all(len(s) <= 10 for s in snippets)


async def test_blank_snippets_are_skipped(rag):
    rag(_Response(payload={"results": [{"text": "  "}, {"text": "有内容"}]}))
    assert await kc.search("手机") == ["有内容"]


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
async def test_non_200_degrades_to_empty(rag, status):
    rag(_Response(status_code=status, text="oops"))
    assert await kc.search("手机") == []


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.HTTPError("generic"),
    ],
)
async def test_transport_errors_degrade_to_empty(rag, exc):
    rag(exc=exc)
    assert await kc.search("手机") == []


async def test_malformed_payload_degrades_to_empty(rag):
    rag(_Response(payload=ValueError("not json")))
    assert await kc.search("手机") == []


async def test_payload_without_results_key_is_empty(rag):
    rag(_Response(payload={"other": []}))
    assert await kc.search("手机") == []


async def test_payload_results_wrong_shape_degrades(rag):
    rag(_Response(payload={"results": "not-a-list"}))
    assert await kc.search("手机") == []


async def test_warnings_are_throttled(rag, caplog):
    rag(_Response(status_code=500, text="boom"))
    with caplog.at_level(logging.WARNING, logger="mall_agent.kb"):
        await kc.search("手机")
        await kc.search("手机")
        await kc.search("手机")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected 1 throttled warning, got {len(warnings)}"


# ── 运行期开关 ─────────────────────────────────────────────────────────


def test_set_enabled_requires_configured_url(monkeypatch):
    monkeypatch.setattr(kc, "_url_configured", False)
    assert kc.set_enabled(True) is False   # 没有 URL 也开不起来
    assert kc.enabled() is False


def test_set_enabled_can_disable(monkeypatch):
    monkeypatch.setattr(kc, "_url_configured", True)
    assert kc.set_enabled(False) is False


def test_set_enabled_turns_on_when_url_configured(monkeypatch):
    monkeypatch.setattr(kc, "_url_configured", True)
    assert kc.set_enabled(True) is True
    assert kc.enabled() is True


async def test_aclose_client_is_idempotent():
    await kc.aclose_client()
    await kc.aclose_client()
    assert kc._client is None

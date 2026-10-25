"""P2-01：意图识别（规则短路 / JSON 解析 / LLM 降级）。

P1-01 把「意图 + 检索词扩展」合并成一次调用，并加了规则短路。这两处都是
**每次对话都会走**的热路径，出错的表现是「多一次 LLM、多 1~2 秒延迟」而不是
报错，所以必须有断言把它们钉住：

- 规则短路必须只在「明确短句」上命中，长句/含糊句交回 LLM；
- 规则短路返回的 payload 必须带 ``terms_ready=True``，否则 service 会多调一次
  检索词扩展，P1-01 省下的那次 LLM 又还回去了；
- LLM 失败必须降级且 ``terms_ready=False``（降级回旧路径要有检索词）。
"""
import pytest

from mall_agent import intent_classifier as ic
from mall_agent import stats as stats_mod


class _Msg:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Msg(content)


class _Usage:
    prompt_tokens = 11
    completion_tokens = 7


class _Response:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]
        self.usage = _Usage()


class _Completions:
    def __init__(self, content: str | None, exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return _Response(self._content)


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _Client:
    def __init__(self, completions):
        self.chat = _Chat(completions)


@pytest.fixture
def stub_client(monkeypatch):
    """把 intent 模块的 OpenAI client 换成可编程假客户端。"""

    def install(content: str | None = None, exc: Exception | None = None):
        completions = _Completions(content, exc)
        monkeypatch.setattr(ic, "_client", lambda: _Client(completions))
        return completions

    return install


# ── 规则短路 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    ["你好", "您好", "hello", "Hi!", "在吗？", "谢谢", "你是谁", " 再见。 "],
)
def test_rule_short_circuit_chat_phrases(message):
    result = ic.rule_short_circuit(message)
    assert result is not None
    assert result["intent"] == "chat"
    assert result["category"] is None
    assert result["terms"] == []
    assert result["terms_ready"] is True
    assert result["reason"].startswith("规则")


@pytest.mark.parametrize(
    "message,expected_intent,expected_kw",
    [
        ("想买手机", "recommend", "手机"),
        ("手机多少钱", "product_query", "手机"),
        ("有没有运动鞋", "recommend", "鞋"),   # 别名表按声明顺序取第一个命中
        ("推荐个笔记本", "recommend", "笔记本"),
        ("零食价格", "product_query", "零食"),
    ],
)
def test_rule_short_circuit_category_plus_buy_word(
    message, expected_intent, expected_kw
):
    result = ic.rule_short_circuit(message)
    assert result is not None
    assert result["intent"] == expected_intent
    assert result["keywords"] == expected_kw
    # 规则已给出检索词 → 不能再触发一次 LLM 扩展
    assert result["terms"] == [expected_kw]
    assert result["terms_ready"] is True


@pytest.mark.parametrize(
    "message",
    [
        "",                       # 空串
        "   ",                    # 纯空白
        "今天天气不错",            # 无品类关键词
        "帮我推荐一下",            # 有推荐词但无品类
        "我想买一台适合写代码、预算八千左右、屏幕要好一点的笔记本电脑",  # 太长
    ],
)
def test_rule_short_circuit_declines_unclear_messages(message):
    assert ic.rule_short_circuit(message) is None


def test_fallback_intent_without_keyword_is_chat():
    result = ic._fallback_intent("随便聊聊")
    assert result["intent"] == "chat"
    # chat 必须清空 category/keywords，否则会去查商品
    assert result["category"] is None and result["keywords"] is None


def test_fallback_intent_aliases_map_to_canonical_category():
    result = ic._fallback_intent("想买一件T恤")
    assert result["category"] == "服装"
    assert result["keywords"] == "T恤"


# ── JSON 解析 ───────────────────────────────────────────────────────────


def test_parse_intent_fused_payload_keeps_terms():
    payload = (
        '{"intent":"recommend","category":"手机","keywords":"手机",'
        '"terms":["手机","智能手机","5G手机"],"reason":"想买手机"}'
    )
    parsed = ic._parse_intent(payload, with_terms=True)
    assert parsed["intent"] == "recommend"
    assert parsed["terms"] == ["手机", "智能手机", "5G手机"]
    assert parsed["terms_ready"] is True


def test_parse_intent_caps_terms_at_five():
    payload = (
        '{"intent":"recommend","category":"手机","keywords":"手机","terms":'
        '["a","b","c","d","e","f","g"]}'
    )
    assert ic._parse_intent(payload, with_terms=True)["terms"] == list("abcde")


def test_parse_intent_strips_blank_terms():
    payload = '{"intent":"recommend","keywords":"手机","terms":["手机","  ","",null]}'
    assert ic._parse_intent(payload, with_terms=True)["terms"] == ["手机"]


def test_parse_intent_forces_chat_to_null_fields():
    payload = '{"intent":"chat","category":"手机","keywords":"手机","terms":["手机"]}'
    parsed = ic._parse_intent(payload, with_terms=True)
    assert parsed["category"] is None and parsed["keywords"] is None
    assert parsed["terms"] == []


def test_parse_intent_tolerates_code_fence():
    payload = '```json\n{"intent":"chat","reason":"问候"}\n```'
    assert ic._parse_intent(payload, with_terms=False)["intent"] == "chat"


def test_parse_intent_legacy_path_has_no_terms_key():
    payload = '{"intent":"recommend","keywords":"手机","terms":["手机"]}'
    parsed = ic._parse_intent(payload, with_terms=False)
    assert "terms" not in parsed and "terms_ready" not in parsed


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",                                  # 不是对象
        '{"intent":"buy"}',                    # 意图不在枚举内
        '{"intent":"chat"',                    # 截断
    ],
)
def test_parse_intent_rejects_bad_payloads(payload):
    with pytest.raises(ValueError):
        ic._parse_intent(payload, with_terms=True)


# ── LLM 调用与降级 ──────────────────────────────────────────────────────


async def test_classify_fused_success_returns_terms_ready(stub_client):
    completions = stub_client(
        '{"intent":"recommend","category":"手机","keywords":"手机",'
        '"terms":["手机"],"reason":"ok"}'
    )
    result = await ic.classify_fused("想买手机")
    assert result["terms_ready"] is True
    assert result["terms"] == ["手机"]
    assert "degraded" not in result
    # 合并调用只打一次 LLM
    assert len(completions.calls) == 1
    assert completions.calls[0]["max_tokens"] == 260
    assert completions.calls[0]["temperature"] == 0.0


async def test_classify_intent_legacy_uses_intent_prompt(stub_client):
    completions = stub_client('{"intent":"chat","reason":"问候"}')
    result = await ic.classify_intent("你好")
    assert result["intent"] == "chat"
    # 旧路径不做检索词，terms_ready 必须是 False（否则 service 会跳过扩展）
    assert result["terms_ready"] is False
    assert completions.calls[0]["max_tokens"] == 220


async def test_classify_fused_degrades_on_llm_error(stub_client):
    stub_client(exc=RuntimeError("boom"))
    result = await ic.classify_fused("想买手机")
    assert result["degraded"] is True
    # 降级必须把 terms_ready 置 False，让 service 继续走一次检索词扩展
    assert result["terms_ready"] is False
    assert result["terms"] == []
    assert result["intent"] == "recommend"
    assert result["keywords"] == "手机"


async def test_classify_fused_degrades_on_bad_json(stub_client):
    stub_client("这不是 JSON")
    result = await ic.classify_fused("想买手机")
    assert result["degraded"] is True
    assert result["keywords"] == "手机"


async def test_classify_records_success_and_failure(stub_client, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        stats_mod.stats,
        "record_llm",
        lambda ok, **kw: calls.append((ok, kw)),
    )

    stub_client('{"intent":"chat"}')
    await ic.classify_fused("你好")
    assert calls[0][0] is True
    assert calls[0][1]["tokens_in"] == 11
    assert calls[0][1]["tokens_out"] == 7

    stub_client(exc=RuntimeError("boom"))
    await ic.classify_fused("你好")
    assert calls[1][0] is False


def test_warn_degraded_is_throttled(monkeypatch, caplog):
    """降级日志必须节流：每个用户失败一次就打一条会把日志刷爆。"""
    monkeypatch.setattr(ic, "_last_warn_ts", 0.0)
    with caplog.at_level("WARNING", logger="mall_agent.intent"):
        ic._warn_degraded("intent", RuntimeError("first"))
        ic._warn_degraded("intent", RuntimeError("second"))
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 1
    assert "RuntimeError" in messages[0]

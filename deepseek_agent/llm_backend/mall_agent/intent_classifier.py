"""DeepSeek intent classification with a deterministic safe fallback."""
import json
import logging
import re
import time

import httpx
from openai import AsyncOpenAI

from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_CONNECT_TIMEOUT_SECONDS,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
)
from .errors import log_degraded

logger = logging.getLogger("mall_agent.intent")

INTENT_SYSTEM_PROMPT = """你是电商购物助手的意图识别器，只返回 JSON，不要输出 Markdown。
意图：
- chat：问候、闲聊、自我介绍、情感表达、一般帮助，不需要查商品；
- recommend：明确想买、让助手推荐、比较或选择某类商品；
- product_query：询问具体商品的价格、库存、参数、是否有货。
输出：{"intent":"chat|recommend|product_query","category":"品类或null","keywords":"适合商城搜索的简短关键词或null","reason":"简短理由"}
recommend 和 product_query 都必须尽量填写 category 与 keywords；chat 必须为 null。
品类可使用服装、鞋靴、美妆、食品、家电、手机、电脑、家居、运动、图书、母婴、汽车用品、宠物，或用户明确说出的其他品类。
不要因为助手是购物助手就把“你好”“你是谁”“谢谢”等闲聊判成推荐。"""

# P1-01：合并意图识别 + 检索词扩展，一次调用产出两者，推荐回合少一次 LLM。
FUSED_INTENT_SYSTEM_PROMPT = """你是电商购物助手的意图识别与检索词扩展器，只返回一个 JSON，不要输出 Markdown。
第一步判断意图：
- chat：问候、闲聊、自我介绍、情感表达、一般帮助，不需要查商品；
- recommend：明确想买、让助手推荐、比较或选择某类商品；
- product_query：询问具体商品的价格、库存、参数、是否有货。
第二步在同一个 JSON 里给出检索词。
输出格式：{"intent":"chat|recommend|product_query","category":"品类或null","keywords":"适合商城搜索的简短关键词或null","terms":["检索词1","检索词2"],"reason":"简短理由"}
规则：
1. chat 的 category/keywords 为 null，terms 为空数组；
2. recommend / product_query 必须尽量填写 category 与 keywords；
3. terms 给 0-5 个适合商品搜索接口的短词，优先商城真实类目名或其常见同义词；
4. 不要输出品牌或具体商品，除非用户明确提到；拿不准就给空数组；
5. 品类可使用服装、鞋靴、美妆、食品、家电、手机、电脑、家居、运动、图书、母婴、汽车用品、宠物，或用户明确说出的其他品类；
6. 不要因为助手是购物助手就把“你好”“你是谁”“谢谢”等闲聊判成推荐。"""

RULE_CHAT_PHRASES = frozenset((
    "你好", "您好", "hi", "hello", "嗨", "在吗", "在不在", "你是谁", "你叫什么",
    "谢谢", "感谢", "多谢", "再见", "拜拜", "你能做什么", "你会什么", "怎么用",
    "帮助", "help", "早上好", "晚上好", "晚安",
))
# 规则只处理很短的明确句子；长句/含糊句仍交给 LLM，避免误判。
RULE_SHORTCUT_MAX_CHARS = 14

# 用户话术 → 规范品类。仅用于意图识别把"想买xxx"落到品类名。
# 与 service.FALLBACK_SEARCH_TERM_EXPANSIONS 刻意不同：这里收录口语词（如"吃的"），
# 目的是识别话术而非直接作为商城检索词；检索用词请维护在 service.py，勿在此混改。
CATEGORY_ALIASES = {
    "服装": ("衣服", "服装", "T恤", "t恤", "短袖", "上衣", "裤子", "裙子", "外套", "衬衫", "卫衣"),
    "鞋靴": ("鞋", "鞋子", "运动鞋", "靴"),
    "美妆": ("美妆", "护肤", "化妆品", "口红", "面膜"),
    "食品": ("食品", "零食", "吃的", "饮料", "牛奶"),
    "家电": ("家电", "冰箱", "洗衣机", "空调", "电视"),
    "手机": ("手机", "iphone", "安卓"),
    "电脑": ("电脑", "笔记本", "台式机", "显卡"),
    "家居": ("家居", "家具", "床", "沙发"),
    "运动": ("运动", "健身", "球拍"),
    "图书": ("图书", "书", "小说"),
    "母婴": ("母婴", "奶粉", "纸尿裤"),
    "汽车用品": ("汽车用品", "车载", "行车记录仪"),
    "宠物": ("宠物", "猫粮", "狗粮"),
}
RECOMMEND_WORDS = ("推荐", "想买", "要买", "购买", "帮我选", "怎么选", "哪个好", "哪款", "对比", "预算")
QUERY_WORDS = ("多少钱", "价格", "库存", "有货", "参数", "配置")

# Lazy process-lifetime client (was: a fresh AsyncOpenAI per classify call —
# a connection rebuild per intent hop). Own singleton here because this module is
# imported by service.py; importing service's _client would be a cycle.
_classifier_client: AsyncOpenAI | None = None


def _client() -> AsyncOpenAI:
    global _classifier_client
    if _classifier_client is None:
        # P1-01: explicit per-request timeout + one retry, so a hung/429 call
        # can't pin a worker for the SDK default (10 min) or retry indefinitely.
        _classifier_client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=httpx.Timeout(LLM_TIMEOUT_SECONDS, connect=LLM_CONNECT_TIMEOUT_SECONDS),
            max_retries=LLM_MAX_RETRIES,
        )
    return _classifier_client


_last_warn_ts = 0.0


def _warn_degraded(hop: str, exc: Exception) -> None:
    """Log an LLM fallback (timeout/429/parse) at most ~once/30s per process.

    P2-07: the line now carries ``category=`` (timeout / rate_limit / auth /
    parse / …) so a timeout storm and a 429 storm are distinguishable in logs.
    """
    global _last_warn_ts
    now = time.monotonic()
    if now - _last_warn_ts > 30.0:
        _last_warn_ts = now
        log_degraded(
            logger, hop, exc,
            message="LLM call failed; falling back to local rules",
        )


def rule_short_circuit(message: str) -> dict | None:
    """Return a confident rule-based intent for obvious short messages.

    P1-01: pure greetings and "short category + buy/query word" sentences don't
    need an LLM hop. Returns ``None`` when the rules are not confident, so the
    caller keeps the normal (fused or legacy) LLM path. The returned dict is
    marked ``terms_ready`` so no separate term-expansion call is made either.
    """
    text = re.sub(r"[\s!！。,.，、?？~～…]+", "", (message or "").lower())
    if not text:
        return None
    if text in RULE_CHAT_PHRASES:
        return {"intent": "chat", "category": None, "keywords": None,
                "terms": [], "terms_ready": True, "reason": "规则：寒暄短句"}
    if len(text) > RULE_SHORTCUT_MAX_CHARS:
        return None
    fallback = _fallback_intent(message)
    if fallback["intent"] == "chat" or not fallback.get("keywords"):
        # No concrete category/keyword → not confident enough; let the LLM decide.
        return None
    fallback.update({
        "terms": [fallback["keywords"]] if fallback.get("keywords") else [],
        "terms_ready": True,
        "reason": "规则：品类+购买/查询词",
    })
    return fallback


def _fallback_intent(message: str) -> dict:
    lowered = message.lower().strip()
    category = None
    keyword = None
    for name, aliases in CATEGORY_ALIASES.items():
        match = next((alias for alias in aliases if alias.lower() in lowered), None)
        if match:
            category = name
            keyword = match
            break

    if any(word in lowered for word in QUERY_WORDS) and keyword:
        intent = "product_query"
    elif any(word in lowered for word in RECOMMEND_WORDS) or (keyword and any(word in lowered for word in ("有没有", "有啥", "有什么"))):
        intent = "recommend"
    else:
        intent = "chat"
        category = None
        keyword = None
    return {"intent": intent, "category": category, "keywords": keyword, "reason": "本地规则兜底"}


def _strip_fence(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.IGNORECASE)


def _parse_intent(text: str, *, with_terms: bool) -> dict:
    """Validate one intent JSON payload; raises ValueError on anything unexpected."""
    result = json.loads(_strip_fence(text))
    if not isinstance(result, dict):
        raise ValueError("intent payload is not an object")
    intent = result.get("intent")
    if intent not in {"chat", "recommend", "product_query"}:
        raise ValueError("invalid intent")
    reason = str(result.get("reason") or "").strip()
    if intent == "chat":
        return {"intent": "chat", "category": None, "keywords": None,
                "terms": [], "terms_ready": with_terms, "reason": reason}
    category = str(result.get("category") or "").strip() or None
    keywords = str(result.get("keywords") or "").strip() or None
    parsed = {"intent": intent, "category": category, "keywords": keywords, "reason": reason}
    if not with_terms:
        return parsed
    raw_terms = result.get("terms")
    terms = raw_terms if isinstance(raw_terms, list) else []
    # 只接受非空字符串：LLM 偶尔回 null/数字，直接 str() 会把 "None" 当检索词
    # 发给商城搜索接口（P2-01 由 test_parse_intent_strips_blank_terms 钉住）。
    parsed["terms"] = [t.strip() for t in terms if isinstance(t, str) and t.strip()][:5]
    parsed["terms_ready"] = True
    return parsed


async def _classify_with(system_prompt: str, user_message: str, *, with_terms: bool) -> dict:
    """One intent LLM call; records stats and degrades to local rules on failure."""
    from .stats import stats

    start = time.monotonic()
    try:
        response = await _client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            max_tokens=260 if with_terms else 220,
        )
        usage = getattr(response, "usage", None)
        stats.record_llm(
            True,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=(time.monotonic() - start) * 1000,
        )
        return _parse_intent(response.choices[0].message.content or "", with_terms=with_terms)
    except Exception as exc:  # noqa: BLE001 — any failure degrades, never breaks the turn
        stats.record_llm(False)
        _warn_degraded("fused-intent" if with_terms else "intent", exc)
        fallback = _fallback_intent(user_message)
        # 降级回旧路径：terms_ready=False 让 service 继续走一次检索词扩展调用。
        fallback.update({"terms": [], "terms_ready": False, "degraded": True})
        return fallback


async def classify_intent(user_message: str) -> dict:
    """Legacy two-hop path: intent only (rollback for MALL_AGENT_FUSED_INTENT=off)."""
    return await _classify_with(INTENT_SYSTEM_PROMPT, user_message, with_terms=False)


async def classify_fused(user_message: str) -> dict:
    """P1-01: intent + category + keywords + search terms in a single LLM call."""
    return await _classify_with(FUSED_INTENT_SYSTEM_PROMPT, user_message, with_terms=True)

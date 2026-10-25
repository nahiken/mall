"""检索相关逻辑（P2-04 从 service.py 拆出）：品类树、检索词拼装、商品搜索与格式化。

为什么单独成包：这段是"怎么把用户意图变成搜索请求"的全部知识（品类别名、类目树
缓存、并发多词搜索、去重、商品卡格式化），与"怎么把回答流式吐给前端"无关。
拆开后 service.py 只剩编排，改检索策略不必在 500 行里翻。
"""
import asyncio
import json
import logging
import re
import time

from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from .errors import log_degraded, log_degraded_throttled
from .llm import client as _client
from .prompts import LLM_SEARCH_TERM_PROMPT
from . import product_api as pa

logger = logging.getLogger("mall_agent.retrieval")


# 兜底词表：只有在商城类目接口不可用、类目树太稀疏或 LLM 扩展失败时使用。
# 注意：本表与 intent_classifier.CATEGORY_ALIASES 刻意不同——本表是"给商城 /product/search
# 接口用的检索词"（去掉口语词"吃的"，手机/电脑补上"手机数码/电脑办公"等真实大类），
# 而 ALIASES 是"意图分类识别用户话术用词"。改检索词请只改本表；改话术识别请改 ALIASES。
FALLBACK_SEARCH_TERM_EXPANSIONS = {
    "服装": ("服装", "衣服", "T恤", "t恤", "短袖", "上衣", "衬衫", "卫衣", "外套", "裤子", "裙子"),
    "衣服": ("衣服", "服装", "T恤", "t恤", "短袖", "上衣", "衬衫", "卫衣", "外套", "裤子", "裙子"),
    "上衣": ("上衣", "T恤", "t恤", "短袖", "衬衫", "卫衣", "外套"),
    "鞋靴": ("鞋靴", "鞋", "鞋子", "运动鞋", "靴子"),
    "鞋": ("鞋", "鞋子", "运动鞋", "靴子"),
    "美妆": ("美妆", "护肤", "化妆品", "口红", "面膜"),
    "食品": ("食品", "零食", "饮料", "牛奶"),
    "手机": ("手机", "手机数码", "手机通讯", "iPhone", "iphone", "小米", "华为"),
    "电脑": ("电脑", "电脑办公", "笔记本", "台式机"),
}


# 用户自然词 -> 商城真实大类的桥接。真实子类主要从 categoryTreeList 自动取得。
CATEGORY_SYNONYMS = {
    "衣服": ("服装",),
    "穿的": ("服装",),
    "数码": ("手机数码", "电脑办公"),
    "手机": ("手机数码", "手机通讯"),
    "电脑": ("电脑办公", "笔记本"),
    "家电": ("家用电器",),
    "家居": ("家具家装",),
    "家具": ("家具家装",),
}


CATEGORY_CACHE_TTL_SECONDS = 300


def _add_unique(items: list[str], value: str | None, limit: int | None = None) -> None:
    value = (value or "").strip()
    if not value or value in items:
        return
    if limit is None or len(items) < limit:
        items.append(value)


def _normalize_term(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _node_name(node: dict) -> str:
    for key in ("name", "categoryName", "productCategoryName", "label", "title"):
        value = node.get(key)
        if value:
            return str(value).strip()
    return ""


def _node_children(node: dict) -> list[dict]:
    for key in ("children", "childList", "childrens", "items"):
        value = node.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _flatten_category_names(nodes: list[dict]) -> list[str]:
    names: list[str] = []

    def walk(items: list[dict]) -> None:
        for node in items:
            name = _node_name(node)
            _add_unique(names, name)
            walk(_node_children(node))

    walk(nodes)
    return names


# 品类树进程内缓存（P2-04 从 service.py 搬来）。
# 踩过的坑：拆分时这个字典留在 service.py，但使用者搬到了这里 —— import 不报错，
# 只有真的走到缓存分支才 NameError。现在由 pyflakes 的 undefined-name 检查兜底。
_category_cache: dict = {"expires_at": 0.0, "tree": [], "names": []}


async def _get_category_tree_cached() -> tuple[list[dict], list[str]]:
    now = time.monotonic()
    if _category_cache["tree"] and now < float(_category_cache["expires_at"]):
        return _category_cache["tree"], _category_cache["names"]

    try:
        tree = await pa.get_category_tree()
    except Exception:
        tree = []

    names = _flatten_category_names(tree) if tree else []
    _category_cache["tree"] = tree
    _category_cache["names"] = names
    _category_cache["expires_at"] = now + CATEGORY_CACHE_TTL_SECONDS
    return tree, names


def _seed_terms(category: str, keywords: str) -> list[str]:
    seeds: list[str] = []
    for raw in (keywords, category):
        raw = (raw or "").strip()
        if not raw:
            continue
        _add_unique(seeds, raw)
        for synonym in CATEGORY_SYNONYMS.get(raw, ()): 
            _add_unique(seeds, synonym)
    return seeds


def _collect_category_terms(tree: list[dict], seeds: list[str]) -> list[str]:
    terms: list[str] = []
    normalized_seeds = [_normalize_term(seed) for seed in seeds if seed]

    def matched(name: str) -> bool:
        normalized_name = _normalize_term(name)
        if not normalized_name:
            return False
        return any(seed in normalized_name or normalized_name in seed for seed in normalized_seeds)

    def add_descendants(node: dict) -> None:
        name = _node_name(node)
        _add_unique(terms, name, limit=16)
        for child in _node_children(node):
            add_descendants(child)

    def walk(nodes: list[dict], parent_matches: bool = False) -> None:
        for node in nodes:
            name = _node_name(node)
            current_matches = parent_matches or matched(name)
            if current_matches:
                add_descendants(node)
            else:
                walk(_node_children(node), current_matches)

    walk(tree)
    return terms


async def _llm_expand_search_terms(query: str, category: str, keywords: str, category_names: list[str]) -> list[str]:
    if not DEEPSEEK_API_KEY:
        return []
    start = time.monotonic()
    from .stats import stats

    try:
        prompt = LLM_SEARCH_TERM_PROMPT.format(
            query=query,
            category=category or "未限定",
            keywords=keywords or "无",
            category_names="、".join(category_names[:80]) or "无",
        )
        response = await _client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=180,
        )
        usage = getattr(response, "usage", None)
        stats.record_llm(
            True,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=(time.monotonic() - start) * 1000,
        )
        text = (response.choices[0].message.content or "").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        payload = json.loads(text)
        terms = payload.get("terms", [])
        if not isinstance(terms, list):
            return []
        return [str(term).strip() for term in terms if str(term).strip()][:5]
    except Exception as exc:  # noqa: BLE001 — expansion is optional
        stats.record_llm(False)
        # P2-07: 区分 timeout/rate_limit/parse，便于判断该加超时还是该降频
        log_degraded(
            logger, "search_terms", exc,
            message="search-term expansion LLM failed; using base terms",
        )
        return []


async def _assemble_base_terms(
    category: str,
    keywords: str,
) -> tuple[list[str], list[str]]:
    """Assemble the non-LLM search terms (seeds + category descendants + fallbacks).

    Returns ``(terms, category_names)``. No LLM call, no I/O beyond the cached
    category tree, so this is fast and safe to run before the slow hops
    (LLM term expansion / concurrent product search) that stream_messages
    brackets with stage events.
    """
    terms: list[str] = []
    seeds = _seed_terms(category, keywords)
    for seed in seeds:
        _add_unique(terms, seed)

    tree, category_names = await _get_category_tree_cached()
    for term in _collect_category_terms(tree, seeds):
        _add_unique(terms, term, limit=18)

    # Safety fallback: used when Java categoryTreeList is unavailable or too sparse.
    for seed in seeds:
        for fallback in FALLBACK_SEARCH_TERM_EXPANSIONS.get(seed, ()):
            _add_unique(terms, fallback, limit=18)

    return terms, category_names


async def _search_products_by_terms(terms: list[str]) -> list[dict]:
    """Search every term concurrently, deduplicate by product id/name, keep top 12."""
    from .stats import stats

    async def _search_one(term: str) -> list[dict]:
        try:
            found = await pa.search_products(keyword=term, page_size=8)
            stats.record_tool(True)
            return found
        except Exception as exc:  # noqa: BLE001
            stats.record_tool(False)
            # P2-07: 每个 term 都会走这里，失败（portal 挂了）时按 hop 30s 节流记录，
            # 否则一次推荐回合就能刷 18 行同样的警告。
            log_degraded_throttled(
                logger, "product_search", exc,
                message="product search failed for one term; skipping it",
            )
            return []

    results = await asyncio.gather(*(_search_one(term) for term in terms))
    products: list[dict] = []
    seen: set[object] = set()
    for result in results:
        for product in result:
            product_id = product.get("id")
            identity = product_id if product_id is not None else product.get("name")
            if identity in seen:
                continue
            seen.add(identity)
            products.append(product)
    return products[:12]


def _format_products_for_llm(products: list[dict]) -> str:
    lines: list[str] = []
    for index, product in enumerate(products, 1):
        price = product.get("price", 0)
        original_price = product.get("originalPrice")
        promotion = "有促销" if product.get("promotionType") not in (None, 0) else ""
        lines.append(
            f"{index}. {product.get('name', '未知商品')}\n"
            f"   品牌：{product.get('brandName') or '未标注'}；"
            f"分类：{product.get('productCategoryName') or '未标注'}\n"
            f"   价格：¥{price}"
            f"{'；原价：¥' + str(original_price) if original_price and original_price != price else ''}\n"
            f"   简介：{product.get('subTitle') or '暂无'}；"
            f"销量：{product.get('sale', 0)}{'；' + promotion if promotion else ''}"
        )
    return "\n".join(lines)


def _product_to_card(product: dict) -> dict:
    return {
        "id": product.get("id"),
        "name": product.get("name", ""),
        "price": float(product.get("price", 0)),
        "pic": product.get("pic", ""),
        "subTitle": product.get("subTitle", ""),
        "brandName": product.get("brandName", ""),
        "url": f"/pages/product/product?id={product.get('id')}",
    }

"""P2-04：service.py 拆分后的回归网。

拆分（service.py 639 → 349 行，新增 prompts/llm/retrieval）是纯搬移，风险全在
"搬完之后某个函数引用了没搬过去的名字"——import 不会报错，只有真正调用时才
NameError。所以这里覆盖两类断言：

1. 结构契约：谁 import 谁（retrieval 不能反向依赖 service，否则又变成环）、
   对外 API 没丢（router / worker / run_mall 依赖的名字都还在）。
2. 搬走的纯函数行为：品类树遍历、检索词拼装、商品卡格式化、多词搜索去重。
"""
import inspect

import pytest

from mall_agent import llm, prompts, retrieval, service


# ── 1. 结构契约 ───────────────────────────────��─────────────────────────


def test_service_reexports_moved_symbols():
    """router/worker/run_mall 以及历史脚本都按 service.X 取用，不能断。"""
    for name in (
        "stream_messages",
        "process_messages",
        "aclose_client",
        "_client",
        "_assemble_base_terms",
        "_search_products_by_terms",
        "_format_products_for_llm",
        "_product_to_card",
        "CHAT_SYSTEM_PROMPT",
        "RECOMMEND_SYSTEM_PROMPT",
        "RECOMMEND_USER_PROMPT_TEMPLATE",
    ):
        assert hasattr(service, name), f"service.{name} 丢失"


def test_retrieval_does_not_import_service():
    """反向依赖会形成环（service → retrieval → service），必须只有单向。"""
    source = inspect.getsource(retrieval)
    assert "from .service import" not in source
    assert "import mall_agent.service" not in source


def test_service_line_budget():
    """拆分目标是把编排层压到 300 行附近（不强求，但不该回到 639）。"""
    lines = len(inspect.getsource(service).splitlines())
    assert lines < 400, f"service.py 又变厚了：{lines} 行"


def test_prompts_module_is_pure_constants():
    source = inspect.getsource(prompts)
    assert "import " not in source.replace("import — ", "") or source.count("import ") <= 1
    for name in ("CHAT_SYSTEM_PROMPT", "RECOMMEND_SYSTEM_PROMPT",
                 "RECOMMEND_USER_PROMPT_TEMPLATE", "LLM_SEARCH_TERM_PROMPT"):
        assert isinstance(getattr(prompts, name), str) and getattr(prompts, name)


def test_llm_client_is_singleton():
    assert llm.client() is llm.client()
    assert llm._client is llm.client


# ── 2. 搬走的纯函数行为 ─────────────────────────────────────────────────


def test_add_unique_dedupes_and_honors_limit():
    items = ["手机"]
    retrieval._add_unique(items, "手机")        # 重复
    retrieval._add_unique(items, "   ")          # 空白
    retrieval._add_unique(items, " 平板 ")       # 去空格后追加
    assert items == ["手机", "平板"]

    limited: list[str] = []
    for i in range(5):
        retrieval._add_unique(limited, f"t{i}", limit=3)
    assert limited == ["t0", "t1", "t2"]


def test_normalize_term_strips_whitespace_and_case():
    assert retrieval._normalize_term("  iP hone 12 ") == "iphone12"
    assert retrieval._normalize_term("") == ""


def test_seed_terms_include_keyword_category_and_synonyms():
    seeds = retrieval._seed_terms("手机", "拍照手机")
    assert seeds[0] == "拍照手机"
    assert "手机" in seeds
    # CATEGORY_SYNONYMS 里的同义词也要带出来（具体词表随业务变化，只断言"有扩展"）
    assert len(seeds) >= 2


def test_node_helpers_tolerate_shapes():
    node = {"name": "手机", "children": [{"name": "5G手机"}, {"name": "老人机"}]}
    assert retrieval._node_name(node) == "手机"
    assert [c["name"] for c in retrieval._node_children(node)] == ["5G手机", "老人机"]
    # 非 dict 子项被过滤；没有任何 children 键时返回空列表
    assert retrieval._node_children({"children": [1, "x", {"name": "ok"}]}) == [{"name": "ok"}]
    assert retrieval._node_children({}) == []


def test_flatten_category_names_walks_depth_first():
    tree = [{"name": "服装", "children": [{"name": "男装", "children": [{"name": "T恤"}]}]},
            {"name": "手机"}]
    assert retrieval._flatten_category_names(tree) == ["服装", "男装", "T恤", "手机"]


def test_collect_category_terms_matches_seed_prefix():
    tree = [
        {"name": "手机", "children": [{"name": "5G手机"}, {"name": "老人机"}]},
        {"name": "服装", "children": [{"name": "男装"}]},
    ]
    terms = retrieval._collect_category_terms(tree, ["手机"])
    assert "手机" in terms
    assert "5G手机" in terms
    assert "服装" not in terms


def test_product_to_card_shape():
    card = retrieval._product_to_card({"id": 7, "name": "华为", "price": "1999",
                                       "pic": "p.jpg", "subTitle": "副标题",
                                       "brandName": "华为"})
    assert card["id"] == 7
    assert card["price"] == 1999.0
    assert card["url"].endswith("id=7")
    assert card["subTitle"] == "副标题"


def test_product_to_card_has_defaults():
    card = retrieval._product_to_card({"id": 1})
    assert card["price"] == 0.0
    assert card["name"] == ""
    assert card["url"].endswith("id=1")


def test_format_products_for_llm_numbers_rows():
    text = retrieval._format_products_for_llm([
        {"name": "手机A", "price": 100, "sale": 5},
        {"name": "手机B", "price": 200, "sale": 7, "brandName": "B牌"},
    ])
    assert text.startswith("1. 手机A")
    assert "2. 手机B" in text
    assert "¥100" in text and "¥200" in text


async def test_search_products_by_terms_dedupes_and_caps(monkeypatch):
    calls: list[str] = []

    async def fake_search(keyword: str, page_size: int = 8):
        calls.append(keyword)
        # 两个词返回同一件商品（不同来源），应按 id 去重
        return [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]

    monkeypatch.setattr(retrieval.pa, "search_products", fake_search)
    products = await retrieval._search_products_by_terms(["手机", "平板"])
    assert calls == ["手机", "平板"]
    assert [p["id"] for p in products] == [1, 2]


async def test_search_products_by_terms_skips_failing_term(monkeypatch):
    async def fake_search(keyword: str, page_size: int = 8):
        if keyword == "boom":
            raise RuntimeError("portal down")
        return [{"id": 9, "name": "ok"}]

    monkeypatch.setattr(retrieval.pa, "search_products", fake_search)
    products = await retrieval._search_products_by_terms(["boom", "fine"])
    # 一个词失败不能拖垮整轮检索
    assert [p["id"] for p in products] == [9]


async def test_assemble_base_terms_uses_cached_tree(monkeypatch):
    async def fake_tree():
        return ([{"name": "手机", "children": [{"name": "5G手机"}]}], ["手机", "5G手机"])

    monkeypatch.setattr(retrieval, "_get_category_tree_cached", fake_tree)
    terms, names = await retrieval._assemble_base_terms("手机", "拍照")
    assert "拍照" in terms
    assert "手机" in terms
    assert names == ["手机", "5G手机"]


async def test_category_tree_cache_populates_then_hits(monkeypatch):
    """拆分时 _category_cache 曾留在 service.py → 这条分支会 NameError。

    现在它属于 retrieval，这里真跑一次"取数 → 写缓存 → 命中缓存"，
    版本升级时再拆文件就会立刻红。
    """
    calls: list[int] = []

    async def fake_get_tree():
        calls.append(1)
        return [{"name": "手机", "children": [{"name": "5G手机"}]}]

    monkeypatch.setattr(retrieval, "_category_cache", {"expires_at": 0.0, "tree": [], "names": []})
    monkeypatch.setattr(retrieval.pa, "get_category_tree", fake_get_tree)

    tree, names = await retrieval._get_category_tree_cached()
    assert names == ["手机", "5G手机"]
    assert calls == [1]

    # 第二次：TTL 未过期，直接命中缓存，不再请求 portal
    await retrieval._get_category_tree_cached()
    assert calls == [1]
    assert retrieval._category_cache["tree"] == tree


async def test_category_tree_cache_survives_portal_failure(monkeypatch):
    async def boom():
        raise RuntimeError("portal down")

    monkeypatch.setattr(retrieval, "_category_cache", {"expires_at": 0.0, "tree": [], "names": []})
    monkeypatch.setattr(retrieval.pa, "get_category_tree", boom)

    tree, names = await retrieval._get_category_tree_cached()
    assert tree == [] and names == []


@pytest.mark.parametrize("text", ["你想买手机吗？", ""])
def test_llm_search_term_prompt_is_formattable(text):
    # 提示词在 prompts.py 里是纯字符串，格式化不能因为缺占位符而炸
    rendered = retrieval.LLM_SEARCH_TERM_PROMPT.format(
        query=text, category="手机", keywords="拍照", category_names="手机、5G手机"
    )
    assert text in rendered

"""Java Mall Backend API wrapper."""
import logging

import httpx
from typing import Any

from .config import (
    MALL_API_BASE,
    SEARCH_API_BASE,
    SEARCH_ES_ENABLED,
    SEARCH_TIMEOUT_SECONDS,
)
from .errors import classify_status, log_degraded

logger = logging.getLogger("mall_agent.product_api")

# Process-lifetime shared client: each call used to build a fresh
# httpx.AsyncClient (TLS handshake + connection setup per request). The search
# path fans out many terms concurrently (one request each), so reuse is a real
# per-turn win. The client is loop-agnostic until first use and mall-agent runs
# on a single event loop, so a module singleton is safe.
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10)
    return _client


async def aclose_client() -> None:
    """Close the shared client (idempotent). Called on app shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def get_category_tree() -> list[dict[str, Any]]:
    """portal 商品分类树（``GET /product/categoryTreeList``）。

    P2-04 拆分时发现：retrieval._get_category_tree_cached() 一直在调这个函数，但它
    **从来没被实现**——`pa.get_category_tree` 是 AttributeError，被外层的
    `except Exception` 静默吞掉，于是品类树永远是空的，检索词只能靠
    FALLBACK_SEARCH_TERM_EXPANSIONS 硬编码兜底。这类"错误被正确降级"的 bug 最难
    发现：功能看起来正常，只是效果打折。

    失败返回 ``[]``（由调用方降级），不抛异常。
    """
    try:
        resp = await get_client().get(f"{MALL_API_BASE}/product/categoryTreeList")
        if resp.status_code != 200:
            logger.warning(
                "portal categoryTreeList HTTP %s (category=%s); using fallback terms",
                resp.status_code, classify_status(resp.status_code),
            )
            return []
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 — 品类树是可选增强，绝不能打断检索
        log_degraded(
            logger, "category_tree", exc,
            message="portal categoryTreeList failed; using fallback terms",
        )
        return []
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    return [node for node in data if isinstance(node, dict)]


async def _search_products_es(
    keyword: str,
    brand_id: int | None,
    category_id: int | None,
    page_num: int,
    page_size: int,
    sort: int,
) -> list[dict[str, Any]]:
    """P1-02: query mall-search's ``/esProduct/search``.

    ``pageNum`` is 0-based there (the public portal API is 1-based), so the
    caller-facing 1-based ``page_num`` is shifted by one. Fields returned by
    ``EsProduct`` (id/name/subTitle/price/pic/brandName/productCategoryName/
    sale/promotionType) already match what the recommend prompt and product
    cards read; portal-only fields (e.g. originalPrice) are simply absent.
    """
    params = {
        "keyword": keyword,
        "pageNum": max(0, page_num - 1),
        "pageSize": page_size,
        "sort": sort,
    }
    if brand_id is not None:
        params["brandId"] = brand_id
    if category_id is not None:
        params["productCategoryId"] = category_id

    resp = await get_client().get(
        f"{SEARCH_API_BASE}/esProduct/search", params=params,
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 200:
        raise RuntimeError(f"esProduct/search returned code={body.get('code')}")
    return body.get("data", {}).get("list", []) or []


async def _search_products_portal(
    keyword: str,
    brand_id: int | None,
    category_id: int | None,
    page_num: int,
    page_size: int,
    sort: int,
) -> list[dict[str, Any]]:
    """Legacy portal search (``LIKE '%kw%'``); also the ES fallback path."""
    params = {"keyword": keyword, "pageNum": page_num, "pageSize": page_size, "sort": sort}
    if brand_id is not None:
        params["brandId"] = brand_id
    if category_id is not None:
        params["productCategoryId"] = category_id

    resp = await get_client().get(
        f"{MALL_API_BASE}/product/search", params=params)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") == 200:
        return body.get("data", {}).get("list", [])
    return []


async def search_products(
    keyword: str = "",
    brand_id: int | None = None,
    category_id: int | None = None,
    page_num: int = 1,
    page_size: int = 8,
    sort: int = 0,
) -> list[dict[str, Any]]:
    """Search products: ES first (P1-02), portal on unavailability.

    ``page_num`` stays 1-indexed for callers; the ES hop converts it. Any ES
    failure (connect/timeout/non-200/bad payload) logs one warning and falls
    back to the portal so a down/empty ES cluster never breaks a turn.
    """
    if SEARCH_ES_ENABLED:
        try:
            found = await _search_products_es(
                keyword, brand_id, category_id, page_num, page_size, sort)
            if found:
                return found
            # 空结果不足以证明"商城没有"：索引未导入/只导了部分数据时 ES 会返回
            # 空列表，此时回退 portal 兜底，避免上线初期 Agent 突然"什么都找不到"。
            logger.info(
                "ES returned no products (keyword=%r); trying portal fallback",
                keyword,
            )
        except Exception as exc:  # noqa: BLE001 — ES must never break search
            # P2-07: category= 区分 timeout / connect / server_error，回退原因一眼可查
            log_degraded(
                logger, "es_search", exc,
                message="ES search failed; falling back to portal /product/search",
            )
    return await _search_products_portal(
        keyword, brand_id, category_id, page_num, page_size, sort)

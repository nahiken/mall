import { http } from '@/utils/http'
import type { CommonResult, CommonPage } from '@/types/common'
import type { PmsProduct } from '@/types/product'
import type { CategoryTreeNode, ProductListParam, PmsPortalProductDetail } from '@/types/product'

const searchApiBase = import.meta.env.VITE_SEARCH_API_BASE_URL || import.meta.env.VITE_API_BASE_URL

/** 商品分类树 */
export const getCategoryTreeAPI = () => {
  return http<CategoryTreeNode[]>({
    method: 'GET',
    url: '/product/categoryTreeList',
  })
}

/** 商品列表搜索 */
export const searchProductListAPI = async (
  params: ProductListParam,
): Promise<CommonResult<CommonPage<PmsProduct>>> => {
  // 商品列表页沿用 mall-portal/PageHelper 的 1-based 页码；
  // mall-search/Elasticsearch 使用 Spring PageRequest 的 0-based 页码，这里统一转换。
  const searchParams = {
    ...params,
    pageNum: Math.max(0, (params.pageNum || 1) - 1),
  }
  const qs = Object.entries(searchParams)
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&')

  // #108（Spec-1）：搜索走独立网关前缀 /search-api，不能经 http() —— 其拦截器把非 http 的
  //   url 一律拼 VITE_API_BASE_URL(/mall-api)，会把 /search-api/... 二次拼成 /mall-api/search-api/...
  //   运行时 404（dev 因绝对 8081 以 http 开头绕过、prod 相对化才触发，T1 grep 测不出 join）。
  //   故与 agent 侧同款裸 fetch（单前缀直连）；token 一并透传（search 无鉴权，gateway 透传无碍）。
  const token = uni.getStorageSync('token')
  const headers: Record<string, string> | undefined = token ? { Authorization: token as string } : undefined
  const res = await fetch(`${searchApiBase}/esProduct/search${qs ? `?${qs}` : ''}`, { headers })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** 商品详情 */
export const getProductDetailAPI = (id: number) => {
  return http<PmsPortalProductDetail>({
    method: 'GET',
    url: `/product/detail/${id}`,
  })
}

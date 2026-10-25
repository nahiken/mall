/**
 * AI 购物助手（悬浮球 / 悬浮窗）可见性范围。
 *
 * 悬浮球只应出现在“浏览商品”相关页面；登录/注册、购物车、我的/设置/地址、
 * 订单/创建订单、支付/支付成功、优惠券、通知等一律不出现。
 * 用“目录前缀白名单”而非黑名单：未来新增非浏览页天然不带悬浮球，最安全。
 */

export const BROWSE_SCOPE_PREFIXES = [
  '/pages/index/', // 首页
  '/pages/category/', // 分类
  '/pages/product/', // 商品列表/搜索/详情/人气/新品（全部是浏览页）
  '/pages/brand/', // 品牌列表 / 品牌详情
] as const

/** 路由（如 '/pages/product/product'）是否属于浏览白名单。空串返回 false。 */
export const isBrowseScopeRoute = (route: string): boolean =>
  !!route && BROWSE_SCOPE_PREFIXES.some((prefix) => route.startsWith(prefix))

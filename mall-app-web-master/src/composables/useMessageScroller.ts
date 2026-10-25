/**
 * useMessageScroller —— 聊天消息滚动条（吸底）逻辑的单一实现。
 *
 * 此前这份机制在 floating-agent.vue 与 pages/agent/index.vue 两处逐字复制
 * （stickToBottom / lastScrollTop / ignoreScrollUntil / isNearBottom /
 * jumpToBottom / jumpToBottomAfterRender / handleMessageScroll 共约 120 行）。
 * 这里收敛为一处；两个调用点只需提供"消息滚动容器"的 DOM 选择器。
 *
 * 设计要点
 * --------
 * - 组件用 :scroll-top="scrollTopRef" 驱动 <scroll-view>，配合 @scroll 回调维持吸底；
 *   对 H5 直接操作底层 DOM（scrollTop = scrollHeight）做兜底，保证两端行为一致。
 * - ``isGated``（默认 false）：为 true 时组件不可见/收起期间调用 jumpToBottom 直接跳过。
 *   floating-agent.vue 用 ``!agent.expanded`` 作门，agent/index.vue 不用（常驻全屏）。
 * - ``scrollNonce`` / ``messageCount`` 是"需要强制重新触底"的信号源：内容变化或 store 主动
 *   bump 后，调用方 watch 它们并触发 jumpToBottom。本 composable 不 watch，交由调用方决定时机。
 */

import { nextTick, ref } from 'vue'

export interface MessageScroller {
  /** 绑定到 <scroll-view :scroll-top> 的 ref。 */
  scrollTopRef: ReturnType<typeof ref<number>>
  /** 绑到 @scroll 的回调（吸底判定 + 用户上滑时解除吸底）。 */
  onScroll: (event: unknown) => void
  /** 强制滚到底部；force=true 无视吸底标记。 */
  jumpToBottom: (force?: boolean) => void
  /** 渲染后再补一次触底（setTimeout 60ms），用于消息刚插入时。 */
  jumpToBottomAfterRender: (force?: boolean) => void
  /** 外部显式置为吸底（发送前、展开时等）。 */
  stickToBottom: () => void
}

export function useMessageScroller(options: {
  /** 返回消息滚动容器的 DOM 元素（H5 端兜底直接滚）。 */
  getScroller: () => HTMLElement | null
  /** 滚动信号：store 的 scrollNonce 或消息长度等变化会强制触底。 */
  scrollNonce?: () => number
  messageCount?: () => number
  /** true=收起/隐藏期间调用 jumpToBottom 直接跳过（如悬浮窗未展开）。 */
  isGated?: () => boolean
  /** 触发 H5 端 DOM 滚动的事件回调（用于组件 @scroll 通知吸底状态变化）。 */
  onStickChange?: (stick: boolean) => void
}): MessageScroller {
  const { getScroller, scrollNonce, messageCount, isGated, onStickChange } = options
  const scrollTopRef = ref(0)
  let stick = true
  let lastScrollTop = 0
  let ignoreScrollUntil = 0

  const isNearBottom = (scrollTop: number): boolean => {
    const el = getScroller()
    if (!el) return true
    return el.scrollHeight - el.clientHeight - scrollTop < 40
  }

  const setStick = (value: boolean) => {
    if (stick !== value) {
      stick = value
      onStickChange?.(value)
    }
  }

  const jumpToBottom = (force = false): void => {
    if (isGated?.()) return
    if (!force && !stick) return
    ignoreScrollUntil = Date.now() + 100
    const bump = (scrollNonce?.() ?? 0) + (messageCount?.() ?? 0)
    nextTick(() => {
      const nextTop = 9999999 + bump
      scrollTopRef.value = nextTop
      const el = getScroller()
      if (el) {
        el.scrollTop = el.scrollHeight
        lastScrollTop = el.scrollTop
      } else {
        lastScrollTop = nextTop
      }
    })
  }

  const jumpToBottomAfterRender = (force = false): void => {
    jumpToBottom(force)
    setTimeout(() => jumpToBottom(force), 60)
  }

  const onScroll = (event: unknown): void => {
    const detail = (event as { detail?: { scrollTop?: number } })?.detail
    const scrollTop = Number(detail?.scrollTop ?? 0)
    if (Date.now() < ignoreScrollUntil) {
      lastScrollTop = scrollTop
      return
    }
    if (scrollTop < lastScrollTop - 8) {
      setStick(false)
    } else if (isNearBottom(scrollTop)) {
      setStick(true)
    }
    lastScrollTop = scrollTop
  }

  const stickToBottom = (): void => {
    setStick(true)
  }

  return {
    scrollTopRef,
    onScroll,
    jumpToBottom,
    jumpToBottomAfterRender,
    stickToBottom,
  }
}

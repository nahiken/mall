import { computed, nextTick, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { useMemberStore } from '@/stores/member'
import { isAbortError, parseSseStream } from '@/utils/sse'

export interface AgentProduct {
  id: number
  name: string
  price: number
  pic: string
  subTitle?: string
  brandName?: string
  url: string
}

/** 决策流步骤（后端 stage 事件，进入节点即下发；done 为结束哨兵不入列表） */
export interface AgentStep {
  node: string
  label: string
  at_ms: number
  duration_ms?: number
  detail?: Record<string, unknown>
}

/**
 * 历史消息明细里的可选 agent_trace：后端 worker 每轮落库的决策流记录。
 * 形状与 live 的 AgentStep 完全同构（trace 每步即一个 stage/step 数据），直接
 * 复用 AgentStep —— 保留本别名导出以兼容既有引用（stores/agent.ts 内部 HistoryMessage
 * 用它标注 agent_trace 的元素类型）。
 */
export type AgentTraceStep = AgentStep

/** 会话列表摘要（后端 /api/mall/conversations 的 items 单条），最新在前。
 * 「历史会话」页（pages/agent/history.vue）复用本类型，避免各端各自声明。 */
export interface ConversationSummary {
  id: number
  title: string
  message_count: number
  created_at: string
  updated_at: string
}

export interface AgentMessage {
  role: 'user' | 'assistant'
  content: string
  products?: AgentProduct[]
  loading?: boolean
  /** 实时或历史（agent_trace 回放）收集的决策步骤（竖向小卡片）。 */
  steps?: AgentStep[]
  /** 本轮是否已结束（收到 done/error，或历史消息带 agent_trace）——最后一步由“运行中”转“已完成” */
  finished?: boolean
  /** 回合结束时刻(ms)——为最后一步估算耗时；历史消息折算自落库末步 duration_ms */
  endMs?: number
}

/** 历史消息明细里的可选 agent_trace：后端 worker 每轮落库的决策流记录。
 * 形状与 live 的 AgentStep 完全同构（见上方 AgentTraceStep 别名）。 */
interface HistoryMessage {
  role: 'user' | 'assistant'
  content: string
  msg_type: string
  extra?: { agent_trace?: AgentTraceStep[] } | null
}

/**
 * 第 i 个步骤的已完成耗时(ms)：i 不是最后一步 → 下一行 at_ms 之差；
 * 是最后一步 → 已结束取 endMs 之差，未结束返回 null（前端显示 …）。
 * 纯函数导出，供 floating-agent.vue 与 pages/agent/index.vue 共用。
 * 耗时口径与 admin 端 chat/index.vue 的 stepDuration 是同一规则的跨库重复
 * （三处：本函数组 / admin stepDuration / 模板“末步三元”），R4 若改末步计时
 * 锚点（例如改用后端 done stage 的 at_ms）需三处一起改。
 */
export function stepDurationMs(message: AgentMessage, i: number): number | null {
  const steps = message.steps || []
  const current = steps[i]
  if (!current) return null
  const next = steps[i + 1]
  const endAt = next ? next.at_ms : message.endMs
  if (!endAt) return null
  return Math.max(0, endAt - current.at_ms)
}

export function formatStepDuration(ms: number | null): string {
  if (ms == null) return '…'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

const WELCOME_MESSAGE: AgentMessage = {
  role: 'assistant',
  content: '你好！我是小M，您的智能购物助手。可以和我聊天，也可以告诉我预算、用途和想买的品类。',
}

// P0-04：缺 env 时回退同源（prod 经 nginx /agent-api 反代），不再兜底 localhost。
const agentApi = import.meta.env.VITE_AGENT_BASE_URL || ''

/** #106：取本地登录 token（member.ts 存的是 `${tokenHead}${token}`，如 "Bearer xxx"）。
 * agent 端用它验签比对 user_id（见 deepseek_agent auth.py）；无 token 时为 ''。 */
function agentAuthHeader(): Record<string, string> {
  try {
    const token = uni.getStorageSync('token')
    return token ? { Authorization: token } : {}
  } catch {
    return {}
  }
}

const POSITION_STORAGE_KEY = 'mall-agent-floating-position'

interface FloatingPosition {
  ballX: number
  ballY: number
  panelX: number
  panelY: number
}

const DEFAULT_FLOATING_POSITION: FloatingPosition = {
  ballX: 295,
  ballY: 480,
  panelX: 12,
  panelY: 80,
}

const readFloatingPosition = (): FloatingPosition => {
  try {
    const stored = uni.getStorageSync(POSITION_STORAGE_KEY)
    if (stored && typeof stored === 'object') {
      return { ...DEFAULT_FLOATING_POSITION, ...stored }
    }
  } catch {
    // Ignore storage errors and use defaults.
  }
  return { ...DEFAULT_FLOATING_POSITION }
}

export const useAgentStore = defineStore('agent', () => {
  const memberStore = useMemberStore()
  const messages = ref<AgentMessage[]>([{ ...WELCOME_MESSAGE }])
  const conversationId = ref<number | null>(null)
  const sending = ref(false)
  const initializedUserId = ref('')
  const expanded = ref(false)
  const floatingPosition = reactive(readFloatingPosition())
  const scrollNonce = ref(0)
  const errorMessage = ref('')
  // P2-05：当前进行中那一轮 SSE 的取消句柄（同一时刻最多一轮，sending 互斥）
  let streamAbort: AbortController | null = null

  const userId = computed(() =>
    memberStore.memberInfo?.id ? String(memberStore.memberInfo.id) : '',
  )

  const touchScroll = () => {
    scrollNonce.value += 1
    nextTick(() => {
      scrollNonce.value += 1
    })
  }

  const saveFloatingPosition = () => {
    try {
      uni.setStorageSync(POSITION_STORAGE_KEY, { ...floatingPosition })
    } catch {
      // Ignore storage errors; Pinia still keeps the position during this app session.
    }
  }

  const setFloatingPosition = (patch: Partial<FloatingPosition>) => {
    Object.assign(floatingPosition, patch)
    saveFloatingPosition()
  }

  const requireLogin = () => {
    if (userId.value) return true
    uni.navigateTo({ url: '/pages/public/login' })
    return false
  }

  const resetLocalConversation = () => {
    messages.value = [{ ...WELCOME_MESSAGE }]
    conversationId.value = null
    sending.value = false
    errorMessage.value = ''
    touchScroll()
  }

  /**
   * 把一批历史消息(后端 items)还原为前端 AgentMessage 列表。
   * - products 独立 msg_type → 挂到前一条 assistant 文本消息；
   * - assistant 文本消息若带 extra.agent_trace(R4 落库) → 还原 steps 只读回放：
   *   trace 每步自带 duration_ms，末步折算进 endMs，使 stepDurationMs/模板
   *   “末步三元”直接复用（finished 恒 true → 全走“已完成”分支）。
   *
   * 耗时口径（记账）：中间步耗时前端由相邻 at_ms 重算（stepDurationMs），仅末步
   * 信任落库 duration_ms（折算进 endMs）；若改末步计时锚点（如改取 done stage 的
   * at_ms）需三处同改：本函数 / admin 端 stepDuration 组 / 模板“末步三元”。
   *
   * 跨库互指：本函数与 admin 端 views/agent/chat/index.vue 的 toMessage 是同一
   * “历史 trace 回放引擎”（products 合并 + endMs=末步 at_ms+duration_ms 折算）的
   * 两处跨库实现 —— 改任一口径需两侧同改。
   */
  const restoreFromHistory = (items: HistoryMessage[]): AgentMessage[] => {
    const restored: AgentMessage[] = []
    for (const item of items) {
      if (item.msg_type === 'products') {
        const previous = restored[restored.length - 1]
        if (previous?.role === 'assistant') {
          try {
            previous.products = JSON.parse(item.content)
          } catch {
            // Ignore malformed legacy product data.
          }
        }
        continue
      }
      const msg: AgentMessage = { role: item.role, content: item.content || '' }
      const trace = item.extra?.agent_trace
      if (item.role === 'assistant' && Array.isArray(trace) && trace.length) {
        const steps = trace.map((s) => ({
          node: s.node ?? '',
          label: s.label ?? s.node ?? '',
          at_ms: Number(s.at_ms) || 0,
          detail: s.detail,
        }))
        const last = trace[trace.length - 1]
        msg.steps = steps
        msg.finished = true
        // 仅末步信任落库 duration_ms（中间步由相邻 at_ms 重算，见函数头记账注释）：
        // endMs = 末步 at_ms + duration_ms
        msg.endMs = (Number(last?.at_ms) || 0) + (Number(last?.duration_ms) || 0)
      }
      restored.push(msg)
    }
    return restored
  }

  const fetchConversationMessages = async (id: number) => {
    const res = await fetch(
      `${agentApi}/api/mall/conversations/${id}/messages?user_id=${encodeURIComponent(userId.value)}&page=1&page_size=40`,
      { headers: { ...agentAuthHeader() } },
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    return restoreFromHistory((data?.items || []) as HistoryMessage[])
  }

  /** 会话列表（最新在前，供「历史会话」页渲染）。 */
  const listConversationSummaries = async (): Promise<ConversationSummary[]> => {
    if (!requireLogin()) return []
    const res = await fetch(
      `${agentApi}/api/mall/conversations?user_id=${encodeURIComponent(userId.value)}&page=1&page_size=50`,
      { headers: { ...agentAuthHeader() } },
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    return (data?.items || []) as Array<ConversationSummary>
  }

  /** 打开指定会话：加载其消息并写入 store（历史会话入口 / 自动恢复共用）。
   * 返回 boolean —— true 加载成功；false 表示未登录（已跳登录页）或加载失败，
   * 调用方据此决定是否继续（如 history.vue 仅在 true 时才 navigateBack）。 */
  const loadConversation = async (id: number): Promise<boolean> => {
    if (!requireLogin()) return false
    try {
      const restored = await fetchConversationMessages(id)
      conversationId.value = id
      if (restored.length > 0) messages.value = restored
      touchScroll()
      return true
    } catch (error) {
      errorMessage.value = 'AI 服务未启动，请先启动 deepseek_agent。'
      console.error('Load conversation failed:', error)
      return false
    }
  }

  const loadLatestConversation = async () => {
    if (!requireLogin()) return
    if (initializedUserId.value === userId.value) return

    initializedUserId.value = userId.value
    resetLocalConversation()

    try {
      const listResponse = await fetch(
        `${agentApi}/api/mall/conversations?user_id=${encodeURIComponent(userId.value)}&page=1&page_size=1`,
        { headers: { ...agentAuthHeader() } },
      )
      if (!listResponse.ok) throw new Error(`HTTP ${listResponse.status}`)
      const listData = await listResponse.json()
      const latest = listData?.items?.[0]
      if (!latest) return
      await loadConversation(latest.id)
    } catch (error) {
      errorMessage.value = 'AI 服务未启动，请先启动 deepseek_agent。'
      console.error('Load agent history failed:', error)
    }
  }

  const consumeEvent = (
    eventName: string,
    data: any,
    assistant: AgentMessage,
  ) => {
    if (eventName === 'meta' && data?.conversation_id) {
      conversationId.value = data.conversation_id
    } else if (eventName === 'stage' && data?.node && data.node !== 'done') {
      // 决策流步骤：进入节点即点亮（竖向小卡片）
      assistant.steps = assistant.steps || []
      assistant.steps.push(data as AgentStep)
      touchScroll()
    } else if (eventName === 'text' && data?.content) {
      assistant.loading = false
      assistant.content += data.content
      touchScroll()
    } else if (eventName === 'products' && Array.isArray(data?.products)) {
      assistant.products = data.products
      touchScroll()
    } else if (eventName === 'error') {
      assistant.finished = true
      throw new Error(data?.message || 'AI 服务响应失败')
    }
  }

  const sendMessage = async (rawText: string) => {
    const text = rawText.trim()
    if (!text || sending.value || !requireLogin()) return

    errorMessage.value = ''
    messages.value.push({ role: 'user', content: text })
    const assistant: AgentMessage = {
      role: 'assistant',
      content: '',
      loading: true,
    }
    messages.value.push(assistant)
    sending.value = true
    touchScroll()

    const history = messages.value
      .filter((item) => !item.loading && item.content)
      .slice(-12)
      .map(({ role, content }) => ({ role, content }))

    // P2-05：本轮流的取消句柄。离开会话页时 cancelStream() 会 abort 它，
    // 让服务端这一轮也停掉（不再白占一个 agent worker）。
    const abort = new AbortController()
    streamAbort = abort

    try {
      const response = await fetch(`${agentApi}/api/mall/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...agentAuthHeader() },
        body: JSON.stringify({
          messages: history,
          user_id: userId.value,
          conversation_id: conversationId.value,
        }),
        signal: abort.signal,
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      if (!response.body) throw new Error('浏览器不支持流式响应')

      await parseSseStream(
        response,
        (evt) => {
          if (evt.data === '[DONE]') {
            // 结束哨兵：该事件名由同一帧的 event: 给出
            if (evt.event === 'done') {
              assistant.finished = true
              assistant.endMs = Date.now()
            }
            return
          }
          consumeEvent(evt.event, evt.data, assistant)
        },
        { signal: abort.signal },
      )

      // 与 admin chat/index.vue 收流后兜底一致：若流被异常“干净”地结束（EOF 却无
      // done/error 终帧），这里收尾本轮——否则最后一步会一直脉动并显示 …。
      if (!assistant.finished) {
        assistant.finished = true
        assistant.endMs = Date.now()
      }
    } catch (error) {
      assistant.loading = false
      assistant.finished = true
      assistant.endMs = Date.now()
      if (isAbortError(error) || abort.signal.aborted) {
        // 主动离开页面取消：不是故障，不要把"已取消"写成"服务连不上"
        assistant.content = assistant.content || '（已取消）'
      } else {
        assistant.content = 'AI 服务暂时无法连接。请确认 8001 端口的 Mall Agent 已启动后重试。'
        errorMessage.value = assistant.content
        console.error('Agent stream failed:', error)
      }
    } finally {
      if (streamAbort === abort) streamAbort = null
      assistant.loading = false
      sending.value = false
      touchScroll()
    }
  }

  /**
   * P2-05：取消进行中的一轮（离开会话页/用户主动停止）。
   * abort 后 sendMessage 的 await 会正常返回，assistant 消息标记"已取消"。
   */
  const cancelStream = () => {
    const abort = streamAbort
    streamAbort = null
    if (!abort) return false
    abort.abort()
    sending.value = false
    return true
  }

  const hideHistory = async () => {
    if (!requireLogin()) return false
    const response = await fetch(
      `${agentApi}/api/mall/conversations/hide?user_id=${encodeURIComponent(userId.value)}`,
      { method: 'POST', headers: { ...agentAuthHeader() } },
    )
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    resetLocalConversation()
    return true
  }

  return {
    messages,
    conversationId,
    sending,
    expanded,
    floatingPosition,
    scrollNonce,
    errorMessage,
    userId,
    loadLatestConversation,
    loadConversation,
    listConversationSummaries,
    sendMessage,
    cancelStream,
    hideHistory,
    resetLocalConversation,
    touchScroll,
    setFloatingPosition,
  }
})

import type { CommonResult } from '@/types/common'
import { useUserStore } from '@/stores/user'
// P2-05：SSE 解析与 app 端共用一份（src/utils/sse.ts），避免协议细节两处漂移
import { parseSseStream } from '@/utils/sse'

// Agent 服务直连地址（独立于 Java 后端 8080）。
// P0-04：去掉 localhost 兜底字面量——缺 env 时回退同源（prod 经 nginx 反代），
// 而不是静默把请求打到开发者本机。
const agentApi = import.meta.env.VITE_AGENT_BASE_URL || ''

/**
 * P0-01：管理面（/api/monitor/*）鉴权头。
 * 复用 mall-admin 登录态 token（`Bearer ...`，与 mall-admin 的 JWT 同源），
 * Agent 侧用 ADMIN_JWT_SECRET 验签（见 mall_agent/monitor_auth.py）。
 * 不把 token 写进 VITE_* 变量：那样会被打进前端 bundle，等于公开密钥。
 */
function authHeaders(): Record<string, string> {
  const token = useUserStore().userInfo.token
  return token ? { Authorization: token } : {}
}

export interface AgentProduct {
  id: number
  name: string
  price: number
  pic: string
  subTitle?: string
  brandName?: string
  url: string
}

/** 一个决策流步骤（后端 stage 事件，node 开始时下发；done 为结束哨兵，不入列表） */
export interface AgentStep {
  node: string
  label: string
  at_ms: number
  detail?: Record<string, unknown>
}

export interface AgentMessage {
  role: 'user' | 'assistant'
  content: string
  products?: AgentProduct[]
  loading?: boolean
  /** 本轮实时收集的决策步骤（竖向卡片）；仅存在于本次前端会话内存，不随历史加载 */
  steps?: AgentStep[]
  /** 本轮是否已结束（收到 done/error）——用于把最后一个步骤从“运行中”转为已完成计时 */
  finished?: boolean
  /** 回合结束时刻(ms)——仅用于为最后一个步骤估算耗时；历史加载的消息不设 */
  endMs?: number
}

export interface ConversationItem {
  id: string
  session_id: string
  title: string
  created_at: number
  updated_at: number
}

export interface SseEvent {
  type: string
  data: unknown
}

/**
 * SSE 流式对话。
 *
 * P2-05：解析逻辑已抽到 `@/utils/sse`（与 app 端共用同一套协议细节）；
 * `signal` 用于离开页面时取消，取消后 Promise 正常 resolve（不抛 AbortError）。
 */
export function chatStream(
  messages: Array<{ role: string; content: string }>,
  userId: string,
  conversationId: string | null,
  onEvent: (evt: { event: string; data: unknown }) => void,
  signal?: AbortSignal
): Promise<void> {
  const body = { messages, user_id: userId, conversation_id: conversationId }
  return fetch(`${agentApi}/api/mall/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  }).then(res => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return parseSseStream(res, onEvent, { signal })
  })
}

/** 会话列表（page=1 最新） */
export async function listConversations(
  userId: string,
  page = 1,
  pageSize = 10
): Promise<{ items: ConversationItem[] }> {
  const res = await fetch(
    `${agentApi}/api/mall/conversations?user_id=${userId}&page=${page}&page_size=${pageSize}`
  )
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** 会话消息（page=1 最新、页内最旧在前；assistant products 独立 msg_type） */
export async function listMessages(
  conversationId: string,
  userId: string,
  page = 1,
  pageSize = 40
): Promise<{ items: Array<Record<string, unknown>> }> {
  const res = await fetch(
    `${agentApi}/api/mall/conversations/${conversationId}/messages?user_id=${userId}&page=${page}&page_size=${pageSize}`
  )
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** 隐藏该用户全部会话历史 */
export async function hideHistory(userId: string): Promise<void> {
  await fetch(`${agentApi}/api/mall/conversations/hide?user_id=${userId}`, { method: 'POST' })
}

/** 实时监控快照 */
export async function fetchSnapshot(): Promise<Record<string, unknown>> {
  const res = await fetch(`${agentApi}/api/monitor/snapshot`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** /history 每秒聚合点（R1：1s 桶；llm_per_sec = 该秒成功 LLM 调用数，瞬时速率） */
export interface MonitorHistoryPoint {
  ts: number
  llm_per_sec: number
  tokens_in: number
  tokens_out: number
  llm_errors: number
  tools_calls: number
  tools_errors: number
  tasks_ok: number
  tasks_err: number
  p95_latency_ms: number
}

export interface MonitorHistory {
  seconds: number
  points: MonitorHistoryPoint[]
  latency: Array<{ ts: number; latency_ms: number }>
}

/** 秒级历史聚合（服务端常驻累计；seconds 由服务端钳制到保留窗口内） */
export async function fetchHistory(seconds = 300): Promise<MonitorHistory> {
  const res = await fetch(`${agentApi}/api/monitor/history?seconds=${seconds}`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** 历史压测结果 */
export async function listLoadTests(): Promise<{ items: Array<Record<string, unknown>> }> {
  const res = await fetch(`${agentApi}/api/monitor/loadtests`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export type { CommonResult }

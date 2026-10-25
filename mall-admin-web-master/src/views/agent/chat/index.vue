<template>
  <div class="app-container">
    <el-card shadow="never" class="chat-card">
      <template #header>
        <div class="chat-header">
          <span>Agent 会话（mall-agent :8002）</span>
          <span class="header-actions">
            <el-button size="small" @click="loadConversations">刷新</el-button>
            <el-button size="small" type="danger" plain @click="onClear">清空历史</el-button>
          </span>
        </div>
      </template>

      <div class="chat-body">
        <!-- 会话列表 -->
        <div class="conv-panel">
          <el-select v-model="currentConv" placeholder="选择会话（新会话请留空）" clearable
                     size="default" style="width:100%" @change="onConvChange">
            <el-option v-for="c in convs" :key="c.id" :label="c.title" :value="c.id" />
          </el-select>
        </div>

        <!-- 消息区 -->
        <div ref="msgBox" class="msg-list" v-loading="loading">
          <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
            <div class="bubble">
              <!-- 决策流竖向步骤卡片：本轮实时收集；历史消息无 steps 则跳过 -->
              <div v-if="m.role === 'assistant' && m.steps && m.steps.length" class="steps">
                <div
                  v-for="(s, si) in m.steps"
                  :key="si"
                  class="step"
                  :class="{
                    'step--done': m.finished || si < m.steps.length - 1,
                    'step--current': !m.finished && si === m.steps.length - 1,
                  }"
                >
                  <span class="step-dot" />
                  <span class="step-label">{{ s.label }}</span>
                  <!-- “末步运行中→…，finished/非末步→已计时”三元：与 app 端 floating-agent.vue、pages/agent/index.vue 内联同款（三处模板），R4 改口径需同改 -->
                  <span v-if="si < m.steps.length - 1 || m.finished" class="step-dur">
                    · {{ stepDuration(m, si) }}
                  </span>
                  <span v-else class="step-dur step-dur--live">…</span>
                </div>
              </div>
              <div>{{ m.content }}</div>
              <div v-if="m.products && m.products.length" class="products">
                <div v-for="(p, pi) in m.products" :key="pi" class="product-card">
                  <span class="p-name">{{ p.name }}</span>
                  <span class="p-price">¥{{ p.price }}</span>
                  <el-link type="primary" :href="p.url" target="_blank">详情</el-link>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 输入区 -->
        <div class="composer">
          <el-input v-model="input" type="textarea" :rows="2" placeholder="输入消息，Enter 发送（Shift+Enter 换行）"
                    @keydown.enter.exact.prevent="send" />
          <el-button type="primary" :loading="streaming" @click="send" style="margin-left:8px">发送</el-button>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  chatStream, listConversations, listMessages, hideHistory,
  type AgentMessage, type AgentStep, type ConversationItem,
} from '@/apis/agent'
import { isAbortError } from '@/utils/sse'

const USER_ID = 'admin'
const input = ref('')
const messages = ref<AgentMessage[]>([])
const convs = ref<ConversationItem[]>([])
const currentConv = ref<string>('')
const loading = ref(false)
const streaming = ref(false)
const msgBox = ref<HTMLElement>()

// P2-05：本轮流的取消句柄。切换会话/离开页面时 abort，避免组件卸载后仍在读流
// （旧实现里请求会一直跑到服务端写完，白白占一个 agent worker）。
let streamAbort: AbortController | null = null

onBeforeUnmount(() => {
  streamAbort?.abort()
  streamAbort = null
})

/**
 * 竖向步骤卡片：当前节点高亮，完成节点打勾转灰；每节点只给 label + 本步耗时
 * 耗时口径与 app 端 stores/agent.ts 的 stepDurationMs/formatStepDuration 为同一
 * 规则的跨库重复（三处同改：本函数 / app 导出函数组 / 模板“末步三元”），
 * R4 若改末步计时锚点（如改取后端 done stage 的 at_ms）需一起改。
 */
function stepDuration(m: AgentMessage, i: number): string {
  const steps = m.steps || []
  const cur = steps[i]
  if (!cur) return ''
  const next = steps[i + 1]
  const endAt = next ? next.at_ms : m.endMs
  if (!endAt) return '…'
  const ms = Math.max(0, endAt - cur.at_ms)
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

async function loadConversations() {
  try {
    const data = await listConversations(USER_ID, 1, 20)
    convs.value = data.items
  } catch (e) {
    ElMessage.error('加载会话失败：' + (e as Error).message + '（请确认 :8002 服务已启动）')
  }
}

async function onConvChange(id: string) {
  currentConv.value = id || ''
  messages.value = []
  if (!id) return
  try {
    const data = await listMessages(id, USER_ID, 1, 40)
    const out: AgentMessage[] = []
    for (const it of data.items) {
      const role = it.role as 'user' | 'assistant'
      const content = String(it.content ?? '')
      if (it.msg_type === 'products') {
        const last = out[out.length - 1]
        if (last && last.role === role) {
          try { last.products = JSON.parse(content) } catch { /* ignore */ }
        }
      } else {
        out.push(toMessage(role, content, it.extra as Record<string, unknown> | null))
      }
    }
    messages.value = out
    scrollBottom()
  } catch (e) {
    ElMessage.error('加载消息失败：' + (e as Error).message)
  }
}

/**
 * 把一条历史消息还原为前端消息（含 R4 落库的 agent_trace 只读回放）。
 * 后端每条 assistant 记录带 extra.agent_trace（steps + 各自 duration_ms），
 * 这里把末步耗时折算进 endMs，使共用 stepDuration/模板“末步三元”即可正确显示
 * —— 历史步已结束，finished 恒 true，全部走“已完成”分支。
 *
 * 耗时口径（记账）：中间步耗时前端由相邻 at_ms 重算（stepDuration），仅末步信任
 * 落库 duration_ms（折算进 endMs）；若改末步计时锚点（如改取 done stage 的 at_ms）
 * 需三处同改：本函数 / admin 端 stepDuration 组 / app 端模板“末步三元”。
 *
 * 跨库互指：本函数与 app 端 stores/agent.ts 的 restoreFromHistory 是同一“历史
 * trace 回放引擎”（products 合并 + endMs=末步 at_ms+duration_ms 折算）的两处跨库
 * 实现 —— 改任一口径需两侧同改。
 */
function toMessage(role: 'user' | 'assistant', content: string, extra: Record<string, unknown> | null): AgentMessage {
  const msg: AgentMessage = { role, content }
  if (role !== 'assistant') return msg
  const trace = extra?.agent_trace
  if (Array.isArray(trace) && trace.length) {
    const steps = trace.map((s) => ({
      node: String((s as any).node ?? ''),
      label: String((s as any).label ?? (s as any).node ?? ''),
      at_ms: Number((s as any).at_ms ?? 0),
      detail: (s as any).detail as Record<string, unknown> | undefined,
    }))
    const last = trace[trace.length - 1] as any
    msg.steps = steps
    msg.finished = true
    // 仅末步信任落库 duration_ms（中间步由相邻 at_ms 重算，见函数头记账注释）：
    // endMs = 末步 at_ms + duration_ms
    msg.endMs = (Number(last?.at_ms) || 0) + (Number(last?.duration_ms) || 0)
  }
  return msg
}

async function send() {
  const text = input.value.trim()
  if (!text || streaming.value) return
  input.value = ''
  messages.value.push({ role: 'user', content: text })
  const ai: AgentMessage = { role: 'assistant', content: '', loading: true }
  messages.value.push(ai)
  streaming.value = true
  loading.value = true
  scrollBottom()
  const abort = new AbortController()
  streamAbort = abort
  try {
    const hist = messages.value
      .filter(m => !m.loading && m.content)
      .slice(-12)
      .map(m => ({ role: m.role, content: m.content }))
    let done = false
    await chatStream(hist, USER_ID, currentConv.value || null, evt => {
      if (evt.event === 'meta') {
        const d = evt.data as { conversation_id: string }
        if (d?.conversation_id) {
          currentConv.value = d.conversation_id
          // 新会话则刷新列表
          if (!convs.value.some(c => c.id === d.conversation_id)) loadConversations()
        }
      } else if (evt.event === 'stage') {
        // 决策流步骤：进入节点即点亮（竖向步骤卡片）；忽略 done 哨兵不入列表
        const d = evt.data as AgentStep
        if (d?.node && d.node !== 'done') {
          ai.steps = ai.steps || []
          ai.steps.push(d)
          scrollBottom()
        }
      } else if (evt.event === 'text') {
        const d = evt.data as { content?: string }
        if (d?.content) {
          ai.loading = false
          ai.content += d.content
          scrollBottom()
        }
      } else if (evt.event === 'products') {
        const d = evt.data as { products?: AgentMessage['products'] }
        if (d?.products) ai.products = d.products
      } else if (evt.event === 'error') {
        const d = evt.data as { message?: string }
        ai.loading = false
        ai.finished = true
        ai.content += '\n[错误] ' + (d?.message || '未知错误')
      } else if (evt.event === 'done') {
        done = true
        ai.finished = true
        ai.endMs = Date.now()
      }
    }, abort.signal)
    if (!done) {
      // 被主动取消时不要伪装成"连接中断"：用户已经离开，这条消息不会再被看到
      if (abort.signal.aborted) {
        ai.content = ai.content || '（已取消）'
      } else {
        ai.content = ai.content || '（连接中断，未收到完成标记）'
      }
      ai.finished = true
      ai.endMs = Date.now()
    }
    ai.loading = false
    scrollBottom()
  } catch (e) {
    ai.loading = false
    ai.finished = true
    ai.endMs = Date.now()
    ai.content = isAbortError(e) || abort.signal.aborted
      ? ai.content || '（已取消）'
      : 'AI 服务暂时无法连接：' + (e as Error).message
  } finally {
    if (streamAbort === abort) streamAbort = null
    streaming.value = false
    loading.value = false
  }
}

async function onClear() {
  try {
    await ElMessageBox.confirm('旧记录会保留在数据库中，但此账号以后不会再自动加载它们。确定清空？', '提示')
  } catch {
    return
  }
  try {
    await hideHistory(USER_ID)
    convs.value = []
    currentConv.value = ''
    messages.value = []
    ElMessage.success('已清空历史')
  } catch (e) {
    ElMessage.error('清空失败：' + (e as Error).message)
  }
}

function scrollBottom() {
  nextTick(() => {
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  })
}

onMounted(() => {
  loadConversations()
})
</script>

<style scoped>
.chat-card { min-height: calc(100vh - 120px); }
.chat-header { display: flex; justify-content: space-between; align-items: center; }
.chat-body { display: flex; flex-direction: column; gap: 10px; }
.conv-panel { margin-bottom: 4px; }
.msg-list {
  height: calc(100vh - 320px); overflow-y: auto; background: #f7f8fa;
  border-radius: 4px; padding: 12px; display: flex; flex-direction: column; gap: 10px;
}
.msg-row { display: flex; }
.msg-row.user { justify-content: flex-end; }
.msg-row.assistant { justify-content: flex-start; }
.bubble {
  max-width: 70%; padding: 8px 12px; border-radius: 8px; white-space: pre-wrap;
  word-break: break-word; line-height: 1.5; font-size: 14px;
}
.msg-row.user .bubble { background: #409eff; color: #fff; }
.msg-row.assistant .bubble { background: #fff; border: 1px solid #e4e7ed; }
.products { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.product-card {
  background: #f0f2f5; border-radius: 4px; padding: 6px 10px; font-size: 13px;
  display: flex; align-items: center; gap: 8px;
}
.p-name { font-weight: 500; }
.p-price { color: #f56c6c; }
.composer { display: flex; align-items: flex-end; }

/* 决策流竖向步骤卡片（R3）：当前节点高亮脉动，完成打勾转灰 */
.steps {
  display: flex; flex-direction: column; gap: 2px;
  margin-bottom: 8px; padding: 8px 10px;
  border: 1px dashed #d0d7de; border-radius: 8px; background: #fbfcfe;
}
.step {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; line-height: 1.9; color: #909399;
}
.step-dot {
  flex: none; width: 6px; height: 6px; border-radius: 50%;
  background: #c0c4cc;
}
.step-label { color: inherit; }
.step--done .step-dot { background: #67c23a; }
.step--done { color: #909399; }
.step--done .step-label::before { content: '✓ '; color: #67c23a; }
.step--current {
  color: #409eff; font-weight: 600;
  animation: step-pulse 1.2s ease-in-out infinite;
}
.step--current .step-dot {
  background: #409eff;
  box-shadow: 0 0 0 0 rgba(64, 158, 255, 0.5);
  animation: step-ring 1.2s ease-out infinite;
}
.step-dur { color: #b0b3b8; font-variant-numeric: tabular-nums; }
.step-dur--live { color: #409eff; }
@keyframes step-pulse { 50% { opacity: 0.55; } }
@keyframes step-ring {
  0% { box-shadow: 0 0 0 0 rgba(64, 158, 255, 0.5); }
  100% { box-shadow: 0 0 0 6px rgba(64, 158, 255, 0); }
}
</style>

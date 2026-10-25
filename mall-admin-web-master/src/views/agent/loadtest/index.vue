<template>
  <div class="app-container">
    <el-card shadow="never">
      <template #header>
        <div class="lt-header">
          <span>压测指标（R6 perf_test --publish）</span>
          <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        </div>
      </template>

      <!-- 空态：给出真实可运行的压测命令（perf_test 本身是纯客户端 HTTP 压测，
           通过 --publish 把运行结果发布到 /api/monitor/loadtests，再在本页展示） -->
      <div v-if="!current" class="empty">
        <p>暂无压测记录。在 llm_backend 目录下运行下面的命令（会把结果自动发布到本页）：</p>
        <pre>cd deepseek_agent/llm_backend
../.venv/Scripts/python.exe scripts/perf_test.py --scenario chat_rag_on --levels 1,2,4,8,16,32 --duration 40 --json-out logs/perf_chat_rag_on.json --publish http://localhost:8002</pre>
        <p class="empty-sub">perf_test 为客户端侧 HTTP 压测驱动（QPS/延迟/成功率/服务端 token 吞吐，来自
          /api/monitor/history），不含 CPU/内存/网络等服务器资源指标。</p>
      </div>

      <template v-else>
        <!-- 选中运行的摘要 -->
        <div class="run-header">
          <el-tag>{{ textOf(run.scenario) }}</el-tag>
          <el-tag :type="ragTagType(run.rag_enabled)">RAG: {{ ragText(run.rag_enabled) }}</el-tag>
          <span class="run-meta">接收 {{ fmtMs(current?.received_at_ms) }} · base {{ textOf(run.base) }}</span>
        </div>
        <el-row :gutter="12" class="stat-row">
          <el-col :span="6">
            <el-statistic title="tokens/s" :value="numOrDash(run.tokens_s)" :formatter="statText" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="server tokens in" :value="numOrDash(tok('in'))" :formatter="statText" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="server tokens out" :value="numOrDash(tok('out'))" :formatter="statText" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="server llm_calls" :value="numOrDash(tok('calls'))" :formatter="statText" />
          </el-col>
        </el-row>

        <!-- 分档明细 -->
        <h4>并发档位明细（共 {{ (run.levels || []).length }} 档）</h4>
        <el-table :data="levels" size="small" border stripe :row-class-name="rowClass">
          <el-table-column prop="level" label="并发" width="70" />
          <el-table-column prop="mode" label="mode" width="90" />
          <el-table-column label="成功率%">
            <template #default="{ row }">{{ fmtRate(row.success_rate) }}</template>
          </el-table-column>
          <el-table-column prop="qps" label="QPS" width="90" />
          <el-table-column prop="p50_ms" label="p50(ms)" width="90" />
          <el-table-column prop="p95_ms" label="p95(ms)" width="90" />
          <el-table-column prop="p99_ms" label="p99(ms)" width="90" />
          <el-table-column prop="requests" label="requests" width="90" />
          <el-table-column prop="ok" label="ok" width="70" />
          <el-table-column prop="wall_s" label="wall_s" width="80" />
          <el-table-column prop="degraded_reason" label="degraded_reason" min-width="180" show-overflow-tooltip />
        </el-table>

        <!-- 历史运行列表：点击切换查看对应运行的分档明细 -->
        <h4>历史运行（{{ items.length }} 条，点击查看）</h4>
        <div v-if="items.length" class="run-list">
          <div
            v-for="(item, i) in items"
            :key="item.id || i"
            class="run-item"
            :class="{ active: item === current }"
            @click="current = item"
          >
            <span class="run-item-name">{{ textOf(item.run?.scenario) }}</span>
            <span class="run-item-meta">{{ fmtMs(item.received_at_ms) }}</span>
          </div>
        </div>
        <el-empty v-else description="暂无发布记录" />
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { listLoadTests } from '@/apis/agent'

// 弱类型是仓库既有惯例：listLoadTests 返回未 schema 化的后端 envelope
// ({id, received_at_ms, run:{scenario,rag_enabled,base,levels_tried,server_tokens,
//   tokens_s,levels:[{mode,level,requests,ok,success_rate,qps,wall_s,p50_ms,p95_ms,
//   p99_ms,flags,degraded_reason}]}})，前端按约定字段读取即可。
const items = ref<Array<Record<string, any>>>([])
const loading = ref(false)
const current = ref<Record<string, any> | null>(null)

const run = computed(() => (current.value?.run as Record<string, any>) || {})
const levels = computed<Array<Record<string, any>>>(() => (run.value.levels as Array<Record<string, any>>) || [])

async function load() {
  loading.value = true
  try {
    const data = await listLoadTests()
    items.value = data.items
    current.value = items.value[0] || null
  } catch (e) {
    // 忽略（服务未启动时留空，命中空态）
  } finally {
    loading.value = false
  }
}

function numOrDash(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : Number.NaN
}

/** el-statistic 的 formatter：NaN 显示 "—"，避免把缺失值渲染成 0 */
function statText(v: number): string {
  return Number.isFinite(v) ? String(v) : '—'
}

function textOf(v: unknown): string {
  return v == null || v === '' ? '—' : String(v)
}

function ragText(v: unknown): string {
  if (v === true) return 'true'
  if (v === false) return 'false'
  return '—'
}

function ragTagType(v: unknown): 'success' | 'info' {
  return v === true ? 'success' : 'info'
}

function fmtRate(rate: unknown): string {
  return typeof rate === 'number' ? `${(rate * 100).toFixed(1)}%` : '—'
}

function fmtMs(ms: unknown): string {
  const n = Number(ms)
  if (!Number.isFinite(n)) return '—'
  return new Date(n).toLocaleString('zh-CN', { hour12: false })
}

/** server_tokens 汇总字段读取：{tokens_in,tokens_out,llm_calls}，缺失返回 null */
function tok(which: 'in' | 'out' | 'calls'): number | null {
  const st = run.value.server_tokens as Record<string, any> | undefined
  if (!st || typeof st !== 'object') return null
  const map: Record<'in' | 'out' | 'calls', string> = {
    in: 'tokens_in',
    out: 'tokens_out',
    calls: 'llm_calls',
  }
  const v = st[map[which]]
  return typeof v === 'number' ? v : null
}

/** 命中降级或未全成功的行高亮（mode=degraded 或 success_rate<1） */
function rowClass({ row }: { row: Record<string, any> }): string {
  const degraded = row.mode === 'degraded'
  const partial = typeof row.success_rate === 'number' && row.success_rate < 1
  return degraded || partial ? 'row-degraded' : ''
}

onMounted(load)
</script>

<style scoped>
.lt-header { display: flex; justify-content: space-between; align-items: center; }
.stat-row { margin-bottom: 16px; }
.empty { color: #909399; padding: 20px 0; }
.empty pre {
  background: #1e1e1e; color: #d4d4d4; padding: 12px 14px; border-radius: 6px;
  overflow-x: auto; font-size: 12px; line-height: 1.6;
}
.empty-sub { font-size: 12px; color: #c0c4cc; }
.run-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.run-meta { color: #909399; font-size: 12px; }
h4 { margin: 16px 0 8px; }
.run-list { display: flex; flex-wrap: wrap; gap: 8px; }
.run-item {
  border: 1px solid #dcdfe6; border-radius: 6px; padding: 6px 12px;
  cursor: pointer; font-size: 13px; display: flex; flex-direction: column; gap: 2px;
}
.run-item.active { border-color: #409eff; background: #ecf5ff; }
.run-item-name { color: #303133; }
.run-item-meta { color: #909399; font-size: 12px; }
</style>

<style>
/* 降级 / 成功率不足 1 的行标红（el-table row-class-name 命中） */
.el-table .row-degraded { background: #fef0f0; }
</style>

<template>
  <div class="app-container">
    <el-card shadow="never">
      <template #header>
        <div class="monitor-header">
          <span>Agent 实时监控</span>
          <div class="monitor-actions">
            <span class="hint">卡片=近 60s 窗口+累计；折线=1s 粒度历史（服务端常驻累积）</span>
            <el-select
              v-model="winSec"
              size="small"
              style="width: 120px; margin: 0 8px"
              @change="refresh"
            >
              <el-option v-for="w in WINDOWS" :key="w.value" :label="w.label" :value="w.value" />
            </el-select>
            <el-tag :type="connected ? 'success' : 'danger'" size="small">
              {{ connected ? '轮询正常' : '连接中断（自动重试）' }}
            </el-tag>
          </div>
        </div>
      </template>

      <!-- 数字卡 -->
      <el-row :gutter="12" class="stat-row">
        <el-col v-for="card in statCards" :key="card.title" :xs="12" :sm="6">
          <div class="stat-cell">
            <div class="stat-title">{{ card.title }}</div>
            <div class="stat-value">{{ card.value }}</div>
          </div>
        </el-col>
      </el-row>

      <!-- F4（S4）：服务不可达/拉取失败时给一条弱提示，区别于“确实无流量”的空轴 -->
      <p v-if="dataError" class="data-error">监控数据暂不可达，2s 后自动重试…</p>

      <!-- 折线图（共享同一 ts 秒级横轴；每图独立 y 轴；grid containLabel 自适应） -->
      <el-row :gutter="12">
        <el-col :xs="24" :sm="12" :lg="12">
          <div class="chart-box">
            <div ref="qpsChart" class="chart" />
          </div>
        </el-col>
        <el-col :xs="24" :sm="12" :lg="12">
          <div class="chart-box">
            <div ref="latChart" class="chart" />
          </div>
        </el-col>
      </el-row>
      <el-row :gutter="12">
        <el-col :xs="24" :sm="12" :lg="12">
          <div class="chart-box">
            <div ref="tokenChart" class="chart" />
          </div>
        </el-col>
        <el-col :xs="24" :sm="12" :lg="12">
          <div class="chart-box">
            <div ref="toolChart" class="chart" />
          </div>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue'
// P2-05：原来是 `import * as echarts from 'echarts'` —— 整个 echarts 包（约 1.2MB）
// 都进了监控页的 chunk，而这里只画折线图。改成 core + 显式注册用到的模块（与
// home/index.vue 的做法一致），监控页 chunk 从 590 kB 降到约 1/3。
import { init, use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { fetchSnapshot, fetchHistory } from '@/apis/agent'
import type { MonitorHistory } from '@/apis/agent'

// 只注册 renderChart() 真正用到的能力：折线、坐标系、标题、图例、tooltip
// （tooltip 里的 axisPointer 属于 TooltipComponent，无需单独注册）。
use([LineChart, GridComponent, LegendComponent, TitleComponent, TooltipComponent, CanvasRenderer])

const snap = ref<Record<string, any>>({})
const connected = ref(false)
// F4（S4）：/snapshot 或 /history 拉取失败置 true → 顶栏弱提示；2s 下一轮成功自动熄灭
const dataError = ref(false)

const fmt2 = (v: unknown) => Number(v ?? 0).toFixed(2)
const fmt6 = (v: unknown) => Number(v ?? 0).toFixed(6)

/** 顶部 8 张数字卡（QPS 等保留原 snapshot 60s 窗口语义） */
const statCards = computed(() => {
  const s = snap.value
  const t = s.tokens || {}
  return [
    { title: 'QPS(60s 均值)', value: fmt2(s.qps) },
    { title: 'LLM 调用(窗口/累计)', value: `${s.llm_calls || 0}/${s.lifetime_llm_calls || 0}` },
    { title: 'Token in/out', value: `${t.in || 0}/${t.out || 0}` },
    { title: '累计成本(¥)', value: fmt6(s.cost_rmb) },
    { title: '工具调用/错误', value: `${s.tools?.calls || 0}/${s.tools?.errors || 0}` },
    { title: '任务完成/失败', value: `${s.tasks?.ok || 0}/${s.tasks?.err || 0}` },
    { title: 'LLM 单次 P95(ms)', value: fmt2(s.latency?.p95_total_ms) },
    { title: '活跃 trial', value: `${s.active_trials || 0}` },
  ]
})

/** 折线图历史窗口选项（秒） */
const WINDOWS = [
  { label: '1 分钟', value: 60 },
  { label: '5 分钟', value: 300 },
  { label: '15 分钟', value: 900 },
  { label: '1 小时', value: 3600 },
]
const winSec = ref(300)

/**
 * 共享横轴的数据缓冲：data.ts 为 [start,end] 的每一整秒（对齐补齐），
 * 各指标数组与 ts 等长；无数据秒：计数填 0、延迟填 null（断线，不画假 0）。
 * F1：有数据时 data.ts 起点为“最早活动秒”（服务存活起点），再往前的空窗秒不入列。
 */
const data = reactive({
  ts: [] as number[],
  qps: [] as number[],
  tokIn: [] as number[],
  tokOut: [] as number[],
  tools: [] as number[],
  respP95: [] as Array<number | null>,
})

const pad2 = (v: number) => String(v).padStart(2, '0')
const fmtTime = (ts: number) => {
  const d = new Date(ts * 1000)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}
const fmtNum = (v: number) =>
  Number.isFinite(v) ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : ''

/** 样本集的 P95；空集返回 null（noUncheckedIndexedAccess 下用 ?? 兜底） */
function percentileP95(values: number[]): number | null {
  // 与后端 stats.py p95 同式（min(len-1, ceil(n*0.95)-1)）——改一处需联动
  if (!values.length) return null
  const a = [...values].sort((x, y) => x - y)
  return a[Math.min(a.length - 1, Math.ceil(a.length * 0.95) - 1)] ?? null
}

/**
 * 将一次 /history 响应对齐到连续整秒横轴。
 * points 只含“有活动的秒”（稀疏）→ 按 ts 回填；latency 是整轮时延原始样本
 * → 按秒聚合为该秒完成回合的 P95。范围取 min(请求窗口, 服务端实际回显秒)。
 * F1（S3/P5）：服务刚启/重启、存活 < 所选窗时，服务端把 seconds 钳到保留上限而非真实跨度，
 * 窗口前段“进程尚未存活”的秒若硬补 0 会被误读成 0 流量——故有数据时把起点裁剪到最早活动秒。
 */
function applyHistory(h: MonitorHistory) {
  const sec = Math.max(1, Math.min(winSec.value, h.seconds || winSec.value))
  const end = Math.floor(Date.now() / 1000)
  const reqStart = end - sec + 1
  const n = sec

  const qps = new Array<number>(n).fill(0)
  const tokIn = new Array<number>(n).fill(0)
  const tokOut = new Array<number>(n).fill(0)
  const tools = new Array<number>(n).fill(0)
  const tsArr = new Array<number>(n)
  const latencyBuckets: number[][] = Array.from({ length: n }, () => [])
  for (let i = 0; i < n; i++) tsArr[i] = reqStart + i

  let earliestData = Infinity
  for (const p of h.points || []) {
    const i = p.ts - reqStart
    if (i < 0 || i >= n) continue
    qps[i] = p.llm_per_sec || 0
    tokIn[i] = p.tokens_in || 0
    tokOut[i] = p.tokens_out || 0
    tools[i] = p.tools_calls || 0
    if (p.ts < earliestData) earliestData = p.ts
  }
  for (const s of h.latency || []) {
    const i = s.ts - reqStart
    if (i >= 0 && i < n) {
      latencyBuckets[i]?.push(s.latency_ms)
      if (s.ts < earliestData) earliestData = s.ts
    }
  }

  // 前导段“进程尚未存活”的空窗秒整体裁掉：各数组按同一 crop 偏移切片，与 ts 保持对齐。
  // points 与 latency 皆空（真无数据）时保留整窗——空轴即“暂无数据”信号，非错误。
  const crop = earliestData === Infinity ? 0 : Math.max(0, earliestData - reqStart)
  const off = crop > 0
  data.ts = off ? tsArr.slice(crop) : tsArr
  data.qps = off ? qps.slice(crop) : qps
  data.tokIn = off ? tokIn.slice(crop) : tokIn
  data.tokOut = off ? tokOut.slice(crop) : tokOut
  data.tools = off ? tools.slice(crop) : tools
  data.respP95 = (off ? latencyBuckets.slice(crop) : latencyBuckets).map(percentileP95)
}

const charts: Record<string, any> = {}

interface SeriesDef {
  name: string
  values: Array<number | null>
  color?: string
}

function renderChart(
  dom: HTMLElement | undefined,
  title: string,
  defs: SeriesDef[],
  opts: { legend?: boolean; yFmt?: (v: number) => string } = {},
): void {
  if (!dom) return
  if (!charts[title]) charts[title] = init(dom)
  const legend = opts.legend && defs.length > 1
  const yFmt = opts.yFmt || String
  const hasTitleRow = legend
  const chart = charts[title]
  chart.setOption({
    title: {
      text: title,
      left: 12,
      top: 8,
      textStyle: { fontSize: 13, fontWeight: 600, color: '#303133' },
    },
    legend: legend
      ? { top: 6, right: 12, itemWidth: 14, itemHeight: 6, textStyle: { fontSize: 11 } }
      : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: '#c0c4cc', type: 'dashed' } },
      formatter: (params: any) => {
        if (!Array.isArray(params) || !params.length) return ''
        const first = params[0]
        const x = Array.isArray(first.value) ? first.value[0] : first.axisValue
        let html = `<b>${fmtTime(Number(x))}</b>`
        for (const p of params) {
          const raw = Array.isArray(p.value) ? p.value[1] : p.value
          if (raw == null) continue
          html += `<br/><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.color};margin-right:6px"></span>${p.seriesName}: <b>${fmtNum(Number(raw))}</b>`
        }
        return html
      },
    },
    // containLabel：grid 自动把轴标签（纵轴刻度/横轴时间）计入可用区，
    // 不再固定 left:50 —— 左右两列图表各自自适应边距，标题放 top 不粘连纵轴。
    grid: { top: hasTitleRow ? 54 : 44, left: 8, right: 14, bottom: 4, containLabel: true },
    xAxis: {
      type: 'value',
      min: data.ts.length ? data.ts[0] : 0,
      max: data.ts.length ? data.ts[data.ts.length - 1] : 1,
      axisLine: { lineStyle: { color: '#dcdfe6' } },
      axisTick: { show: false },
      axisLabel: { color: '#909399', formatter: (v: number) => fmtTime(v) },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#909399', formatter: (v: number) => yFmt(v) },
      splitLine: { lineStyle: { color: '#f2f3f5', type: 'dashed' } },
    },
    series: defs.map(d => ({
      name: d.name,
      type: 'line' as const,
      showSymbol: false,
      smooth: true,
      connectNulls: false,
      lineStyle: { width: 2, color: d.color },
      itemStyle: { color: d.color },
      data: data.ts.map((t, i) => [t, d.values[i]]),
    })),
    // 全窗重画：每次 setOption 整体替换数据与轴，notMerge 避免残留旧序列/切换窗口动画闪帧
  }, { notMerge: true, lazyUpdate: false })
}

function renderAll() {
  renderChart(qpsChart.value, 'QPS（每秒成功 LLM 调用）', [
    { name: 'QPS', values: data.qps, color: '#409EFF' },
  ])
  // P95 线 = worker 整轮时延（latency[]）按秒聚合的 P95，区别于顶部数字卡“LLM 单次 P95”（snapshot latency.p95_total_ms）
  renderChart(latChart.value, '整轮响应 P95(ms)', [
    { name: 'P95', values: data.respP95, color: '#E6A23C' },
  ])
  renderChart(tokenChart.value, 'Token 吞吐(in/out)', [
    { name: 'Token in', values: data.tokIn, color: '#67C23A' },
    { name: 'Token out', values: data.tokOut, color: '#909399' },
  ], { legend: true, yFmt: fmtNum })
  renderChart(toolChart.value, '工具调用(每秒)', [
    { name: 'Tools', values: data.tools, color: '#F56C6C' },
  ])
}

const qpsChart = ref<HTMLElement>()
const latChart = ref<HTMLElement>()
const tokenChart = ref<HTMLElement>()
const toolChart = ref<HTMLElement>()

let pollTimer: ReturnType<typeof setInterval> | null = null
let resizeHandler: (() => void) | null = null
// F3（S1）请求令牌：window @change 与 2s 轮询并发在途时，仅最新一次 refresh 有权写状态
let reqToken = 0

async function refresh() {
  // 每 2s 全窗重拉 /history ≈“先拉历史再增量”：服务端契约本就每请求返回整窗，无丢点（P2）
  // F3（S1）：进入即取自增令牌，await 后令牌失配则弃用本次——旧窗慢响应不得覆盖新窗
  const token = ++reqToken
  let failed = false
  // 数字卡：snapshot（独立，失败不拖垮图表；仍计入 dataError 弱提示）
  try {
    const s = await fetchSnapshot()
    if (token !== reqToken) return
    snap.value = s as Record<string, any>
  } catch {
    if (token !== reqToken) return
    failed = true
  }
  // 折线图：/history（打开先拉历史再增量，空流量也显示历史曲线）
  try {
    const h = await fetchHistory(winSec.value)
    if (token !== reqToken) return
    applyHistory(h)
  } catch {
    if (token !== reqToken) return
    failed = true
  }
  // F4（S4）：任一拉取失败亮弱提示；2s 后下一轮成功自动熄灭（仅提示行，不阻塞）
  dataError.value = failed
  // P0-01：连接状态改为「最近一轮轮询是否成功」。原先用 EventSource 当心跳，
  // 但 EventSource 无法携带 Authorization 头，且服务端仍在为它重复算快照。
  connected.value = !failed
  renderAll()
}

// P1-05：单通道轮询。页面不可见时停表，回到前台立即补一次再恢复 2s 节奏。
function pausePolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function resumePolling() {
  if (pollTimer) return
  refresh()
  pollTimer = setInterval(refresh, 2000)
}

function onVisibilityChange() {
  if (document.hidden) pausePolling()
  else resumePolling()
}

onMounted(() => {
  renderAll()
  refresh()
  pollTimer = setInterval(refresh, 2000)
  document.addEventListener('visibilitychange', onVisibilityChange)
  resizeHandler = () => {
    for (const k of Object.keys(charts)) charts[k]?.resize?.()
  }
  window.addEventListener('resize', resizeHandler)
})

onUnmounted(() => {
  pausePolling()
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
  for (const k of Object.keys(charts)) {
    charts[k]?.dispose?.()
    delete charts[k]
  }
})
</script>

<style scoped>
.monitor-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.monitor-actions { display: flex; align-items: center; }
.hint { font-size: 12px; color: #909399; margin-right: 4px; }
.data-error { font-size: 12px; color: #e6a23c; margin: 0 0 12px; }
.stat-row { margin-bottom: 16px; }
.stat-cell { border: 1px solid #ebeef5; border-radius: 4px; padding: 10px 12px; background: #fff; }
.stat-title { font-size: 12px; color: #909399; }
.stat-value { font-size: 20px; font-weight: 600; color: #303133; margin-top: 4px; line-height: 1.2; }
.chart-box { border: 1px solid #ebeef5; border-radius: 4px; margin-bottom: 12px; }
.chart { height: 240px; width: 100%; }
</style>

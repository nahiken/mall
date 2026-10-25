<template>
  <div class="app-container">
    <el-card shadow="never" class="upload-card">
      <template #header>
        <div class="kb-header">
          <span>知识库文档（txt / md / pdf，单文件 ≤ 5MB）</span>
          <el-button size="small" type="primary" :loading="uploading" @click="handlePick"
            >上传文档</el-button
          >
          <input
            ref="fileInput"
            type="file"
            accept=".txt,.md,.pdf"
            style="display: none"
            @change="handleFileChange"
          />
        </div>
      </template>

      <!-- 拖拽/点击上传 -->
      <el-upload
        drag
        action="#"
        :auto-upload="false"
        :show-file-list="false"
        :disabled="uploading"
        accept=".txt,.md,.pdf"
        @change="handleDropChange"
      >
        <div v-if="!uploading" class="upload-hint">
          <el-icon><i class="el-icon-upload" /></el-icon>
          <div class="el-upload__text">将文件拖到此处，或<em>点击选择</em></div>
          <div class="el-upload__tip">仅支持 .txt / .md / .pdf，≤ 5MB</div>
        </div>
        <div v-else class="upload-progress">
          <el-progress type="circle" :percentage="progress" :width="90" />
          <div class="upload-text">{{ uploadStatusText }}</div>
        </div>
      </el-upload>
    </el-card>

    <!-- 上传成功的摄取结果：当场展示 ✂️ 切成了几块 + 每块全文 -->
    <el-card v-if="lastResult" shadow="never" class="result-card">
      <template #header>
        <div class="kb-header">
          <span class="result-title">
            摄取完成：{{ lastResult.filename }}（共 {{ lastResult.total_chunks ?? 0 }} 分块）
          </span>
          <el-button size="small" text type="primary" @click="closeResult">收起</el-button>
        </div>
      </template>
      <div v-if="lastResult.stages?.length" class="result-stages">
        <span v-for="(s, i) in lastResult.stages" :key="i" class="result-stage-chip">
          {{ ragStageLabel(s.stage)
          }}<template v-if="s.stage === 'split' && s.chunk_count"
            >（{{ s.chunk_count }} 块）</template
          >
        </span>
      </div>
      <div v-if="lastResult.chunks?.length" class="result-chunks">
        <ChunkList :chunks="lastResult.chunks" marker="✂️ " show-char-len />
      </div>
      <div v-else class="result-empty">
        该响应未含分块全文（文件被跳过或旧版 RAG 服务），请用上方「预览」查看。
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="kb-header">
          <span>文档列表</span>
          <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        </div>
      </template>

      <el-table :data="docs" v-loading="loading" border stripe size="small">
        <el-table-column prop="source_path" label="文件名" min-width="200" show-overflow-tooltip />
        <el-table-column label="类型" width="80">
          <template #default="{ row }">{{ fileExt(row.source_path) }}</template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="分块数" width="90" />
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ fmtSize(row.size_bytes) }}</template>
        </el-table-column>
        <el-table-column label="上传时间" width="170">
          <template #default="{ row }">{{ fmtTime(row.modified_at) }}</template>
        </el-table-column>
        <el-table-column
          prop="document_id"
          label="document_id"
          min-width="180"
          show-overflow-tooltip
        />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="openPreview(row)">预览</el-button>
            <el-button size="small" type="danger" text @click="confirmDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!loading && docs.length === 0" class="empty">
        知识库为空。上传一个 .txt/.md/.pdf 文档即可开始向量化入库。
      </div>
    </el-card>

    <!-- 预览 / 检索抽屉 -->
    <el-drawer
      :model-value="drawerVisible"
      :title="drawerTitle"
      size="46%"
      @close="drawerVisible = false"
    >
      <div class="drawer-body">
        <div class="search-row">
          <el-input
            v-model="searchText"
            placeholder="在知识库中检索…"
            clearable
            @keyup.enter="doSearch"
          >
            <template #append>
              <el-button :loading="searching" @click="doSearch">检索</el-button>
            </template>
          </el-input>
        </div>

        <template v-if="searching">
          <el-skeleton :rows="4" animated />
        </template>
        <template v-else-if="searchText.trim()">
          <el-empty v-if="searchHits.length === 0" description="无命中" />
          <div v-for="(hit, i) in searchHits" :key="i" class="hit">
            <div class="hit-meta">{{ hit.source_path }} · 相关度 {{ hit.score }}</div>
            <div class="hit-text">{{ hit.text }}</div>
          </div>
        </template>

        <template v-else>
          <ChunkList v-if="preview.chunks?.length" :chunks="preview.chunks" />
          <el-empty v-if="preview.chunks?.length === 0" description="该文档无分块" />
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ChunkList from './ChunkList.vue'
import {
  listDocuments,
  deleteDocument,
  previewDocument,
  searchDocuments,
  uploadDocument,
  ragStageLabel,
  type RagDocument,
  type RagChunkPreview,
  type RagUploadResult,
} from '@/apis/rag'

const docs = ref<RagDocument[]>([])
const loading = ref(false)

// 上传（字节进度）：摄取为秒级同步，无逐阶段实时推送——进度圈只管上传字节，
// 阶段/分块结果在上传完成后由 lastResult 的 stages/chunks 呈现（见结果卡）。
const fileInput = ref<HTMLInputElement>()
const uploading = ref(false)
const progress = ref(0)
const uploadStatusText = ref('正在摄取并向量化…')
// 上传成功的摄取结果（当场展示切分效果）
const lastResult = ref<RagUploadResult | null>(null)

// 预览/检索
const drawerVisible = ref(false)
const drawerTitle = ref('')
const preview = ref<{ chunks: RagChunkPreview[] }>({ chunks: [] })
const searchText = ref('')
const searchHits = ref<Array<{ source_path: string; score: number; text: string }>>([])
const searching = ref(false)

async function load() {
  loading.value = true
  try {
    docs.value = await listDocuments()
  } catch (e: any) {
    ElMessage.error(`RAG 服务不可用：${e?.message || e}（请确认 :8123 已启动）`)
  } finally {
    loading.value = false
  }
}

async function doUpload(file: File) {
  if (!/\.(txt|md|pdf)$/i.test(file.name)) {
    ElMessage.warning('仅支持 .txt / .md / .pdf')
    return
  }
  if (file.size > 5 * 1024 * 1024) {
    ElMessage.warning('文件超过 5MB 上限')
    return
  }
  uploading.value = true
  progress.value = 0
  uploadStatusText.value = '正在摄取并向量化…'
  try {
    const res = await uploadDocument(file, p => {
      progress.value = p
    })
    if (res.ok) {
      const chunks = (res.chunks ?? []) as RagChunkPreview[]
      lastResult.value = { ...res, chunks, stages: res.stages }
      ElMessage.success(`已摄取：${res.filename}（${res.total_chunks ?? 0} 分块）`)
      uploadStatusText.value = `完成：✂️ 切成 ${res.total_chunks ?? 0} 块`
      await load()
    } else {
      // 服务端判定文件被跳过（无法解析/内容未变化）：明确报因，不渲染分块
      lastResult.value = null
      uploadStatusText.value = '摄取失败'
      ElMessage.error(res.error || '文件未摄取（无法解析或内容未变化）')
    }
  } catch (e: any) {
    uploadStatusText.value = '上传失败'
    ElMessage.error(e?.message || '上传失败')
  } finally {
    uploading.value = false
    progress.value = 0
    if (fileInput.value) fileInput.value.value = ''
  }
}

function closeResult() {
  lastResult.value = null
}

function handlePick() {
  if (!uploading.value) fileInput.value?.click()
}
function handleFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) doUpload(file)
}
function handleDropChange(up: any) {
  const file = up?.raw
  if (file) doUpload(file)
}

async function openPreview(row: RagDocument) {
  drawerVisible.value = true
  drawerTitle.value = `${row.source_path}（${row.chunk_count} 分块）`
  searchText.value = ''
  searchHits.value = []
  preview.value = { chunks: [] }
  try {
    preview.value = await previewDocument(row.document_id)
  } catch (e: any) {
    ElMessage.error(e?.message || '预览失败')
  }
}

async function doSearch() {
  const q = searchText.value.trim()
  if (!q) return
  searching.value = true
  try {
    searchHits.value = await searchDocuments(q, 5)
  } catch (e: any) {
    ElMessage.error(e?.message || '检索失败')
    searchHits.value = []
  } finally {
    searching.value = false
  }
}

async function confirmDelete(row: RagDocument) {
  try {
    await ElMessageBox.confirm(
      `确认删除文档「${row.source_path}」？该操作会清除其全部向量分块。`,
      '删除确认',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  try {
    const res = await deleteDocument(row.document_id)
    ElMessage.success(`已删除 ${res.deleted ?? 0} 个分块`)
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

function fileExt(name: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(name || '')
  const ext = m?.[1]
  return ext ? '.' + ext.toLowerCase() : '-'
}
function fmtSize(bytes: number | null): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}
function fmtTime(ts: number | null): string {
  if (!ts) return '-'
  const d = new Date(ts)
  return d.toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<style scoped>
.kb-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.upload-card {
  margin-bottom: 16px;
}
.upload-hint {
  text-align: center;
  padding: 6px 0;
  color: #606266;
}
.upload-progress {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
}
.upload-text {
  color: #909399;
  font-size: 13px;
}
.result-card {
  margin-bottom: 16px;
}
.result-title {
  font-weight: 600;
}
.result-stages {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.result-stage-chip {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 12px;
  background: #ecf5ff;
  color: #409eff;
}
.result-empty {
  color: #909399;
  font-size: 13px;
}
.result-chunks {
  max-height: 420px;
  overflow-y: auto;
  padding-right: 4px;
}
.empty {
  color: #909399;
  text-align: center;
  padding: 24px 0;
}
.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.search-row {
  margin-bottom: 4px;
}
.hit {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.hit-meta {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}
.hit-text {
  font-size: 13px;
  color: #303133;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>

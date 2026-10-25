// RAG 知识库服务直连地址（RAG-MCP-SERVER 的 HTTP REST 层，独立于 Java 8080 / mall-agent 8002）
// P0-04：缺 env 时回退同源（prod 经 nginx /rag-api 反代），不再兜底 localhost。
const ragApi = import.meta.env.VITE_RAG_BASE_URL || ''

// 摄取阶段的单一来源：顺序 + 展示名 + emoji。服务端 stage 名（load/split/embed/upsert）
// 与本表对齐；「知识库管理」页的分步条/结果卡都从这里派生，避免阶段名散落多处手写。
export const RAG_STAGES: Array<{ key: string; label: string; ico: string }> = [
  { key: 'load', label: '加载', ico: '📄' },
  { key: 'split', label: '分块', ico: '✂️' },
  { key: 'embed', label: '向量化', ico: '🔢' },
  { key: 'upsert', label: '入库', ico: '💾' },
]
export const RAG_STAGE_ORDER: string[] = RAG_STAGES.map(s => s.key)
export function ragStageLabel(key: string): string {
  return RAG_STAGES.find(s => s.key === key)?.label ?? key
}

export interface RagDocument {
  document_id: string
  source_path: string
  chunk_count: number
  size_bytes: number | null
  modified_at: number | null
}

export interface RagChunkPreview {
  chunk_id: string
  text: string
  order: number
  /** 分块字符数（后端已返回，预览/回放展示用） */
  char_len?: number
}

/** /upload 的成功响应：除基础统计外携带分步清单与切分全文（供前端展示摄取效果） */
export interface RagUploadResult {
  ok: boolean
  document_id?: string
  filename?: string
  collection?: string
  imported?: number
  skipped?: number
  total_chunks?: number
  /** 摄取阶段清单，按序（load/split/embed/upsert…）；老服务端可能缺失。
   *  split 阶段额外带 chunk_count。当前为同步上传，无逐阶段 current/total 推送。 */
  stages?: Array<{ stage: string; chunk_count?: number }>
  /** 分块全文（无截断），用于上传成功后当场展示切分结果 */
  chunks?: RagChunkPreview[]
  error?: string
}

/** 列出知识库文档 */
export async function listDocuments(): Promise<RagDocument[]> {
  const res = await fetch(`${ragApi}/documents`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  return data?.documents ?? []
}

/** 删除文档 */
export async function deleteDocument(documentId: string): Promise<{ deleted: number }> {
  const res = await fetch(`${ragApi}/documents/${encodeURIComponent(documentId)}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** 预览文档（前若干 chunk）；full=true 取未截断全文 */
export async function previewDocument(
  documentId: string,
  full = true,
): Promise<{
  document_id: string
  source_path: string
  chunk_count: number
  chunks: RagChunkPreview[]
}> {
  const url = `${ragApi}/documents/${encodeURIComponent(documentId)}${full ? '?full=1' : ''}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** 检索知识库，返回命中片段 */
export async function searchDocuments(
  text: string,
  topK = 3,
): Promise<Array<{ chunk_id: string; source_path: string; score: number; text: string }>> {
  const res = await fetch(`${ragApi}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, top_k: topK }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  return data?.results ?? []
}

/** 上传文档（fetch 无上传进度，改用 XHR）；返回含分步清单与分块全文 */
export function uploadDocument(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<RagUploadResult> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('file', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${ragApi}/upload`)

    xhr.upload.onprogress = e => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText)
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data)
        } else {
          reject(new Error(data?.error || `HTTP ${xhr.status}`))
        }
      } catch {
        reject(new Error(`HTTP ${xhr.status}`))
      }
    }
    xhr.onerror = () => reject(new Error('网络错误，请确认 RAG 服务(:8123) 已启动'))
    xhr.send(form)
  })
}

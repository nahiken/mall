<template>
  <div v-if="chunks.length" class="chunk-list">
    <div v-for="(chunk, i) in chunks" :key="chunk.chunk_id || i" class="preview-chunk">
      <div class="chunk-head">
        {{ marker }}#{{ i + 1 }} · {{ chunk.chunk_id }}
        <template v-if="showCharLen"> · {{ chunk.char_len ?? chunk.text.length }} 字</template>
      </div>
      <div class="chunk-text">{{ chunk.text }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { RagChunkPreview } from '@/apis/rag'

defineProps<{
  chunks: RagChunkPreview[]
  /** 每块前缀标记，如 '✂️ '（切分回放）或 ''（文档预览） */
  marker?: string
  /** 是否显示每块字符数 */
  showCharLen?: boolean
}>()
</script>

<style scoped>
.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.preview-chunk {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px 12px;
}
.chunk-head {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}
.chunk-text {
  font-size: 13px;
  color: #303133;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>

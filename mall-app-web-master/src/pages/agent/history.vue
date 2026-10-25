<template>
  <view class="history-page">
    <!-- 会话列表：点击加载该会话并返回聊天（回放含决策流 agent_trace 卡片） -->
    <view v-if="items.length" class="list">
      <view
        v-for="item in items"
        :key="item.id"
        class="history-row"
        @click="openConversation(item.id)"
      >
        <view class="history-main">
          <text class="history-title">{{ item.title || '未命名对话' }}</text>
          <text class="history-meta">
            {{ formatTime(item.updated_at || item.created_at) }} · {{ item.message_count }} 条消息
          </text>
        </view>
        <text class="history-chevron">›</text>
      </view>
    </view>

    <view v-else-if="loading" class="empty-state">
      <text class="empty-hint">加载中…</text>
    </view>
    <view v-else class="empty-state">
      <text class="empty-hint">还没有历史会话</text>
      <text class="empty-sub">在“智能购物助手”里聊过天后，会出现在这里。</text>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { useAgentStore, type ConversationSummary } from '@/stores/agent'

const agent = useAgentStore()
const items = ref<ConversationSummary[]>([])
const loading = ref(false)

const formatTime = (iso: string) => {
  // 服务端返回 ISO 无时区（本地时间），去掉秒便于列表展示，避免 Date 本地化偏移
  return (iso || '').replace('T', ' ').slice(0, 16)
}

const loadList = async () => {
  loading.value = true
  try {
    items.value = await agent.listConversationSummaries()
  } catch (e) {
    uni.showToast({ title: '加载失败，请确认 Agent 已启动', icon: 'none' })
  } finally {
    loading.value = false
  }
}

const openConversation = async (id: number) => {
  // 仅加载成功才返回聊天页：未登录（loadConversation 内部已跳登录页）或加载失败
  // 返回 false 时留在历史页，避免误 navigateBack 回列表。
  const ok = await agent.loadConversation(id)
  if (ok) uni.navigateBack()
}

onShow(loadList)
</script>

<style lang="scss" scoped>
.history-page {
  min-height: 100vh;
  padding: 20rpx;
  background: $paper;
  box-sizing: border-box;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.history-row {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 22rpx 24rpx;
  background: $bg-card;
  border: 1px solid $brass-soft;
  border-radius: 16rpx;
  box-shadow: 0 4rpx 14rpx rgba(34, 39, 46, 0.05);
}
.history-main {
  flex: 1;
  min-width: 0;
}
.history-title {
  display: block;
  font-size: 29rpx;
  font-weight: 600;
  color: $text-strong;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.history-meta {
  display: block;
  margin-top: 6rpx;
  font-size: 22rpx;
  color: $mist;
}
.history-chevron {
  flex: none;
  font-size: 40rpx;
  color: $brass-soft;
  line-height: 1;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
  padding-top: 160rpx;
}
.empty-hint {
  font-size: 28rpx;
  color: $text-sub;
}
.empty-sub {
  font-size: 23rpx;
  color: $mist;
  text-align: center;
  padding: 0 60rpx;
}
</style>

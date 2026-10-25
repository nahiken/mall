<template>
  <view class="agent-page">
    <view class="agent-header">
      <text class="header-title">智能购物助手</text>
      <view class="header-actions">
        <text class="header-history" @click="openHistory">历史</text>
        <text class="header-clear" @click="handleClearHistory">清空历史</text>
      </view>
    </view>
    <scroll-view
      class="message-list"
      scroll-y
      :scroll-top="pageScrollTop"
      @scroll="handleMessageScroll"
    >
      <view class="message-inner">
        <view
          v-for="(message, index) in agent.messages"
          :key="index"
          class="message-row"
          :class="{ 'message-row--user': message.role === 'user' }"
        >
          <view
            class="message-bubble"
            :class="message.role === 'user' ? 'message-bubble--user' : 'message-bubble--assistant'"
          >
            <view v-if="message.steps && message.steps.length" class="message-steps">
              <view
                v-for="(step, si) in message.steps"
                :key="si"
                class="message-step"
                :class="{
                  'message-step--done': message.finished || si < message.steps.length - 1,
                  'message-step--current': !message.finished && si === message.steps.length - 1,
                }"
              >
                <view class="message-step-line">
                  <!-- “末步…/已计时”三元：与 admin chat/index.vue、floating-agent.vue 同款（三处模板），R4 改口径需同改 -->
                  <text class="message-step-label">{{ step.label }}</text>
                  <text class="message-step-dur">
                    {{ si < message.steps.length - 1 || message.finished
                        ? formatStepDuration(stepDurationMs(message, si))
                        : '…' }}
                  </text>
                </view>
              </view>
            </view>
            <text v-if="message.loading" class="thinking">思考中...</text>
            <text v-else class="message-text">{{ message.content }}</text>
            <scroll-view
              v-if="message.products?.length"
              class="product-scroll"
              scroll-x
              :show-scrollbar="false"
            >
              <view class="product-track">
                <view
                  v-for="product in message.products"
                  :key="product.id"
                  class="product-card"
                  @click="openProduct(product.url)"
                >
                  <image class="product-image" :src="product.pic" mode="aspectFill" />
                  <text class="product-name">{{ product.name }}</text>
                  <text class="product-price">¥{{ product.price }}</text>
                </view>
              </view>
            </scroll-view>
          </view>
        </view>
        <view class="scroll-anchor"></view>
      </view>
    </scroll-view>
    <scroll-view class="quick-scroll" scroll-x :show-scrollbar="false">
      <view class="quick-track">
        <view
          v-for="item in quickPrompts"
          :key="item"
          class="quick-chip"
          @click="sendQuick(item)"
          >{{ item }}</view
        >
      </view>
    </scroll-view>
    <view class="composer">
      <input
        v-model="inputText"
        class="composer-input"
        placeholder="输入聊天内容或选购需求..."
        :disabled="agent.sending"
        confirm-type="send"
        @confirm="submit"
      />
      <button class="send-button" :disabled="!inputText.trim() || agent.sending" @click="submit"
        >发送</button
      >
    </view>
  </view>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { onUnload } from '@dcloudio/uni-app'
import { useAgentStore, stepDurationMs, formatStepDuration } from '@/stores/agent'
import { useMessageScroller } from '@/composables/useMessageScroller'
const agent = useAgentStore()
const inputText = ref('')
const quickPrompts = [
  '预算3000元，推荐拍照手机',
  '推荐适合办公的电脑',
  '给我推荐几件衣服',
  '有什么好吃的零食',
  '你好，介绍一下你自己',
]

const getMessageScroller = () => {
  try {
    return document.querySelector('.agent-page .message-list') as HTMLElement | null
  } catch {
    return null
  }
}
const scroller = useMessageScroller({
  getScroller: getMessageScroller,
  scrollNonce: () => agent.scrollNonce,
  messageCount: () => agent.messages.length,
})
const { scrollTopRef: pageScrollTop, onScroll: handleMessageScroll } = scroller

const scrollToBottom = (force = false) => {
  scroller.stickToBottom()
  scroller.jumpToBottomAfterRender(force)
}

onMounted(() => agent.loadLatestConversation().finally(() => scrollToBottom(true)))
// P2-05：离开会话页即取消进行中的流，避免后台一直读 SSE、白占 agent worker。
// 只在 onUnload（页面销毁）而不是 onHide（切后台）触发——切后台回来仍应看到回复。
onUnload(() => {
  agent.cancelStream()
})
watch(
  () => agent.scrollNonce,
  () => scroller.jumpToBottom(false),
  { flush: 'post' },
)
watch(
  () => agent.messages.length,
  () => scroller.jumpToBottomAfterRender(false),
  { flush: 'post' },
)
const submit = async () => {
  const text = inputText.value
  inputText.value = ''
  scrollToBottom(true)
  await agent.sendMessage(text)
  scrollToBottom(true)
}
const sendQuick = async (text: string) => {
  scrollToBottom(true)
  await agent.sendMessage(text)
  scrollToBottom(true)
}
const openProduct = (url: string) => {
  if (url) uni.navigateTo({ url })
}
const openHistory = () => {
  uni.navigateTo({ url: '/pages/agent/history' })
}
const handleClearHistory = () => {
  uni.showModal({
    title: '清空历史',
    content: '旧记录会保留在数据库中，但此账号以后不会再自动加载它们。',
    success: async ({ confirm }) => {
      if (!confirm) return
      try {
        await agent.hideHistory()
        scrollToBottom(true)
        uni.showToast({ title: '已开启新会话', icon: 'success' })
      } catch {
        uni.showToast({ title: '清空失败，请确认 Agent 已启动', icon: 'none' })
      }
    },
  })
}
</script>

<style lang="scss" scoped>
.agent-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--window-top, 44px));
  min-height: 0;
  background: $paper;
}
.agent-header {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18rpx 26rpx;
  color: #f7f2e9;
  background: $ink;
}
.header-title {
  font-size: 32rpx;
  font-weight: 700;
  letter-spacing: 2rpx;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 12rpx;
}
.header-history {
  padding: 8rpx 18rpx;
  font-size: 24rpx;
  border-radius: 24rpx;
  background: rgba(255, 255, 255, 0.12);
  color: #f0e6d2;
}
.header-clear {
  padding: 8rpx 18rpx;
  font-size: 24rpx;
  border-radius: 24rpx;
  background: rgba(167, 134, 63, 0.28);
  color: #f0e6d2;
}
.message-list {
  flex: 1;
  min-height: 0;
  height: auto;
  padding: 20rpx;
}
.message-inner {
  padding-bottom: 12rpx;
}
.message-row {
  display: flex;
  justify-content: flex-start;
  margin-bottom: 20rpx;
}
.message-row--user {
  justify-content: flex-end;
}
.message-bubble {
  max-width: 86%;
  padding: 18rpx 22rpx;
  border-radius: 20rpx;
  font-size: 27rpx;
  line-height: 1.55;
  overflow: hidden;
}
.message-bubble--assistant {
  color: $text-body;
  background: $bg-card;
  border-bottom-left-radius: 6rpx;
  box-shadow: 0 4rpx 16rpx rgba(34, 39, 46, 0.06);
}
.message-bubble--user {
  color: #fff;
  background: $flag;
  border-bottom-right-radius: 6rpx;
}
.message-text {
  white-space: pre-wrap;
  word-break: break-word;
}
.thinking {
  color: $mist;
}
// 决策流竖向小卡片：当前节点朱红点亮脉动，完成转灰打勾（与悬浮窗同款）
.message-steps {
  margin-bottom: 10rpx;
  padding: 8rpx 14rpx 8rpx 20rpx;
  border-left: 4rpx solid $brass-soft;
  background: $paper-light;
  border-radius: 0 12rpx 12rpx 0;
}
.message-step {
  position: relative;
  padding-left: 30rpx;
  min-height: 40rpx;
}
.message-step::before {
  content: '';
  position: absolute;
  left: 0;
  top: 14rpx;
  width: 12rpx;
  height: 12rpx;
  border-radius: 50%;
  background: $mist;
}
.message-step-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12rpx;
}
.message-step-label {
  font-size: 24rpx;
  color: $text-sub;
}
.message-step-dur {
  font-size: 21rpx;
  color: $mist;
}
.message-step--done::before {
  background: $flag;
}
.message-step--done .message-step-label::before {
  content: '✓ ';
  color: $flag;
}
.message-step--current .message-step-label {
  color: $flag;
  font-weight: 600;
}
.message-step--current::before {
  background: $flag;
  box-shadow: 0 0 0 6rpx rgba(208, 58, 46, 0.18);
  animation: message-step-pulse 1.1s ease-in-out infinite;
}
@keyframes message-step-pulse {
  50% { opacity: 0.4; }
}
.product-scroll {
  width: 100%;
  height: 276rpx;
  margin-top: 18rpx;
  border-top: 1px solid $brass-soft;
}
.product-track {
  display: inline-flex;
  flex-wrap: nowrap;
  gap: 14rpx;
  padding-top: 16rpx;
  padding-right: 16rpx;
}
.product-card {
  flex: none;
  width: 190rpx;
  padding: 10rpx;
  border-radius: 14rpx;
  background: $paper-light;
  border: 1px solid $brass-soft;
}
.product-image {
  width: 170rpx;
  height: 150rpx;
  border-radius: 10rpx;
}
.product-name {
  display: block;
  margin-top: 8rpx;
  font-size: 23rpx;
  color: $text-strong;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.product-price {
  display: block;
  margin-top: 5rpx;
  color: $flag;
  font-size: 25rpx;
  font-weight: 700;
}
.quick-scroll {
  flex: none;
  width: 100%;
  height: 76rpx;
  background: $bg-card;
  border-top: 1px solid $brass-soft;
}
.quick-track {
  display: inline-flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 12rpx;
  height: 76rpx;
  padding: 0 18rpx;
}
.quick-chip {
  flex: none;
  padding: 10rpx 20rpx;
  color: $text-sub;
  font-size: 23rpx;
  white-space: nowrap;
  border: 1px solid $brass-soft;
  border-radius: 28rpx;
  background: $bg-card;
}
.composer {
  flex: none;
  display: flex;
  align-items: center;
  gap: 14rpx;
  width: 100%;
  padding: 14rpx 18rpx calc(14rpx + env(safe-area-inset-bottom));
  background: $bg-card;
  border-top: 1px solid $brass-soft;
}
.composer-input {
  flex: 1;
  min-width: 0;
  height: 70rpx;
  padding: 0 26rpx;
  font-size: 27rpx;
  border-radius: 36rpx;
  background: $paper-light;
}
.send-button {
  flex: none;
  height: 70rpx;
  line-height: 70rpx;
  padding: 0 28rpx;
  margin: 0;
  color: #fff;
  font-size: 26rpx;
  font-weight: 700;
  border-radius: 36rpx;
  background: $flag;
}
.send-button[disabled] {
  color: #cbbfa8;
  background: #e7dcc3;
}
.scroll-anchor {
  height: 1px;
}
</style>

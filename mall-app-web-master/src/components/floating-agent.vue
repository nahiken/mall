<template>
  <view
    v-if="inBrowseScope && !agent.expanded"
    class="agent-ball"
    :style="ballStyle"
    @touchstart.stop="startBallDrag"
    @touchmove.stop.prevent="applyDrag"
    @touchend.stop="finishDrag"
    @mousedown.stop.prevent="startBallDrag"
  >
    <text>小M</text>
  </view>

  <view v-else-if="inBrowseScope" class="agent-window" :style="windowStyle">
    <view
      class="window-header"
      @touchstart.stop="startWindowDrag"
      @touchmove.stop.prevent="applyDrag"
      @touchend.stop="finishDrag"
      @mousedown.stop.prevent="startWindowDrag"
    >
      <view>
        <text class="window-title">智能购物助手</text>
        <text class="window-subtitle">聊天与选购记录实时互通</text>
      </view>
      <text class="window-close" @click.stop="closeWindow">×</text>
    </view>

    <scroll-view
      class="window-messages"
      scroll-y
      :scroll-top="windowScrollTop"
      @scroll="handleMessageScroll"
    >
      <view class="window-message-inner">
        <view
          v-for="(message, index) in agent.messages"
          :key="index"
          class="window-message"
          :class="{ 'window-message--user': message.role === 'user' }"
        >
          <view class="window-bubble" :class="{ 'window-bubble--user': message.role === 'user' }">
            <view v-if="message.steps && message.steps.length" class="window-steps">
              <view
                v-for="(step, si) in message.steps"
                :key="si"
                class="window-step"
                :class="{
                  'window-step--done': message.finished || si < message.steps.length - 1,
                  'window-step--current': !message.finished && si === message.steps.length - 1,
                }"
              >
                <view class="window-step-line">
                  <!-- “末步…/已计时”三元：与 admin chat/index.vue、app pages/agent/index.vue 同款（三处模板），R4 改口径需同改 -->
                  <text class="window-step-label">{{ step.label }}</text>
                  <text class="window-step-dur">
                    {{ si < message.steps.length - 1 || message.finished
                        ? formatStepDuration(stepDurationMs(message, si))
                        : '…' }}
                  </text>
                </view>
              </view>
            </view>
            <text v-if="message.loading" class="window-thinking">思考中...</text>
            <text v-else class="window-text">{{ message.content }}</text>
            <scroll-view
              v-if="message.products?.length"
              class="window-products"
              scroll-x
              :show-scrollbar="false"
            >
              <view class="window-product-track">
                <view
                  v-for="product in message.products"
                  :key="product.id"
                  class="window-product"
                  @click="openProduct(product.url)"
                >
                  <image :src="product.pic" class="window-product-image" mode="aspectFill" />
                  <text class="window-product-name">{{ product.name }}</text>
                  <text class="window-product-price">¥{{ product.price }}</text>
                </view>
              </view>
            </scroll-view>
          </view>
        </view>
        <view class="window-anchor"></view>
      </view>
    </scroll-view>

    <scroll-view class="window-quick-scroll" scroll-x :show-scrollbar="false">
      <view class="window-quick-track">
        <view
          v-for="item in quickPrompts"
          :key="item"
          class="window-quick-chip"
          @click="sendQuick(item)"
        >
          {{ item }}
        </view>
      </view>
    </scroll-view>

    <view class="window-composer">
      <input
        v-model="inputText"
        class="window-input"
        placeholder="聊天或输入选购需求..."
        :disabled="agent.sending"
        confirm-type="send"
        @confirm="submit"
      />
      <text
        class="window-send"
        :class="{ 'window-send--active': inputText.trim() && !agent.sending }"
        @click="submit"
      >
        发送
      </text>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useAgentStore, stepDurationMs, formatStepDuration } from '@/stores/agent'
import { isBrowseScopeRoute } from '@/utils/agent-scope'
import { useMessageScroller } from '@/composables/useMessageScroller'

const agent = useAgentStore()
const inputText = ref('')
const quickPrompts = ['推荐手机', '推荐衣服', '办公电脑怎么选', '有什么零食', '你好']

const viewport = reactive({ width: 375, height: 667 })
const drag = reactive({
  active: false,
  kind: '' as 'ball' | 'window' | '',
  startX: 0,
  startY: 0,
  originX: 0,
  originY: 0,
  moved: false,
})

const currentRoute = ref('')
let routeTimer: ReturnType<typeof setInterval> | null = null

const panelWidth = computed(() => Math.min(viewport.width - 24, 420))
const panelHeight = computed(() => Math.min(viewport.height - 40, 560))
const ballStyle = computed(
  () => `left:${agent.floatingPosition.ballX}px;top:${agent.floatingPosition.ballY}px;`,
)
const windowStyle = computed(
  () =>
    `left:${agent.floatingPosition.panelX}px;top:${agent.floatingPosition.panelY}px;width:${panelWidth.value}px;height:${panelHeight.value}px;`,
)

const inBrowseScope = computed(() => isBrowseScopeRoute(currentRoute.value))

const updateCurrentRoute = () => {
  try {
    const pages = getCurrentPages()
    const current = pages[pages.length - 1] as any
    currentRoute.value = current?.route ? '/' + current.route : ''
  } catch {
    currentRoute.value = ''
  }
}
const getMessageScroller = () => {
  try {
    return document.querySelector('.agent-window .window-messages') as HTMLElement | null
  } catch {
    return null
  }
}
const scroller = useMessageScroller({
  getScroller: getMessageScroller,
  scrollNonce: () => agent.scrollNonce,
  messageCount: () => agent.messages.length,
  // 悬浮窗收起时（!agent.expanded）调用跳底直接跳过，避免操作隐藏 DOM。
  isGated: () => !agent.expanded,
})
const { scrollTopRef: windowScrollTop, onScroll: handleMessageScroll } = scroller

const clampPosition = () => {
  agent.setFloatingPosition({
    ballX: Math.max(0, Math.min(viewport.width - 58, agent.floatingPosition.ballX)),
    ballY: Math.max(0, Math.min(viewport.height - 78, agent.floatingPosition.ballY)),
    panelX: Math.max(0, Math.min(viewport.width - panelWidth.value, agent.floatingPosition.panelX)),
    panelY: Math.max(
      0,
      Math.min(viewport.height - panelHeight.value, agent.floatingPosition.panelY),
    ),
  })
}

const updateViewport = () => {
  const info = uni.getSystemInfoSync()
  viewport.width = info.windowWidth || 375
  viewport.height = info.windowHeight || 667
  clampPosition()
}

const point = (event: any) => {
  const source = event.touches?.[0] || event.changedTouches?.[0] || event
  return { x: source.clientX, y: source.clientY }
}

const begin = (event: any, kind: 'ball' | 'window') => {
  const current = point(event)
  Object.assign(drag, {
    active: true,
    kind,
    startX: current.x,
    startY: current.y,
    originX: kind === 'ball' ? agent.floatingPosition.ballX : agent.floatingPosition.panelX,
    originY: kind === 'ball' ? agent.floatingPosition.ballY : agent.floatingPosition.panelY,
    moved: false,
  })
  if (event.type === 'mousedown') {
    window.addEventListener('mousemove', mouseMove)
    window.addEventListener('mouseup', mouseUp, { once: true })
  }
}

const applyDrag = (event: any) => {
  if (!drag.active) return
  const current = point(event)
  const dx = current.x - drag.startX
  const dy = current.y - drag.startY
  if (Math.abs(dx) + Math.abs(dy) > 5) drag.moved = true

  if (drag.kind === 'ball') {
    agent.setFloatingPosition({
      ballX: Math.max(0, Math.min(viewport.width - 58, drag.originX + dx)),
      ballY: Math.max(0, Math.min(viewport.height - 78, drag.originY + dy)),
    })
  } else {
    agent.setFloatingPosition({
      panelX: Math.max(0, Math.min(viewport.width - panelWidth.value, drag.originX + dx)),
      panelY: Math.max(0, Math.min(viewport.height - panelHeight.value, drag.originY + dy)),
    })
  }
}

const finishDrag = () => {
  const shouldOpen = drag.kind === 'ball' && !drag.moved
  drag.active = false
  drag.kind = ''
  if (shouldOpen) {
    agent.expanded = true
    scroller.stickToBottom()
    agent.loadLatestConversation().finally(() => scroller.jumpToBottomAfterRender(true))
    scroller.jumpToBottomAfterRender(true)
  }
}

const closeWindow = () => {
  agent.expanded = false
}

const startBallDrag = (event: any) => begin(event, 'ball')
const startWindowDrag = (event: any) => begin(event, 'window')
const mouseMove = (event: MouseEvent) => applyDrag(event)
const mouseUp = () => {
  window.removeEventListener('mousemove', mouseMove)
  finishDrag()
}

const submit = async () => {
  const text = inputText.value
  inputText.value = ''
  scroller.stickToBottom()
  scroller.jumpToBottomAfterRender(true)
  await agent.sendMessage(text)
  scroller.jumpToBottomAfterRender(true)
}

const sendQuick = async (text: string) => {
  scroller.stickToBottom()
  scroller.jumpToBottomAfterRender(true)
  await agent.sendMessage(text)
  scroller.jumpToBottomAfterRender(true)
}

const openProduct = (url: string) => {
  if (url) uni.navigateTo({ url })
}

watch(inBrowseScope, (allowed) => {
  if (!allowed) agent.expanded = false
})

watch(
  () => agent.expanded,
  (expanded) => {
    if (expanded) {
      clampPosition()
      scroller.stickToBottom()
      agent.touchScroll()
      scroller.jumpToBottomAfterRender(true)
    }
  },
)
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

onMounted(() => {
  updateViewport()
  updateCurrentRoute()
  routeTimer = setInterval(updateCurrentRoute, 300)
  if (agent.expanded) scroller.jumpToBottomAfterRender(true)
  window.addEventListener('resize', updateViewport)
  // H5 浏览器前进/返回、hash 变化时即时刷新路由，弥补 300ms 轮询间隔
  if (typeof window !== 'undefined') {
    window.addEventListener('popstate', updateCurrentRoute)
    window.addEventListener('hashchange', updateCurrentRoute)
  }
})

onUnmounted(() => {
  if (routeTimer) {
    clearInterval(routeTimer)
    routeTimer = null
  }
  window.removeEventListener('resize', updateViewport)
  window.removeEventListener('mousemove', mouseMove)
  if (typeof window !== 'undefined') {
    window.removeEventListener('popstate', updateCurrentRoute)
    window.removeEventListener('hashchange', updateCurrentRoute)
  }
})
</script>

<style lang="scss" scoped>
// 悬浮球 —— 朱红“导购”圆章（百货吊牌识别色）
.agent-ball {
  position: fixed;
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  width: 96rpx;
  height: 96rpx;
  color: #fff;
  line-height: 1;
  border-radius: 50%;
  background: $flag;
  box-shadow: 0 6rpx 20rpx rgba(208, 58, 46, 0.34);
  touch-action: none;
  user-select: none;
}
.agent-ball text {
  font-size: 24rpx;
  font-weight: 700;
  letter-spacing: 1rpx;
}
.agent-ball::after {
  content: '';
  position: absolute;
  top: 6rpx;
  width: 26rpx;
  height: 5rpx;
  border-radius: 2rpx;
  background: rgba(255, 255, 255, 0.7);
}
.agent-ball text {
  margin-top: 8rpx;
}
// 悬浮窗 —— 白卡 + 墨黑导购帽檐
.agent-window {
  position: fixed;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  min-width: 300px;
  overflow: hidden;
  border-radius: 20rpx;
  background: $bg-card;
  box-shadow: 0 14rpx 44rpx rgba(34, 39, 46, 0.22);
}
.window-header {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16rpx 22rpx;
  color: #f7f2e9;
  background: $ink;
  touch-action: none;
  user-select: none;
}
.window-title,
.window-subtitle {
  display: block;
}
.window-title {
  font-size: 29rpx;
  font-weight: 700;
}
.window-subtitle {
  margin-top: 3rpx;
  font-size: 20rpx;
  color: $brass-soft;
}
.window-close {
  padding: 8rpx 12rpx;
  font-size: 38rpx;
}
.window-messages {
  flex: 1;
  min-height: 0;
  height: auto;
  padding: 18rpx;
  background: $paper;
}
.window-message-inner {
  padding-bottom: 8rpx;
}
.window-message {
  display: flex;
  justify-content: flex-start;
  margin-bottom: 16rpx;
}
.window-message--user {
  justify-content: flex-end;
}
.window-bubble {
  max-width: 86%;
  padding: 15rpx 18rpx;
  color: $ink-soft;
  font-size: 25rpx;
  line-height: 1.5;
  border-radius: 16rpx;
  background: $bg-card;
  box-shadow: 0 2rpx 8rpx rgba(34, 39, 46, 0.06);
  overflow: hidden;
}
.window-bubble--user {
  color: #fff;
  background: $flag;
}
.window-text {
  white-space: pre-wrap;
  word-break: break-word;
}
.window-thinking {
  color: $mist;
}
// 决策流竖向小卡片：当前节点朱红点亮，完成转青灰 + 打勾（百货“台步”节奏）
.window-steps {
  margin-bottom: 8rpx;
  padding: 6rpx 12rpx 6rpx 16rpx;
  border-left: 3rpx solid $brass-soft;
  background: $paper-light;
  border-radius: 0 10rpx 10rpx 0;
}
.window-step {
  position: relative;
  padding-left: 24rpx;
  min-height: 34rpx;
}
.window-step::before {
  content: '';
  position: absolute;
  left: 0;
  top: 12rpx;
  width: 10rpx;
  height: 10rpx;
  border-radius: 50%;
  background: $mist;
}
.window-step-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10rpx;
}
.window-step-label {
  font-size: 22rpx;
  color: $text-sub;
}
.window-step-dur {
  font-size: 20rpx;
  color: $mist;
}
.window-step--done::before {
  background: $flag;
}
.window-step--done .window-step-label {
  color: $text-sub;
}
.window-step--done .window-step-label::before {
  content: '✓ ';
  color: $flag;
}
.window-step--current .window-step-label {
  color: $flag;
  font-weight: 600;
}
.window-step--current::before {
  background: $flag;
  box-shadow: 0 0 0 6rpx rgba(208, 58, 46, 0.18);
  animation: window-step-pulse 1.1s ease-in-out infinite;
}
@keyframes window-step-pulse {
  50% { opacity: 0.4; }
}
.window-products {
  width: 100%;
  height: 226rpx;
  margin-top: 12rpx;
}
.window-product-track {
  display: inline-flex;
  flex-wrap: nowrap;
  gap: 10rpx;
}
.window-product {
  flex: none;
  width: 154rpx;
  padding: 8rpx;
  border-radius: 10rpx;
  background: $paper-light;
}
.window-product-image {
  width: 138rpx;
  height: 120rpx;
  border-radius: 8rpx;
}
.window-product-name,
.window-product-price {
  display: block;
}
.window-product-name {
  margin-top: 5rpx;
  font-size: 21rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.window-product-price {
  margin-top: 3rpx;
  color: $flag;
  font-size: 22rpx;
}
.window-quick-scroll {
  flex: none;
  width: 100%;
  height: 66rpx;
  background: $bg-card;
  border-top: 1rpx solid $brass-soft;
}
.window-quick-track {
  display: inline-flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 10rpx;
  height: 66rpx;
  padding: 0 14rpx;
}
.window-quick-chip {
  flex: none;
  padding: 8rpx 16rpx;
  color: $text-sub;
  font-size: 21rpx;
  white-space: nowrap;
  border: 1rpx solid $brass-soft;
  border-radius: 22rpx;
}
.window-composer {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10rpx;
  padding: 12rpx 16rpx calc(12rpx + env(safe-area-inset-bottom));
  border-top: 1rpx solid $brass-soft;
  background: $bg-card;
}
.window-input {
  flex: 1;
  min-width: 0;
  height: 62rpx;
  padding: 0 20rpx;
  font-size: 24rpx;
  border-radius: 32rpx;
  background: $paper-light;
}
.window-send {
  flex: none;
  padding: 12rpx;
  color: $mist;
  font-size: 24rpx;
}
.window-send--active {
  color: $flag;
  font-weight: 600;
}
.window-anchor {
  height: 1px;
}
</style>

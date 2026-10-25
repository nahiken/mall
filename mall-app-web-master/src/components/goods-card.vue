<template>
  <view class="goods-card" @click="handleClick">
    <view class="card-media" :style="{ height: imageHeight }">
      <image :src="item.pic" :mode="imageMode"></image>
      <view v-if="badge" class="card-badge tag-red">{{ badge }}</view>
    </view>

    <view class="card-body">
      <text class="card-name clamp">{{ item.name }}</text>
      <text v-if="item.subTitle" class="card-subtitle ellipsis">{{ item.subTitle }}</text>

      <view class="card-foot" :class="{ 'card-foot--ticket': showTicket }">
        <view class="card-price">
          <text class="card-price__symbol">￥</text>
          <text class="card-price__num">{{ formatPrice }}</text>
        </view>
        <text v-if="showSold && item.sale !== undefined" class="card-sold">已售 {{ item.sale }}</text>
        <slot name="price-extra" />
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PmsProduct } from '@/types/product'

const props = withDefaults(
  defineProps<{
    item: PmsProduct
    /** 图片裁剪模式：商品用 aspectFill，品牌 logo 可 aspectFit */
    imageMode?: 'aspectFill' | 'aspectFit'
    /** 图高，默认 330rpx（商品）；品牌卡可覆盖更矮 */
    imageHeight?: string
    /** 是否显示“已售 N” */
    showSold?: boolean
    /** 是否启用票根撕角价签（商品陈列用） */
    showTicket?: boolean
    /** 右上角小吊牌徽标（如 秒杀/新品） */
    badge?: string
  }>(),
  {
    imageMode: 'aspectFill',
    imageHeight: '330rpx',
    showSold: false,
    showTicket: true,
    badge: '',
  },
)

const emit = defineEmits<{ (e: 'click', item: PmsProduct): void }>()

const formatPrice = computed(() => {
  const n = Number(props.item.price)
  return Number.isFinite(n) ? Number(n.toFixed(2)).toString() : '0'
})

const handleClick = () => emit('click', props.item)
</script>

<style lang="scss" scoped>
.goods-card {
  display: flex;
  flex-direction: column;
  width: 100%;
  overflow: hidden;
  background: $bg-card;
  border-radius: 10rpx;
  // 白卡在纸米底上透缝：不画重阴影，只留一道浅投影
  box-shadow: 0 2rpx 12rpx rgba(34, 39, 46, 0.05);
}

.card-media {
  position: relative;
  width: 100%;
  overflow: hidden;
  background: $image-bg-color;
}
.card-media image {
  display: block;
  width: 100%;
  height: 100%;
}

// 右上角小吊牌
.card-badge {
  position: absolute;
  top: 12rpx;
  right: 0;
  padding: 4rpx 12rpx;
  font-size: 20rpx;
  letter-spacing: 0;
  border-radius: 6rpx 0 0 6rpx;
}

.card-body {
  display: flex;
  flex-direction: column;
  padding: 16rpx 14rpx 18rpx;
}
.card-name {
  color: $text-strong;
  font-size: 26rpx;
  font-weight: 600;
  line-height: 1.4;
}
.card-subtitle {
  margin-top: 6rpx;
  color: $text-sub;
  font-size: 22rpx;
  line-height: 1.35;
}

.card-foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-top: 14rpx;
}

// —— 票根撕角价签（签名元素②）——
// 左侧贴朱红小票根：左上留一个“撕角”，底部用虚点撕口，右侧延伸浅渍底
.card-foot--ticket {
  position: relative;
  align-items: flex-end;
  margin-top: 14rpx;
  padding: 0 12rpx 0 0;
}
.card-foot--ticket .card-price {
  position: relative;
  display: inline-flex;
  align-items: baseline;
  padding: 6rpx 16rpx 6rpx 14rpx;
  color: $flag;
  background: $flag-soft;
  border-radius: 6rpx 4rpx 4rpx 0;
}
// 撕角：左上 45° 缺口
.card-foot--ticket .card-price::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  width: 12rpx;
  height: 12rpx;
  background: $bg-card;
  border-radius: 0 0 100% 0;
}
// 撕口：下缘一段雾灰虚线，像票据齿孔
.card-foot--ticket .card-price::after {
  content: '';
  position: absolute;
  left: 12rpx;
  right: 6rpx;
  bottom: 0;
  height: 2rpx;
  opacity: 0.7;
  background: repeating-linear-gradient(
    to right,
    transparent 0,
    transparent 4rpx,
    $mist-soft 4rpx,
    $mist-soft 6rpx
  );
}

.card-price__symbol {
  font-size: 22rpx;
  font-weight: 700;
}
.card-price__num {
  font-size: 34rpx;
  font-weight: 800;
  letter-spacing: 0.5rpx;
}
.card-sold {
  margin-left: auto;
  color: $text-sub;
  font-size: 20rpx;
}
</style>

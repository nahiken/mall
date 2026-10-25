<template>
  <view class="page-skeleton" :class="`page-skeleton--${type}`">
    <view v-if="type === 'home'" class="hero shimmer"></view>
    <view v-if="type === 'home'" class="icon-row">
      <view v-for="item in 5" :key="item" class="icon-item">
        <view class="icon shimmer"></view>
        <view class="icon-label shimmer"></view>
      </view>
    </view>
    <view v-if="type === 'home'" class="section">
      <view class="section-title shimmer"></view>
      <view class="card-grid">
        <view v-for="item in 4" :key="item" class="card">
          <view class="card-image shimmer"></view>
          <view class="card-line shimmer"></view>
          <view class="card-line card-line--short shimmer"></view>
        </view>
      </view>
    </view>

    <template v-else>
      <view class="category-left">
        <view v-for="item in 8" :key="item" class="category-tab shimmer"></view>
      </view>
      <view class="category-right">
        <view v-for="item in 9" :key="item" class="category-item">
          <view class="category-icon shimmer"></view>
          <view class="category-label shimmer"></view>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ type?: 'home' | 'category' }>(), {
  type: 'home',
})
</script>

<style lang="scss" scoped>
.page-skeleton {
  position: absolute;
  inset: 0;
  z-index: 100;
  background: #f5f5f5;
  overflow: hidden;
}

.shimmer {
  background: linear-gradient(90deg, #eeeeee 25%, #f7f7f7 37%, #eeeeee 63%);
  background-size: 400% 100%;
  animation: shimmer 1.4s ease infinite;
}

.hero {
  height: 350rpx;
  margin: 20rpx 28rpx;
  border-radius: 12rpx;
}

.icon-row {
  display: flex;
  justify-content: space-around;
  padding: 28rpx 20rpx;
  background: #fff;
}

.icon-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.icon {
  width: 82rpx;
  height: 82rpx;
  border-radius: 50%;
}

.icon-label {
  width: 70rpx;
  height: 20rpx;
  margin-top: 14rpx;
  border-radius: 10rpx;
}

.section {
  margin-top: 16rpx;
  padding: 28rpx;
  background: #fff;
}

.section-title {
  width: 180rpx;
  height: 30rpx;
  margin-bottom: 24rpx;
  border-radius: 15rpx;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 24rpx;
}

.card-image {
  height: 260rpx;
  border-radius: 10rpx;
}

.card-line {
  height: 24rpx;
  margin-top: 16rpx;
  border-radius: 12rpx;
}

.card-line--short {
  width: 60%;
}

.page-skeleton--category {
  display: flex;
  background: #f8f8f8;
}

.category-left {
  width: 25%;
  padding: 18rpx;
  background: #fff;
}

.category-tab {
  height: 54rpx;
  margin: 24rpx 0;
  border-radius: 10rpx;
}

.category-right {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  align-content: start;
  gap: 30rpx 18rpx;
  margin: 20rpx;
  padding: 28rpx 18rpx;
  background: #fff;
}

.category-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.category-icon {
  width: 120rpx;
  height: 120rpx;
  border-radius: 12rpx;
}

.category-label {
  width: 80rpx;
  height: 20rpx;
  margin-top: 14rpx;
  border-radius: 10rpx;
}

@keyframes shimmer {
  0% { background-position: 100% 50%; }
  100% { background-position: 0 50%; }
}
</style>

<template>
  <view class="content">
    <text class="success-icon yticon icon-xuanzhong2"></text>
    <text class="tit">支付成功</text>
    <text v-if="orderId" class="order-tip">订单 {{ orderId }} 已进入待发货状态</text>

    <view class="btn-group">
      <navigator url="/pages/order/order?state=1" open-type="redirect" class="mix-btn">
        查看订单
      </navigator>
      <navigator url="/pages/index/index" open-type="switchTab" class="mix-btn hollow">
        返回首页
      </navigator>
    </view>
  </view>
  <!-- #ifndef H5 -->
  <floating-agent />
  <!-- #endif -->
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'

const orderId = ref<number | null>(null)

onLoad((options: any) => {
  const id = Number(options?.orderId)
  if (Number.isSafeInteger(id) && id > 0) orderId.value = id
})
</script>

<style lang="scss" scoped>
.content {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.success-icon {
  margin-top: 100rpx;
  font-size: 160rpx;
  color: #D03A2E;
}

.tit {
  font-size: 38rpx;
  color: #303133;
}

.order-tip {
  margin-top: 20rpx;
  font-size: 26rpx;
  color: #909399;
}

.btn-group {
  padding-top: 100rpx;
}

.mix-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 600rpx;
  height: 80rpx;
  margin-top: 30rpx;
  font-size: $font-lg;
  color: #fff;
  background-color: $base-color;
  border-radius: 10rpx;

  &.hollow {
    color: #303133;
    background: #fff;
    border: 1px solid #ccc;
  }
}
</style>

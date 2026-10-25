<template>
  <view class="app">
    <view class="price-box">
      <text>支付金额</text>
      <text class="price">{{ orderInfo.payAmount }}</text>
    </view>

    <view class="pay-type-list">
      <view class="type-item b-b">
        <text class="icon yticon icon-alipay"></text>
        <view class="con">
          <text class="tit">支付宝支付</text>
          <text>本地模拟支付，不会产生真实扣款</text>
        </view>
        <label class="radio">
          <radio name="payType" value="1" color="#D03A2E" :checked="true" />
        </label>
      </view>
    </view>

    <button class="mix-btn" :disabled="paying || orderInfo.status !== 0" @click="handleConfirmPay">
      {{ paying ? '支付处理中...' : '确认支付' }}
    </button>
  </view>
  <!-- #ifndef H5 -->
  <floating-agent />
  <!-- #endif -->
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { getOrderDetailAPI, payOrderSuccessAPI } from '@/apis/order'
import type { OmsOrderDetail } from '@/types/order'

const orderId = ref<number | null>(null)
const orderInfo = ref<Partial<OmsOrderDetail>>({})
const paying = ref(false)

const loadData = async () => {
  if (!orderId.value) return

  try {
    const res = await getOrderDetailAPI(orderId.value)
    orderInfo.value = res.data
    if (res.data.status !== 0) {
      uni.showToast({ title: '该订单无需再次支付', icon: 'none' })
    }
  } catch (error) {
    console.error('加载订单详情失败', error)
  }
}

onLoad((options: any) => {
  const id = Number(options?.orderId)
  if (!Number.isSafeInteger(id) || id <= 0) {
    uni.showToast({ title: '订单参数错误', icon: 'none' })
    return
  }
  orderId.value = id
  loadData()
})

const handleConfirmPay = async () => {
  if (!orderId.value || paying.value) return
  if (orderInfo.value.status !== 0) {
    uni.showToast({ title: '该订单无需再次支付', icon: 'none' })
    return
  }

  paying.value = true
  uni.showLoading({ title: '支付处理中', mask: true })
  try {
    await payOrderSuccessAPI({ orderId: orderId.value, payType: 1 })
    uni.redirectTo({ url: `/pages/money/paySuccess?orderId=${orderId.value}` })
  } catch (error) {
    console.error('支付失败', error)
  } finally {
    paying.value = false
    uni.hideLoading()
  }
}
</script>

<style lang="scss" scoped>
.app {
  width: 100%;
}

.price-box {
  height: 265rpx;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  font-size: 28rpx;
  color: #909399;
  background-color: #fff;

  .price {
    margin-top: 12rpx;
    font-size: 50rpx;
    color: #303133;

    &::before {
      content: '￥';
      font-size: 40rpx;
    }
  }
}

.pay-type-list {
  margin-top: 20rpx;
  padding-left: 60rpx;
  background-color: #fff;

  .type-item {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 120rpx;
    padding: 20rpx 60rpx 20rpx 0;
    font-size: 30rpx;
  }

  .icon {
    width: 100rpx;
    font-size: 52rpx;
  }

  .icon-alipay {
    color: #01aaef;
  }

  .tit {
    margin-bottom: 4rpx;
    font-size: $font-lg;
    color: $font-color-dark;
  }

  .con {
    flex: 1;
    display: flex;
    flex-direction: column;
    font-size: $font-sm;
    color: $font-color-light;
  }
}

.mix-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 630rpx;
  height: 80rpx;
  margin: 80rpx auto 30rpx;
  font-size: $font-lg;
  color: #fff;
  background-color: $base-color;
  border-radius: 10rpx;
  box-shadow: 1px 2px 5px rgba(219, 63, 96, 0.4);

  &[disabled] {
    color: #fff;
    opacity: 0.65;
  }
}
</style>

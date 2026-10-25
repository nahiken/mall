<template>
  <view class="content">
    <!-- 顶部 Banner -->
    <image src="/static/hot_product_banner.png" mode="widthFix" class="banner-image"></image>
    <!-- 吊牌行标题 -->
    <view class="section-head">
      <view class="section-hook tag-hook"></view>
      <view class="section-tit tag-red">人气商品</view>
      <text class="section-sub">大家正在抢</text>
    </view>
    <!-- 商品列表 -->
    <view class="goods-list">
      <goods-card
        v-for="item in productList"
        :key="item.id"
        :item="item"
        show-sold
        show-ticket
        @click="handleNavToDetail"
      />
    </view>
    <!-- 加载状态 -->
    <view class="loading-more">
      <text v-if="loadingType === 'loading'">加载中...</text>
      <text v-else-if="loadingType === 'nomore'">没有更多了</text>
    </view>
  </view>
  <!-- #ifndef H5 -->
  <floating-agent />
  <!-- #endif -->
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { getHotProductListAPI } from '@/apis/home'
import type { PageParam } from '@/types/common'
import type { PmsProduct } from '@/types/product'

// ===== 页面数据 =====
// 加载状态：more-可加载，loading-加载中，nomore-没有更多
const loadingType = ref<'more' | 'loading' | 'nomore'>('more')
// 商品列表
const productList = ref<PmsProduct[]>([])
// 搜索参数
const searchParam = ref<PageParam>({
  pageNum: 1,
  pageSize: 6,
})

// ===== loadData =====
// 加载商品列表，支持下拉刷新和上滑加载
const loadData = async (type: 'refresh' | 'add' = 'add') => {
  // 没有更多直接返回
  if (type === 'add' && loadingType.value === 'nomore') {
    return
  }

  if (type === 'add') {
    loadingType.value = 'loading'
  } else {
    loadingType.value = 'more'
  }

  // 刷新时重置页码和列表
  if (type === 'refresh') {
    searchParam.value.pageNum = 1
    productList.value = []
  }

  try {
    const res = await getHotProductListAPI(searchParam.value)
    const list = res.data

    if (list.length === 0) {
      // 没有更多了
      loadingType.value = 'nomore'
      searchParam.value.pageNum--
    } else if (list.length < searchParam.value.pageSize) {
      loadingType.value = 'nomore'
      searchParam.value.pageNum--
    } else {
      loadingType.value = 'more'
    }

    // 追加或替换数据
    productList.value = productList.value.concat(list)

    // 刷新时停止加载动画
    if (type === 'refresh') {
      uni.hideLoading()
      uni.stopPullDownRefresh()
    }
  } catch (e) {
    console.error('加载人气商品失败', e)
    loadingType.value = 'more'
  }
}

// ===== onLoad =====
// 页面加载
onLoad(() => {
  loadData()
})

// ===== onPullDownRefresh =====
// 下拉刷新
onPullDownRefresh(() => {
  loadData('refresh')
})

// ===== onReachBottom =====
// 上拉加载
onReachBottom(() => {
  searchParam.value.pageNum++
  loadData()
})

// ===== 事件处理方法 =====
// 跳转商品详情
const handleNavToDetail = (item: PmsProduct) => {
  uni.navigateTo({
    url: `/pages/product/product?id=${item.id}`,
  })
}
</script>

<style lang="scss" scoped>
page,
.content {
  background: $page-color-base;
}

.banner-image {
  width: 100%;
}

.section-head {
  display: flex;
  align-items: center;
  padding: 26rpx 30rpx 20rpx;
  background: $page-color-base;
}
.section-sub {
  margin-left: 16rpx;
  color: $font-color-light;
  font-size: $font-sm;
}

/* 商品列表 —— 双列白瓷片卡，纸米底透缝 */
.goods-list {
  display: flex;
  flex-wrap: wrap;
  padding: 0 24rpx 40rpx;
  gap: 20rpx;

  .goods-card {
    width: calc((100% - 20rpx) / 2);
  }
}

/* 加载状态 */
.loading-more {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 30rpx;
  font-size: 28rpx;
  color: #999;
}
</style>

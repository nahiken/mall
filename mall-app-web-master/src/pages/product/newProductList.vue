<template>
  <view class="content">
    <!-- 顶部 Banner -->
    <image src="/static/new_product_banner.png" mode="widthFix" class="banner-image"></image>
    <!-- 吊牌行标题 -->
    <view class="section-head">
      <view class="section-hook tag-hook"></view>
      <view class="section-tit tag-red">新鲜好物</view>
      <text class="section-sub">刚到的新货</text>
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
    <view v-if="loadingType === 'more'" class="load-more">上拉加载更多</view>
    <view v-else-if="loadingType === 'loading'" class="load-more">加载中...</view>
    <view v-else class="load-more">没有更多了</view>
  </view>
  <!-- #ifndef H5 -->
  <floating-agent />
  <!-- #endif -->
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { getNewProductListAPI } from '@/apis/home'
import type { PmsProduct } from '@/types/product'
import type { PageParam } from '@/types/common'

// ===== 页面数据 =====
// 加载更多状态
const loadingType = ref<'more' | 'loading' | 'nomore'>('more')
// 新鲜商品列表
const productList = ref<PmsProduct[]>([])
// 分页请求参数
const searchParam = ref<PageParam>({
  pageNum: 1,
  pageSize: 6,
})

// ===== loadData =====
// 加载商品列表，带下拉刷新和上滑加载
const loadData = async (type: 'refresh' | 'add' = 'add') => {
  // 没有更多直接返回
  if (type === 'add') {
    if (loadingType.value === 'nomore') {
      return
    }
    loadingType.value = 'loading'
  } else {
    loadingType.value = 'more'
  }

  // 下拉刷新时重置
  if (type === 'refresh') {
    searchParam.value.pageNum = 1
    productList.value = []
  }

  try {
    const res = await getNewProductListAPI(searchParam.value)
    const productListData = res.data

    if (productListData.length === 0) {
      // 没有更多了
      loadingType.value = 'nomore'
      searchParam.value.pageNum = Math.max(1, searchParam.value.pageNum - 1)
    } else {
      if (productListData.length < searchParam.value.pageSize) {
        loadingType.value = 'nomore'
        searchParam.value.pageNum = Math.max(1, searchParam.value.pageNum - 1)
      } else {
        loadingType.value = 'more'
      }
      productList.value = productList.value.concat(productListData)
    }

    // 下拉刷新完成后停止
    if (type === 'refresh') {
      uni.stopPullDownRefresh()
    }
  } catch (e) {
    console.error('加载新鲜商品失败', e)
    if (type === 'refresh') {
      uni.stopPullDownRefresh()
    }
  }
}

// ===== onLoad =====
// 页面加载时执行
onLoad(() => {
  loadData()
})

// ===== onPullDownRefresh =====
// 下拉刷新
onPullDownRefresh(() => {
  loadData('refresh')
})

// ===== onReachBottom =====
// 上拉加载更多
onReachBottom(() => {
  searchParam.value.pageNum++
  loadData()
})

// ===== 事件处理方法 =====
// 跳转商品详情页
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

.load-more {
  text-align: center;
  padding: 20rpx;
  font-size: 28rpx;
  color: $font-color-light;
}
</style>

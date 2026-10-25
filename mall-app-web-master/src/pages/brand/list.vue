<template>
  <view class="content">
    <!-- 顶部 Banner -->
    <image src="/static/recommend_brand_banner.png" mode="widthFix" class="banner-image"></image>
    <!-- 吊牌行标题 -->
    <view class="section-head">
      <view class="section-hook tag-hook"></view>
      <view class="section-tit tag-red">品牌货架</view>
      <text class="section-sub">甄选好牌子</text>
    </view>
    <!-- 品牌列表 -->
    <view class="brand-list">
      <view
        v-for="(item, index) in brandList"
        :key="index"
        class="goods-item"
        @click="handleNavToDetail(item)"
      >
        <view class="image-wrapper">
          <image :src="item.logo" mode="aspectFit"></image>
        </view>
        <text class="title clamp">{{ item.name }}</text>
        <text class="title2">商品数量：{{ item.productCount }}</text>
      </view>
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
import { getBrandRecommendListAPI } from '@/apis/brand'
import type { PmsBrand } from '@/types/brand'
import type { PageParam } from '@/types/common'

// ===== 页面数据 =====
// 加载更多状态
const loadingType = ref<'more' | 'loading' | 'nomore'>('more')
// 推荐品牌列表
const brandList = ref<PmsBrand[]>([])
// 分页请求参数
const searchParam = ref<PageParam>({
  pageNum: 1,
  pageSize: 6,
})

// ===== loadData =====
// 加载品牌列表，带下拉刷新和上滑加载
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
    brandList.value = []
  }

  try {
    const res = await getBrandRecommendListAPI(searchParam.value)
    const brandListData = res.data

    if (brandListData.length === 0) {
      // 没有更多了
      loadingType.value = 'nomore'
      searchParam.value.pageNum = Math.max(1, searchParam.value.pageNum - 1)
    } else {
      if (brandListData.length < searchParam.value.pageSize) {
        loadingType.value = 'nomore'
        searchParam.value.pageNum = Math.max(1, searchParam.value.pageNum - 1)
      } else {
        loadingType.value = 'more'
      }
      brandList.value = brandList.value.concat(brandListData)
    }

    // 下拉刷新完成后停止
    if (type === 'refresh') {
      uni.stopPullDownRefresh()
    }
  } catch (e) {
    console.error('加载推荐品牌失败', e)
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
// 跳转品牌详情页
const handleNavToDetail = (item: PmsBrand) => {
  uni.navigateTo({
    url: `/pages/brand/brandDetail?id=${item.id}`,
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

/* 品牌列表 —— 双列白瓷片卡，纸米透缝 */
.brand-list {
  display: flex;
  flex-wrap: wrap;
  gap: 20rpx;
  padding: 0 24rpx 40rpx;

  .goods-item {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    width: calc((100% - 20rpx) / 2);
    overflow: hidden;
    background: $bg-card;
    border-radius: 10rpx;
    box-shadow: 0 2rpx 12rpx rgba(34, 39, 46, 0.05);
  }

  .image-wrapper {
    width: 100%;
    height: 150rpx;
    background-color: $paper-light;

    image {
      display: block;
      width: 100%;
      height: 100%;
      opacity: 1;
    }
  }

  .title {
    font-size: $font-lg;
    color: $font-color-dark;
    font-weight: 600;
    line-height: 80rpx;
    text-align: center;
  }

  .title2 {
    font-size: $font-sm;
    color: $font-color-light;
    line-height: 40rpx;
    height: 60rpx;
    overflow: hidden;
    text-overflow: ellipsis;
    text-align: center;
    display: block;
  }
}

.load-more {
  text-align: center;
  padding: 20rpx;
  font-size: 28rpx;
  color: $font-color-light;
}
</style>

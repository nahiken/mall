<template>
  <view class="content">
    <!-- 顶部大图 -->
    <view class="top-image">
      <view class="image-wrapper">
        <image :src="brand.bigPic" mode="aspectFill"></image>
      </view>
    </view>
    <!-- 品牌信息 -->
    <view class="info">
      <view class="image-wrapper">
        <image :src="brand.logo" mode="aspectFit"></image>
      </view>
      <view class="title">
        <text>{{ brand.name }}</text>
        <text>品牌首字母：{{ brand.firstLetter }}</text>
      </view>
      <view>
        <text
          class="yticon icon-shoucang"
          :class="{ active: favoriteStatus }"
          @click="handleToggleFavorite"
        ></text>
      </view>
    </view>
    <!-- 品牌故事 -->
    <view class="section-head">
      <view class="section-hook tag-hook"></view>
      <view class="section-tit tag-red">品牌故事</view>
    </view>
    <view class="brand-story">
      <text class="text">{{ brand.brandStory }}</text>
    </view>
    <!-- 相关商品 -->
    <view class="section-head">
      <view class="section-hook tag-hook"></view>
      <view class="section-tit tag-red">相关商品</view>
    </view>
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
import { getBrandDetailAPI, getBrandProductListAPI } from '@/apis/brand'
import type { BrandProductListParam } from '@/apis/brand'
import {
  createBrandAttentionAPI,
  deleteBrandAttentionAPI,
  getBrandAttentionDetailAPI,
} from '@/apis/memberBrandAttention'
import type { PmsBrand } from '@/types/brand'
import type { PmsProduct } from '@/types/product'
import { useMemberStore } from '@/stores/member'

// ===== 页面数据 =====
// 品牌信息
const brand = ref<PmsBrand>({} as PmsBrand)
// 商品列表
const productList = ref<PmsProduct[]>([])
// 加载更多状态
const loadingType = ref<'more' | 'loading' | 'nomore'>('more')
// 收藏状态
const favoriteStatus = ref(false)
// 分页请求参数
const queryParam = ref<BrandProductListParam>({
  brandId: 0,
  pageNum: 1,
  pageSize: 4,
})

// ===== 加载品牌详情 =====
const loadBrandDetail = async (id: number) => {
  const res = await getBrandDetailAPI(id)
  brand.value = res.data
  // 初始化收藏状态
  initBrandAttention()
}

// ===== 加载商品列表 =====
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
    queryParam.value.pageNum = 1
    productList.value = []
  }

  try {
    const res = await getBrandProductListAPI(queryParam.value)
    const list = res.data.list

    if (list.length === 0) {
      // 没有更多了
      loadingType.value = 'nomore'
      queryParam.value.pageNum = Math.max(1, queryParam.value.pageNum - 1)
    } else {
      if (list.length < queryParam.value.pageSize) {
        loadingType.value = 'nomore'
        queryParam.value.pageNum = Math.max(1, queryParam.value.pageNum - 1)
      } else {
        loadingType.value = 'more'
      }
      productList.value = productList.value.concat(list)
    }

    // 下拉刷新完成后停止
    if (type === 'refresh') {
      uni.stopPullDownRefresh()
    }
  } catch (e) {
    console.error('加载品牌商品失败', e)
    if (type === 'refresh') {
      uni.stopPullDownRefresh()
    }
  }
}

// ===== 初始化收藏状态 =====
const initBrandAttention = async () => {
  const memberStore = useMemberStore()
  if (memberStore.hasLogin) {
    try {
      const res = await getBrandAttentionDetailAPI(brand.value.id)
      favoriteStatus.value = res.data != null
    } catch {
      favoriteStatus.value = false
    }
  }
}

// ===== 收藏/取消收藏 =====
const handleToggleFavorite = async () => {
  const memberStore = useMemberStore()
  if (!memberStore.hasLogin) {
    uni.showModal({
      title: '提示',
      content: '你还没登录，是否要登录？',
      confirmText: '去登录',
      cancelText: '取消',
      success: (res) => {
        if (res.confirm) {
          uni.navigateTo({ url: '/pages/public/login' })
        }
      },
    })
    return
  }

  try {
    if (favoriteStatus.value) {
      // 取消收藏
      await deleteBrandAttentionAPI(brand.value.id)
      uni.showToast({ title: '取消收藏成功！', icon: 'none' })
    } else {
      // 收藏
      await createBrandAttentionAPI({
        brandId: brand.value.id,
        brandName: brand.value.name,
        brandLogo: brand.value.logo,
        brandCity: '',
      })
      uni.showToast({ title: '收藏成功！', icon: 'none' })
    }
    favoriteStatus.value = !favoriteStatus.value
  } catch (e) {
    console.error('收藏操作失败', e)
  }
}

// ===== 跳转商品详情 =====
const handleNavToDetail = (item: PmsProduct) => {
  uni.navigateTo({
    url: `/pages/product/product?id=${item.id}`,
  })
}

// ===== 生命周期 =====
onLoad((options) => {
  const id = Number(options?.id)
  queryParam.value.brandId = id
  loadBrandDetail(id)
  loadData('refresh')
})

onPullDownRefresh(() => {
  loadData('refresh')
})

onReachBottom(() => {
  queryParam.value.pageNum++
  loadData()
})
</script>

<style lang="scss" scoped>
page,
.content {
  background: $page-color-base;
}

.top-image {
  height: 200px;

  .image-wrapper {
    display: flex;
    justify-content: center;
    align-content: center;
    width: 100%;
    height: 100%;
    overflow: hidden;

    image {
      width: 100%;
      height: 100%;
    }
  }
}

.info {
  display: flex;
  align-items: center;
  padding: 30rpx 40rpx;
  background: $bg-card;
  margin: 16rpx 24rpx 0;
  border-radius: 12rpx;
  box-shadow: 0 2rpx 12rpx rgba(34, 39, 46, 0.05);

  .image-wrapper {
    width: 210rpx;
    height: 70rpx;
    background: transparent;

    image {
      width: 100%;
      height: 100%;
    }
  }

  .title {
    flex: 1;
    display: flex;
    flex-direction: column;
    font-size: $font-lg + 4rpx;
    margin-left: 30rpx;
    color: $font-color-dark;

    text:last-child {
      font-size: $font-sm;
      color: $font-color-light;
      margin-top: 8rpx;
    }
  }

  .yticon {
    font-size: 80rpx;
    color: $font-color-base;
    margin: 0 10rpx 0 30rpx;

    &.active {
      color: $accent;
    }
  }
}

.brand-story {
  display: flex;
  margin: 20rpx 24rpx 0;
  padding: 30rpx;
  background: $bg-card;
  border-radius: 12rpx;

  .text {
    font-size: $font-sm;
    color: $font-color-light;
    line-height: 1.7;
  }
}

/* 吊牌行标题 */
.section-head {
  display: flex;
  align-items: center;
  padding: 26rpx 30rpx 14rpx;
}

/* 相关商品 —— 双列白瓷片卡 */
.goods-list {
  display: flex;
  flex-wrap: wrap;
  gap: 20rpx;
  padding: 0 24rpx 20rpx;

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

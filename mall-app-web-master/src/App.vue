<script setup lang="ts">
import { onLaunch, onShow, onHide } from '@dcloudio/uni-app'

onLaunch(() => {
  console.log('App Launch')
})
onShow(() => {
  console.log('App Show')
})
onHide(() => {
  console.log('App Hide')
})
</script>

<style lang="scss">
// 字体图标
@use '@/styles/fonts.scss';

view,
navigator,
input,
scroll-view {
  box-sizing: border-box;
}

button::after {
  border: none;
}

swiper,
scroll-view {
  flex: 1;
  height: 100%;
  overflow: hidden;
}

image {
  width: 100%;
  height: 100%;
  vertical-align: middle;
}

// 一行省略
.clamp {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}

// 两行省略
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  line-clamp: 2;
  -webkit-line-clamp: 2;
}

// 边框样式（底部边框）
.b-b:after,
.b-t:after {
  position: absolute;
  z-index: 3;
  left: 0;
  right: 0;
  height: 0;
  content: '';
  transform: scaleY(0.5);
  border-bottom: 1px solid #e4e7ed;
}

.b-b:after {
  bottom: 0;
}

.b-t:after {
  top: 0;
}

/* ===== 百货吊牌 · 全局主题工具类 ===== */

// 吊牌行标题：黄铜绳钩（细竖绳 + 顶圈） + 内容
.tag-hook {
  position: relative;
  flex: none;
  width: 14rpx;
  height: 30rpx;
  margin-right: 14rpx;
}
.tag-hook::before {
  content: '';
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  width: 2rpx;
  transform: translateX(-50%);
  background: $brass;
}
.tag-hook::after {
  content: '';
  position: absolute;
  left: 50%;
  top: -4rpx;
  width: 10rpx;
  height: 10rpx;
  transform: translateX(-50%);
  border-radius: 50%;
  background: $brass;
}

// 朱红吊牌底板（白字题名）—— 用作楼面 / 栏目吊牌
.tag-red {
  flex: none;
  display: inline-flex;
  align-items: center;
  padding: 6rpx 20rpx;
  color: #fff;
  font-size: 27rpx;
  font-weight: 700;
  line-height: 1.4;
  letter-spacing: 2rpx;
  border-radius: 4rpx;
  background: $flag;
  box-shadow: 0 2rpx 8rpx rgba(208, 58, 46, 0.18);
}

// 层板分隔线（黄铜细线 + 雾灰虚线辅助）
.divider-line {
  position: relative;
  height: 1px;
  margin: 0;
  overflow: visible;
  border: 0;
  background: transparent;
}
.divider-line::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 1px;
  background: linear-gradient(to right, $brass, $brass-soft, transparent);
}

// 价签贴标：浅渍底 + 朱红价，票根撕角用伪元素
.price-stamp {
  display: inline-flex;
  align-items: baseline;
  padding: 2rpx 10rpx 2rpx 14rpx;
  color: $flag;
  border-radius: 0 6rpx 6rpx 0;
  background: $flag-soft;
}
</style>

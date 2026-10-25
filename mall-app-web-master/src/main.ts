import { createSSRApp } from 'vue'
// #ifdef H5
import { createApp as createVueApp } from 'vue'
import FloatingAgent from '@/components/floating-agent.vue'
// #endif
import { createPinia } from 'pinia'
import persist from 'pinia-plugin-persistedstate'

import App from './App.vue'
export function createApp() {
  // 创建 Vue 应用
  const app = createSSRApp(App)
  // 创建 Pinia
  const pinia = createPinia()
  // 使用 Pinia 持久化插件
  pinia.use(persist)
  // 使用 Pinia 插件
  app.use(pinia)

  // #ifdef H5
  // H5 端把 AI 浮窗挂到 document.body，避免页面首次按需编译/切换时跟随页面卸载而闪烁。
  if (typeof document !== 'undefined') {
    setTimeout(() => {
      if (document.getElementById('floating-agent-root')) return
      const root = document.createElement('div')
      root.id = 'floating-agent-root'
      document.body.appendChild(root)
      const floatingApp = createVueApp(FloatingAgent)
      floatingApp.use(pinia)
      floatingApp.mount(root)
    }, 0)
  }
  // #endif

  return {
    app,
  }
}

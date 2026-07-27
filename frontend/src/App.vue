<template>
  <el-container class="layout">
    <el-header class="header" :class="{ 'header-mobile': isMobile }">
      <template v-if="isMobile">
        <el-button class="hamburger" text @click="drawer = true">☰</el-button>
        <div class="brand">📈 Stock AI</div>
        <div class="actions actions-mobile">
          <el-tag v-if="status.running" type="warning" effect="dark" size="small">进行中</el-tag>
          <el-tag v-else-if="status.schedule_enabled" type="success" size="small">
            下次 {{ shortNextRun }}
          </el-tag>
          <el-tag v-else type="info" size="small">已关闭</el-tag>
          <el-button size="small" type="primary" :loading="triggering" :disabled="status.running" @click="trigger">
            运行
          </el-button>
        </div>
      </template>
      <template v-else>
        <div class="brand">📈 Stock AI <span class="sub">A 股 AI 模拟交易</span></div>
        <el-menu mode="horizontal" :default-active="activePath" router class="menu" :ellipsis="false">
          <el-menu-item v-for="item in NAV" :key="item.path" :index="item.path">{{ item.label }}</el-menu-item>
        </el-menu>
        <div class="actions">
          <el-tag v-if="status.running" type="warning" effect="dark">决策进行中…</el-tag>
          <el-tag v-else-if="status.schedule_enabled" type="success">
            {{ status.schedule_times }} · 下次决策 {{ status.next_run || '-' }}
          </el-tag>
          <el-tag v-else type="info">调度已关闭</el-tag>
          <el-button type="primary" :loading="triggering" :disabled="status.running" @click="trigger">
            立即运行一轮
          </el-button>
        </div>
      </template>
    </el-header>

    <el-drawer v-model="drawer" direction="ltr" size="220px" :with-header="false">
      <div class="drawer-brand">📈 Stock AI</div>
      <el-menu :default-active="activePath" router @select="drawer = false">
        <el-menu-item v-for="item in NAV" :key="item.path" :index="item.path">{{ item.label }}</el-menu-item>
      </el-menu>
    </el-drawer>

    <el-main :class="{ 'main-mobile': isMobile }">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from './api/index.js'
import { useIsMobile } from './composables/useIsMobile.js'

const NAV = [
  { path: '/', label: '仪表盘' },
  { path: '/runs', label: '决策记录' },
  { path: '/watchlist', label: '股池管理' },
  { path: '/orders', label: '交易记录' },
  { path: '/models', label: '模型管理' },
]

const route = useRoute()
const activePath = computed(() => (route.path.startsWith('/runs') ? '/runs' : route.path))
const { isMobile } = useIsMobile()
const drawer = ref(false)

const status = ref({})
const triggering = ref(false)
let timer = null

const shortNextRun = computed(() => {
  const next = status.value.next_run || ''
  const match = next.match(/\d{2}:\d{2}$/)
  return match ? match[0] : next || '-'
})

async function refreshStatus() {
  try {
    status.value = await api.getStatus()
  } catch { /* 后端未启动时静默 */ }
}

async function trigger() {
  triggering.value = true
  try {
    await api.triggerRun()
    ElMessage.success('决策流程已启动,请稍后在决策记录中查看')
    await refreshStatus()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    triggering.value = false
  }
}

onMounted(() => {
  refreshStatus()
  timer = setInterval(refreshStatus, 10000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style>
body { margin: 0; background: #f5f7fa; font-family: 'Helvetica Neue', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif; }
.layout { min-height: 100vh; }
.header { display: flex; align-items: center; gap: 24px; background: #fff; border-bottom: 1px solid #e4e7ed; }
.header-mobile { gap: 8px; padding: 0 8px; }
.hamburger { font-size: 20px; padding: 8px; }
.brand { font-size: 18px; font-weight: 700; white-space: nowrap; }
.brand .sub { font-size: 12px; color: #909399; font-weight: 400; margin-left: 6px; }
.menu { flex: 1; border-bottom: none !important; }
.actions { display: flex; align-items: center; gap: 12px; white-space: nowrap; }
.actions-mobile { flex: 1; justify-content: flex-end; gap: 6px; }
.main-mobile { padding: 8px !important; }
.drawer-brand { font-size: 17px; font-weight: 700; padding: 16px; border-bottom: 1px solid #e4e7ed; }
.up { color: #f56c6c; }
.down { color: #67c23a; }

/* 移动端卡片通用样式 */
.m-card { background: #fff; border: 1px solid #ebeef5; border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; }
.m-card:last-child { margin-bottom: 0; }
.m-card-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.m-card-title { font-size: 14px; font-weight: 700; }
.m-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; }
.m-field .m-label { font-size: 11px; color: #909399; }
.m-field .m-value { font-size: 13px; }
.m-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }

/* 移动端全局收紧 Element Plus 间距 */
@media (max-width: 768px) {
  .el-card { --el-card-padding: 12px; border-radius: 10px; }
  .el-card__header { padding: 10px 12px; font-size: 14px; font-weight: 600; }
  .el-main { padding: 8px !important; }
  h1, h2, h3 { font-size: 16px; }
}
</style>

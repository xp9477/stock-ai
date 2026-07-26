<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="brand">📈 Stock AI <span class="sub">A 股 AI 模拟交易</span></div>
      <el-menu mode="horizontal" :default-active="activePath" router class="menu" :ellipsis="false">
        <el-menu-item index="/">仪表盘</el-menu-item>
        <el-menu-item index="/runs">决策记录</el-menu-item>
        <el-menu-item index="/watchlist">股池管理</el-menu-item>
        <el-menu-item index="/orders">交易记录</el-menu-item>
        <el-menu-item index="/models">模型管理</el-menu-item>
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
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from './api/index.js'

const route = useRoute()
const activePath = computed(() => (route.path.startsWith('/runs') ? '/runs' : route.path))

const status = ref({})
const triggering = ref(false)
let timer = null

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
.brand { font-size: 18px; font-weight: 700; white-space: nowrap; }
.brand .sub { font-size: 12px; color: #909399; font-weight: 400; margin-left: 6px; }
.menu { flex: 1; border-bottom: none !important; }
.actions { display: flex; align-items: center; gap: 12px; white-space: nowrap; }
.up { color: #f56c6c; }
.down { color: #67c23a; }
</style>

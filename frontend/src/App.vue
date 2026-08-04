<template>
  <div class="shell" :class="{ mobile: isMobile }">
    <!-- Desktop sidebar -->
    <aside v-if="!isMobile" class="sidebar">
      <div class="brand-block">
        <img class="brand-mark-img" src="/favicon.png" width="36" height="36" alt="" />
        <div>
          <div class="brand-name">Stock AI</div>
          <div class="brand-tag">多模型赛马台</div>
        </div>
      </div>
      <nav class="nav">
        <router-link
          v-for="item in NAV"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: isActive(item.path) }"
        >
          <span class="nav-ico" aria-hidden="true">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="sidebar-foot">
        <div class="chip" :class="status.fuyao_configured ? 'ok' : 'bad'">
          {{ status.fuyao_configured ? '扶摇已连接' : '扶摇未配置' }}
        </div>
        <div class="chip muted">{{ status.data_primary || '—' }} · {{ status.news_source || '—' }}</div>
      </div>
    </aside>

    <div class="main-col">
      <header class="topbar">
        <template v-if="isMobile">
          <button class="icon-btn" type="button" aria-label="菜单" @click="drawer = true">☰</button>
          <img class="brand-mark-img sm" src="/favicon.png" width="28" height="28" alt="" />
          <div class="brand-name sm">Stock AI</div>
        </template>
        <div v-else class="top-meta">
          <span class="meta-item">{{ status.schedule_times || '调度' }}</span>
          <span v-if="status.next_run" class="meta-item dim">下次 {{ status.next_run }}</span>
        </div>
        <div class="top-actions">
          <span
            v-if="status.running"
            class="live-dot"
            role="status"
            aria-live="polite"
          >决策中</span>
          <el-button
            size="small"
            type="warning"
            plain
            :loading="rebalancing"
            @click="rebalance"
          >规则调仓</el-button>
          <el-button
            size="small"
            type="primary"
            :loading="triggering"
            :disabled="status.running"
            @click="trigger"
          >{{ isMobile ? 'AI 决策' : '立即 AI 决策' }}</el-button>
        </div>
      </header>

      <main class="content">
        <router-view />
      </main>
    </div>

    <el-drawer v-model="drawer" direction="ltr" size="260px" :with-header="false" class="mob-drawer">
      <div class="brand-block drawer-brand">
        <img class="brand-mark-img" src="/favicon.png" width="36" height="36" alt="" />
        <div>
          <div class="brand-name">Stock AI</div>
          <div class="brand-tag">多模型赛马台</div>
        </div>
      </div>
      <nav class="nav">
        <router-link
          v-for="item in NAV"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: isActive(item.path) }"
          @click="drawer = false"
        >
          <span class="nav-ico" aria-hidden="true">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from './api/index.js'
import { useIsMobile } from './composables/useIsMobile.js'

const NAV = [
  { path: '/', label: '战报', icon: '◈' },
  { path: '/strategies', label: '策略', icon: '◎' },
  { path: '/watchlist', label: '股池', icon: '▣' },
  { path: '/runs', label: '决策', icon: '☰' },
  { path: '/orders', label: '成交', icon: '⇄' },
  { path: '/models', label: '参赛账户', icon: '◇' },
  { path: '/settings', label: '设置', icon: '⚙' },
]

const route = useRoute()
const { isMobile } = useIsMobile()
const drawer = ref(false)
const status = ref({})
const triggering = ref(false)
const rebalancing = ref(false)
let timer = null

function isActive(path) {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}

async function refreshStatus() {
  try {
    status.value = await api.getStatus()
  } catch { /* silent */ }
}

async function trigger() {
  triggering.value = true
  try {
    await api.triggerRun()
    ElMessage.success('AI 决策已启动，稍后在「决策」查看')
    await refreshStatus()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    triggering.value = false
  }
}

async function rebalance() {
  rebalancing.value = true
  try {
    const res = await api.rulesRebalance()
    const ok = (res.results || []).filter((r) => r.ok).length
    ElMessage.success(`规则调仓完成（${ok} 个策略）`)
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    rebalancing.value = false
  }
}

onMounted(() => {
  refreshStatus()
  timer = setInterval(refreshStatus, 12000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
  color: var(--text);
}
.sidebar {
  width: var(--sidebar-w);
  flex: none;
  border-right: 1px solid var(--border);
  background: rgba(12, 18, 32, 0.92);
  backdrop-filter: blur(12px);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
}
.brand-block {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 16px 16px;
}
.brand-mark-img {
  width: 36px; height: 36px; border-radius: 10px;
  object-fit: cover;
  box-shadow: 0 0 0 1px var(--border), 0 4px 12px rgba(0,0,0,0.35);
  flex: none;
}
.brand-mark-img.sm { width: 28px; height: 28px; border-radius: 8px; }
.brand-name { font-weight: 700; font-size: 15px; letter-spacing: -0.02em; }
.brand-name.sm { font-size: 15px; font-weight: 700; }
.brand-tag { font-size: 11px; color: var(--text-dim); margin-top: 1px; }
.nav { flex: 1; padding: 8px 0; }
.nav-item {
  display: flex; align-items: center; gap: 10px;
  margin: 3px 10px; padding: 0 12px; height: 40px;
  border-radius: 8px; color: var(--text-muted);
  transition: background 0.15s, color 0.15s;
}
.nav-item:hover { background: var(--panel-2); color: var(--text); }
.nav-item.active { background: var(--accent-dim); color: var(--accent); font-weight: 600; }
.nav-ico { width: 1.1em; opacity: 0.85; font-size: 13px; }
.sidebar-foot { padding: 12px 14px 18px; display: flex; flex-direction: column; gap: 6px; }
.chip {
  font-size: 11px; padding: 4px 8px; border-radius: 6px;
  background: var(--panel-2); color: var(--text-muted); border: 1px solid var(--border);
}
.chip.ok { color: var(--down); border-color: rgba(45, 212, 168, 0.3); }
.chip.bad { color: var(--up); border-color: rgba(255, 90, 95, 0.3); }
.chip.muted { opacity: 0.85; }

.main-col { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.topbar {
  height: var(--header-h);
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border);
  background: rgba(7, 11, 20, 0.75);
  backdrop-filter: blur(10px);
  position: sticky; top: 0; z-index: 20;
}
.top-meta { display: flex; gap: 14px; font-size: 12px; color: var(--text-muted); }
.meta-item.dim { color: var(--text-dim); }
.top-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.live-dot {
  font-size: 12px; font-weight: 600; color: var(--warn);
  display: flex; align-items: center; gap: 6px;
}
.live-dot::before {
  content: ''; width: 7px; height: 7px; border-radius: 50%;
  background: var(--warn); box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.25);
  animation: pulse 1.4s ease infinite;
}
@keyframes pulse {
  50% { opacity: 0.5; }
}
.icon-btn {
  background: transparent; border: 1px solid var(--border); color: var(--text);
  width: 36px; height: 36px; border-radius: 8px; font-size: 16px;
}
.content { padding: 18px 20px 40px; flex: 1; }

.shell.mobile .topbar { padding: 0 10px; }
.shell.mobile .content { padding: 12px 10px 32px; }
.drawer-brand { border-bottom: 1px solid var(--border); margin-bottom: 8px; }

@media (max-width: 768px) {
  .top-actions .el-button + .el-button { margin-left: 0; }
}
</style>

<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">股池</h1>
        <p class="page-sub">
          共享交易宇宙 ·
          <span class="mono" :class="items.length >= poolHint ? 'warn-text' : ''">
            {{ items.length }}/{{ poolHint }}
          </span>
          · AI 与规则只交易池内标的
        </p>
      </div>
    </div>

    <section class="panel">
      <div class="add-row">
        <el-input
          v-model="newCode"
          placeholder="6 位代码，如 600519"
          maxlength="6"
          class="code-input"
          :disabled="items.length >= poolHint"
          @keyup.enter="add"
        />
        <el-button
          type="primary"
          :loading="adding"
          :disabled="items.length >= poolHint"
          @click="add"
        >添加</el-button>
        <el-button type="warning" :loading="selecting" @click="autoSelect">AI 选股</el-button>
      </div>
      <p class="hint">
        AI 选股约 1–2 分钟；手动添加的票不会被自动淘汰。
        <span v-if="items.length >= poolHint" class="warn-text">
          股池已满，请先移除后再添加，或到「设置 → 选股」提高上限。
        </span>
      </p>
    </section>

    <section class="panel">
      <div class="panel-h">
        <div class="panel-title">
          当前 {{ items.length }} 只
          <span v-if="lastRefresh" class="refresh-hint mono">· {{ lastRefresh }} 自动刷新</span>
        </div>
        <el-button size="small" text type="primary" :loading="loading" @click="load">刷新</el-button>
      </div>

      <div v-if="isMobile">
        <div v-for="row in items" :key="row.code" class="m-card">
          <div style="display:flex;align-items:flex-start;gap:8px">
            <div style="flex:1">
              <div class="m-card-title">
                {{ row.name }}
                <el-tag size="small" :type="row.source === 'auto' ? 'warning' : 'info'">
                  {{ row.source === 'auto' ? 'AI' : '手动' }}
                </el-tag>
              </div>
              <div class="dim mono" style="font-size:12px">{{ row.code }}</div>
            </div>
            <div style="text-align:right">
              <div class="mono" style="font-weight:600">{{ row.price ?? '—' }}</div>
              <div class="mono" style="font-size:12px" :class="row.pct_change >= 0 ? 'up' : 'down'">
                {{ row.pct_change != null ? row.pct_change.toFixed(2) + '%' : '—' }}
              </div>
            </div>
            <el-button size="small" type="danger" plain @click="remove(row.code)">移除</el-button>
          </div>
          <div v-if="row.select_reason" class="reason">{{ row.select_reason }}</div>
        </div>
        <el-empty v-if="!loading && !items.length" description="股池为空 — 添加或 AI 选股" />
      </div>

      <el-table v-else v-loading="loading" :data="items" stripe empty-text="股池为空">
        <el-table-column prop="code" label="代码" width="100" />
        <el-table-column prop="name" label="名称" width="120" />
        <el-table-column label="来源" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source === 'auto' ? 'warning' : 'info'">
              {{ row.source === 'auto' ? 'AI' : '手动' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="现价" width="100">
          <template #default="{ row }"><span class="mono">{{ row.price ?? '—' }}</span></template>
        </el-table-column>
        <el-table-column label="涨跌" width="100">
          <template #default="{ row }">
            <span v-if="row.pct_change != null" class="mono" :class="row.pct_change >= 0 ? 'up' : 'down'">
              {{ row.pct_change.toFixed(2) }}%
            </span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="select_reason" label="选入理由" show-overflow-tooltip />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button size="small" type="danger" plain @click="remove(row.code)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'

const { isMobile } = useIsMobile()
const items = ref([])
const newCode = ref('')
const loading = ref(false)
const adding = ref(false)
const selecting = ref(false)
const poolHint = ref(30)
const lastRefresh = ref('')
let timer = null
let selectTimer = null

function stampRefresh() {
  const d = new Date()
  lastRefresh.value = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    items.value = await api.getWatchlist()
    const st = await api.getStatus()
    if (st.pool_max) poolHint.value = st.pool_max
    stampRefresh()
  } catch (err) {
    if (!silent) ElMessage.error(err.message)
  } finally {
    if (!silent) loading.value = false
  }
}

async function add() {
  if (!/^\d{6}$/.test(newCode.value)) {
    ElMessage.warning('请输入 6 位数字代码')
    return
  }
  if (items.value.length >= poolHint.value) {
    ElMessage.warning(`股池已满（${items.value.length}/${poolHint.value}）`)
    return
  }
  adding.value = true
  try {
    await api.addWatchlist(newCode.value)
    ElMessage.success('已添加')
    newCode.value = ''
    load()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    adding.value = false
  }
}

async function autoSelect() {
  selecting.value = true
  try {
    await api.autoSelect()
    ElMessage.success('AI 选股已启动，完成后自动刷新列表')
    if (selectTimer) clearInterval(selectTimer)
    let ticks = 0
    let wasSelecting = true
    selectTimer = setInterval(async () => {
      ticks += 1
      try {
        const st = await api.getStatus()
        if (st.selecting) {
          wasSelecting = true
          await load(true)
        } else if (wasSelecting) {
          // 选股刚结束
          clearInterval(selectTimer)
          selectTimer = null
          selecting.value = false
          await load()
          ElMessage.success('AI 选股已完成，列表已更新')
        }
      } catch { /* silent poll */ }
      if (ticks >= 40) {
        // ~3 分钟兜底
        clearInterval(selectTimer)
        selectTimer = null
        selecting.value = false
        load()
      }
    }, 4500)
  } catch (err) {
    ElMessage.error(err.message)
    selecting.value = false
  }
}

async function remove(code) {
  try {
    await api.removeWatchlist(code)
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

onMounted(async () => {
  await load()
  // 若进入页面时选股已在跑，接上轮询
  try {
    const st = await api.getStatus()
    if (st.selecting) {
      selecting.value = true
      autoSelectFollow()
    }
  } catch { /* ignore */ }
  timer = setInterval(() => load(true), 60_000)
})

function autoSelectFollow() {
  if (selectTimer) clearInterval(selectTimer)
  let ticks = 0
  selectTimer = setInterval(async () => {
    ticks += 1
    try {
      const st = await api.getStatus()
      if (!st.selecting) {
        clearInterval(selectTimer)
        selectTimer = null
        selecting.value = false
        await load()
        ElMessage.success('AI 选股已完成')
      } else {
        await load(true)
      }
    } catch { /* ignore */ }
    if (ticks >= 40) {
      clearInterval(selectTimer)
      selectTimer = null
      selecting.value = false
    }
  }, 4500)
}

onUnmounted(() => {
  clearInterval(timer)
  if (selectTimer) clearInterval(selectTimer)
})
</script>

<style scoped>
.add-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.code-input { width: 200px; max-width: 100%; }
.hint { margin: 10px 0 0; font-size: 12px; color: var(--text-dim); }
.warn-text { color: var(--warn, #fbbf24); }
.reason { margin-top: 8px; font-size: 12px; color: var(--text-muted); line-height: 1.5; }
.refresh-hint { font-size: 11px; font-weight: 400; color: var(--text-dim); margin-left: 6px; }
@media (max-width: 600px) {
  .code-input { width: 100%; }
  .add-row .el-button { flex: 1; }
}
</style>

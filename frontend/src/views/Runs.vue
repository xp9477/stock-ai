<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">决策</h1>
        <p class="page-sub">AI 流水线与选股记录 · 默认突出买卖，观望折叠</p>
      </div>
    </div>
  <el-card>
    <template #header>运行列表</template>

    <template v-if="isMobile">
      <div v-for="row in runs" :key="row.id" class="m-card clickable-card" @click="goDetail(row)">
        <div class="m-card-head">
          <span class="m-card-title">#{{ row.id }}</span>
          <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
          <el-tag size="small" :type="triggerTagType(row.trigger)">
            {{ triggerText(row.trigger) }}
          </el-tag>
        </div>
        <div class="time-line">{{ (row.started_at || '').slice(5, 16) }} → {{ row.finished_at ? row.finished_at.slice(11, 16) : '-' }}</div>
        <div v-if="row.error" class="err-line">{{ row.error }}</div>
        <div class="summary-line" @click.stop>
          <template v-if="summaryLine(row)">
            <span class="summary-idle">{{ summaryLine(row) }}</span>
          </template>
          <template v-if="tradeItems(row).length">
            <el-tag v-for="(item, i) in tradeItems(row)" :key="'t'+i" size="small" class="mr"
              :type="actionType(item.action)" effect="dark">
              {{ item.model ? item.model + '·' : '' }}{{ item.name }} {{ actionText(item) }}
            </el-tag>
          </template>
          <span v-else-if="!summaryLine(row) && holdItems(row).length" class="summary-idle">本轮无买卖</span>
          <button
            v-if="realHoldItems(row).length"
            type="button"
            class="hold-toggle"
            :class="{ open: expandedHolds[row.id] }"
            @click="toggleHolds(row.id)"
          >
            {{ expandedHolds[row.id] ? '收起' : '另有' }}
            {{ realHoldItems(row).length }} 条观望
            <span class="chev">{{ expandedHolds[row.id] ? '▴' : '▾' }}</span>
          </button>
          <template v-if="expandedHolds[row.id]">
            <el-tag v-for="(item, i) in realHoldItems(row)" :key="'h'+i" size="small" class="mr hold-tag"
              type="info" effect="plain">
              {{ item.model }}·{{ item.name }} {{ actionText(item) }}
            </el-tag>
          </template>
        </div>
      </div>
      <el-empty v-if="!loading && !runs.length" description="暂无决策记录，点右上角「AI 决策」" />
    </template>

    <template v-else>
      <el-table :data="runs" stripe v-loading="loading" @row-click="goDetail" class="clickable">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="started_at" label="开始时间" width="170" />
        <el-table-column prop="finished_at" label="结束时间" width="170" />
        <el-table-column label="触发" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="triggerTagType(row.trigger)">
              {{ triggerText(row.trigger) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="决策摘要">
          <template #default="{ row }">
            <div class="summary-line" @click.stop>
              <div v-if="row.error" class="err-line">{{ row.error }}</div>
              <template v-if="summaryLine(row)">
                <span class="summary-idle">{{ summaryLine(row) }}</span>
              </template>
              <template v-if="tradeItems(row).length">
                <el-tag v-for="(item, i) in tradeItems(row)" :key="'t'+i" size="small" class="mr"
                  :type="actionType(item.action)" effect="dark">
                  {{ item.model ? item.model + '·' : '' }}{{ item.name }} {{ actionText(item) }}
                </el-tag>
              </template>
              <span v-else-if="!summaryLine(row) && holdItems(row).length" class="summary-idle">本轮无买卖</span>
              <button
                v-if="realHoldItems(row).length"
                type="button"
                class="hold-toggle"
                :class="{ open: expandedHolds[row.id] }"
                @click="toggleHolds(row.id)"
              >
                {{ expandedHolds[row.id] ? '收起' : '另有' }}
                {{ realHoldItems(row).length }} 条观望
                <span class="chev">{{ expandedHolds[row.id] ? '▴' : '▾' }}</span>
              </button>
              <template v-if="expandedHolds[row.id]">
                <el-tag v-for="(item, i) in realHoldItems(row)" :key="'h'+i" size="small" class="mr hold-tag"
                  type="info" effect="plain">
                  {{ item.model }}·{{ item.name }} {{ actionText(item) }}
                </el-tag>
              </template>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !runs.length" description="暂无决策记录，点右上角「AI 决策」" />
    </template>
  </el-card>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'

const { isMobile } = useIsMobile()
const router = useRouter()
const runs = ref([])
const loading = ref(false)
const expandedHolds = reactive({})
let timer = null

async function load() {
  try {
    runs.value = await api.getRuns()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function goDetail(row) {
  router.push(`/runs/${row.id}`)
}

function tradeItems(row) {
  return (row.summary || []).filter((d) => d.action === 'buy' || d.action === 'sell')
}

function holdItems(row) {
  return (row.summary || []).filter((d) => d.action === 'hold')
}

/** 过滤列表占位 hold（无 code 的汇总行） */
function realHoldItems(row) {
  return holdItems(row).filter((d) => d.code)
}

function summaryLine(row) {
  const r = row.result
  if (row.trigger === 'selector' && r?.kind === 'selector') {
    const a = (r.added || []).length
    const rm = (r.removed || []).length
    if (a || rm) return `选股 · 入池 ${a} · 移出 ${rm} · 池内 ${r.pool_size ?? '—'}`
    return `选股完成 · 池内 ${r.pool_size ?? '—'} · 无变动`
  }
  if (r?.kind === 'pipeline' && r.trade_n === 0 && row.status === 'done') {
    return `决策完成 · ${r.hold || 0} 条观望 · 无买卖`
  }
  if (r?.kind === 'pipeline' && row.status === 'done') {
    return `买 ${r.buy || 0} · 卖 ${r.sell || 0} · 观望 ${r.hold || 0}`
  }
  return ''
}

function toggleHolds(id) {
  expandedHolds[id] = !expandedHolds[id]
}

const triggerTagType = (t) => ({ manual: 'primary', selector: 'warning' }[t] || 'info')
const triggerText = (t) => ({ manual: '手动', schedule: '定时', selector: 'AI 选股' }[t] || t)
const statusType = (s) => ({ running: 'warning', done: 'success', failed: 'danger', cancelled: 'info' }[s] || 'info')
const statusText = (s) => ({ running: '运行中', done: '完成', failed: '失败', cancelled: '已取消' }[s] || s)
const actionType = (a) => ({ buy: 'danger', sell: 'success', hold: 'info' }[a] || 'info')

/** hold：目标仓位>0 视为继续持有；选股用 label */
function actionText(item) {
  if (!item) return ''
  if (item.label) return item.label
  if (item.action === 'buy') return '买入'
  if (item.action === 'sell') return '卖出'
  if (item.action === 'hold') {
    return (item.target_position_pct || 0) > 0.001 ? '继续持有' : '观望'
  }
  return item.action
}

onMounted(() => {
  loading.value = true
  load().finally(() => (loading.value = false))
  timer = setInterval(load, 15000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.clickable :deep(.el-table__row) { cursor: pointer; }
.clickable-card { cursor: pointer; }
.mr { margin-right: 4px; margin-bottom: 4px; }
.time-line { font-size: 11px; color: #909399; margin-bottom: 6px; }
.summary-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0 2px;
}
.summary-idle {
  font-size: 12px;
  color: var(--text-dim, #5c6b82);
  margin-right: 8px;
}
.hold-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin: 0 4px 4px 0;
  padding: 2px 8px;
  border: 1px dashed var(--border, #1e2d42);
  border-radius: 999px;
  background: transparent;
  color: var(--text-dim, #5c6b82);
  font-size: 11px;
  line-height: 1.4;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.hold-toggle:hover,
.hold-toggle.open {
  color: var(--text-muted, #8b9bb3);
  border-color: var(--border-strong, #2a3d56);
  background: rgba(255, 255, 255, 0.03);
}
.hold-toggle .chev { font-size: 10px; opacity: 0.8; }
.hold-tag {
  opacity: 0.72;
  font-weight: 400;
}
.err-line {
  width: 100%;
  font-size: 12px;
  color: var(--up, #ff5a5f);
  margin-bottom: 4px;
  line-height: 1.4;
}
.clickable-card .m-card-title { font-size: 15px; }
</style>

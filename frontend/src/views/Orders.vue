<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">成交</h1>
        <p class="page-sub">模拟撮合订单与盘中事件 · 可按账户筛选</p>
      </div>
      <div class="head-actions">
        <el-select
          v-model="filterModel"
          size="small"
          clearable
          placeholder="全部账户"
          style="width: 180px"
          @change="loadOrders"
        >
          <el-option
            v-for="m in models"
            :key="m.id"
            :label="m.name"
            :value="m.id"
          />
        </el-select>
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </div>
    <el-card>
      <template #header>
        <div class="card-h">
          <span>交易记录</span>
          <span class="dim mono" style="font-size:12px">最近 {{ orders.length }} 条</span>
        </div>
      </template>

      <template v-if="isMobile">
        <div v-for="row in orders" :key="row.id" class="o-row">
          <div class="o-left">
            <div class="o-name">
              {{ row.name }}
              <el-tag :type="row.side === 'buy' ? 'danger' : 'success'" size="small">
                {{ row.side === 'buy' ? '买入' : '卖出' }}
              </el-tag>
              <el-tag v-if="row.status !== 'filled'" type="info" size="small">拒绝</el-tag>
            </div>
            <div class="o-meta">{{ (row.created_at || '').slice(5, 16) }} · {{ row.model }}</div>
            <div v-if="row.reject_reason" class="reject">{{ row.reject_reason }}</div>
          </div>
          <div class="o-right" v-if="row.status === 'filled'">
            <div class="o-amount">{{ row.amount }}</div>
            <div class="o-detail">{{ row.price }} × {{ row.qty }}</div>
          </div>
        </div>
        <el-empty v-if="!loading && !orders.length" description="暂无交易记录" />
      </template>

      <template v-else>
        <el-table :data="orders" stripe v-loading="loading">
          <el-table-column prop="created_at" label="时间" width="165" />
          <el-table-column prop="model" label="账户" width="120" />
          <el-table-column prop="code" label="代码" width="85" />
          <el-table-column prop="name" label="名称" width="100" />
          <el-table-column label="方向" width="70">
            <template #default="{ row }">
              <el-tag :type="row.side === 'buy' ? 'danger' : 'success'" size="small">
                {{ row.side === 'buy' ? '买入' : '卖出' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="price" label="价格" width="90" />
          <el-table-column prop="qty" label="数量" width="90" />
          <el-table-column prop="amount" label="金额" width="110" />
          <el-table-column prop="fee" label="费用" width="80" />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.status === 'filled' ? 'success' : 'info'" size="small">
                {{ row.status === 'filled' ? '成交' : '拒绝' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reject_reason" label="备注" show-overflow-tooltip />
        </el-table>
        <el-empty v-if="!loading && !orders.length" description="暂无交易记录" />
      </template>
    </el-card>

    <el-card class="mt">
      <template #header>盘中监控事件 (止盈/止损复审)</template>

      <template v-if="isMobile">
        <div v-for="row in events" :key="row.id" class="m-card ev-card"
          :class="{ 'deep-loss-card': row.trigger === 'deep_loss' }">
          <div class="m-card-head">
            <span class="m-card-title">{{ row.name }}</span>
            <el-tag :type="triggerType(row.trigger)" size="small" :effect="row.trigger === 'deep_loss' ? 'dark' : 'light'">
              {{ triggerText(row.trigger) }}
            </el-tag>
            <span :class="(row.pnl_pct ?? 0) >= 0 ? 'up' : 'down'" class="pnl">
              {{ row.pnl_pct != null ? row.pnl_pct.toFixed(2) + '%' : '—' }}
            </span>
            <el-tag :type="row.action === 'review_sell' ? 'success' : 'info'" size="small" class="ev-action">
              {{ actionText(row.action) }}
            </el-tag>
          </div>
          <div class="o-meta">{{ row.created_at.slice(5, 16) }} · {{ row.model }}</div>
          <el-collapse v-if="row.detail" class="detail-collapse">
            <el-collapse-item title="AI 推理">
              <pre class="detail">{{ row.detail }}</pre>
            </el-collapse-item>
          </el-collapse>
        </div>
        <el-empty v-if="!events.length" description="暂无监控事件" :image-size="60" />
      </template>

      <template v-else>
        <el-table :data="events" stripe :row-class-name="rowClass">
          <el-table-column prop="created_at" label="时间" width="165" />
          <el-table-column prop="model" label="模型" width="110" />
          <el-table-column prop="name" label="股票" width="100" />
          <el-table-column label="触发" width="110">
            <template #default="{ row }">
              <el-tag :type="triggerType(row.trigger)" size="small" :effect="row.trigger === 'deep_loss' ? 'dark' : 'light'">
                {{ triggerText(row.trigger) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="盈亏" width="90">
            <template #default="{ row }">
              <span :class="(row.pnl_pct ?? 0) >= 0 ? 'up' : 'down'">
                {{ row.pnl_pct != null ? row.pnl_pct.toFixed(2) + '%' : '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="复审结果" width="110">
            <template #default="{ row }">
              <el-tag :type="row.action === 'review_sell' ? 'success' : 'info'" size="small">
                {{ actionText(row.action) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="AI 推理">
            <template #default="{ row }">
              <el-popover width="480" trigger="click">
                <template #reference>
                  <el-button size="small" link type="primary">查看推理</el-button>
                </template>
                <pre class="detail">{{ row.detail }}</pre>
              </el-popover>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!events.length" description="暂无监控事件" :image-size="60" />
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'

const { isMobile } = useIsMobile()
const orders = ref([])
const events = ref([])
const models = ref([])
const filterModel = ref(null)
const loading = ref(false)

const triggerType = (t) => ({ stop_loss: 'warning', take_profit: 'success', deep_loss: 'danger' }[t] || 'info')
const triggerText = (t) => ({ stop_loss: '止损警戒', take_profit: '止盈警戒', deep_loss: '深度亏损' }[t] || t)
const actionText = (a) => ({
  review_sell: '复审卖出',
  review_hold: '继续持有',
  alert: '仅告警',
  force_sell: '强制砍仓',
}[a] || a)
const rowClass = ({ row }) => (row.trigger === 'deep_loss' ? 'deep-loss-row' : '')

async function loadOrders() {
  orders.value = await api.getOrders(filterModel.value ?? undefined)
}

async function reload() {
  loading.value = true
  try {
    const [ord, ev, ms] = await Promise.all([
      api.getOrders(filterModel.value ?? undefined),
      api.getMonitorEvents(),
      api.getModels().catch(() => []),
    ])
    orders.value = ord
    events.value = Array.isArray(ev) ? ev : []
    models.value = ms
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.card-h { display: flex; justify-content: space-between; align-items: center; gap: 8px; width: 100%; }
.mt { margin-top: 12px; }
.detail {
  white-space: pre-wrap; font-size: 12px; max-height: 320px; overflow-y: auto;
  margin: 0; color: var(--text-muted); font-family: var(--mono);
}
:deep(.deep-loss-row) { background: rgba(255, 90, 95, 0.08) !important; }
.deep-loss-card {
  border-color: rgba(255, 90, 95, 0.35);
  background: rgba(255, 90, 95, 0.06);
}

.o-row {
  display: flex; align-items: center; gap: 8px; padding: 10px 4px;
  border-bottom: 1px solid var(--border);
}
.o-row:last-child { border-bottom: none; }
.o-left { flex: 1; min-width: 0; }
.o-name { font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.o-meta { font-size: 11px; color: var(--text-dim); margin-top: 2px; }
.o-right { text-align: right; flex: none; }
.o-amount { font-size: 14px; font-weight: 700; font-family: var(--mono); }
.o-detail { font-size: 11px; color: var(--text-dim); font-family: var(--mono); }
.reject { font-size: 12px; color: var(--warn); margin-top: 2px; }
.pnl { font-weight: 700; font-size: 14px; font-family: var(--mono); }
.ev-action { margin-left: auto; }
.detail-collapse { margin-top: 4px; }
.detail-collapse :deep(.el-collapse-item__header) {
  height: 30px; font-size: 12px; color: var(--accent); background: transparent;
}
.detail-collapse :deep(.el-collapse-item__wrap) { background: transparent; }
</style>

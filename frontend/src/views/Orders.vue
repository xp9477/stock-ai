<template>
  <div>
    <el-card>
      <template #header>交易记录</template>
      <el-table :data="orders" stripe v-loading="loading">
        <el-table-column prop="created_at" label="时间" width="165" />
        <el-table-column prop="model" label="模型" width="110" />
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
    </el-card>

    <el-card class="mt">
      <template #header>盘中监控事件 (止盈/止损复审)</template>
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
            <span :class="row.pnl_pct >= 0 ? 'up' : 'down'">{{ row.pnl_pct.toFixed(2) }}%</span>
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
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

const orders = ref([])
const events = ref([])
const loading = ref(false)

const triggerType = (t) => ({ stop_loss: 'warning', take_profit: 'success', deep_loss: 'danger' }[t] || 'info')
const triggerText = (t) => ({ stop_loss: '止损警戒', take_profit: '止盈警戒', deep_loss: '深度亏损' }[t] || t)
const actionText = (a) => ({ review_sell: '复审卖出', review_hold: '继续持有', alert: '仅告警' }[a] || a)
const rowClass = ({ row }) => (row.trigger === 'deep_loss' ? 'deep-loss-row' : '')

onMounted(async () => {
  loading.value = true
  try {
    orders.value = await api.getOrders()
    events.value = await api.getMonitorEvents()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.mt { margin-top: 16px; }
.detail { white-space: pre-wrap; font-size: 12px; max-height: 320px; overflow-y: auto; margin: 0; }
:deep(.deep-loss-row) { background: #fef0f0 !important; }
</style>

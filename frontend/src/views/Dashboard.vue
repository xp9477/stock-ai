<template>
  <div>
    <el-card>
      <template #header>
        <div class="card-header">
          <span>模型排行榜</span>
          <el-popconfirm title="确认重置所有模型账户?全部持仓与记录将清空" @confirm="reset">
            <template #reference><el-button size="small" type="danger" plain>重置全部账户</el-button></template>
          </el-popconfirm>
        </div>
      </template>
      <el-table :data="leaderboard" stripe>
        <el-table-column label="#" type="index" width="50" />
        <el-table-column prop="name" label="模型" width="140">
          <template #default="{ row }">
            {{ row.name }}
            <el-tag v-if="row.type === 'ensemble'" size="small" type="warning">合议</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="total_equity" label="总资产" width="130" />
        <el-table-column label="收益率" width="110">
          <template #default="{ row }">
            <span :class="row.pnl_pct >= 0 ? 'up' : 'down'">{{ row.pnl_pct.toFixed(2) }}%</span>
          </template>
        </el-table-column>
        <el-table-column label="盈亏" width="120">
          <template #default="{ row }">
            <span :class="row.pnl >= 0 ? 'up' : 'down'">{{ row.pnl.toFixed(2) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="max_drawdown_pct" label="最大回撤%" width="110" />
        <el-table-column prop="position_count" label="持仓数" />
      </el-table>
    </el-card>

    <el-card class="mt">
      <template #header>收益曲线对比 (vs 沪深300)</template>
      <div ref="chartEl" style="height: 360px" />
      <el-empty v-if="!hasCurve" description="暂无数据,运行一轮决策后生成资产快照" :image-size="60" />
    </el-card>

    <el-card class="mt">
      <template #header>
        <div class="card-header">
          <span>持仓明细</span>
          <el-radio-group v-model="activeModel" size="small" @change="loadPortfolio">
            <el-radio-button v-for="m in models" :key="m.id" :value="m.id">{{ m.name }}</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <el-descriptions v-if="portfolio.total_equity != null" :column="4" class="mb" border size="small">
        <el-descriptions-item label="总资产">{{ portfolio.total_equity }}</el-descriptions-item>
        <el-descriptions-item label="可用资金">{{ portfolio.cash }}</el-descriptions-item>
        <el-descriptions-item label="累计盈亏">
          <span :class="portfolio.total_pnl >= 0 ? 'up' : 'down'">{{ portfolio.total_pnl }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="收益率">
          <span :class="portfolio.total_pnl_pct >= 0 ? 'up' : 'down'">{{ portfolio.total_pnl_pct }}%</span>
        </el-descriptions-item>
      </el-descriptions>
      <el-table :data="portfolio.positions || []" stripe>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column prop="name" label="名称" width="110" />
        <el-table-column prop="total_qty" label="持仓" width="80" />
        <el-table-column prop="available_qty" label="可卖" width="80" />
        <el-table-column prop="avg_cost" label="成本" width="90" />
        <el-table-column prop="price" label="现价" width="90" />
        <el-table-column label="今日涨跌" width="95">
          <template #default="{ row }">
            <span :class="row.pct_change >= 0 ? 'up' : 'down'">
              {{ row.pct_change != null ? row.pct_change.toFixed(2) + '%' : '-' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="market_value" label="市值" width="110" />
        <el-table-column label="浮动盈亏" width="150">
          <template #default="{ row }">
            <span :class="row.pnl >= 0 ? 'up' : 'down'">
              {{ row.pnl.toFixed(2) }} ({{ row.pnl_pct.toFixed(2) }}%)
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="buy_reason" label="买入理由" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

const leaderboard = ref([])
const models = ref([])
const activeModel = ref(null)
const portfolio = ref({})
const chartEl = ref(null)
const hasCurve = ref(false)
let chart = null
let timer = null

async function loadPortfolio() {
  if (activeModel.value == null) return
  try {
    portfolio.value = await api.getPortfolio(activeModel.value)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function load() {
  try {
    leaderboard.value = await api.getLeaderboard()
    models.value = leaderboard.value
    if (activeModel.value == null && models.value.length) {
      activeModel.value = models.value[0].id
    }
    await loadPortfolio()
    const curve = await api.getEquityCurve()
    hasCurve.value = curve.series.length > 0
    if (hasCurve.value && chart) {
      const series = curve.series.map((s) => ({
        name: s.name, type: 'line', smooth: true, showSymbol: false,
        data: s.points.map((p) => [p.time, p.pct]),
        lineStyle: { width: s.type === 'ensemble' ? 3 : 2 },
      }))
      series.push({
        name: '沪深300', type: 'line', smooth: true, showSymbol: false,
        data: curve.hs300.map((p) => [p.time, p.pct]),
        lineStyle: { width: 1, type: 'dashed', color: '#909399' },
        itemStyle: { color: '#909399' },
      })
      chart.setOption({
        tooltip: { trigger: 'axis', valueFormatter: (v) => (typeof v === 'number' ? v.toFixed(2) + '%' : v) },
        legend: { top: 0 },
        grid: { left: 50, right: 20, top: 40, bottom: 30 },
        xAxis: { type: 'time' },
        yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
        series,
      }, { notMerge: true })
    }
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function reset() {
  try {
    await api.resetAccount()
    ElMessage.success('全部账户已重置')
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

onMounted(() => {
  chart = echarts.init(chartEl.value)
  load()
  timer = setInterval(load, 30000)
})
onUnmounted(() => {
  clearInterval(timer)
  chart?.dispose()
})
</script>

<style scoped>
.mt { margin-top: 16px; }
.mb { margin-bottom: 12px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>

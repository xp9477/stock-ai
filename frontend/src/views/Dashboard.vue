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

      <template v-if="isMobile">
        <div v-for="(row, i) in leaderboard" :key="row.id" class="lb-row">
          <span class="rank" :class="'rank-' + (i + 1)">{{ i + 1 }}</span>
          <div class="lb-main">
            <div class="lb-name">
              {{ row.name }}
              <el-tag v-if="row.type === 'ensemble'" size="small" type="warning" class="lb-tag">合议</el-tag>
            </div>
            <div class="lb-sub">
              {{ row.total_equity }} · 持仓 {{ row.position_count }} · 回撤 {{ row.max_drawdown_pct }}%
            </div>
          </div>
          <div class="lb-right">
            <div class="lb-pct" :class="row.pnl >= 0 ? 'up' : 'down'">
              {{ row.pnl >= 0 ? '+' : '' }}{{ row.pnl_pct.toFixed(2) }}%
            </div>
            <div class="lb-pnl" :class="row.pnl >= 0 ? 'up' : 'down'">{{ row.pnl.toFixed(0) }}</div>
          </div>
        </div>
      </template>

      <el-table v-else :data="leaderboard" stripe>
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
      <div ref="chartEl" :style="{ height: isMobile ? '260px' : '360px' }" />
      <el-empty v-if="!hasCurve" description="暂无数据,运行一轮决策后生成资产快照" :image-size="60" />
    </el-card>

    <el-card class="mt">
      <template #header>
        <div class="card-header">
          <span>持仓明细</span>
          <el-select v-if="isMobile" v-model="activeModel" size="small" style="width: 130px" @change="loadPortfolio">
            <el-option v-for="m in models" :key="m.id" :value="m.id" :label="m.name" />
          </el-select>
          <el-radio-group v-else v-model="activeModel" size="small" @change="loadPortfolio">
            <el-radio-button v-for="m in models" :key="m.id" :value="m.id">{{ m.name }}</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <el-descriptions v-if="portfolio.total_equity != null" :column="isMobile ? 2 : 4" class="mb" border size="small">
        <el-descriptions-item label="总资产">{{ portfolio.total_equity }}</el-descriptions-item>
        <el-descriptions-item label="可用资金">{{ portfolio.cash }}</el-descriptions-item>
        <el-descriptions-item label="累计盈亏">
          <span :class="portfolio.total_pnl >= 0 ? 'up' : 'down'">{{ portfolio.total_pnl }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="收益率">
          <span :class="portfolio.total_pnl_pct >= 0 ? 'up' : 'down'">{{ portfolio.total_pnl_pct }}%</span>
        </el-descriptions-item>
      </el-descriptions>

      <template v-if="isMobile">
        <div v-for="row in portfolio.positions || []" :key="row.code" class="m-card">
          <div class="m-card-head">
            <span class="m-card-title">{{ row.name }}</span>
            <span class="code">{{ row.code }}</span>
            <span :class="row.pct_change >= 0 ? 'up' : 'down'" class="pct">
              {{ row.pct_change != null ? row.pct_change.toFixed(2) + '%' : '-' }}
            </span>
          </div>
          <div class="m-grid">
            <div class="m-field"><div class="m-label">持仓 / 可卖</div>
              <div class="m-value">{{ row.total_qty }} / {{ row.available_qty }}</div>
            </div>
            <div class="m-field"><div class="m-label">成本 / 现价</div>
              <div class="m-value">{{ row.avg_cost }} / {{ row.price }}</div>
            </div>
            <div class="m-field"><div class="m-label">市值</div><div class="m-value">{{ row.market_value }}</div></div>
            <div class="m-field"><div class="m-label">浮动盈亏</div>
              <div class="m-value" :class="row.pnl >= 0 ? 'up' : 'down'">
                {{ row.pnl.toFixed(2) }} ({{ row.pnl_pct.toFixed(2) }}%)
              </div>
            </div>
          </div>
          <el-collapse v-if="row.buy_reason" class="reason-collapse">
            <el-collapse-item title="买入理由">
              <div class="reason-text">{{ row.buy_reason }}</div>
            </el-collapse-item>
          </el-collapse>
        </div>
        <el-empty v-if="!(portfolio.positions || []).length" description="暂无持仓" :image-size="60" />
      </template>

      <el-table v-else :data="portfolio.positions || []" stripe>
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
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'

const { isMobile } = useIsMobile()
const leaderboard = ref([])
const models = ref([])
const activeModel = ref(null)
const portfolio = ref({})
const chartEl = ref(null)
const hasCurve = ref(false)
let chart = null
let timer = null
let lastCurve = null

async function loadPortfolio() {
  if (activeModel.value == null) return
  try {
    portfolio.value = await api.getPortfolio(activeModel.value)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function renderChart() {
  if (!lastCurve || !chart) return
  const curve = lastCurve
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
  const mobile = isMobile.value
  chart.setOption({
    tooltip: { trigger: 'axis', valueFormatter: (v) => (typeof v === 'number' ? v.toFixed(2) + '%' : v) },
    legend: mobile ? { bottom: 0, type: 'scroll', orient: 'horizontal' } : { top: 0 },
    grid: mobile
      ? { left: 6, right: 8, top: 15, bottom: 42, containLabel: true }
      : { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
    series,
  }, { notMerge: true })
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
    if (hasCurve.value) {
      lastCurve = curve
      renderChart()
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

const onResize = () => chart?.resize()

watch(isMobile, () => {
  setTimeout(() => {
    chart?.resize()
    renderChart()
  }, 50)
})

onMounted(() => {
  chart = echarts.init(chartEl.value)
  window.addEventListener('resize', onResize)
  load()
  timer = setInterval(load, 30000)
})
onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('resize', onResize)
  chart?.dispose()
})
</script>

<style scoped>
.mt { margin-top: 12px; }
.mb { margin-bottom: 12px; }
.card-header { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.code { color: #909399; font-size: 13px; }
.pct { margin-left: auto; font-weight: 600; }
.reason-collapse { margin-top: 8px; border-top: none; }
.reason-collapse :deep(.el-collapse-item__header) { height: 32px; font-size: 12px; color: #909399; }
.reason-text { font-size: 13px; color: #606266; line-height: 1.6; }

/* 移动端排行榜:紧凑行 */
.lb-row { display: flex; align-items: center; gap: 10px; padding: 9px 2px; border-bottom: 1px solid #f2f3f5; }
.lb-row:last-child { border-bottom: none; }
.rank { width: 22px; height: 22px; border-radius: 6px; background: #f2f3f5; color: #909399;
  font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex: none; }
.rank-1 { background: #fdf6ec; color: #e6a23c; }
.rank-2 { background: #f0f2f5; color: #606266; }
.rank-3 { background: #fef0f0; color: #c45656; }
.lb-main { flex: 1; min-width: 0; }
.lb-name { font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 4px; }
.lb-tag { transform: scale(0.85); transform-origin: left center; }
.lb-sub { font-size: 11px; color: #909399; margin-top: 2px; }
.lb-right { text-align: right; flex: none; }
.lb-pct { font-size: 15px; font-weight: 700; }
.lb-pnl { font-size: 11px; }
</style>

<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card><el-statistic title="总资产 (元)" :value="portfolio.total_equity || 0" :precision="2" /></el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="累计盈亏 (元)" :value="portfolio.total_pnl || 0" :precision="2"
            :value-style="pnlStyle(portfolio.total_pnl)" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="累计收益率 (%)" :value="portfolio.total_pnl_pct || 0" :precision="2"
            :value-style="pnlStyle(portfolio.total_pnl_pct)" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card><el-statistic title="可用资金 (元)" :value="portfolio.cash || 0" :precision="2" /></el-card>
      </el-col>
    </el-row>

    <el-card class="mt">
      <template #header>
        <div class="card-header">
          <span>收益曲线 (vs 沪深300)</span>
          <el-popconfirm title="确认重置模拟账户?所有持仓与记录将清空" @confirm="reset">
            <template #reference><el-button size="small" type="danger" plain>重置账户</el-button></template>
          </el-popconfirm>
        </div>
      </template>
      <div ref="chartEl" style="height: 320px" />
      <el-empty v-if="!hasCurve" description="暂无数据,运行一轮决策后生成资产快照" :image-size="60" />
    </el-card>

    <el-card class="mt">
      <template #header>持仓明细</template>
      <el-table :data="portfolio.positions || []" stripe>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column prop="name" label="名称" width="120" />
        <el-table-column prop="total_qty" label="持仓" width="90" />
        <el-table-column prop="available_qty" label="可卖" width="90" />
        <el-table-column prop="avg_cost" label="成本" width="100" />
        <el-table-column prop="price" label="现价" width="100" />
        <el-table-column label="今日涨跌" width="100">
          <template #default="{ row }">
            <span :class="row.pct_change >= 0 ? 'up' : 'down'">
              {{ row.pct_change != null ? row.pct_change.toFixed(2) + '%' : '-' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="market_value" label="市值" width="120" />
        <el-table-column label="浮动盈亏">
          <template #default="{ row }">
            <span :class="row.pnl >= 0 ? 'up' : 'down'">
              {{ row.pnl.toFixed(2) }} ({{ row.pnl_pct.toFixed(2) }}%)
            </span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

const portfolio = ref({})
const chartEl = ref(null)
const hasCurve = ref(false)
let chart = null
let timer = null

function pnlStyle(value) {
  return { color: (value || 0) >= 0 ? '#f56c6c' : '#67c23a' }
}

async function load() {
  try {
    portfolio.value = await api.getPortfolio()
    const curve = await api.getEquityCurve()
    hasCurve.value = curve.equity.length > 0
    if (hasCurve.value && chart) {
      chart.setOption({
        tooltip: { trigger: 'axis', valueFormatter: (v) => v?.toFixed(2) + '%' },
        legend: { data: ['组合收益', '沪深300'] },
        grid: { left: 50, right: 20, top: 40, bottom: 30 },
        xAxis: { type: 'category', data: curve.equity.map((p) => p.time) },
        yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
        series: [
          { name: '组合收益', type: 'line', smooth: true, showSymbol: false,
            data: curve.equity.map((p) => p.pct), lineStyle: { width: 2 } },
          { name: '沪深300', type: 'line', smooth: true, showSymbol: false,
            data: curve.hs300.map((p) => p.pct), lineStyle: { width: 1, type: 'dashed' } },
        ],
      })
    }
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function reset() {
  try {
    await api.resetAccount()
    ElMessage.success('账户已重置')
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
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>

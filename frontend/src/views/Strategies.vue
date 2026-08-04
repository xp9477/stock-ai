<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">策略</h1>
        <p class="page-sub">规则基线与因子截面 · 不消耗 LLM</p>
      </div>
      <div class="head-actions">
        <el-button size="small" :loading="backtesting" @click="runBacktest">历史回测</el-button>
        <el-button size="small" type="warning" :loading="rebalancing" @click="rebalance">立即调仓</el-button>
      </div>
    </div>

    <div class="stat-grid">
      <div class="stat">
        <div class="stat-label">调仓节奏</div>
        <div class="stat-value" style="font-size:15px">{{ rules.schedule || '周一 14:50' }}</div>
        <div class="stat-hint">{{ rules.is_rebalance_day ? '今天是调仓日' : '非周一不自动调' }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">S2 持仓只数 N</div>
        <div class="stat-value mono">{{ rules.top_n || 10 }}</div>
        <div class="stat-hint">综合分前 N 等权</div>
      </div>
      <div class="stat">
        <div class="stat-label">因子有效标的</div>
        <div class="stat-value mono">{{ factors.items?.length || 0 }}</div>
        <div class="stat-hint">当前股池截面</div>
      </div>
    </div>

    <!-- Strategy cards -->
    <div class="strat-grid">
      <article
        v-for="s in strategies"
        :key="s.model_id"
        class="panel strat-card"
      >
        <div class="strat-top">
          <span class="lane-pill rule">RULE</span>
          <el-tag v-if="s.enabled === false" size="small" type="info">已停用</el-tag>
          <el-tag v-else size="small" type="success">启用</el-tag>
        </div>
        <h3 class="strat-name">{{ s.name || s.model_id }}</h3>
        <p class="strat-desc">{{ descOf(s.model_id) }}</p>
        <div class="strat-metrics">
          <div>
            <div class="m-label">总资产</div>
            <div class="m-value">{{ s.exists ? fmt(s.total_equity) : '—' }}</div>
          </div>
          <div>
            <div class="m-label">收益率</div>
            <div class="m-value" :class="(s.pnl_pct || 0) >= 0 ? 'up' : 'down'">
              {{ s.exists ? `${s.pnl_pct >= 0 ? '+' : ''}${s.pnl_pct}%` : '—' }}
            </div>
          </div>
          <div>
            <div class="m-label">持仓数</div>
            <div class="m-value">{{ s.exists ? s.position_count : '—' }}</div>
          </div>
          <div>
            <div class="m-label">现金</div>
            <div class="m-value">{{ s.exists ? fmt(s.cash) : '—' }}</div>
          </div>
        </div>
        <el-button
          size="small"
          type="warning"
          plain
          class="strat-btn"
          :loading="oneLoading === s.model_id"
          :disabled="!s.exists"
          @click="rebalanceOne(s.model_id)"
        >单独调仓</el-button>
      </article>
    </div>

    <!-- Factor table -->
    <section class="panel">
      <div class="panel-h">
        <div class="panel-title">S2 因子截面</div>
        <el-button size="small" text type="primary" :loading="factorLoading" @click="loadFactors">刷新</el-button>
      </div>
      <p v-if="factors.message" class="hint">{{ factors.message }}</p>
      <p v-if="factors.top_n?.length" class="hint">
        当前前 {{ factors.top_n_size }}：
        <span class="mono accent">{{ factors.top_n.join(' · ') }}</span>
      </p>
      <el-table v-if="factors.items?.length" :data="factors.items" stripe size="small" max-height="420">
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column label="综合分" width="100">
          <template #default="{ row }">
            <span class="mono">{{ fmtNum(row.score) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="短动量" width="90">
          <template #default="{ row }"><span class="mono">{{ fmtNum(row.mom_short, 3) }}</span></template>
        </el-table-column>
        <el-table-column label="中动量" width="90">
          <template #default="{ row }"><span class="mono">{{ fmtNum(row.mom_mid, 3) }}</span></template>
        </el-table-column>
        <el-table-column label="低波" width="90">
          <template #default="{ row }"><span class="mono">{{ fmtNum(row.low_vol, 3) }}</span></template>
        </el-table-column>
        <el-table-column label="EP" width="80">
          <template #default="{ row }"><span class="mono">{{ fmtNum(row.ep, 3) }}</span></template>
        </el-table-column>
        <el-table-column label="BP" width="80">
          <template #default="{ row }"><span class="mono">{{ fmtNum(row.bp, 3) }}</span></template>
        </el-table-column>
        <el-table-column label="ROE" width="80">
          <template #default="{ row }"><span class="mono">{{ fmtNum(row.quality_roe, 2) }}</span></template>
        </el-table-column>
        <el-table-column prop="n_factors" label="有效因子" width="90" />
      </el-table>
      <el-empty v-else description="股池为空或因子未算出 — 先在「股池」加票" :image-size="56" />
    </section>

    <!-- Backtest result -->
    <section v-if="backtest" class="panel">
      <div class="panel-h">
        <div class="panel-title">最近一次历史回测</div>
        <span class="dim" style="font-size:12px">{{ backtest.start }} → 今 · {{ backtest.codes?.length }} 只</span>
      </div>
      <div class="bt-grid">
        <div class="bt-card" v-for="key in ['equal_weight', 'factor_weekly']" :key="key">
          <div class="bt-name">{{ key === 'equal_weight' ? '池内等权' : 'S2 周频' }}</div>
          <div class="bt-metrics" v-if="backtest[key]?.metrics">
            <div><span class="m-label">总收益</span>
              <span class="mono" :class="backtest[key].metrics.total_return >= 0 ? 'up' : 'down'">
                {{ (backtest[key].metrics.total_return * 100).toFixed(2) }}%
              </span>
            </div>
            <div><span class="m-label">夏普</span>
              <span class="mono">{{ backtest[key].metrics.sharpe }}</span>
            </div>
            <div><span class="m-label">最大回撤</span>
              <span class="mono down">{{ (backtest[key].metrics.max_drawdown * 100).toFixed(2) }}%</span>
            </div>
            <div><span class="m-label">平仓笔数</span>
              <span class="mono">{{ backtest[key].closed_trades }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

const rules = ref({ strategies: [] })
const factors = ref({ items: [], top_n: [] })
const rebalancing = ref(false)
const oneLoading = ref('')
const factorLoading = ref(false)
const backtesting = ref(false)
const backtest = ref(null)

const strategies = computed(() => rules.value.strategies || [])

function descOf(id) {
  if (id === 's2_weekly') return '六因子截面 z 分等权合成，每周持有综合分前 N 只'
  if (id === 'pool_equal') return '股池全部标的等权持有，作为躺平锚'
  return '规则策略'
}

function fmt(v) {
  if (v == null) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
function fmtNum(v, d = 4) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(d)
}

async function loadRules() {
  try {
    rules.value = await api.rulesStatus()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function loadFactors() {
  factorLoading.value = true
  try {
    factors.value = await api.factorsSnapshot()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    factorLoading.value = false
  }
}

async function rebalance() {
  rebalancing.value = true
  try {
    await api.rulesRebalance()
    ElMessage.success('规则组调仓完成')
    await loadRules()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    rebalancing.value = false
  }
}

async function rebalanceOne(id) {
  oneLoading.value = id
  try {
    const r = await api.rulesRebalanceOne(id)
    if (r.ok === false) ElMessage.warning(r.error || '调仓未完成')
    else ElMessage.success(`${id} 调仓完成`)
    await loadRules()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    oneLoading.value = ''
  }
}

async function runBacktest() {
  backtesting.value = true
  try {
    backtest.value = await api.runBacktest({ years: 3 })
    ElMessage.success('回测完成')
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    backtesting.value = false
  }
}

onMounted(() => {
  loadRules()
  loadFactors()
})
</script>

<style scoped>
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.strat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
}
.strat-card { display: flex; flex-direction: column; gap: 10px; }
.strat-top { display: flex; align-items: center; gap: 8px; }
.strat-name { margin: 0; font-size: 17px; font-weight: 700; letter-spacing: -0.02em; }
.strat-desc { margin: 0; font-size: 12px; color: var(--text-muted); line-height: 1.5; min-height: 2.8em; }
.strat-metrics {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
  padding: 10px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
}
.strat-btn { align-self: flex-start; margin-top: 4px; }
.hint { font-size: 12px; color: var(--text-muted); margin: 0 0 10px; }
.accent { color: var(--accent); }
.bt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.bt-card {
  background: var(--panel-2); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 14px;
}
.bt-name { font-weight: 600; margin-bottom: 10px; }
.bt-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px; }
.bt-metrics .m-label { display: block; margin-bottom: 2px; }

@media (max-width: 700px) {
  .bt-grid { grid-template-columns: 1fr; }
}
</style>

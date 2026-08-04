<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">今日战报</h1>
        <p class="page-sub">AI 日频 vs 规则周频 · 同一股池 · 同一撮合</p>
      </div>
      <el-button size="small" type="danger" plain @click="confirmReset">重置全部账户</el-button>
    </div>

    <div class="stat-grid">
      <div class="stat">
        <div class="stat-label">账本平仓</div>
        <div class="stat-value mono">{{ ledger.closed_trades ?? 0 }}<span class="slash">/{{ ledger.min_closed_trades || 100 }}</span></div>
        <div class="stat-hint">样本门槛 · 笔数</div>
      </div>
      <div class="stat">
        <div class="stat-label">交易日门槛</div>
        <div class="stat-value mono">{{ ledger.min_trade_days || 60 }}</div>
        <div class="stat-hint">日 ∧ 笔 都达标才可晋级</div>
      </div>
      <div class="stat">
        <div class="stat-label">样本状态</div>
        <div class="stat-value" :class="ledger.sample_ok ? 'down' : 'dim'">{{ ledger.sample_ok ? '达标' : '不足' }}</div>
        <div class="stat-hint">{{ ledger.sample_ok ? '可进入候选评估' : '继续前瞻模拟' }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">规则调仓</div>
        <div class="stat-value sched">{{ rules.is_rebalance_day ? '今天·周一' : '周一 14:50' }}</div>
        <div class="stat-hint">{{ rules.schedule || '—' }}</div>
      </div>
    </div>

    <!-- 双赛道：flex stretch 强制外框同高，中间 grow 顶开，脚栏贴底 -->
    <div class="lanes">
      <section class="lane-panel ai-lane">
        <header class="lane-h">
          <div class="panel-title">
            <span class="lane-pill ai">AI</span>
            日频赛道
          </div>
          <span class="count-badge">{{ aiRows.length }} 选手</span>
        </header>
        <div class="lb-head mono">
          <span class="c-rank">#</span>
          <span class="c-name">选手</span>
          <span class="c-eq">资产</span>
          <span class="c-ret">收益</span>
          <span class="c-pos">仓</span>
          <span class="c-dd">回撤</span>
        </div>
        <div class="lb-rows">
          <div v-if="!aiRows.length" class="empty-lane">
            <img src="/empty-race.png" alt="" class="empty-img" width="72" height="72" />
            <p>暂无 AI 选手</p>
          </div>
          <button
            v-for="(row, i) in aiRows"
            :key="row.id"
            type="button"
            class="lb-tr"
            :class="{ active: activeModel === row.id }"
            :aria-pressed="activeModel === row.id"
            :aria-label="`查看 ${row.name} 持仓，收益 ${fmtPct(row.pnl_pct)}`"
            @click="selectModel(row.id)"
          >
            <span class="c-rank mono" :class="'r' + Math.min(i + 1, 4)">{{ i + 1 }}</span>
            <span class="c-name">
              <span class="name-txt">{{ row.name }}</span>
              <span v-if="row.type === 'ensemble'" class="mini-tag ens">合议</span>
              <span v-else class="mini-tag llm">LLM</span>
            </span>
            <span class="c-eq mono">{{ fmtCompact(row.total_equity) }}</span>
            <span class="c-ret mono" :class="row.pnl_pct >= 0 ? 'up' : 'down'">{{ fmtPct(row.pnl_pct) }}</span>
            <span class="c-pos mono">{{ row.position_count }}</span>
            <span class="c-dd mono dim">{{ Number(row.max_drawdown_pct).toFixed(1) }}%</span>
          </button>
        </div>
        <div class="lane-grow" aria-hidden="true" />
        <footer class="lane-foot">
          <span class="dim">最佳</span>
          <span class="mono" :class="bestAi >= 0 ? 'up' : 'down'">{{ fmtPct(bestAi) }}</span>
        </footer>
      </section>

      <section class="lane-panel rule-lane">
        <header class="lane-h">
          <div class="panel-title">
            <span class="lane-pill rule">RULE</span>
            规则赛道
          </div>
          <el-button size="small" type="warning" :loading="rebalancing" @click="doRebalance">调仓</el-button>
        </header>
        <div class="lb-head mono">
          <span class="c-rank">#</span>
          <span class="c-name">策略</span>
          <span class="c-eq">资产</span>
          <span class="c-ret">收益</span>
          <span class="c-pos">仓</span>
          <span class="c-dd">回撤</span>
        </div>
        <div class="lb-rows">
          <div v-if="!ruleRows.length" class="empty-lane">
            <img src="/empty-race.png" alt="" class="empty-img" width="72" height="72" />
            <p>暂无规则策略</p>
          </div>
          <button
            v-for="(row, i) in ruleRows"
            :key="row.id"
            type="button"
            class="lb-tr"
            :class="{ active: activeModel === row.id }"
            :aria-pressed="activeModel === row.id"
            :aria-label="`查看 ${row.name} 持仓，收益 ${fmtPct(row.pnl_pct)}`"
            @click="selectModel(row.id)"
          >
            <span class="c-rank mono" :class="'r' + Math.min(i + 1, 4)">{{ i + 1 }}</span>
            <span class="c-name">
              <span class="name-txt">{{ row.name }}</span>
              <span class="mini-tag rule">规则</span>
            </span>
            <span class="c-eq mono">{{ fmtCompact(row.total_equity) }}</span>
            <span class="c-ret mono" :class="row.pnl_pct >= 0 ? 'up' : 'down'">{{ fmtPct(row.pnl_pct) }}</span>
            <span class="c-pos mono">{{ row.position_count }}</span>
            <span class="c-dd mono dim">{{ Number(row.max_drawdown_pct).toFixed(1) }}%</span>
          </button>
        </div>
        <div class="lane-grow" aria-hidden="true" />
        <footer class="lane-foot">
          <span class="dim">最佳</span>
          <span class="mono" :class="bestRule >= 0 ? 'up' : 'down'">{{ fmtPct(bestRule) }}</span>
          <span v-if="aiRows.length && ruleRows.length" class="vs">
            vs AI
            <span class="mono" :class="bestRule - bestAi >= 0 ? 'up' : 'down'">
              {{ fmtPct(bestRule - bestAi, true) }}
            </span>
          </span>
        </footer>
      </section>
    </div>

    <section class="panel">
      <div class="panel-h">
        <div class="panel-title">收益曲线 · vs 沪深300</div>
      </div>
      <div ref="chartEl" class="chart" :style="{ height: isMobile ? '240px' : '340px' }" />
      <el-empty v-if="!hasCurve" description="暂无快照。规则调仓或 AI 决策后会生成曲线" :image-size="56" />
    </section>

    <section class="panel">
      <div class="panel-h">
        <div class="panel-title" id="pos-title">持仓明细</div>
        <label class="sr-only" for="pos-model-select">选择选手</label>
        <el-select
          id="pos-model-select"
          v-model="activeModel"
          size="small"
          style="width: 168px"
          aria-labelledby="pos-title"
          @change="loadPortfolio"
        >
          <el-option v-for="m in leaderboard" :key="m.id" :value="m.id" :label="m.name" />
        </el-select>
      </div>
      <div v-if="portfolio.total_equity != null" class="stat-grid pos-stats">
        <div class="stat">
          <div class="stat-label">总资产</div>
          <div class="stat-value mono sm">{{ fmtMoney(portfolio.total_equity) }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">现金</div>
          <div class="stat-value mono sm">{{ fmtMoney(portfolio.cash) }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">累计盈亏</div>
          <div class="stat-value mono sm" :class="portfolio.total_pnl >= 0 ? 'up' : 'down'">
            {{ fmtMoney(portfolio.total_pnl) }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-label">收益率</div>
          <div class="stat-value mono sm" :class="portfolio.total_pnl_pct >= 0 ? 'up' : 'down'">
            {{ portfolio.total_pnl_pct }}%
          </div>
        </div>
      </div>

      <div v-if="isMobile" class="mt">
        <div v-for="row in portfolio.positions || []" :key="row.code" class="m-card">
          <div style="display:flex;justify-content:space-between;gap:8px">
            <div class="m-card-title">{{ row.name }} <span class="dim mono">{{ row.code }}</span></div>
            <div class="mono" :class="row.pnl >= 0 ? 'up' : 'down'">{{ row.pnl_pct?.toFixed(2) }}%</div>
          </div>
          <div class="m-grid">
            <div><div class="m-label">持仓/可卖</div><div class="m-value">{{ row.total_qty }} / {{ row.available_qty }}</div></div>
            <div><div class="m-label">成本/现价</div><div class="m-value">{{ row.avg_cost }} / {{ row.price }}</div></div>
          </div>
        </div>
        <el-empty v-if="!(portfolio.positions || []).length" description="空仓 — 点上方账户行或先调仓/决策" :image-size="48" />
      </div>
      <el-table v-else class="mt" :data="portfolio.positions || []" stripe empty-text="空仓 — 点击赛道行切换账户，或先调仓/决策">
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column prop="name" label="名称" width="100" />
        <el-table-column prop="total_qty" label="持仓" width="80" />
        <el-table-column prop="available_qty" label="可卖" width="80" />
        <el-table-column prop="avg_cost" label="成本" width="90" />
        <el-table-column prop="price" label="现价" width="90" />
        <el-table-column label="涨跌" width="90">
          <template #default="{ row }">
            <span class="mono" :class="row.pct_change >= 0 ? 'up' : 'down'">
              {{ row.pct_change != null ? row.pct_change.toFixed(2) + '%' : '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="market_value" label="市值" width="110" />
        <el-table-column label="浮盈亏" min-width="140">
          <template #default="{ row }">
            <span class="mono" :class="row.pnl >= 0 ? 'up' : 'down'">
              {{ row.pnl?.toFixed(0) }} ({{ row.pnl_pct?.toFixed(2) }}%)
            </span>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'

const { isMobile } = useIsMobile()
const leaderboard = ref([])
const ledger = ref({})
const rules = ref({})
const activeModel = ref(null)
const portfolio = ref({})
const chartEl = ref(null)
const hasCurve = ref(false)
const rebalancing = ref(false)
let chart = null
let timer = null
let lastCurve = null

const aiRows = computed(() =>
  leaderboard.value.filter((r) => r.type !== 'rule'))
const ruleRows = computed(() =>
  leaderboard.value.filter((r) => r.type === 'rule' || r.lane === 'rule'))

const bestAi = computed(() => {
  if (!aiRows.value.length) return 0
  return Math.max(...aiRows.value.map((r) => Number(r.pnl_pct) || 0))
})
const bestRule = computed(() => {
  if (!ruleRows.value.length) return 0
  return Math.max(...ruleRows.value.map((r) => Number(r.pnl_pct) || 0))
})

function fmtMoney(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
function fmtCompact(v) {
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  if (Math.abs(n) >= 1e6) return (n / 1e4).toFixed(1) + '万'
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(2) + '万'
  return n.toFixed(0)
}
function fmtPct(v, signed = true) {
  const n = Number(v) || 0
  const s = n.toFixed(2) + '%'
  if (!signed) return s
  return n > 0 ? '+' + s : s
}

function selectModel(id) {
  activeModel.value = id
  loadPortfolio()
}

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
  const colors = ['#e8b84a', '#38bdf8', '#c4b5fd', '#2dd4a8', '#f472b6', '#94a3b8', '#fb923c']
  const series = curve.series.map((s, idx) => ({
    name: s.name,
    type: 'line',
    smooth: true,
    showSymbol: false,
    data: s.points.map((p) => [p.time, p.pct]),
    lineStyle: { width: s.type === 'rule' || s.type === 'ensemble' ? 2.4 : 1.8 },
    itemStyle: { color: colors[idx % colors.length] },
  }))
  if (curve.hs300?.length) {
    series.push({
      name: '沪深300',
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: curve.hs300.map((p) => [p.time, p.pct]),
      lineStyle: { width: 1, type: 'dashed', color: '#64748b' },
      itemStyle: { color: '#64748b' },
    })
  }
  chart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#8b9bb3', fontFamily: 'DM Sans, sans-serif' },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#141e2e',
      borderColor: '#1e2d42',
      textStyle: { color: '#e8eef7' },
      valueFormatter: (v) => (typeof v === 'number' ? `${v.toFixed(2)}%` : v),
    },
    legend: {
      type: 'scroll',
      bottom: 0,
      textStyle: { color: '#8b9bb3', fontSize: 11 },
      pageTextStyle: { color: '#8b9bb3' },
    },
    grid: { left: 12, right: 12, top: 16, bottom: 36, containLabel: true },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#1e2d42' } },
      axisLabel: { color: '#5c6b82' },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { formatter: '{value}%', color: '#5c6b82' },
      splitLine: { lineStyle: { color: '#1e2d42', type: 'dashed' } },
    },
    series,
  }, { notMerge: true })
}

async function load() {
  try {
    const [lb, led, rs] = await Promise.all([
      api.getLeaderboard(),
      api.ledgerStats().catch(() => ({})),
      api.rulesStatus().catch(() => ({})),
    ])
    leaderboard.value = lb
    ledger.value = led
    rules.value = rs
    if (activeModel.value == null && lb.length) activeModel.value = lb[0].id
    await loadPortfolio()
    const curve = await api.getEquityCurve()
    hasCurve.value = curve.series?.length > 0
    if (hasCurve.value) {
      lastCurve = curve
      renderChart()
    }
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function doRebalance() {
  rebalancing.value = true
  try {
    await api.rulesRebalance()
    ElMessage.success('规则调仓完成')
    await load()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    rebalancing.value = false
  }
}

async function confirmReset() {
  try {
    await ElMessageBox.confirm('将清空全部持仓、订单与决策记录，资金重置为初始值。', '重置全部账户', {
      type: 'warning',
      confirmButtonText: '确认重置',
      cancelButtonText: '取消',
    })
    await api.resetAccount()
    ElMessage.success('已重置')
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || String(e))
  }
}

const onResize = () => chart?.resize()
watch(isMobile, () => setTimeout(() => { chart?.resize(); renderChart() }, 50))

onMounted(() => {
  if (chartEl.value) chart = echarts.init(chartEl.value, 'dark')
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
.slash { font-size: 13px; color: var(--text-dim); font-weight: 500; }
.stat-value.sm { font-size: 16px; }
.stat-value.sched { font-size: 15px; font-weight: 600; }

/*
 * 等高关键：父级 flex + align-items:stretch，
 * 子项 flex:1 同宽，column 布局里 lane-grow 吃掉多余高度，
 * 脚栏始终在底部。不要用 height:100% / grid 1fr（高度不定时不可靠）。
 */
.lanes {
  display: flex;
  flex-direction: row;
  align-items: stretch;
  gap: 14px;
}
.lane-panel {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px 0;
  box-sizing: border-box;
}
.ai-lane { border-top: 2px solid rgba(232, 184, 74, 0.55); }
.rule-lane { border-top: 2px solid rgba(56, 189, 248, 0.55); }

.lane-h {
  flex: 0 0 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.count-badge {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-dim);
  background: var(--panel-2);
  border: 1px solid var(--border);
  padding: 2px 8px;
  border-radius: 999px;
  white-space: nowrap;
}

.lb-head,
.lb-tr {
  display: grid;
  grid-template-columns: 28px minmax(0, 1.45fr) 0.95fr 0.85fr 36px 52px;
  gap: 6px;
  align-items: center;
  width: 100%;
  text-align: left;
  box-sizing: border-box;
}
.lb-head {
  flex: 0 0 auto;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  padding: 0 8px 8px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 4px;
}
.lb-rows {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.lb-tr {
  min-height: 40px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 6px 8px;
  color: inherit;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.lb-tr:hover { background: rgba(255, 255, 255, 0.03); }
.lb-tr.active {
  background: rgba(232, 184, 74, 0.08);
  border-color: rgba(232, 184, 74, 0.25);
}
.rule-lane .lb-tr.active {
  background: rgba(56, 189, 248, 0.08);
  border-color: rgba(56, 189, 248, 0.28);
}

/* 关键：把矮的一侧「撑」到和另一侧一样高 */
.lane-grow {
  flex: 1 1 auto;
  min-height: 8px;
}

.c-rank {
  width: 22px; height: 22px; border-radius: 6px;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
  background: var(--panel-2); color: var(--text-dim);
}
.c-rank.r1 { background: rgba(232,184,74,0.2); color: var(--accent); }
.c-rank.r2 { background: rgba(148,163,184,0.15); color: #cbd5e1; }
.c-rank.r3 { background: rgba(251,146,60,0.15); color: #fb923c; }
.c-name {
  display: flex; align-items: center; gap: 6px; min-width: 0;
  font-size: 13px; font-weight: 600; text-align: left;
}
.name-txt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mini-tag {
  flex: none; font-size: 10px; font-weight: 600;
  padding: 1px 5px; border-radius: 4px;
}
.mini-tag.llm { background: var(--accent-dim); color: var(--lane-ai); }
.mini-tag.ens { background: rgba(167,139,250,0.14); color: #c4b5fd; }
.mini-tag.rule { background: var(--lane-rule-dim); color: var(--lane-rule); }
.c-eq, .c-ret, .c-pos, .c-dd { font-size: 12px; text-align: right; }
.lb-head .c-eq, .lb-head .c-ret, .lb-head .c-pos, .lb-head .c-dd { text-align: right; }

.empty-lane {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  gap: 8px;
  color: var(--text-dim); font-size: 13px; padding: 28px 16px;
}
.empty-img { opacity: 0.85; border-radius: 12px; }
.empty-lane p { margin: 0; }

.lane-foot {
  flex: 0 0 48px;
  margin-top: 0;
  padding: 12px 4px 14px;
  border-top: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
  font-size: 12px;
}
.lane-foot .vs {
  margin-left: auto; color: var(--text-muted);
  display: flex; align-items: center; gap: 6px;
}

.chart { width: 100%; }
.mt { margin-top: 12px; }
.pos-stats { margin-bottom: 4px; }

@media (max-width: 960px) {
  .lanes { flex-direction: column; }
  .lane-grow { display: none; }
  .lb-head .c-dd, .lb-tr .c-dd { display: none; }
  .lb-head, .lb-tr {
    grid-template-columns: 28px minmax(0, 1.3fr) 0.9fr 0.85fr 36px;
  }
}
@media (max-width: 480px) {
  .lb-head .c-eq, .lb-tr .c-eq { display: none; }
  .lb-head, .lb-tr {
    grid-template-columns: 28px minmax(0, 1fr) 0.9fr 32px;
  }
}
</style>

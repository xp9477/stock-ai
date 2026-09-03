<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <button type="button" class="back-link" @click="$router.push('/runs')">← 决策列表</button>
        <h1 class="page-title">决策 #{{ $route.params.id }}</h1>
        <p class="page-sub">
          <el-tag size="small" :type="statusType(detail.status)" class="mr">
            {{ statusText(detail.status) }}
          </el-tag>
          <span v-if="liveProgress?.message" class="dim">{{ liveProgress.message }}</span>
          <span v-else class="dim">独立判断、最终交易员与风险复核 · 买卖结论只生成候选计划</span>
        </p>
      </div>
      <div class="head-actions">
        <el-switch
          v-model="debugMode"
          size="small"
          inline-prompt
          active-text="调试"
          inactive-text="证据链"
        />
        <el-button
          v-if="isLive"
          size="small"
          type="danger"
          plain
          :loading="cancelling"
          @click="cancelRun"
        >停止本轮</el-button>
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </div>

    <!-- 实时进度条 -->
    <section v-if="isLive || liveProgress" class="panel progress-panel">
      <div class="progress-row">
        <span class="progress-label">进度</span>
        <span class="mono progress-msg">{{ liveProgress?.message || (isLive ? '运行中…' : '') }}</span>
      </div>
      <el-progress
        v-if="progressPct != null"
        :percentage="progressPct"
        :stroke-width="10"
        :status="detail.status === 'cancelled' ? 'warning' : undefined"
      />
      <div v-if="liveProgress" class="progress-meta mono dim">
        <span v-if="liveProgress.model_name">模型 {{ liveProgress.model_index }}/{{ liveProgress.model_total }} · {{ liveProgress.model_name }}</span>
        <span v-if="liveProgress.code"> · {{ liveProgress.stock_name }}({{ liveProgress.code }}) {{ liveProgress.stock_index }}/{{ liveProgress.stock_total }}</span>
        <span v-if="liveProgress.agent"> · {{ agentName(liveProgress.agent) }}</span>
        <span v-if="status.cancel_requested"> · 停止中</span>
      </div>
    </section>

    <el-alert v-if="detail.error" type="error" :title="detail.error" class="mb" show-icon />
    <el-alert
      v-if="detail.status === 'cancelled'"
      type="warning"
      title="本轮已协作取消：当前步骤结束后停止，已落库判断保留，未完成判断不会生成候选计划。"
      class="mb"
      show-icon
      :closable="false"
    />

    <!-- 选股结果卡片 -->
    <section v-if="detail.trigger === 'selector' && detail.result" class="panel mb">
      <div class="panel-h">
        <div class="panel-title">选股结果</div>
        <router-link class="flow-link" to="/watchlist">查看股池 →</router-link>
      </div>
      <div class="sel-stats mono">
        <span>入池 <b class="up">{{ (detail.result.added || []).length }}</b></span>
        <span>移出 <b class="down">{{ (detail.result.removed || []).length }}</b></span>
        <span>池内 {{ detail.result.pool_size ?? '—' }}</span>
        <span v-if="detail.result.model">模型 {{ detail.result.model }}</span>
      </div>
      <div v-if="(detail.result.added || []).length" class="sel-tags">
        <el-tag v-for="c in detail.result.added" :key="'a'+c" size="small" type="danger" effect="dark" class="mr">
          +{{ c }}
        </el-tag>
      </div>
      <div v-if="(detail.result.removed || []).length" class="sel-tags">
        <el-tag v-for="c in detail.result.removed" :key="'r'+c" size="small" type="info" class="mr">
          −{{ c }}
        </el-tag>
      </div>
      <p v-if="!(detail.result.added || []).length && !(detail.result.removed || []).length" class="dim">
        本轮股池无变动（已满或 keep 与现池一致）。
      </p>
    </section>

    <!-- 决策汇总 -->
    <section v-if="detail.result?.kind === 'pipeline'" class="panel mb">
      <div class="panel-h">
        <div class="panel-title">本轮汇总</div>
        <router-link class="flow-link" to="/orders">查看候选计划 →</router-link>
      </div>
      <div class="sel-stats mono">
        <span>候选买入 <b class="up">{{ detail.result.signal_buy ?? detail.result.buy ?? 0 }}</b></span>
        <span>候选卖出 <b class="down">{{ detail.result.signal_sell ?? detail.result.sell ?? 0 }}</b></span>
        <span>观望 {{ detail.result.hold || 0 }}</span>
        <span>独立模型 {{ detail.result.llm_models || 0 }} · 最终汇总 {{ detail.result.ensembles || 0 }}</span>
      </div>
      <div v-if="detail.result.entry_setup_counts" class="sel-stats mono" style="margin-top:10px">
        <span>门禁通过 <b class="up">{{ detail.result.entry_setup_counts.actionable || 0 }}</b></span>
        <span>观察 {{ detail.result.entry_setup_counts.watch || 0 }}</span>
        <span>拒绝 {{ detail.result.entry_setup_counts.rejected || 0 }}</span>
        <span>数据不足 {{ detail.result.entry_setup_counts.data_insufficient || 0 }}</span>
        <span>{{ detail.result.entry_setup_version }}</span>
      </div>
      <div v-if="(detail.result.entry_setup_actionable_codes || []).length" class="sel-tags" style="margin-top:10px">
        <span class="dim" style="margin-right:8px">进入合议</span>
        <el-tag
          v-for="code in detail.result.entry_setup_actionable_codes"
          :key="code" size="small" type="danger" effect="plain" class="mr"
        >{{ code }}</el-tag>
      </div>
      <p v-if="detail.result.degraded" class="down" style="margin:10px 0 0">
        本轮存在数据或模型降级；请查看判断失败数与具体决策错误后再审批。
      </p>
    </section>

    <!-- 时间线（当前模型） -->
    <section v-if="timelineSteps.length" class="panel mb">
      <div class="panel-h">
        <div class="panel-title">决策证据时间线</div>
        <span class="dim" style="font-size:12px">完成即可见 · 运行中自动刷新</span>
      </div>
      <ol class="timeline">
        <li
          v-for="(step, i) in timelineSteps"
          :key="i"
          class="tl-item"
          :class="[step.state, { muted: step.muted }]"
        >
          <span class="tl-dot" />
          <div class="tl-body">
            <div class="tl-head">
              <b>{{ step.title }}</b>
              <el-tag size="small" :type="stepTag(step.state)">{{ stepStateText(step.state) }}</el-tag>
              <span v-if="step.time" class="mono time">{{ step.time }}</span>
            </div>
            <div v-if="step.preview && !debugMode" class="tl-preview dim">{{ step.preview }}</div>
            <div v-if="debugMode && step.output" class="markdown" v-html="render(step.output)" />
            <div v-if="debugMode && step.input" class="input-block">
              <div class="input-label">输入摘要 / 调试</div>
              <pre class="input-summary">{{ step.input }}</pre>
            </div>
          </div>
        </li>
      </ol>
    </section>

    <el-tabs v-model="activeModel" class="detail-tabs" v-if="(detail.models || []).length">
      <el-tab-pane v-for="slot in detail.models" :key="slot.model_pk"
        :label="slot.model" :name="String(slot.model_pk)">
        <el-collapse v-if="slot.market_report || slot.reflection || slot.selector_report" class="mb report-collapse">
          <el-collapse-item v-if="slot.selector_report" name="selector">
            <template #title><b>AI 选股报告</b></template>
            <div class="markdown" v-html="render(slot.selector_report)" />
          </el-collapse-item>
          <el-collapse-item v-if="slot.market_report" name="market">
            <template #title><b>冻结市场事实</b></template>
            <div class="markdown" v-html="render(slot.market_report)" />
            <div v-if="debugMode" class="input-block">
              <div class="input-label">调试 · 已落库全文见上</div>
            </div>
          </el-collapse-item>
          <el-collapse-item v-if="slot.reflection" name="reflect">
            <template #title><b>历史复盘（不参与当前判断）</b></template>
            <div class="markdown" v-html="render(slot.reflection)" />
          </el-collapse-item>
        </el-collapse>

        <div v-if="!activeStocks(slot).length && holdStocks(slot).length" class="idle-banner mb">
          本轮该模型未生成候选买卖计划，以下均为观望 / 不操作。
        </div>

        <!-- 买卖 / 进行中 / 失败：默认展开 -->
        <div
          v-for="stock in activeStocks(slot)"
          :key="stock.code"
          class="panel mb stock-panel stock-active"
        >
          <div class="stock-header">
            <span class="stock-title">{{ stock.name || '' }} <span class="mono dim">({{ stock.code }})</span></span>
            <template v-if="stock.decision">
              <el-tag :type="actionType(stock.decision.action)" effect="dark">
                {{ decisionActionText(stock.decision) }}
                <template v-if="stock.decision.action !== 'hold'">
                  → {{ (stock.decision.target_position_pct * 100).toFixed(1) }}%
                </template>
              </el-tag>
              <span class="confidence mono">信心 {{ (stock.decision.confidence * 100).toFixed(0) }}%</span>
            </template>
            <el-tag v-else size="small" type="info">判断中 / 待形成计划</el-tag>
          </div>

          <el-alert v-if="stock.decision?.error" type="error"
            :title="'分析失败: ' + stock.decision.error" :closable="false" class="mb" show-icon />
          <el-alert v-else-if="stock.decision" :type="reasonAlertType(stock.decision.action)"
            :title="stock.decision.reason" :closable="false" class="mb" show-icon />

          <el-collapse class="agent-collapse">
            <el-collapse-item v-for="agent in stock.agents" :key="agent.agent" :name="agent.agent">
              <template #title>
                <b>{{ agentName(agent.agent) }}</b>
                <span class="time mono">{{ agent.created_at }}</span>
              </template>
              <div class="markdown" v-html="render(agent.output)" />
              <div v-if="debugMode && agent.input_summary" class="input-block">
                <div class="input-label">输入摘要（调试）</div>
                <pre class="input-summary">{{ agent.input_summary }}</pre>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>

        <!-- 观望：默认折叠、弱化 -->
        <el-collapse
          v-if="holdStocks(slot).length"
          v-model="holdOpen[slot.model_pk]"
          class="hold-fold mb"
        >
          <el-collapse-item name="holds">
            <template #title>
              <div class="hold-fold-title">
                <span class="hold-fold-label">观望 / 不操作</span>
                <el-tag size="small" type="info" effect="plain" class="hold-count">
                  {{ holdStocks(slot).length }} 只
                </el-tag>
                <span class="hold-fold-hint dim">默认折叠 · 未生成交易计划</span>
              </div>
            </template>
            <div
              v-for="stock in holdStocks(slot)"
              :key="stock.code"
              class="panel mb stock-panel stock-hold"
            >
              <div class="stock-header">
                <span class="stock-title">{{ stock.name || '' }} <span class="mono dim">({{ stock.code }})</span></span>
                <el-tag type="info" effect="plain" class="hold-action-tag">
                  {{ decisionActionText(stock.decision) }}
                </el-tag>
                <span class="confidence mono">信心 {{ (stock.decision.confidence * 100).toFixed(0) }}%</span>
              </div>
              <el-alert
                v-if="stock.decision?.reason"
                type="info"
                :title="stock.decision.reason"
                :closable="false"
                class="mb hold-reason"
                show-icon
              />
              <el-collapse class="agent-collapse hold-agents">
                <el-collapse-item v-for="agent in stock.agents" :key="agent.agent" :name="agent.agent">
                  <template #title>
                    <b>{{ agentName(agent.agent) }}</b>
                    <span class="time mono">{{ agent.created_at }}</span>
                  </template>
                  <div class="markdown" v-html="render(agent.output)" />
                  <div v-if="debugMode && agent.input_summary" class="input-block">
                    <div class="input-label">输入摘要（调试）</div>
                    <pre class="input-summary">{{ agent.input_summary }}</pre>
                  </div>
                </el-collapse-item>
              </el-collapse>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-tab-pane>
    </el-tabs>

    <el-empty
      v-if="!loading && !(detail.models || []).length && !isLive && detail.trigger !== 'selector'"
      description="该轮无分析数据"
    />
    <el-empty
      v-else-if="!loading && !(detail.models || []).length && isLive"
      description="决策流水线启动中，独立判断完成后将显示在此…"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import { renderMarkdown } from '../utils/markdown.js'

const route = useRoute()
const detail = ref({})
const status = ref({})
const loading = ref(true)
const activeModel = ref('')
const debugMode = ref(false)
const cancelling = ref(false)
/** model_pk → open collapse names；默认 [] = 折叠观望 */
const holdOpen = reactive({})
let pollTimer = null

const AGENT_NAMES = {
  independent_judgment: '独立判断', judgment: '独立判断',
  final_trader: '最终交易员', trader: '最终交易员（旧记录）',
  risk_review: '风险复核', risk: '风险复核（旧记录）',
  market: '冻结市场事实',
  technical: '历史技术论证', fundamental: '历史基本面论证', news: '历史新闻论证',
  bull_1: '历史论证 · 1A', bear_1: '历史论证 · 1B',
  bull_2: '历史论证 · 2A', bear_2: '历史论证 · 2B',
  reflect: '历史复盘（不参与当前判断）',
}
const agentName = (key) => AGENT_NAMES[key] || key
const actionType = (a) => ({ buy: 'danger', sell: 'success', hold: 'info' }[a] || 'info')
const reasonAlertType = (a) => ({ buy: 'error', sell: 'success', hold: 'info' }[a] || 'info')

/** hold：目标仓位>0 → 继续持有，否则 → 观望 */
function decisionActionText(decision) {
  if (!decision) return ''
  const a = decision.action
  if (a === 'buy') return '候选买入'
  if (a === 'sell') return '候选卖出'
  if (a === 'hold') {
    return (decision.target_position_pct || 0) > 0.001 ? '继续持有' : '观望'
  }
  return a
}

const statusType = (s) => ({
  running: 'warning', done: 'success', failed: 'danger', cancelled: 'info',
}[s] || 'info')
const statusText = (s) => ({
  running: '运行中', done: '完成', failed: '失败', cancelled: '已取消',
}[s] || s || '…')
const render = renderMarkdown

function isQuietHold(stock) {
  const d = stock?.decision
  return Boolean(d && !d.error && d.action === 'hold')
}

function activeStocks(slot) {
  const stocks = slot?.stocks || []
  const rank = (s) => {
    const a = s.decision?.action
    if (a === 'buy') return 0
    if (a === 'sell') return 1
    if (s.decision?.error) return 2
    if (!s.decision) return 3
    return 4
  }
  return stocks.filter((s) => !isQuietHold(s)).sort((a, b) => rank(a) - rank(b))
}

function holdStocks(slot) {
  return (slot?.stocks || []).filter(isQuietHold)
}

const isLive = computed(() => {
  if (detail.value.status === 'running') return true
  const rid = Number(route.params.id)
  return Boolean(status.value.running && status.value.current_run_id === rid)
})

const liveProgress = computed(() => {
  if (!isLive.value) return null
  return status.value.progress || null
})

const progressPct = computed(() => {
  const p = liveProgress.value
  if (!p || !p.model_total || !p.stock_total) return isLive.value ? 5 : null
  const models = p.model_total
  const stocks = p.stock_total
  const totalUnits = models * (stocks + 1) // +1 market/reflect approx
  const doneUnits = Math.max(0, (p.model_index - 1) * (stocks + 1) + (p.stock_index || 0))
  const pct = Math.min(95, Math.round((doneUnits / Math.max(totalUnits, 1)) * 100))
  return Math.max(3, pct)
})

const timelineSteps = computed(() => {
  const slot = (detail.value.models || []).find(
    (m) => String(m.model_pk) === activeModel.value,
  ) || (detail.value.models || [])[0]
  if (!slot) {
    if (liveProgress.value?.agent) {
      return [{
        title: `${liveProgress.value.model_name || ''} · ${agentName(liveProgress.value.agent)}`,
        state: 'running',
        time: '',
        preview: liveProgress.value.message,
      }]
    }
    return []
  }
  const steps = []
  if (slot.market_report) {
    steps.push({
      title: '冻结市场事实',
      state: 'done',
      time: '',
      output: slot.market_report,
      preview: String(slot.market_report).slice(0, 80),
    })
  }
  const ordered = [...activeStocks(slot), ...holdStocks(slot)]
  for (const stock of ordered) {
    for (const ag of stock.agents || []) {
      steps.push({
        title: `${stock.name || stock.code} · ${agentName(ag.agent)}`,
        state: 'done',
        time: ag.created_at,
        output: ag.output,
        input: ag.input_summary,
        preview: String(ag.output || '').replace(/\s+/g, ' ').slice(0, 100),
        muted: isQuietHold(stock),
      })
    }
    if (stock.decision && !stock.decision.error) {
      steps.push({
        title: `${stock.name || stock.code} · 条件计划结论`,
        state: 'done',
        time: '',
        preview: `${decisionActionText(stock.decision)} ${stock.decision.reason || ''}`.slice(0, 120),
        muted: isQuietHold(stock),
      })
    }
  }
  if (slot.reflection) {
    steps.push({
      title: '历史复盘（不参与当前判断）',
      state: 'done',
      output: slot.reflection,
      preview: String(slot.reflection).slice(0, 80),
    })
  }
  if (isLive.value && liveProgress.value?.agent) {
    const cur = liveProgress.value
    const title = `${cur.stock_name || cur.code || ''} · ${agentName(cur.agent)}`.replace(/^ · /, '')
    const exists = steps.some((s) => s.title.includes(agentName(cur.agent)) && s.state === 'running')
    if (!exists) {
      steps.push({
        title: title || agentName(cur.agent),
        state: 'running',
        preview: cur.message,
      })
    }
  }
  return steps.slice(-40)
})

function stepTag(state) {
  return { done: 'success', running: 'warning', failed: 'danger', pending: 'info' }[state] || 'info'
}
function stepStateText(state) {
  return { done: '完成', running: '进行中', failed: '失败', pending: '排队' }[state] || state
}

async function reload() {
  loading.value = true
  try {
    await Promise.all([loadDetail(), loadStatus()])
  } finally {
    loading.value = false
  }
}

async function loadDetail() {
  detail.value = await api.getRunDetail(route.params.id)
  if ((detail.value.models || []).length && !activeModel.value) {
    activeModel.value = String(detail.value.models[0].model_pk)
  }
  for (const slot of detail.value.models || []) {
    if (holdOpen[slot.model_pk] === undefined) {
      holdOpen[slot.model_pk] = []
    }
  }
}

async function loadStatus() {
  try {
    status.value = await api.getStatus()
    if (status.value.debug_show_io_default && debugMode.value === false && !localStorage.getItem('run_debug_touched')) {
      debugMode.value = true
    }
  } catch { /* silent */ }
}

async function cancelRun() {
  cancelling.value = true
  try {
    const res = await api.cancelRun()
    ElMessage.warning(res.message || '已请求停止')
    await reload()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    cancelling.value = false
  }
}

watch(debugMode, () => {
  localStorage.setItem('run_debug_touched', '1')
})

onMounted(async () => {
  try {
    await reload()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
  pollTimer = setInterval(async () => {
    try {
      await loadStatus()
      if (isLive.value || detail.value.status === 'running') {
        await loadDetail()
      }
    } catch { /* silent */ }
  }, 2500)
})
onUnmounted(() => clearInterval(pollTimer))
</script>

<style scoped>
.back-link {
  background: none; border: none; color: var(--text-muted);
  font-size: 12px; padding: 0; margin-bottom: 6px; cursor: pointer;
}
.back-link:hover { color: var(--accent); }
.flow-link { color: var(--accent); font-size: 12px; font-weight: 600; }
.sel-stats {
  display: flex; flex-wrap: wrap; gap: 14px;
  font-size: 13px; color: var(--text-muted); margin-bottom: 10px;
}
.sel-stats b { font-weight: 700; }
.sel-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }
.mb { margin-bottom: 14px; }
.mr { margin-right: 8px; }
.dim { color: var(--text-muted); font-size: 13px; }
.stock-panel { margin-bottom: 12px; }
.stock-header {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 12px;
}
.stock-title { font-size: 15px; font-weight: 700; }
.confidence { color: var(--text-muted); font-size: 12px; }
.time { margin-left: 10px; color: var(--text-dim); font-size: 11px; }
.markdown { line-height: 1.7; overflow-x: auto; color: var(--text); font-size: 13px; }
.markdown :deep(table) { display: block; overflow-x: auto; max-width: 100%; border-collapse: collapse; }
.markdown :deep(th), .markdown :deep(td) {
  border: 1px solid var(--border); padding: 4px 8px;
}
.markdown :deep(p) { margin: 0.4em 0; }
.input-block { margin-top: 12px; }
.input-label { font-size: 11px; color: var(--text-dim); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.04em; }
.input-summary {
  background: var(--bg-elevated); border: 1px solid var(--border);
  padding: 12px; border-radius: 8px; font-size: 12px;
  white-space: pre-wrap; overflow-x: auto; max-height: 280px; overflow-y: auto;
  color: var(--text-muted); margin: 0; font-family: var(--mono);
}
.progress-panel { margin-bottom: 14px; }
.progress-row { display: flex; gap: 12px; align-items: baseline; margin-bottom: 8px; }
.progress-label { font-size: 12px; color: var(--text-muted); font-weight: 600; }
.progress-msg { font-size: 13px; }
.progress-meta { font-size: 11px; margin-top: 8px; }
.panel-h { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
.panel-title { font-weight: 700; font-size: 14px; }
.timeline { list-style: none; margin: 0; padding: 0; }
.tl-item {
  display: flex; gap: 12px; padding: 10px 0;
  border-left: 2px solid var(--border); margin-left: 6px; padding-left: 16px;
  position: relative;
}
.tl-item.running { border-left-color: var(--accent, #e6a23c); }
.tl-item.done { border-left-color: #67c23a; }
.tl-item.muted { opacity: 0.55; }
.tl-dot {
  position: absolute; left: -6px; top: 14px;
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--border);
}
.tl-item.running .tl-dot { background: #e6a23c; box-shadow: 0 0 0 3px rgba(230,162,60,0.25); }
.tl-item.done .tl-dot { background: #67c23a; }
.tl-item.muted .tl-dot { background: var(--text-dim); }
.tl-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tl-preview { font-size: 12px; margin-top: 4px; line-height: 1.5; }
.head-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

.idle-banner {
  font-size: 13px;
  color: var(--text-muted);
  padding: 10px 14px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm, 8px);
  background: rgba(255, 255, 255, 0.02);
}

.hold-fold {
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm, 8px);
  padding: 0 8px;
  background: rgba(255, 255, 255, 0.015);
}
.hold-fold :deep(.el-collapse-item__header) {
  height: auto;
  min-height: 48px;
  line-height: 1.4;
  background: transparent;
  color: var(--text-muted);
  border-bottom-color: transparent;
}
.hold-fold :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom: none;
}
.hold-fold :deep(.el-collapse-item__content) {
  padding-bottom: 8px;
}
.hold-fold-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  width: 100%;
  padding: 4px 0;
}
.hold-fold-label {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-muted);
}
.hold-count { font-variant-numeric: tabular-nums; }
.hold-fold-hint { font-size: 11px; font-weight: 400; }
.stock-hold {
  opacity: 0.88;
  border-style: dashed;
  background: transparent;
}
.stock-hold .stock-title {
  font-weight: 600;
  color: var(--text-muted);
  font-size: 14px;
}
.hold-action-tag { opacity: 0.85; }
.hold-reason :deep(.el-alert__title) {
  font-size: 12px;
  font-weight: 400;
}
.hold-agents { opacity: 0.9; }

:deep(.el-tabs__item) { color: var(--text-muted); }
:deep(.el-tabs__item.is-active) { color: var(--accent); }
:deep(.el-tabs__active-bar) { background: var(--accent); }
:deep(.el-tabs__nav-wrap::after) { background: var(--border); }
</style>

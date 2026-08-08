<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">研究</h1>
        <p class="page-sub">
          理论 → 规格 → 回测 → 晋升 · 存活者进入
          <router-link class="link" to="/strategies">策略对照台</router-link>
        </p>
      </div>
      <div class="head-actions">
        <el-button size="small" :loading="loadingList" @click="loadList">刷新列表</el-button>
        <el-button size="small" :loading="proposing" @click="doPropose('library')">规则库提议</el-button>
        <el-button size="small" :loading="proposing" @click="doPropose('improve')">改进弱假说</el-button>
        <el-button size="small" type="primary" plain :loading="baseBt" @click="runBaseline">
          基线回测
        </el-button>
      </div>
    </div>

    <!-- P4 规则库 + 网格 -->
    <section class="panel">
      <div class="panel-h">
        <div class="panel-title">规则库 · 网格批量（P4）</div>
        <span class="dim" style="font-size:12px">研究层预赛 · 不自动开户</span>
      </div>
      <p class="hint">
        预设因子组合 × TopN × 调仓 × 止损，一次拉面板批量回测；勾选存活者导入为假说后再人工晋升。
      </p>
      <div class="grid-controls">
        <el-select v-model="gridPresets" multiple collapse-tags placeholder="因子预设" style="min-width:200px">
          <el-option v-for="p in library.presets || []" :key="p.id" :label="p.label" :value="p.id" />
        </el-select>
        <el-select v-model="gridTopN" multiple collapse-tags placeholder="Top N" style="width:140px">
          <el-option v-for="n in (library.defaults?.top_n_options || [5,10,15])" :key="n" :label="'N'+n" :value="n" />
        </el-select>
        <el-checkbox v-model="gridEqual">含等权</el-checkbox>
        <el-button type="warning" :loading="gridRunning" @click="runGrid">运行网格</el-button>
        <el-button
          type="primary"
          plain
          :disabled="!selectedGridSpecs.length"
          :loading="gridImporting"
          @click="importGrid"
        >导入选中 ({{ selectedGridSpecs.length }})</el-button>
      </div>
      <el-table
        v-if="gridRows.length"
        :data="gridRows"
        size="small"
        stripe
        max-height="320"
        @selection-change="onGridSelect"
        class="mt"
      >
        <el-table-column type="selection" width="42" />
        <el-table-column prop="rank" label="#" width="50" />
        <el-table-column label="规格" min-width="160">
          <template #default="{ row }">{{ row.spec?.name }}</template>
        </el-table-column>
        <el-table-column label="夏普" width="80">
          <template #default="{ row }"><span class="mono">{{ row.sharpe?.toFixed?.(2) ?? row.sharpe }}</span></template>
        </el-table-column>
        <el-table-column label="超额夏普" width="90">
          <template #default="{ row }">
            <span class="mono" :class="(row.excess_sharpe_vs_anchor||0)>=0?'up':'down'">
              {{ row.excess_sharpe_vs_anchor ?? '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="建议" width="80">
          <template #default="{ row }">{{ suggestText(row.suggestion) || row.error || '—' }}</template>
        </el-table-column>
        <el-table-column label="样本" width="70">
          <template #default="{ row }">
            <el-tag v-if="row.metrics" size="small" :type="row.metrics.sample_ok?'success':'info'">
              {{ row.metrics.sample_ok ? '达标' : '不足' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="gridMeta" class="hint mono">
        {{ gridMeta.n_combos }} 组合 · 股池 {{ gridMeta.codes?.length }} · {{ gridMeta.start }} 起
        · 存活建议 {{ gridMeta.survivors?.length || 0 }}
      </p>
    </section>

    <!-- 新建假说 -->
    <section class="panel">
      <div class="panel-h">
        <div class="panel-title">① 输入投资理论</div>
      </div>
      <el-input
        v-model="theoryText"
        type="textarea"
        :rows="4"
        placeholder="例如：用短中期动量 + ROE 质量选股，每周调仓持有前 10 只，止损 8%。"
      />
      <div class="row-actions">
        <el-input v-model="theoryTitle" placeholder="标题（可选）" style="max-width:220px" />
        <el-button type="primary" :loading="creating" @click="createAndTranslate">
          创建并 AI 译规格
        </el-button>
      </div>
      <p class="hint">无 LLM 时自动启发式翻译。规格可改，确认后再回测。</p>
    </section>

    <!-- 当前编辑 -->
    <section v-if="current" class="panel">
      <div class="panel-h">
        <div class="panel-title">
          ② 规格 · #{{ current.id }} {{ current.title }}
        </div>
        <el-tag size="small" :type="statusType(current.status)">{{ statusText(current.status) }}</el-tag>
      </div>
      <p v-if="translateMeta" class="hint">
        翻译来源：{{ translateMeta.source === 'llm' ? 'LLM' : '启发式' }}
        <span v-if="translateMeta.errors?.length"> · {{ translateMeta.errors.join('; ') }}</span>
      </p>

      <div class="spec-grid">
        <el-form label-position="top" class="spec-form">
          <el-form-item label="名称">
            <el-input v-model="specEdit.name" />
          </el-form-item>
          <el-form-item label="模式">
            <el-select v-model="specEdit.mode" style="width:100%">
              <el-option label="因子截面" value="factor_cross_section" />
              <el-option label="池内等权" value="equal_weight" />
            </el-select>
          </el-form-item>
          <el-form-item label="持仓只数 Top N">
            <el-input-number v-model="specEdit.top_n" :min="1" :max="50" />
          </el-form-item>
          <el-form-item label="调仓">
            <el-select v-model="specEdit.rebalance" style="width:100%">
              <el-option label="周一" value="W-MON" />
              <el-option label="月末" value="ME" />
            </el-select>
          </el-form-item>
          <el-form-item label="因子（截面）">
            <el-select
              v-model="specEdit.factors"
              multiple
              collapse-tags
              style="width:100%"
              :disabled="specEdit.mode === 'equal_weight'"
            >
              <el-option v-for="f in FACTOR_OPTS" :key="f" :label="f" :value="f" />
            </el-select>
          </el-form-item>
          <el-form-item label="事件（可选）">
            <div class="event-row">
              <el-checkbox v-model="evStopOn">止损 %</el-checkbox>
              <el-input-number v-if="evStopOn" v-model="evStop" :min="1" :max="50" size="small" />
            </div>
            <div class="event-row">
              <el-checkbox v-model="evTakeOn">止盈 %</el-checkbox>
              <el-input-number v-if="evTakeOn" v-model="evTake" :min="1" :max="100" size="small" />
            </div>
            <div class="event-row">
              <el-checkbox v-model="evMaOn">跌破 N 日均线出清</el-checkbox>
              <el-input-number v-if="evMaOn" v-model="evMa" :min="5" :max="120" size="small" />
            </div>
          </el-form-item>
          <p v-if="specEdit.unsupported?.length" class="hint warn">
            无法映射：{{ specEdit.unsupported.join(' · ') }}
          </p>
        </el-form>
      </div>

      <div class="row-actions">
        <el-button :loading="savingSpec" @click="saveSpec(false)">保存规格</el-button>
        <el-button type="primary" plain :loading="savingSpec" @click="saveSpec(true)">确认规格</el-button>
        <el-button type="warning" :loading="runningBt" @click="runHypoBacktest">③ 回测</el-button>
        <el-button
          type="success"
          :disabled="!canPromote"
          :loading="promoting"
          @click="doPromote"
        >④ 晋升到策略</el-button>
        <el-button type="danger" plain :loading="discarding" @click="doDiscard">废弃</el-button>
        <el-button
          v-if="current.status === 'promoted'"
          plain
          :loading="retiring"
          @click="doRetire"
        >退役</el-button>
      </div>

      <!-- 回测结果 -->
      <div v-if="current.backtest?.result" class="bt-wrap">
        <div class="bt-banner" :class="current.suggestion">
          建议：
          <b>{{ suggestText(current.suggestion) }}</b>
          — {{ current.backtest.suggestion_reason || '' }}
        </div>
        <div class="bt-grid">
          <div class="bt-card">
            <div class="bt-name">本策略</div>
            <div class="bt-metrics" v-if="current.backtest.result.metrics">
              <div><span class="m-label">收益</span>
                <span class="mono" :class="retClass(current.backtest.result.metrics.total_return)">
                  {{ pct(current.backtest.result.metrics.total_return) }}
                </span>
              </div>
              <div><span class="m-label">夏普</span>
                <span class="mono">{{ current.backtest.result.metrics.sharpe }}</span>
              </div>
              <div><span class="m-label">回撤</span>
                <span class="mono down">{{ pct(current.backtest.result.metrics.max_drawdown) }}</span>
              </div>
              <div><span class="m-label">样本</span>
                <span class="mono">{{ current.backtest.result.metrics.sample_ok ? '达标' : '不足' }}
                  · {{ current.backtest.result.closed_trades }} 笔</span>
              </div>
            </div>
          </div>
          <div class="bt-card" v-if="current.backtest.anchor?.metrics">
            <div class="bt-name">锚 · 池内等权</div>
            <div class="bt-metrics">
              <div><span class="m-label">收益</span>
                <span class="mono" :class="retClass(current.backtest.anchor.metrics.total_return)">
                  {{ pct(current.backtest.anchor.metrics.total_return) }}
                </span>
              </div>
              <div><span class="m-label">夏普</span>
                <span class="mono">{{ current.backtest.anchor.metrics.sharpe }}</span>
              </div>
              <div><span class="m-label">回撤</span>
                <span class="mono down">{{ pct(current.backtest.anchor.metrics.max_drawdown) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 列表 -->
    <section class="panel">
      <div class="panel-h">
        <div class="panel-title">假说列表</div>
        <el-select v-model="statusFilter" size="small" clearable placeholder="全部状态" style="width:120px" @change="loadList">
          <el-option label="草稿" value="draft" />
          <el-option label="已确认" value="confirmed" />
          <el-option label="已回测" value="backtested" />
          <el-option label="有建议" value="suggested" />
          <el-option label="已晋升" value="promoted" />
          <el-option label="已废弃" value="discarded" />
          <el-option label="已退役" value="retired" />
        </el-select>
      </div>
      <el-table :data="items" stripe size="small" v-loading="loadingList" empty-text="暂无假说 — 在上方创建或网格导入">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="title" label="标题" min-width="140" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="建议" width="90">
          <template #default="{ row }">{{ suggestText(row.suggestion) || '—' }}</template>
        </el-table-column>
        <el-table-column prop="promoted_model_id" label="晋升账户" width="110" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="selectItem(row)">打开</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 基线回测 -->
    <section class="panel" v-if="baseline">
      <div class="panel-h">
        <div class="panel-title">基线历史回测（对照）</div>
      </div>
      <div class="bt-grid">
        <div class="bt-card" v-for="key in ['equal_weight', 'factor_weekly']" :key="key">
          <div class="bt-name">{{ key === 'equal_weight' ? '池内等权' : 'S2 周频' }}</div>
          <div class="bt-metrics" v-if="baseline[key]?.metrics">
            <div><span class="m-label">收益</span>
              <span class="mono" :class="retClass(baseline[key].metrics.total_return)">
                {{ pct(baseline[key].metrics.total_return) }}
              </span>
            </div>
            <div><span class="m-label">夏普</span>
              <span class="mono">{{ baseline[key].metrics.sharpe }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api/index.js'

const router = useRouter()

const FACTOR_OPTS = [
  'mom_short', 'mom_mid', 'low_vol', 'ep', 'bp', 'quality_roe',
  'rev_1m', 'low_turn', 'growth_roe',
]

const theoryText = ref('')
const theoryTitle = ref('')
const creating = ref(false)
const items = ref([])
const loadingList = ref(false)
const statusFilter = ref('')
const current = ref(null)
const translateMeta = ref(null)
const specEdit = reactive({
  name: '', mode: 'factor_cross_section', factors: [], top_n: 10,
  rebalance: 'W-MON', unsupported: [], events: [],
})
const evStopOn = ref(false)
const evStop = ref(8)
const evTakeOn = ref(false)
const evTake = ref(15)
const evMaOn = ref(false)
const evMa = ref(20)
const savingSpec = ref(false)
const runningBt = ref(false)
const promoting = ref(false)
const discarding = ref(false)
const retiring = ref(false)
const baseBt = ref(false)
const baseline = ref(null)
const library = ref({ presets: [], defaults: {} })
const gridPresets = ref([])
const gridTopN = ref([5, 10, 15])
const gridEqual = ref(true)
const gridRunning = ref(false)
const gridImporting = ref(false)
const gridRows = ref([])
const gridMeta = ref(null)
const selectedGridSpecs = ref([])
const proposing = ref(false)

const canPromote = computed(() => {
  const s = current.value?.status
  // 须已回测且未终态；草稿无 backtest 不可晋升
  return Boolean(
    s
    && !['draft', 'discarded', 'promoted', 'retired'].includes(s)
    && current.value?.backtest?.result,
  )
})

function statusType(s) {
  return ({
    draft: 'info', confirmed: '', backtested: 'warning',
    suggested: 'warning', promoted: 'success', discarded: 'danger', retired: 'info',
  })[s] || 'info'
}
function statusText(s) {
  return ({
    draft: '草稿', confirmed: '已确认', backtested: '已回测',
    suggested: '有建议', promoted: '已晋升', discarded: '已废弃', retired: '已退役',
  })[s] || s
}
function suggestText(s) {
  return ({ promote: '晋升', discard: '废弃', review: '复核' })[s] || ''
}
function pct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return `${(Number(v) * 100).toFixed(2)}%`
}
function retClass(v) {
  return Number(v) >= 0 ? 'up' : 'down'
}

function applySpecToForm(spec) {
  Object.assign(specEdit, {
    name: spec.name || '',
    mode: spec.mode || 'factor_cross_section',
    factors: [...(spec.factors || [])],
    top_n: spec.top_n || 10,
    rebalance: spec.rebalance || 'W-MON',
    unsupported: [...(spec.unsupported || [])],
    events: [...(spec.events || [])],
  })
  const evs = spec.events || []
  const stop = evs.find((e) => e.type === 'stop_loss_pct')
  const take = evs.find((e) => e.type === 'take_profit_pct')
  const ma = evs.find((e) => e.type === 'ma_exit')
  evStopOn.value = !!stop
  evStop.value = stop ? Math.round(Math.abs(stop.value) * 100) : 8
  evTakeOn.value = !!take
  evTake.value = take ? Math.round(Math.abs(take.value) * 100) : 15
  evMaOn.value = !!ma
  evMa.value = ma?.window || 20
}

function buildSpecFromForm() {
  const events = []
  if (evStopOn.value) events.push({ type: 'stop_loss_pct', value: -Math.abs(evStop.value) / 100 })
  if (evTakeOn.value) events.push({ type: 'take_profit_pct', value: Math.abs(evTake.value) / 100 })
  if (evMaOn.value) events.push({ type: 'ma_exit', window: evMa.value })
  return {
    name: specEdit.name,
    mode: specEdit.mode,
    universe: 'pool',
    factors: specEdit.mode === 'equal_weight' ? [] : [...specEdit.factors],
    top_n: specEdit.top_n,
    rebalance: specEdit.rebalance,
    weighting: 'equal',
    events,
    unsupported: specEdit.unsupported || [],
    notes: '',
  }
}

function selectItem(row) {
  current.value = row
  applySpecToForm(row.spec || {})
  translateMeta.value = null
}

async function loadList() {
  loadingList.value = true
  try {
    const data = await api.listHypotheses(statusFilter.value || undefined)
    items.value = data.items || []
    if (current.value) {
      const fresh = items.value.find((x) => x.id === current.value.id)
      if (fresh) selectItem(fresh)
    }
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loadingList.value = false
  }
}

async function createAndTranslate() {
  if (!theoryText.value.trim()) {
    ElMessage.warning('请填写理论文本')
    return
  }
  creating.value = true
  try {
    const h = await api.createHypothesis({
      theory_text: theoryText.value,
      title: theoryTitle.value,
    })
    const tr = await api.translateHypothesis(h.id)
    selectItem(tr)
    translateMeta.value = {
      source: tr.translate_source,
      errors: tr.translate_errors,
    }
    theoryText.value = ''
    theoryTitle.value = ''
    ElMessage.success(tr.translate_source === 'llm' ? 'LLM 已生成规格' : '已用启发式生成规格')
    await loadList()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    creating.value = false
  }
}

async function saveSpec(confirm) {
  if (!current.value) return
  savingSpec.value = true
  try {
    const res = await api.updateHypothesisSpec(current.value.id, buildSpecFromForm(), confirm)
    selectItem(res)
    ElMessage.success(confirm ? '规格已确认' : '规格已保存')
    await loadList()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    savingSpec.value = false
  }
}

async function runHypoBacktest() {
  if (!current.value) return
  runningBt.value = true
  try {
    // 先保存当前表单规格
    await api.updateHypothesisSpec(current.value.id, buildSpecFromForm(), true)
    const res = await api.backtestHypothesis(current.value.id, 3)
    selectItem(res)
    ElMessage.success(`回测完成 · 建议 ${suggestText(res.suggestion) || res.suggestion}`)
    await loadList()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    runningBt.value = false
  }
}

async function doPromote() {
  if (!current.value) return
  if (!current.value.backtest?.result) {
    ElMessage.warning('请先完成回测再晋升')
    return
  }
  const suggest = current.value.suggestion
  const warn = suggest === 'discard'
    ? '当前回测建议为「废弃」，仍要强行晋升开户吗？'
    : suggest === 'review'
      ? '样本不足或灰区建议「复核」，确认仍要晋升？'
      : '将开独立规则账户参赛（标签：研究晋升）。确认晋升？'
  try {
    await ElMessageBox.confirm(warn, '晋升', { type: 'warning' })
  } catch { return }
  promoting.value = true
  try {
    const res = await api.promoteHypothesis(current.value.id)
    selectItem(res)
    await loadList()
    ElMessage.success(`已晋升：${res.promoted_model_id}`)
    try {
      await ElMessageBox.confirm(
        `已开独立规则账户「${res.promoted_model_id}」。是否前往策略页查看并调仓？`,
        '晋升成功',
        { type: 'success', confirmButtonText: '去策略页', cancelButtonText: '留在研究' },
      )
      router.push('/strategies')
    } catch {
      /* 用户选择留在研究页 */
    }
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    promoting.value = false
  }
}

async function doDiscard() {
  if (!current.value) return
  let reason = ''
  try {
    const { value } = await ElMessageBox.prompt('废弃原因（可选）', '废弃假说', {
      confirmButtonText: '废弃',
      cancelButtonText: '取消',
      inputPlaceholder: '可选',
    })
    reason = value || ''
  } catch { return }
  discarding.value = true
  try {
    const res = await api.discardHypothesis(current.value.id, reason)
    selectItem(res)
    ElMessage.success('已废弃')
    await loadList()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    discarding.value = false
  }
}

async function doRetire() {
  if (!current.value) return
  retiring.value = true
  try {
    const res = await api.retireHypothesis(current.value.id)
    selectItem(res)
    ElMessage.success('已退役（账户停用）')
    await loadList()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    retiring.value = false
  }
}

async function runBaseline() {
  baseBt.value = true
  try {
    baseline.value = await api.runBacktest({ years: 3 })
    ElMessage.success('基线回测完成')
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    baseBt.value = false
  }
}

async function loadLibrary() {
  try {
    library.value = await api.researchLibrary()
    if (!gridPresets.value.length && library.value.presets?.length) {
      gridPresets.value = library.value.presets.slice(0, 4).map((p) => p.id)
    }
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function onGridSelect(rows) {
  selectedGridSpecs.value = (rows || []).map((r) => r.spec).filter(Boolean)
}

async function runGrid() {
  gridRunning.value = true
  try {
    const data = await api.researchGridRun({
      years: 3,
      factor_set_ids: gridPresets.value.length ? gridPresets.value : undefined,
      top_n_list: gridTopN.value.length ? gridTopN.value : undefined,
      include_equal_weight: gridEqual.value,
      stop_losses: [null, -0.08],
    })
    gridRows.value = data.rows || []
    gridMeta.value = data
    ElMessage.success(`网格完成 ${data.n_combos} 组合，建议晋升 ${data.survivors?.length || 0}`)
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    gridRunning.value = false
  }
}

async function importGrid() {
  if (!selectedGridSpecs.value.length) return
  gridImporting.value = true
  try {
    const res = await api.researchGridImport(selectedGridSpecs.value)
    ElMessage.success(`已导入 ${res.imported} 条假说`)
    await loadList()
    if (res.items?.length) selectItem(res.items[0])
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    gridImporting.value = false
  }
}

async function doPropose(mode) {
  proposing.value = true
  try {
    const res = await api.researchPropose({ count: 5, mode })
    ElMessage.success(`已生成 ${res.items?.length || 0} 条提议草稿`)
    await loadList()
    if (res.items?.length) selectItem(res.items[0])
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    proposing.value = false
  }
}

onMounted(() => {
  loadList()
  loadLibrary()
})
</script>

<style scoped>
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.link { color: var(--accent); }
.hint { font-size: 12px; color: var(--text-muted); margin: 8px 0 0; line-height: 1.5; }
.hint.warn { color: #e6a23c; }
.row-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; align-items: center; }
.grid-controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.mt { margin-top: 12px; }
.spec-grid { max-width: 560px; }
.event-row { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.bt-wrap { margin-top: 16px; }
.bt-banner {
  padding: 10px 12px; border-radius: 8px; margin-bottom: 12px;
  font-size: 13px; border: 1px solid var(--border); background: var(--panel-2);
}
.bt-banner.promote { border-color: rgba(103,194,58,0.4); }
.bt-banner.discard { border-color: rgba(245,108,108,0.4); }
.bt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.bt-card {
  background: var(--panel-2); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 14px;
}
.bt-name { font-weight: 600; margin-bottom: 10px; }
.bt-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px; }
.bt-metrics .m-label { display: block; font-size: 11px; color: var(--text-dim); }
@media (max-width: 700px) {
  .bt-grid { grid-template-columns: 1fr; }
}
</style>

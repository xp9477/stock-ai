<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">交易计划</h1>
        <p class="page-sub">候选计划 → 信息门禁 → 价格门禁 → 人工强确认 → 待执行票据</p>
      </div>
      <div class="head-actions">
        <el-select
          v-model="filterModel"
          size="small"
          clearable
          placeholder="全部模型"
          class="model-filter"
          @change="reload"
        >
          <el-option v-for="model in models" :key="model.id" :label="model.name" :value="model.id" />
        </el-select>
        <el-select
          v-model="filterStatus"
          size="small"
          clearable
          placeholder="全部计划状态"
          class="status-filter"
          @change="reload"
        >
          <el-option
            v-for="option in statusOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </div>

    <el-alert
      type="warning"
      title="这里不成交：人工批准只生成待执行票据"
      description="系统不会因 BUY / SELL 结论自动下单。请先刷新信息、校验当前价格，再核对官方公告并强确认；批准后仍需在独立执行环节处理。"
      :closable="false"
      show-icon
      class="flow-alert"
    />

    <el-tabs v-model="activeTab" class="flow-tabs">
      <el-tab-pane :label="`候选计划 ${plans.length}`" name="plans">
        <el-card>
          <template #header>
            <div class="card-head">
              <span>候选计划与门禁</span>
              <span class="dim">点击计划查看冻结事实摘要、门禁记录和人工操作</span>
            </div>
          </template>

          <div v-if="isMobile" v-loading="loading" class="mobile-list">
            <button
              v-for="plan in plans"
              :key="plan.id"
              type="button"
              class="plan-card"
              @click="openPlan(plan)"
            >
              <div class="plan-card-head">
                <div>
                  <b>{{ plan.name || plan.code }}</b>
                  <span class="mono dim code">{{ plan.code }}</span>
                </div>
                <el-tag :type="statusMeta(plan.status).type" size="small">
                  {{ statusMeta(plan.status).label }}
                </el-tag>
              </div>
              <div class="plan-card-grid">
                <span>方向 <b :class="plan.side === 'buy' ? 'up' : 'down'">{{ sideText(plan.side) }}</b></span>
                <span>目标仓位 <b>{{ formatPercent(plan.target_position_pct) }}</b></span>
                <span>最高买价 <b class="mono">{{ formatPrice(plan.max_buy_price) }}</b></span>
                <span>版本 <b class="mono">v{{ plan.lock_version }}</b></span>
              </div>
              <div class="plan-reason">{{ plan.status_reason || '等待下一步门禁检查' }}</div>
              <div class="plan-time">有效期至 {{ formatDateTime(plan.expires_at) }}</div>
            </button>
            <el-empty v-if="!loading && !plans.length" description="暂无候选计划" />
          </div>

          <template v-else>
            <el-table :data="plans" stripe v-loading="loading" @row-click="openPlan">
              <el-table-column label="标的" min-width="145">
                <template #default="{ row }">
                  <b>{{ row.name || row.code }}</b>
                  <span class="mono dim table-code">{{ row.code }}</span>
                </template>
              </el-table-column>
              <el-table-column label="方向" width="105">
                <template #default="{ row }">
                  <el-tag :type="row.side === 'buy' ? 'danger' : 'success'" size="small">
                    {{ sideText(row.side) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="状态" min-width="160">
                <template #default="{ row }">
                  <el-tag :type="statusMeta(row.status).type" size="small">
                    {{ statusMeta(row.status).label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="目标仓位" width="105">
                <template #default="{ row }">{{ formatPercent(row.target_position_pct) }}</template>
              </el-table-column>
              <el-table-column label="最高买价" width="105">
                <template #default="{ row }"><span class="mono">{{ formatPrice(row.max_buy_price) }}</span></template>
              </el-table-column>
              <el-table-column label="锁版本" width="85">
                <template #default="{ row }"><span class="mono">v{{ row.lock_version }}</span></template>
              </el-table-column>
              <el-table-column label="有效期" min-width="170">
                <template #default="{ row }">{{ formatDateTime(row.expires_at) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="90" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click.stop="openPlan(row)">查看</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!loading && !plans.length" description="暂无候选计划" />
          </template>
        </el-card>
      </el-tab-pane>

      <el-tab-pane :label="`待执行票据 ${intents.length}`" name="tickets">
        <el-card>
          <template #header>
            <div class="card-head">
              <span>票据就绪，尚未成交</span>
              <span class="dim">这里只展示人工批准后生成的执行意图</span>
            </div>
          </template>

          <div v-if="isMobile" v-loading="loading" class="mobile-list">
            <div v-for="intent in intents" :key="intent.id" class="ticket-card">
              <div class="plan-card-head">
                <div>
                  <b>{{ intent.plan?.name || intent.plan?.code || `计划 #${intent.plan_id}` }}</b>
                  <span v-if="intent.plan?.code" class="mono dim code">{{ intent.plan.code }}</span>
                </div>
                <el-tag type="warning" size="small">{{ intentStatusText(intent.status) }}</el-tag>
              </div>
              <div class="plan-card-grid">
                <span>方向 <b>{{ sideText(intent.plan?.side) }}</b></span>
                <span>批准数量 <b class="mono">{{ formatQty(intent.authorized_qty) }} 股</b></span>
                <span>批准金额 <b class="mono">{{ formatPrice(intent.authorized_notional) }} 元</b></span>
                <span>确认价 <b class="mono">{{ formatPrice(intent.approval_quote_price) }}</b></span>
                <span>票据版本 <b class="mono">v{{ intent.lock_version }}</b></span>
              </div>
              <div class="plan-time">批准于 {{ formatDateTime(intent.approved_at) }} · 到期 {{ formatDateTime(intent.expires_at) }}</div>
            </div>
            <el-empty v-if="!loading && !intents.length" description="暂无待执行票据" />
          </div>

          <template v-else>
            <el-table :data="intents" stripe v-loading="loading">
              <el-table-column label="票据" width="90">
                <template #default="{ row }"><span class="mono">#{{ row.id }}</span></template>
              </el-table-column>
              <el-table-column label="标的" min-width="145">
                <template #default="{ row }">
                  <b>{{ row.plan?.name || row.plan?.code || `计划 #${row.plan_id}` }}</b>
                  <span v-if="row.plan?.code" class="mono dim table-code">{{ row.plan.code }}</span>
                </template>
              </el-table-column>
              <el-table-column label="方向" width="105">
                <template #default="{ row }">{{ sideText(row.plan?.side) }}</template>
              </el-table-column>
              <el-table-column label="状态" min-width="180">
                <template #default="{ row }">
                  <el-tag type="warning" size="small">{{ intentStatusText(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="批准时价格" width="115">
                <template #default="{ row }"><span class="mono">{{ formatPrice(row.approval_quote_price) }}</span></template>
              </el-table-column>
              <el-table-column label="批准数量" width="110">
                <template #default="{ row }"><span class="mono">{{ formatQty(row.authorized_qty) }} 股</span></template>
              </el-table-column>
              <el-table-column label="批准金额" width="125">
                <template #default="{ row }"><span class="mono">{{ formatPrice(row.authorized_notional) }} 元</span></template>
              </el-table-column>
              <el-table-column label="批准时间" min-width="170">
                <template #default="{ row }">{{ formatDateTime(row.approved_at) }}</template>
              </el-table-column>
              <el-table-column label="到期时间" min-width="170">
                <template #default="{ row }">{{ formatDateTime(row.expires_at) }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!loading && !intents.length" description="暂无待执行票据" />
          </template>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-drawer
      v-model="drawerOpen"
      :size="isMobile ? '100%' : '680px'"
      append-to-body
      destroy-on-close
      class="plan-drawer"
    >
      <template #header>
        <div v-if="selectedPlan" class="drawer-title">
          <div>
            <b>{{ selectedPlan.name || selectedPlan.code }}</b>
            <span class="mono dim">{{ selectedPlan.code }} · 计划 #{{ selectedPlan.id }}</span>
          </div>
          <el-tag :type="statusMeta(selectedPlan.status).type">
            {{ statusMeta(selectedPlan.status).label }}
          </el-tag>
        </div>
      </template>

      <div v-if="selectedPlan" v-loading="detailLoading" class="drawer-body">
        <el-alert
          v-if="selectedPlan.status === 'ticket_ready' || selectedIntent"
          type="success"
          title="票据已就绪，尚未成交"
          description="人工批准时已重新校验资金、持仓名额、未过期票据占用和 Canary；本页面没有向券商发送订单，也没有产生持仓或成交。"
          :closable="false"
          show-icon
          class="section-gap"
        />

        <section v-if="selectedIntent" class="detail-section">
          <h3>本次冻结授权</h3>
          <div class="detail-grid">
            <div><span>批准数量</span><b class="mono">{{ formatQty(selectedIntent.authorized_qty) }} 股</b></div>
            <div><span>批准金额</span><b class="mono">{{ formatPrice(selectedIntent.authorized_notional) }} 元</b></div>
            <div><span>估算费用</span><b class="mono">{{ formatPrice(selectedIntent.estimated_fee) }} 元</b></div>
            <div><span>批准后目标仓位</span><b>{{ formatPercent(selectedIntent.authorized_target_position_pct) }}</b></div>
            <div><span>确认价</span><b class="mono">{{ formatPrice(selectedIntent.approval_quote_price) }}</b></div>
            <div><span>票据到期</span><b>{{ formatDateTime(selectedIntent.expires_at) }}</b></div>
          </div>
        </section>

        <section class="detail-section">
          <h3>条件计划</h3>
          <div class="detail-grid">
            <div><span>方向</span><b>{{ sideText(selectedPlan.side) }}</b></div>
            <div><span>目标仓位</span><b>{{ formatPercent(selectedPlan.target_position_pct) }}</b></div>
            <div><span>参考价格</span><b class="mono">{{ formatPrice(selectedPlan.reference_price) }}</b></div>
            <div><span>最高买价</span><b class="mono">{{ formatPrice(selectedPlan.max_buy_price) }}</b></div>
            <div><span>置信度</span><b>{{ formatConfidence(selectedPlan.confidence) }}</b></div>
            <div><span>当前锁版本</span><b class="mono">v{{ selectedPlan.lock_version }}</b></div>
            <div class="wide"><span>事实截止</span><b>{{ formatDateTime(selectedPlan.data_cutoff_at) }}</b></div>
            <div class="wide"><span>计划有效期</span><b>{{ formatDateTime(selectedPlan.valid_from_at) }} — {{ formatDateTime(selectedPlan.expires_at) }}</b></div>
          </div>
          <div v-if="selectedPlan.status_reason" class="status-reason">
            {{ selectedPlan.status_reason }}
          </div>
        </section>

        <section class="detail-section">
          <h3>冻结判断</h3>
          <div class="text-block">
            <span>核心论点</span>
            <p>{{ selectedPlan.thesis || '—' }}</p>
          </div>
          <div class="text-block">
            <span>失效条件</span>
            <pre>{{ formatConditions(selectedPlan.invalidation_conditions) }}</pre>
          </div>
          <div class="hash-row">
            <span>事实表哈希</span>
            <code>{{ selectedPlan.factsheet_hash || '—' }}</code>
          </div>
        </section>

        <section class="detail-section">
          <div class="section-head">
            <h3>门禁记录</h3>
            <span class="dim">后发生的检查优先</span>
          </div>
          <div v-if="selectedPlan.gates?.length" class="gate-list">
            <article v-for="gate in reversedGates" :key="gate.id" class="gate-card">
              <div class="gate-head">
                <b>{{ gateTypeText(gate.gate_type) }}</b>
                <el-tag :type="gateOutcomeMeta(gate.outcome).type" size="small">
                  {{ gateOutcomeMeta(gate.outcome).label }}
                </el-tag>
                <time>{{ formatDateTime(gate.checked_at) }}</time>
              </div>
              <p>{{ gate.reason || gate.reason_code || '无说明' }}</p>
              <div v-if="gate.quote_price != null" class="gate-metrics mono">
                <span>报价 {{ formatPrice(gate.quote_price) }}</span>
                <span v-if="gate.opening_gap_pct != null">开盘缺口 {{ formatPercent(gate.opening_gap_pct) }}</span>
                <span v-if="gate.dynamic_gap_threshold_pct != null">动态阈值 {{ formatPercent(gate.dynamic_gap_threshold_pct) }}</span>
                <span v-if="gate.signal_price_deviation_pct != null">信号偏离 {{ formatPercent(gate.signal_price_deviation_pct) }}</span>
              </div>
            </article>
          </div>
          <el-empty v-else description="尚无门禁记录" :image-size="64" />
        </section>

        <section v-if="isPlanMutable(selectedPlan)" class="detail-section action-section">
          <h3>人工门禁与强确认</h3>
          <div class="official-confirm">
            <el-checkbox v-model="officialConfirmed">
              我已在交易所或公司官方披露渠道核对公告与重大新闻
            </el-checkbox>
            <p>RSS 和模型摘要仅作补充；未勾选时不能刷新信息门禁，也不能批准。</p>
          </div>

          <div class="action-flow">
            <div class="action-row">
              <div>
                <b>1. 刷新信息</b>
                <span>比较分析截止后的增量信息；发现新个股新闻时，原计划须重新分析。</span>
              </div>
              <el-button
                type="primary"
                plain
                :loading="busyAction === 'information'"
                :disabled="Boolean(busyAction) || !officialConfirmed"
                @click="refreshInformation"
              >刷新信息与公告确认</el-button>
            </div>
            <div class="action-row">
              <div>
                <b>2. 校验价格</b>
                <span>读取新报价，检查有效期、报价时效、开盘缺口、价格偏离和最高买价。</span>
              </div>
              <el-button
                type="primary"
                plain
                :loading="busyAction === 'price'"
                :disabled="Boolean(busyAction) || !canValidatePrice"
                @click="validatePrice"
              >刷新并校验价格</el-button>
            </div>
            <div class="action-row strong-row">
              <div>
                <b>3. 人工强确认</b>
                <span>提交当前锁版本和本次审批的唯一幂等键；后端会再次复核两道门禁。</span>
              </div>
              <el-button
                type="danger"
                :loading="busyAction === 'approve'"
                :disabled="Boolean(busyAction) || !canApprove"
                @click="approvePlan"
              >确认生成待执行票据</el-button>
            </div>
          </div>

          <div class="reject-row">
            <span>不接受该判断或条件时，可留下原因并拒绝候选计划。</span>
            <el-button
              type="danger"
              plain
              :loading="busyAction === 'reject'"
              :disabled="Boolean(busyAction)"
              @click="rejectPlan"
            >拒绝计划</el-button>
          </div>
        </section>

        <el-alert
          v-else-if="selectedPlan.status !== 'ticket_ready'"
          :type="selectedPlan.status === 'rejected' ? 'info' : 'warning'"
          :title="statusMeta(selectedPlan.status).label"
          :description="selectedPlan.status_reason || '该计划已不能继续操作。'"
          :closable="false"
          class="section-gap"
        />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'
import {
  buildApprovalPayload,
  canApprovePlan,
  canValidatePlanPrice,
  createApprovalKeyStore,
  executionIntentLabel,
  isPlanMutable,
} from '../utils/tradePlanContracts.js'

const { isMobile } = useIsMobile()
const activeTab = ref('plans')
const plans = ref([])
const intents = ref([])
const models = ref([])
const filterModel = ref(null)
const filterStatus = ref('')
const loading = ref(false)
const detailLoading = ref(false)
const drawerOpen = ref(false)
const selectedPlan = ref(null)
const selectedIntent = ref(null)
const officialConfirmed = ref(false)
const busyAction = ref('')

const STATUS_META = {
  candidate: { label: '候选计划', type: 'info' },
  preopen_validated: { label: '信息门禁通过', type: 'primary' },
  awaiting_approval: { label: '待人工确认', type: 'warning' },
  blocked_information: { label: '信息门禁阻断', type: 'danger' },
  blocked_capital: { label: '资金门禁阻断', type: 'danger' },
  review_required: { label: '需重新分析', type: 'danger' },
  invalidated_price: { label: '价格条件失效', type: 'danger' },
  invalidated_condition: { label: '判断条件失效', type: 'danger' },
  expired: { label: '已过期', type: 'info' },
  rejected: { label: '已拒绝', type: 'info' },
  superseded: { label: '已被替代', type: 'info' },
  ticket_ready: { label: '票据就绪 · 尚未成交', type: 'success' },
  executed: { label: '已执行', type: 'success' },
  cancelled: { label: '已取消', type: 'info' },
}

const statusOptions = Object.entries(STATUS_META).map(([value, meta]) => ({ value, label: meta.label }))

const GATE_OUTCOMES = {
  pass: { label: '通过', type: 'success' },
  blocked_information: { label: '信息阻断', type: 'danger' },
  review_required: { label: '需重新分析', type: 'danger' },
  invalidated_price: { label: '价格失效', type: 'danger' },
  blocked_quote: { label: '报价不可用', type: 'danger' },
  blocked_capital: { label: '资金阻断', type: 'danger' },
  expired: { label: '已过期', type: 'info' },
}

const reversedGates = computed(() => [...(selectedPlan.value?.gates || [])].reverse())
const canValidatePrice = computed(() => canValidatePlanPrice(selectedPlan.value))
const canApprove = computed(() => canApprovePlan(selectedPlan.value, officialConfirmed.value))

function statusMeta(status) {
  return STATUS_META[status] || { label: status || '未知', type: 'info' }
}

function gateOutcomeMeta(outcome) {
  return GATE_OUTCOMES[outcome] || { label: outcome || '未知', type: 'info' }
}

function gateTypeText(type) {
  return {
    preopen_information: '信息门禁',
    pretrade_quote: '价格门禁',
    pretrade_capital: '资金与 Canary 门禁',
  }[type] || type || '门禁'
}

function sideText(side) {
  if (side === 'buy') return '候选买入'
  if (side === 'sell') return '候选卖出'
  return side || '—'
}

function intentStatusText(status) {
  return status === 'ticket_ready' ? executionIntentLabel(status) : (statusMeta(status).label || status)
}

function formatPrice(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : '—'
}

function formatQty(value) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)).toLocaleString('zh-CN') : '—'
}

function formatPercent(value) {
  const number = Number(value)
  return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : '—'
}

function formatConfidence(value) {
  const number = Number(value)
  return Number.isFinite(number) ? `${(number * 100).toFixed(0)}%` : '—'
}

function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function formatConditions(value) {
  if (!value) return '—'
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function randomToken() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function oneShotKey(prefix, plan) {
  return `${prefix}-plan-${plan.id}-v${plan.lock_version}-${randomToken()}`
}

const getApprovalKey = createApprovalKeyStore(randomToken)

function planParams() {
  const params = {}
  if (filterModel.value != null) params.model_pk = filterModel.value
  if (filterStatus.value) params.status = filterStatus.value
  return params
}

function intentParams() {
  const params = { status: 'ticket_ready' }
  if (filterModel.value != null) params.model_pk = filterModel.value
  return params
}

async function reload() {
  loading.value = true
  try {
    const [planResponse, intentResponse, modelResponse] = await Promise.all([
      api.getTradePlans(planParams()),
      api.getExecutionIntents(intentParams()),
      api.getModels().catch(() => []),
    ])
    plans.value = Array.isArray(planResponse?.items) ? planResponse.items : []
    intents.value = Array.isArray(intentResponse?.items) ? intentResponse.items : []
    models.value = Array.isArray(modelResponse) ? modelResponse : []
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

async function openPlan(plan) {
  selectedPlan.value = plan
  selectedIntent.value = intents.value.find((item) => item.plan_id === plan.id) || null
  officialConfirmed.value = false
  drawerOpen.value = true
  await reloadSelectedPlan()
}

async function reloadSelectedPlan() {
  if (!selectedPlan.value?.id) return
  detailLoading.value = true
  try {
    const detail = await api.getTradePlan(selectedPlan.value.id)
    selectedPlan.value = detail
    selectedIntent.value = intents.value.find((item) => item.plan_id === detail.id) || selectedIntent.value
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    detailLoading.value = false
  }
}

async function refreshListsSilently() {
  try {
    const [planResponse, intentResponse] = await Promise.all([
      api.getTradePlans(planParams()),
      api.getExecutionIntents(intentParams()),
    ])
    plans.value = Array.isArray(planResponse?.items) ? planResponse.items : []
    intents.value = Array.isArray(intentResponse?.items) ? intentResponse.items : []
    if (selectedPlan.value) {
      selectedIntent.value = intents.value.find((item) => item.plan_id === selectedPlan.value.id) || selectedIntent.value
    }
  } catch {
    // 操作结果已明确展示；列表可由用户再次刷新，避免覆盖成功提示。
  }
}

async function refreshInformation() {
  if (!selectedPlan.value || !officialConfirmed.value) return
  busyAction.value = 'information'
  try {
    const plan = selectedPlan.value
    const response = await api.refreshTradePlanInformation(plan.id, {
      expected_lock_version: plan.lock_version,
      human_official_confirmed: true,
      idempotency_key: oneShotKey('information', plan),
    })
    selectedPlan.value = { ...response.plan, gates: selectedPlan.value.gates }
    ElMessage[response.gate?.outcome === 'pass' ? 'success' : 'warning'](
      response.gate?.reason || '信息门禁已刷新',
    )
    await Promise.all([reloadSelectedPlan(), refreshListsSilently()])
  } catch (error) {
    await handleActionError(error)
  } finally {
    busyAction.value = ''
  }
}

async function validatePrice() {
  if (!selectedPlan.value || !canValidatePrice.value) return
  busyAction.value = 'price'
  try {
    const plan = selectedPlan.value
    const response = await api.validateTradePlanPrice(plan.id, {
      expected_lock_version: plan.lock_version,
      idempotency_key: oneShotKey('price', plan),
    })
    selectedPlan.value = { ...response.plan, gates: selectedPlan.value.gates }
    const quoteText = response.quote?.price != null ? `，最新报价 ${formatPrice(response.quote.price)}` : ''
    ElMessage[response.gate?.outcome === 'pass' ? 'success' : 'warning'](
      `${response.gate?.reason || '价格门禁已刷新'}${quoteText}`,
    )
    await Promise.all([reloadSelectedPlan(), refreshListsSilently()])
  } catch (error) {
    await handleActionError(error)
  } finally {
    busyAction.value = ''
  }
}

async function approvePlan() {
  if (!selectedPlan.value || !canApprove.value) return
  try {
    await ElMessageBox.confirm(
      '我确认已在交易所或公司官方披露渠道核对公告与重大新闻，并接受系统用当前报价重新执行信息与价格门禁。确认后只生成待执行票据，尚未成交。',
      '人工强确认',
      {
        type: 'warning',
        confirmButtonText: '已核对，生成票据',
        cancelButtonText: '返回复核',
        closeOnClickModal: false,
      },
    )
  } catch {
    return
  }

  busyAction.value = 'approve'
  try {
    const plan = selectedPlan.value
    const response = await api.approveTradePlan(
      plan.id,
      buildApprovalPayload(plan, getApprovalKey(plan)),
    )
    selectedPlan.value = { ...response.plan, gates: selectedPlan.value.gates }
    selectedIntent.value = response.intent
    activeTab.value = 'tickets'
    ElMessage.success('票据已就绪，尚未成交')
    await Promise.all([reloadSelectedPlan(), refreshListsSilently()])
  } catch (error) {
    await handleActionError(error)
  } finally {
    busyAction.value = ''
  }
}

async function rejectPlan() {
  if (!selectedPlan.value) return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      '请说明拒绝原因。拒绝后该候选计划不能继续生成票据。',
      '拒绝候选计划',
      {
        type: 'warning',
        confirmButtonText: '确认拒绝',
        cancelButtonText: '取消',
        inputPlaceholder: '例如：判断依据不足或失效条件不清晰',
        inputValidator: (value) => Boolean(value?.trim()) || '请填写拒绝原因',
      },
    )
    reason = result.value.trim().slice(0, 500)
  } catch {
    return
  }

  busyAction.value = 'reject'
  try {
    const plan = selectedPlan.value
    selectedPlan.value = await api.rejectTradePlan(plan.id, {
      expected_lock_version: plan.lock_version,
      reason,
    })
    officialConfirmed.value = false
    ElMessage.success('候选计划已拒绝')
    await Promise.all([reloadSelectedPlan(), refreshListsSilently()])
  } catch (error) {
    await handleActionError(error)
  } finally {
    busyAction.value = ''
  }
}

async function handleActionError(error) {
  if (error.status) {
    ElMessage.error(`${error.message}（状态：${error.status}）`)
  } else {
    ElMessage.error(error.message)
  }
  if (error.httpStatus === 409 || error.status) await reloadSelectedPlan()
}

onMounted(reload)
</script>

<style scoped>
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.model-filter { width: 170px; }
.status-filter { width: 180px; }
.flow-alert { margin-bottom: 2px; }
.flow-tabs { min-width: 0; }
.card-head, .section-head, .drawer-title, .gate-head, .plan-card-head {
  display: flex; justify-content: space-between; align-items: center; gap: 10px;
}
.card-head .dim { font-size: 12px; font-weight: 400; }
.mobile-list { min-height: 80px; }
.plan-card, .ticket-card {
  display: block; width: 100%; padding: 13px 4px; text-align: left;
  border: 0; border-bottom: 1px solid var(--border); background: transparent; color: var(--text);
}
button.plan-card { cursor: pointer; }
button.plan-card:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.plan-card:last-child, .ticket-card:last-child { border-bottom: 0; }
.plan-card-head > div { min-width: 0; }
.code, .table-code { margin-left: 7px; font-size: 11px; }
.plan-card-grid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 12px;
  margin-top: 10px; font-size: 12px; color: var(--text-muted);
}
.plan-card-grid b { color: var(--text); }
.plan-reason { margin-top: 9px; font-size: 12px; line-height: 1.5; color: var(--text-muted); }
.plan-time { margin-top: 5px; font-size: 11px; color: var(--text-dim); }
.drawer-title { width: 100%; padding-right: 8px; }
.drawer-title > div { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.drawer-body { min-height: 240px; padding-bottom: 24px; }
.detail-section {
  padding: 16px 0; border-bottom: 1px solid var(--border);
}
.detail-section:first-of-type { padding-top: 0; }
.detail-section h3 { margin: 0 0 12px; font-size: 14px; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.detail-grid > div { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.detail-grid .wide { grid-column: 1 / -1; }
.detail-grid span, .text-block > span, .hash-row > span { font-size: 11px; color: var(--text-dim); }
.detail-grid b { font-size: 13px; overflow-wrap: anywhere; }
.status-reason {
  margin-top: 12px; padding: 10px 12px; border-radius: 7px;
  color: var(--text-muted); background: var(--bg-elevated); font-size: 12px; line-height: 1.6;
}
.text-block { margin-bottom: 12px; }
.text-block p { margin: 5px 0 0; line-height: 1.7; font-size: 13px; }
.text-block pre {
  margin: 5px 0 0; padding: 10px; max-height: 230px; overflow: auto;
  white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid var(--border);
  border-radius: 7px; background: var(--bg-elevated); color: var(--text-muted); font: 12px/1.6 var(--mono);
}
.hash-row { display: flex; flex-direction: column; gap: 5px; }
.hash-row code { overflow-wrap: anywhere; color: var(--text-muted); font-size: 11px; }
.gate-list { display: flex; flex-direction: column; gap: 9px; }
.gate-card { padding: 11px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-elevated); }
.gate-head { justify-content: flex-start; flex-wrap: wrap; }
.gate-head time { margin-left: auto; font-size: 11px; color: var(--text-dim); }
.gate-card p { margin: 7px 0 0; color: var(--text-muted); font-size: 12px; line-height: 1.55; }
.gate-metrics { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 7px; font-size: 11px; color: var(--text-dim); }
.action-section { border-bottom: 0; }
.official-confirm {
  padding: 12px; border: 1px solid rgba(230, 162, 60, 0.45); border-radius: 8px;
  background: rgba(230, 162, 60, 0.08);
}
.official-confirm :deep(.el-checkbox) { height: auto; align-items: flex-start; white-space: normal; }
.official-confirm :deep(.el-checkbox__label) { white-space: normal; line-height: 1.5; font-weight: 600; color: var(--text); }
.official-confirm p { margin: 6px 0 0 24px; color: var(--text-muted); font-size: 11px; line-height: 1.5; }
.action-flow { margin-top: 12px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.action-row {
  display: flex; justify-content: space-between; align-items: center; gap: 14px;
  padding: 12px; border-bottom: 1px solid var(--border);
}
.action-row:last-child { border-bottom: 0; }
.action-row > div { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.action-row b { font-size: 13px; }
.action-row span { color: var(--text-muted); font-size: 11px; line-height: 1.5; }
.strong-row { background: rgba(245, 108, 108, 0.05); }
.reject-row {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  margin-top: 14px; color: var(--text-muted); font-size: 11px;
}
.section-gap { margin-top: 14px; }
.mono { font-family: var(--mono); }
.dim { color: var(--text-muted); }

@media (max-width: 768px) {
  .head-actions { width: 100%; }
  .model-filter, .status-filter { flex: 1 1 145px; width: auto; }
  .card-head { align-items: flex-start; flex-direction: column; }
  .detail-grid { grid-template-columns: 1fr 1fr; }
  .action-row { align-items: stretch; flex-direction: column; }
  .action-row .el-button { width: 100%; margin-left: 0; }
  .reject-row { align-items: stretch; flex-direction: column; }
  .reject-row .el-button { width: 100%; }
  .gate-head time { width: 100%; margin-left: 0; }
}
</style>

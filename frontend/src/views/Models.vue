<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">模型与策略</h1>
        <p class="page-sub">LLM 只做独立判断 · 策略组合才拥有授权资金与持仓</p>
      </div>
      <div class="head-actions" role="toolbar" aria-label="账户操作">
        <el-button size="small" type="primary" @click="openLlmDialog">添加 LLM</el-button>
        <el-button
          size="small"
          type="warning"
          :disabled="hasEnsemble"
          :title="hasEnsemble ? (hasOfficial ? '只允许一个官方策略账户，请编辑现有 ensemble' : '旧库存在多个候选 ensemble；请停用到只剩一个以完成官方身份迁移') : ''"
          @click="openEnsembleDialog"
        >{{ hasOfficial ? '官方策略已建立' : (hasEnsemble ? '官方策略身份冲突' : '建立官方策略') }}</el-button>
      </div>
    </div>

    <section class="panel" aria-label="账户列表">
      <p v-if="!loading && !models.length" class="empty" role="status">
        暂无模型。先添加至少两个独立 LLM，再建立一个条件计划策略。
      </p>

      <div v-else-if="isMobile">
        <div v-for="row in models" :key="row.id" class="m-card">
          <div class="m-card-top">
            <span class="m-card-title" translate="no">{{ row.name }}</span>
            <span class="lane-pill" :class="laneClass(row)">{{ laneLabel(row) }}</span>
            <el-switch
              class="m-switch"
              :model-value="row.enabled"
              :disabled="row.type === 'rule'"
              :aria-label="`启用 ${row.name}`"
              @change="(v) => toggle(row, v)"
            />
          </div>
          <div v-if="row.type === 'llm'" class="meta mono" translate="no">{{ row.model_id }}</div>
          <div v-else-if="row.type === 'ensemble'" class="meta">
            成员：{{ memberNames(row) }}
          </div>
          <div v-else class="meta mono">历史规则证据 {{ row.model_id }}</div>
          <div v-if="row.type === 'llm'" class="advisor-note">独立判断顾问 · 无资金账户</div>
          <div v-else class="m-row">
            <span>
              收益
              <span class="mono" :class="row.pnl_pct >= 0 ? 'up' : 'down'">
                {{ formatPnl(row.pnl_pct) }}
              </span>
            </span>
            <div class="m-actions">
              <el-button v-if="row.is_official_strategy" size="small" type="primary" plain @click="openPositions(row)">策略持仓</el-button>
              <el-popconfirm
                v-if="row.type !== 'rule'"
                title="无证据时删除；已有证据时仅停用并保留历史。继续？"
                confirm-button-text="继续"
                cancel-button-text="取消"
                @confirm="remove(row.id)"
              >
                <template #reference>
                  <el-button size="small" type="danger" plain :aria-label="`删除 ${row.name}`">
                    删除
                  </el-button>
                </template>
              </el-popconfirm>
            </div>
          </div>
        </div>
      </div>

      <el-table v-else :data="models" stripe empty-text="暂无账户">
        <el-table-column prop="name" label="名称" width="160">
          <template #default="{ row }">
            <span translate="no">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="职责" width="110">
          <template #default="{ row }">
            <span class="lane-pill" :class="laneClass(row)">{{ laneLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="标识" min-width="180">
          <template #default="{ row }">
            <span v-if="row.type === 'llm'" class="mono" translate="no">{{ row.model_id }}</span>
            <span v-else-if="row.type === 'rule'" class="mono" translate="no">{{ row.model_id }}</span>
            <span v-else>{{ memberNames(row) || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="策略收益率" width="120">
          <template #default="{ row }">
            <span class="mono" :class="row.pnl_pct >= 0 ? 'up' : 'down'">
              {{ row.is_official_strategy ? formatPnl(row.pnl_pct) : '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              :disabled="row.type === 'rule'"
              :aria-label="`启用 ${row.name}`"
              @change="(v) => toggle(row, v)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button v-if="row.is_official_strategy" size="small" type="primary" plain @click="openPositions(row)">策略持仓</el-button>
            <el-popconfirm
              v-if="row.type !== 'rule'"
              title="无证据时删除；已有证据时仅停用并保留历史。继续？"
              confirm-button-text="继续"
              cancel-button-text="取消"
              @confirm="remove(row.id)"
            >
              <template #reference>
                <el-button size="small" type="danger" plain :aria-label="`删除 ${row.name}`">
                  删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-drawer
      v-model="posDrawer"
      :title="posModel ? `持仓 · ${posModel.name}` : '持仓'"
      :size="isMobile ? '92%' : '480px'"
      destroy-on-close
    >
      <div v-if="posLoading" class="empty">加载中…</div>
      <template v-else-if="posData.total_equity != null">
        <div class="pos-summary">
          <div>
            <div class="m-label">总资产</div>
            <div class="mono m-value">{{ fmtMoney(posData.total_equity) }}</div>
          </div>
          <div>
            <div class="m-label">现金</div>
            <div class="mono m-value">{{ fmtMoney(posData.cash) }}</div>
          </div>
          <div>
            <div class="m-label">收益率</div>
            <div class="mono m-value" :class="posData.total_pnl_pct >= 0 ? 'up' : 'down'">
              {{ formatPnl(posData.total_pnl_pct) }}
            </div>
          </div>
        </div>
        <div v-if="(posData.positions || []).length" class="pos-list">
          <div v-for="p in posData.positions" :key="p.code" class="pos-item">
            <div class="pos-item-h">
              <b>{{ p.name }}</b>
              <span class="mono dim">{{ p.code }}</span>
              <span class="mono" :class="p.pnl >= 0 ? 'up' : 'down'">{{ p.pnl_pct?.toFixed(2) }}%</span>
            </div>
            <div class="pos-item-grid mono">
              <span>持仓 {{ p.total_qty }} / 可卖 {{ p.available_qty }}</span>
              <span>成本 {{ p.avg_cost }} · 现价 {{ p.price }}</span>
              <span>市值 {{ fmtMoney(p.market_value) }}</span>
            </div>
            <p v-if="p.buy_reason" class="pos-reason">{{ p.buy_reason }}</p>
          </div>
        </div>
        <el-empty v-else description="该账户当前空仓" :image-size="56" />
      </template>
    </el-drawer>

    <p class="hint foot">
      LLM 顾问不拥有账户；最终交易与风险复核使用不同成员模型。已有前瞻证据的模型只能停用，不能抹除历史。LLM 共用
      <router-link to="/settings?tab=secrets">设置 → 密钥</router-link>
      中的 Base URL / API Key。
    </p>

    <el-dialog
      v-model="llmDialog"
      title="添加 LLM"
      :width="isMobile ? '92%' : '420px'"
      destroy-on-close
      @closed="llmForm = { name: '', model_id: '' }"
    >
      <el-form label-position="top" @submit.prevent="createLlm">
        <el-form-item label="展示名">
          <el-input
            v-model="llmForm.name"
            name="model_name"
            placeholder="如 GPT 5.6 Sol High…"
            autocomplete="off"
            spellcheck="false"
          />
        </el-form-item>
        <el-form-item label="API 模型名">
          <el-input
            v-model="llmForm.model_id"
            name="model_id"
            placeholder="如 gpt-5.6-sol…"
            autocomplete="off"
            spellcheck="false"
            translate="no"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="llmDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createLlm">添加</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="ensembleDialog"
      title="新建策略组合"
      :width="isMobile ? '92%' : '420px'"
      destroy-on-close
      @closed="ensembleForm = { name: '', members: [] }"
    >
      <el-form label-position="top" @submit.prevent="createEnsemble">
        <el-form-item label="组合名">
          <el-input
            v-model="ensembleForm.name"
            name="ensemble_name"
            placeholder="如 三模合议…"
            autocomplete="off"
            spellcheck="false"
          />
        </el-form-item>
        <el-form-item label="独立判断成员（至少 2 个 LLM）">
          <el-checkbox-group v-if="llmModels.length" v-model="ensembleForm.members" class="member-group">
            <el-checkbox
              v-for="m in llmModels"
              :key="m.id"
              :value="m.id"
              :label="m.name"
            >{{ m.name }}</el-checkbox>
          </el-checkbox-group>
          <p v-else class="hint">请先添加至少 2 个 LLM。</p>
        </el-form-item>
      </el-form>
      <p class="hint">所有成员读取同一冻结事实；最终交易员与风险复核由两个不同模型完成。分析只生成候选计划，不成交。</p>
      <template #footer>
        <el-button @click="ensembleDialog = false">取消</el-button>
        <el-button type="warning" :loading="saving" @click="createEnsemble">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'

const { isMobile } = useIsMobile()
const models = ref([])
const loading = ref(false)
const saving = ref(false)
const llmDialog = ref(false)
const ensembleDialog = ref(false)
const llmForm = ref({ name: '', model_id: '' })
const ensembleForm = ref({ name: '', members: [] })
const posDrawer = ref(false)
const posModel = ref(null)
const posData = ref({})
const posLoading = ref(false)

const llmModels = computed(() => models.value.filter((m) => m.type === 'llm'))
const hasEnsemble = computed(() => models.value.some((m) => m.type === 'ensemble'))
const hasOfficial = computed(() => models.value.some((m) => m.is_official_strategy))
const nameOf = (pk) => models.value.find((m) => m.id === pk)?.name || pk
function memberNames(row) {
  const ms = row?.members
  if (!Array.isArray(ms) || !ms.length) return ''
  return ms.map(nameOf).join(' · ')
}

function fmtMoney(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

async function openPositions(row) {
  posModel.value = row
  posDrawer.value = true
  posLoading.value = true
  posData.value = {}
  try {
    posData.value = await api.getPortfolio(row.id)
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    posLoading.value = false
  }
}

function laneClass(row) {
  if (row.type === 'rule') return 'rule'
  if (row.type === 'ensemble' && !row.is_official_strategy) return 'historical'
  if (row.type === 'ensemble') return 'ensemble'
  return 'ai'
}
function laneLabel(row) {
  if (row.type === 'rule') return '历史证据'
  if (row.type === 'ensemble' && !row.is_official_strategy) return '历史合议'
  if (row.type === 'ensemble') return '策略'
  return '判断顾问'
}

function formatPnl(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

async function load() {
  loading.value = true
  try {
    models.value = await api.getModels()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

function openLlmDialog() {
  llmForm.value = { name: '', model_id: '' }
  llmDialog.value = true
}
function openEnsembleDialog() {
  if (hasEnsemble.value) {
    ElMessage.info(hasOfficial.value
      ? '只允许一个官方策略账户，请编辑现有 ensemble 的成员'
      : '旧库 ensemble 身份冲突：请先停用到只剩一个启用项，系统会将其设为官方策略')
    return
  }
  ensembleForm.value = { name: '', members: [] }
  ensembleDialog.value = true
}

async function createLlm() {
  if (!llmForm.value.name?.trim() || !llmForm.value.model_id?.trim()) {
    ElMessage.error('请填写展示名与 API 模型名')
    return
  }
  saving.value = true
  try {
    await api.createModel({ ...llmForm.value, type: 'llm' })
    ElMessage.success('已添加独立判断模型；它不会获得资金账户')
    llmDialog.value = false
    await load()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    saving.value = false
  }
}

async function createEnsemble() {
  if (!ensembleForm.value.name?.trim() || (ensembleForm.value.members || []).length < 2) {
    ElMessage.error('请填写组合名并至少选 2 个成员')
    return
  }
  saving.value = true
  try {
    await api.createModel({
      name: ensembleForm.value.name,
      type: 'ensemble',
      members: ensembleForm.value.members,
    })
    ElMessage.success('已创建条件计划策略；下轮分析只会生成候选计划')
    ensembleDialog.value = false
    await load()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    saving.value = false
  }
}

async function toggle(row, enabled) {
  try {
    await api.updateModel(row.id, { enabled })
    await load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function remove(id) {
  try {
    const result = await api.deleteModel(id)
    ElMessage.success(result.archived ? '已有证据，已停用并保留历史' : '未产生证据，已删除')
    await load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

onMounted(load)
</script>

<style scoped>
.head-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.m-card-top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.m-switch { margin-left: auto; }
.meta { font-size: 12px; color: var(--text-muted); margin: 6px 0; }
.advisor-note {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-dim);
}
.m-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  gap: 8px;
}
.m-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.pos-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 16px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2, transparent);
}
.pos-list { display: flex; flex-direction: column; gap: 10px; }
.pos-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}
.pos-item-h {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.pos-item-grid {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: var(--text-muted);
}
.pos-reason { margin: 6px 0 0; font-size: 11px; color: var(--text-dim); line-height: 1.4; }
.m-label { font-size: 11px; color: var(--text-dim); }
.m-value { font-size: 15px; font-weight: 600; }
.hint { font-size: 12px; color: var(--text-dim); margin: 0; }
.foot { margin-top: 4px; max-width: 56ch; line-height: 1.5; }
.foot a { color: var(--accent); }
.empty {
  margin: 20px 0;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}
.member-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.lane-pill {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 2px 7px;
  border-radius: 999px;
  text-transform: uppercase;
}
.lane-pill.ai { background: var(--accent-dim); color: var(--lane-ai); }
.lane-pill.ensemble { background: rgba(167, 139, 250, 0.15); color: #c4b5fd; }
.lane-pill.rule { background: var(--lane-rule-dim); color: var(--lane-rule); }
.lane-pill.historical { background: var(--lane-rule-dim); color: var(--text-muted); }
</style>

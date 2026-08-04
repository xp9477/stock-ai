<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">参赛账户</h1>
        <p class="page-sub">增删启停的唯一入口 · 战报只看排行，设置只看摘要</p>
      </div>
      <div class="head-actions" role="toolbar" aria-label="账户操作">
        <el-button size="small" type="primary" @click="openLlmDialog">添加 LLM</el-button>
        <el-button size="small" type="warning" @click="openEnsembleDialog">新建合议</el-button>
      </div>
    </div>

    <section class="panel" aria-label="账户列表">
      <p v-if="!loading && !models.length" class="empty" role="status">
        暂无账户。先添加 LLM，再可选建合议；规则账户由系统种子创建。
      </p>

      <div v-else-if="isMobile">
        <div v-for="row in models" :key="row.id" class="m-card">
          <div class="m-card-top">
            <span class="m-card-title" translate="no">{{ row.name }}</span>
            <span class="lane-pill" :class="laneClass(row)">{{ laneLabel(row) }}</span>
            <el-switch
              class="m-switch"
              :model-value="row.enabled"
              :aria-label="`启用 ${row.name}`"
              @change="(v) => toggle(row, v)"
            />
          </div>
          <div v-if="row.type === 'llm'" class="meta mono" translate="no">{{ row.model_id }}</div>
          <div v-else-if="row.type === 'ensemble'" class="meta">
            成员：{{ (row.members || []).map(nameOf).join(' · ') }}
          </div>
          <div v-else class="meta mono">策略 {{ row.model_id }}</div>
          <div class="m-row">
            <span>
              收益
              <span class="mono" :class="row.pnl_pct >= 0 ? 'up' : 'down'">
                {{ formatPnl(row.pnl_pct) }}
              </span>
            </span>
            <el-popconfirm
              v-if="row.type !== 'rule'"
              title="删除该账户全部数据？"
              confirm-button-text="删除"
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

      <el-table v-else :data="models" stripe empty-text="暂无账户">
        <el-table-column prop="name" label="名称" width="160">
          <template #default="{ row }">
            <span translate="no">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="赛道" width="100">
          <template #default="{ row }">
            <span class="lane-pill" :class="laneClass(row)">{{ laneLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="标识" min-width="180">
          <template #default="{ row }">
            <span v-if="row.type === 'llm'" class="mono" translate="no">{{ row.model_id }}</span>
            <span v-else-if="row.type === 'rule'" class="mono" translate="no">{{ row.model_id }}</span>
            <span v-else>{{ (row.members || []).map(nameOf).join(' · ') || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="收益率" width="110">
          <template #default="{ row }">
            <span class="mono" :class="row.pnl_pct >= 0 ? 'up' : 'down'">
              {{ formatPnl(row.pnl_pct) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              :aria-label="`启用 ${row.name}`"
              @change="(v) => toggle(row, v)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-popconfirm
              v-if="row.type !== 'rule'"
              title="删除该账户全部数据？"
              confirm-button-text="删除"
              cancel-button-text="取消"
              @confirm="remove(row.id)"
            >
              <template #reference>
                <el-button size="small" type="danger" plain :aria-label="`删除 ${row.name}`">
                  删除
                </el-button>
              </template>
            </el-popconfirm>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <p class="hint foot">
      规则账户由系统维护，不可删除。LLM 共用
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
            placeholder="如 Grok 4.5…"
            autocomplete="off"
            spellcheck="false"
          />
        </el-form-item>
        <el-form-item label="API 模型名">
          <el-input
            v-model="llmForm.model_id"
            name="model_id"
            placeholder="如 grok-4.5…"
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
      title="新建合议"
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
        <el-form-item label="成员（至少 2 个 LLM）">
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
      <p class="hint">多数票合成，不额外消耗 LLM。</p>
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

const llmModels = computed(() => models.value.filter((m) => m.type === 'llm'))
const nameOf = (pk) => models.value.find((m) => m.id === pk)?.name || pk

function laneClass(row) {
  if (row.type === 'rule') return 'rule'
  if (row.type === 'ensemble') return 'ensemble'
  return 'ai'
}
function laneLabel(row) {
  if (row.type === 'rule') return '规则'
  if (row.type === 'ensemble') return '合议'
  return 'LLM'
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
    ElMessage.success('已添加 LLM')
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
    ElMessage.success('已创建合议')
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
    await api.deleteModel(id)
    ElMessage.success('已删除')
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
.m-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}
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
</style>

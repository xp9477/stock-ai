<template>
  <div class="page settings-page">
    <header class="page-head">
      <div class="head-text">
        <h1 class="page-title">设置</h1>
        <p class="page-sub">流水线参数 · 密钥 · 账户摘要</p>
      </div>
      <div class="head-actions" role="toolbar" aria-label="设置操作">
        <el-button
          size="small"
          :loading="loading"
          aria-label="刷新设置"
          @click="loadAll"
        >刷新</el-button>
        <el-popconfirm
          v-if="activeGroup !== 'models'"
          title="恢复当前分组为默认值？"
          confirm-button-text="恢复"
          cancel-button-text="取消"
          @confirm="resetGroup"
        >
          <template #reference>
            <el-button size="small" plain :disabled="loading">恢复本组默认</el-button>
          </template>
        </el-popconfirm>
        <el-button
          v-if="activeGroup !== 'models'"
          size="small"
          type="primary"
          :loading="saving"
          :disabled="!dirtyCount || saving"
          aria-label="保存修改"
          @click="save"
        >
          {{ saving ? '保存中…' : dirtyCount ? `保存 ${dirtyCount} 项` : '已保存' }}
        </el-button>
      </div>
    </header>

    <div class="settings-shell" :aria-busy="loading ? 'true' : 'false'">
      <!-- 左侧分组导航 -->
      <nav class="settings-nav" aria-label="设置分组">
        <button
          v-for="g in navGroups"
          :key="g.id"
          type="button"
          class="nav-btn"
          :class="{ active: activeGroup === g.id }"
          :aria-current="activeGroup === g.id ? 'page' : undefined"
          @click="selectGroup(g.id)"
        >
          <span class="nav-label">{{ g.label }}</span>
          <span v-if="navBadge(g)" class="nav-badge">{{ navBadge(g) }}</span>
        </button>
      </nav>

      <!-- 主内容 -->
      <section class="settings-main panel" :aria-labelledby="`sec-${activeGroup}`">
        <div class="sec-head">
          <h2 :id="`sec-${activeGroup}`" class="sec-title">{{ currentGroupMeta.label }}</h2>
          <p class="sec-desc">{{ currentGroupMeta.description }}</p>
        </div>

        <!-- 参赛账户：摘要 + 跳转（CRUD 仅在 /models） -->
        <div v-if="activeGroup === 'models'" class="models-sec">
          <div class="summary-grid" aria-live="polite">
            <div class="summary-card">
              <div class="summary-label">全部账户</div>
              <div class="summary-value mono">{{ modelStats.total }}</div>
            </div>
            <div class="summary-card">
              <div class="summary-label">已启用</div>
              <div class="summary-value mono ok-text">{{ modelStats.enabled }}</div>
            </div>
            <div class="summary-card">
              <div class="summary-label">AI 赛道</div>
              <div class="summary-value mono">{{ modelStats.ai }}</div>
              <div class="summary-hint">LLM + 合议</div>
            </div>
            <div class="summary-card">
              <div class="summary-label">规则赛道</div>
              <div class="summary-value mono">{{ modelStats.rule }}</div>
            </div>
          </div>

          <ul v-if="models.length" class="model-peek" aria-label="账户摘要（只读）">
            <li v-for="row in models" :key="row.id" class="peek-row">
              <span class="peek-name truncate" translate="no">{{ row.name }}</span>
              <span class="lane-pill" :class="laneClass(row)">{{ laneLabel(row) }}</span>
              <span class="peek-state" :class="row.enabled ? 'on' : 'off'">
                {{ row.enabled ? '启用' : '停用' }}
              </span>
              <span
                class="mono peek-pnl"
                :class="row.pnl_pct >= 0 ? 'up' : 'down'"
              >{{ formatPctNum(row.pnl_pct) }}</span>
            </li>
          </ul>
          <p v-else-if="!modelsLoading" class="empty" role="status">
            暂无参赛账户。请到「参赛账户」页添加 LLM。
          </p>
          <p v-else class="empty" role="status">加载中…</p>

          <div class="models-cta">
            <router-link class="cta-link" to="/models">
              管理参赛账户（添加 / 启停 / 删除）→
            </router-link>
            <p class="foot-note">
              战报只看排行；增删与启停统一在「参赛账户」。LLM 密钥在左侧「密钥」。
            </p>
          </div>
        </div>

        <!-- 配置表单 -->
        <div v-else class="form-sec">
          <p v-if="!itemsOf(activeGroup).length" class="empty" role="status">本组暂无配置项。</p>

          <!-- Prompt：纵向长文 -->
          <div v-if="activeGroup === 'prompt'" class="prompt-stack">
            <article
              v-for="item in itemsOf(activeGroup)"
              :key="item.key"
              class="form-block"
              :class="blockClass(item)"
            >
              <div class="block-head">
                <label class="block-label" :for="inputId(item.key)">{{ item.label }}</label>
                <div class="block-tags">
                  <span v-if="item.overridden" class="tag">已覆盖</span>
                  <span v-if="isDirty(item.key)" class="tag warn">未保存</span>
                </div>
                <button
                  v-if="item.overridden || isDirty(item.key)"
                  type="button"
                  class="text-btn"
                  @click="resetOne(item.key)"
                >恢复默认</button>
              </div>
              <p v-if="item.description" class="block-help">{{ item.description }}</p>
              <el-input
                :id="inputId(item.key)"
                v-model="draft[item.key]"
                type="textarea"
                :autosize="{ minRows: 5, maxRows: 16 }"
                :name="item.key"
                :disabled="!item.editable"
                autocomplete="off"
                spellcheck="false"
                class="prompt-area"
              />
            </article>
          </div>

          <!-- 普通 / 密钥：整齐行表单 -->
          <div v-else class="form-stack" role="list">
            <div
              v-for="item in itemsOf(activeGroup)"
              :key="item.key"
              class="form-row"
              :class="blockClass(item)"
              role="listitem"
            >
              <div class="row-label-col">
                <label class="block-label" :for="inputId(item.key)">
                  {{ item.label }}
                  <span v-if="item.unit" class="unit">{{ item.unit }}</span>
                </label>
                <p v-if="item.description" class="block-help">{{ item.description }}</p>
                <div class="block-tags">
                  <span v-if="item.overridden" class="tag">已覆盖</span>
                  <span v-if="isDirty(item.key)" class="tag warn">未保存</span>
                  <span v-if="item.requires_scheduler_reload" class="tag ok">改后重载调度</span>
                </div>
              </div>

              <div class="row-control-col">
                <!-- bool -->
                <el-switch
                  v-if="item.type === 'bool'"
                  :id="inputId(item.key)"
                  v-model="draft[item.key]"
                  :disabled="!item.editable"
                  :aria-label="item.label"
                />

                <!-- secret -->
                <div v-else-if="item.type === 'secret'" class="secret-field">
                  <div class="secret-status" aria-live="polite">
                    <span class="tag" :class="item.configured ? 'ok' : 'bad'">
                      {{ item.configured ? '已配置' : '未配置' }}
                    </span>
                    <span v-if="item.masked" class="mono masked" translate="no">{{ item.masked }}</span>
                    <span v-if="item.source && item.source !== 'none'" class="src">
                      {{ sourceLabel(item.source) }}
                    </span>
                  </div>
                  <el-input
                    :id="inputId(item.key)"
                    v-model="draft[item.key]"
                    type="password"
                    show-password
                    :name="item.key"
                    :placeholder="item.configured ? '留空不修改…' : '粘贴密钥…'"
                    :disabled="!item.editable"
                    autocomplete="off"
                    spellcheck="false"
                  />
                </div>

                <!-- time / str -->
                <el-input
                  v-else-if="item.type === 'time' || item.type === 'str'"
                  :id="inputId(item.key)"
                  v-model="draft[item.key]"
                  :name="item.key"
                  :disabled="!item.editable"
                  :type="item.type === 'time' ? 'text' : 'text'"
                  :inputmode="item.type === 'time' ? 'numeric' : undefined"
                  :placeholder="item.type === 'time' ? '14:35' : ''"
                  autocomplete="off"
                  spellcheck="false"
                  class="ctrl-input"
                />

                <!-- percent -->
                <div v-else-if="item.type === 'percent'" class="pct-field">
                  <el-input-number
                    :id="inputId(item.key)"
                    v-model="draft[item.key]"
                    :disabled="!item.editable"
                    :step="0.01"
                    :precision="2"
                    :min="item.min_value ?? undefined"
                    :max="item.max_value ?? undefined"
                    controls-position="right"
                    :aria-label="item.label"
                  />
                  <span class="pct-hint mono">{{ formatPct(draft[item.key]) }}</span>
                </div>

                <!-- number -->
                <el-input-number
                  v-else
                  :id="inputId(item.key)"
                  v-model="draft[item.key]"
                  :disabled="!item.editable"
                  :step="item.type === 'int' ? 1 : 0.1"
                  :precision="item.type === 'int' ? 0 : 2"
                  :min="item.min_value ?? undefined"
                  :max="item.max_value ?? undefined"
                  controls-position="right"
                  :aria-label="item.label"
                />

                <div class="row-actions">
                  <button
                    v-if="item.overridden || isDirty(item.key)"
                    type="button"
                    class="text-btn"
                    @click="resetOne(item.key)"
                  >恢复默认</button>
                  <span v-if="item.type !== 'secret'" class="default-hint mono">
                    默认 {{ formatDefault(item) }}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <p v-if="activeGroup === 'secrets'" class="foot-note">
            优先级：设置页覆盖 &gt; <code>.env</code> &gt; 空。接口不回传明文；留空保存表示不改。
          </p>
          <p v-else-if="activeGroup === 'schedule'" class="foot-note">
            改时间后保存会热重载调度器，无需重启进程。
          </p>
          <p v-else-if="activeGroup === 'factor'" class="foot-note">
            S3：动量 / 反转 / 低波 / 低换手 / 估值 / ROE / ROE 改善；可选规模与板块中性。
          </p>
        </div>
      </section>
    </div>

  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const modelsLoading = ref(false)
const groups = ref([])
const items = ref([])
const models = ref([])
const activeGroup = ref('secrets')
const draft = reactive({})
const baseline = reactive({})

const STATIC_MODELS_GROUP = {
  id: 'models',
  label: '参赛账户',
  description: '摘要只读 · 增删启停请到「参赛账户」页',
}

const itemMap = computed(() => Object.fromEntries(items.value.map((i) => [i.key, i])))

const navGroups = computed(() => {
  const fromApi = groups.value.length
    ? groups.value.map((g) =>
        g.id === 'models' ? { ...g, ...STATIC_MODELS_GROUP } : g,
      )
    : [
        { id: 'secrets', label: '密钥', description: '' },
        { id: 'selector', label: '选股', description: '' },
        { id: 'prompt', label: 'Prompt', description: '' },
        { id: 'risk', label: '风控', description: '' },
        { id: 'factor', label: '因子', description: '' },
        { id: 'schedule', label: '调度', description: '' },
        { id: 'race', label: '赛马', description: '' },
      ]
  if (!fromApi.find((g) => g.id === 'models')) {
    return [...fromApi, STATIC_MODELS_GROUP]
  }
  return fromApi
})

const currentGroupMeta = computed(() => {
  return navGroups.value.find((g) => g.id === activeGroup.value) || STATIC_MODELS_GROUP
})

const dirtyCount = computed(() =>
  items.value.filter((it) => isDirty(it.key)).length,
)

const modelStats = computed(() => {
  const list = models.value
  return {
    total: list.length,
    enabled: list.filter((m) => m.enabled).length,
    ai: list.filter((m) => m.type === 'llm' || m.type === 'ensemble').length,
    rule: list.filter((m) => m.type === 'rule').length,
  }
})

function itemsOf(groupId) {
  return items.value.filter((i) => i.group === groupId)
}

function inputId(key) {
  return `set-${key.replace(/\./g, '-')}`
}

function navBadge(g) {
  if (g.id === 'models') {
    const on = models.value.filter((m) => m.enabled).length
    return models.value.length ? `${on}/${models.value.length}` : ''
  }
  if (g.id === 'secrets') {
    const secrets = itemsOf('secrets').filter((i) => i.secret || i.type === 'secret')
    if (!secrets.length) return ''
    const ok = secrets.filter((i) => i.configured).length
    return `${ok}/${secrets.length}`
  }
  const n = itemsOf(g.id).filter((i) => i.overridden).length
  return n ? String(n) : ''
}

function sourceLabel(src) {
  return ({ env: '.env', override: '设置页', default: '默认' })[src] || src
}

function blockClass(item) {
  return {
    dirty: isDirty(item.key),
    overridden: item.overridden && !isDirty(item.key),
  }
}

function isDirty(key) {
  if (!(key in draft) || !(key in baseline)) return false
  const it = itemMap.value[key]
  if (it?.secret || it?.type === 'secret') {
    return Boolean(String(draft[key] || '').trim())
  }
  return !same(draft[key], baseline[key])
}

function same(a, b) {
  if (typeof a === 'number' && typeof b === 'number') {
    return Math.abs(a - b) < 1e-9
  }
  return a === b
}

function formatPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return `${(Number(v) * 100).toFixed(1)}%`
}

function formatPctNum(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function formatDefault(item) {
  if (item.type === 'percent') return formatPct(item.default)
  if (item.type === 'bool') return item.default ? '开' : '关'
  if (item.type === 'text') {
    const s = String(item.default || '')
    return s.length > 28 ? `${s.slice(0, 28)}…` : s
  }
  return item.default
}

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

function selectGroup(id) {
  activeGroup.value = id
  router.replace({ query: { ...route.query, tab: id } })
  if (id === 'models') loadModels()
}

function applySettings(data) {
  groups.value = data.groups || []
  items.value = data.items || []
  for (const it of items.value) {
    const v = (it.secret || it.type === 'secret') ? '' : it.value
    draft[it.key] = v
    baseline[it.key] = v
  }
}

async function loadSettings() {
  const data = await api.getSettings()
  applySettings(data)
}

async function loadModels() {
  modelsLoading.value = true
  try {
    models.value = await api.getModels()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    modelsLoading.value = false
  }
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([
      loadSettings(),
      loadModels(), // 角标 + 摘要都需要
    ])
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

async function save() {
  const values = {}
  for (const it of items.value) {
    if (!isDirty(it.key) || !it.editable) continue
    if (it.secret || it.type === 'secret') {
      const s = String(draft[it.key] || '').trim()
      if (!s) continue
      values[it.key] = s
    } else {
      values[it.key] = draft[it.key]
    }
  }
  if (!Object.keys(values).length) {
    ElMessage.info('没有需要保存的修改')
    return
  }
  saving.value = true
  try {
    const res = await api.putSettings(values)
    const n = res.updated?.length || 0
    ElMessage.success(
      res.reload_scheduler ? `已保存 ${n} 项，调度器已重载` : `已保存 ${n} 项`,
    )
    if (res.scheduler_reload_error) {
      ElMessage.warning(`调度重载失败：${res.scheduler_reload_error}`)
    }
    await loadSettings()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    saving.value = false
  }
}

async function resetOne(key) {
  try {
    await api.resetSettings({ keys: [key] })
    ElMessage.success('已恢复默认')
    await loadSettings()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function resetGroup() {
  if (!activeGroup.value || activeGroup.value === 'models') return
  try {
    await api.resetSettings({ group: activeGroup.value })
    ElMessage.success('本组已恢复默认')
    await loadSettings()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function onBeforeUnload(e) {
  if (dirtyCount.value > 0) {
    e.preventDefault()
    e.returnValue = ''
  }
}

watch(
  () => route.query.tab,
  (tab) => {
    if (typeof tab === 'string' && tab && tab !== activeGroup.value) {
      activeGroup.value = tab
      if (tab === 'models') loadModels()
    }
  },
)

onMounted(async () => {
  const tab = typeof route.query.tab === 'string' ? route.query.tab : 'secrets'
  activeGroup.value = tab
  await loadAll()
  window.addEventListener('beforeunload', onBeforeUnload)
})

onUnmounted(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 0;
}

.page-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.head-text { min-width: 0; }

.head-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

/* 双栏壳 */
.settings-shell {
  display: grid;
  grid-template-columns: 168px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
  min-height: 420px;
}

.settings-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: sticky;
  top: 8px;
  padding: 6px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.nav-btn {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  touch-action: manipulation;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.nav-btn:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.04);
}

.nav-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.nav-btn.active {
  color: var(--text);
  background: var(--accent-dim);
  border-color: rgba(232, 184, 74, 0.35);
}

.nav-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-badge {
  flex-shrink: 0;
  font-family: var(--mono);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-dim);
}

.nav-btn.active .nav-badge {
  background: rgba(232, 184, 74, 0.2);
  color: var(--accent);
}

.settings-main {
  min-width: 0;
  padding: 18px 20px 22px;
}

.sec-head {
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}

.sec-title {
  margin: 0 0 6px;
  font-size: 1.1rem;
  font-weight: 650;
  letter-spacing: 0.01em;
  text-wrap: balance;
}

.sec-desc {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
  line-height: 1.45;
  max-width: 56ch;
}

/* 表单：两列行 */
.form-stack {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.form-row {
  display: grid;
  grid-template-columns: minmax(140px, 1fr) minmax(200px, 1.1fr);
  gap: 12px 20px;
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
  align-items: start;
}

.form-row:last-child {
  border-bottom: none;
}

.form-row.dirty {
  background: linear-gradient(90deg, rgba(232, 184, 74, 0.06), transparent 60%);
  margin: 0 -12px;
  padding-left: 12px;
  padding-right: 12px;
  border-radius: var(--radius-sm);
}

.row-label-col {
  min-width: 0;
}

.row-control-col {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.block-label {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  cursor: pointer;
}

.unit {
  font-weight: 400;
  font-size: 12px;
  color: var(--text-dim);
}

.block-help {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.45;
}

.block-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.tag {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(56, 189, 248, 0.12);
  color: var(--lane-rule);
}

.tag.warn {
  background: rgba(232, 184, 74, 0.16);
  color: var(--accent);
}

.tag.ok {
  background: rgba(52, 211, 153, 0.14);
  color: var(--ok);
}

.tag.bad {
  background: rgba(244, 63, 94, 0.14);
  color: var(--danger);
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.text-btn {
  border: none;
  background: none;
  padding: 0;
  font-size: 12px;
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
  touch-action: manipulation;
}

.text-btn:hover { color: #f0c96a; }
.text-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 2px;
}

.default-hint {
  font-size: 11px;
  color: var(--text-dim);
  word-break: break-all;
}

.ctrl-input {
  width: 100%;
  max-width: 320px;
}

.pct-field {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.pct-hint {
  font-size: 12px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.secret-field {
  width: 100%;
  max-width: 360px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.secret-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.masked {
  color: var(--text-dim);
  letter-spacing: 0.04em;
}

.src {
  color: var(--text-dim);
  font-size: 11px;
}

/* Prompt */
.prompt-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-block {
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--panel-2);
}

.form-block.dirty {
  border-color: rgba(232, 184, 74, 0.45);
}

.block-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 6px;
}

.prompt-area :deep(textarea) {
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.55;
}

/* 参赛账户摘要（只读） */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.summary-card {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--panel-2);
  min-width: 0;
}

.summary-label {
  font-size: 11px;
  color: var(--text-muted);
  margin-bottom: 4px;
}

.summary-value {
  font-size: 1.35rem;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}

.summary-value.ok-text { color: var(--ok); }

.summary-hint {
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-dim);
}

.model-peek {
  list-style: none;
  margin: 0 0 16px;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.peek-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  gap: 10px;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--panel-2);
  font-size: 13px;
}

.peek-row:last-child { border-bottom: none; }

.peek-name {
  font-weight: 600;
  min-width: 0;
}

.peek-state {
  font-size: 11px;
  font-weight: 600;
}

.peek-state.on { color: var(--ok); }
.peek-state.off { color: var(--text-dim); }

.peek-pnl {
  font-variant-numeric: tabular-nums;
  min-width: 4.2em;
  text-align: right;
}

.truncate {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lane-pill {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 2px 7px;
  border-radius: 999px;
  text-transform: uppercase;
}

.lane-pill.ai {
  background: var(--accent-dim);
  color: var(--lane-ai);
}

.lane-pill.ensemble {
  background: rgba(167, 139, 250, 0.15);
  color: #c4b5fd;
}

.lane-pill.rule {
  background: var(--lane-rule-dim);
  color: var(--lane-rule);
}

.models-cta {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cta-link {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(232, 184, 74, 0.4);
  background: var(--accent-dim);
  color: var(--accent);
  font-size: 13px;
  font-weight: 600;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.cta-link:hover {
  border-color: var(--accent);
  color: #f0c96a;
}

.cta-link:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.empty {
  margin: 16px 0;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}

.foot-note {
  margin: 0;
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.5;
  max-width: 62ch;
}

.foot-note code {
  font-family: var(--mono);
  font-size: 11px;
}

@media (max-width: 800px) {
  .settings-shell {
    grid-template-columns: 1fr;
  }

  .settings-nav {
    position: static;
    flex-direction: row;
    flex-wrap: nowrap;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    -webkit-overflow-scrolling: touch;
    gap: 6px;
    padding: 8px;
  }

  .nav-btn {
    flex: 0 0 auto;
    white-space: nowrap;
  }

  .form-row {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .row-control-col {
    width: 100%;
  }

  .ctrl-input,
  .secret-field {
    max-width: none;
  }

  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .peek-row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 6px 10px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .nav-btn {
    transition: none;
  }
}
</style>

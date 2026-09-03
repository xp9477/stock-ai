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

        <!-- 模型与策略：摘要 + 跳转（CRUD 仅在 /models） -->
        <div v-if="activeGroup === 'models'" class="models-sec">
          <div class="summary-grid" aria-live="polite">
            <div class="summary-card">
              <div class="summary-label">全部模型</div>
              <div class="summary-value mono">{{ modelStats.total }}</div>
            </div>
            <div class="summary-card">
              <div class="summary-label">已启用</div>
              <div class="summary-value mono ok-text">{{ modelStats.enabled }}</div>
            </div>
            <div class="summary-card">
              <div class="summary-label">判断与正式策略</div>
              <div class="summary-value mono">{{ modelStats.ai }}</div>
              <div class="summary-hint">LLM + 合议</div>
            </div>
            <div class="summary-card">
              <div class="summary-label">历史规则证据</div>
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
            暂无模型。请到「模型与策略」页添加 LLM 顾问。
          </p>
          <p v-else class="empty" role="status">加载中…</p>

          <div class="models-cta">
            <router-link class="cta-link" to="/models">
              管理模型与策略（添加 / 启停 / 删除）→
            </router-link>
            <p class="foot-note">
              只有 ensemble 是资金策略；LLM 是顾问，rule 是历史只读证据。LLM 密钥在左侧「密钥」。
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
                  <span v-if="item.danger === 'confirm'" class="tag warn">改前确认</span>
                  <span v-if="item.danger === 'frozen'" class="tag bad">仅新户/重置</span>
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
                    :step="numberStep(item)"
                    :precision="numberPrecision(item)"
                    :min="item.min_value ?? undefined"
                    :max="item.max_value ?? undefined"
                    controls-position="right"
                    :aria-label="item.label"
                    class="num-input"
                  />
                  <span class="pct-hint mono">{{ formatPct(draft[item.key], item) }}</span>
                </div>

                <!-- number -->
                <el-input-number
                  v-else
                  :id="inputId(item.key)"
                  v-model="draft[item.key]"
                  :disabled="!item.editable"
                  :step="numberStep(item)"
                  :precision="numberPrecision(item)"
                  :min="item.min_value ?? undefined"
                  :max="item.max_value ?? undefined"
                  controls-position="right"
                  :aria-label="item.label"
                  class="num-input"
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

          <!-- 数据源健康面板 -->
          <div v-if="activeGroup === 'datasources'" class="ds-health panel-inset">
            <div class="ds-health-head">
              <div>
                <div class="ds-health-title">数据源健康</div>
                <p class="block-help">角色固定：扶摇个股主源 · 新浪指数/日历兜底 · Tushare 备 · RSS 新闻。下方可探测连通性。</p>
              </div>
              <el-button size="small" type="primary" plain :loading="probing" @click="probeAll">
                立即探测
              </el-button>
            </div>
            <div v-if="probeResults.length" class="ds-grid">
              <article v-for="r in probeResults" :key="r.id" class="ds-card" :class="r.ok ? 'ok' : 'bad'">
                <div class="ds-card-top">
                  <b>{{ r.label || r.id }}</b>
                  <span class="tag" :class="r.ok ? 'ok' : 'bad'">{{ r.ok ? '正常' : '异常' }}</span>
                </div>
                <div class="ds-role dim">{{ r.role }}</div>
                <div class="ds-detail mono">{{ r.detail || '—' }}</div>
                <div class="ds-meta mono dim">
                  {{ r.enabled === false ? '已禁用 · ' : '' }}
                  超时 {{ r.timeout_sec ?? '—' }}s · 失败 {{ r.fail_policy || '—' }}
                  <template v-if="r.latency_ms != null"> · {{ r.latency_ms }}ms</template>
                </div>
              </article>
            </div>
            <p v-else class="empty">尚未探测。保存启停/超时后点「立即探测」。</p>

            <div v-if="rssFeeds.length" class="rss-list">
              <div class="ds-health-title" style="margin:14px 0 8px">RSS 源列表（只读）</div>
              <ul class="rss-ul">
                <li v-for="f in rssFeeds" :key="f.id">
                  <span class="tag" :class="f.region === 'cn' ? 'ok' : ''">{{ f.region === 'cn' ? '国内' : '国际' }}</span>
                  <b>{{ f.name }}</b>
                  <span class="mono dim rss-url">{{ f.url }}</span>
                </li>
              </ul>
            </div>
            <p class="foot-note">
              失败策略：<code>fallback</code> 降级下一源 · <code>hard</code> 立即失败 · <code>skip</code> 跳过。
              启停/超时已接入行情·指数·选股·新闻路径。源 URL 在
              <code>backend/app/data/news_rss.py</code>。
            </p>
          </div>

          <!-- Bark 通知测试 -->
          <div v-if="activeGroup === 'notifications'" class="ds-health panel-inset">
            <div class="ds-health-head">
              <div>
                <div class="ds-health-title">通知通道测试</div>
                <p class="block-help">先保存 Bark Server 与 Device Key，再发送真实测试通知。测试不要求先打开“启用”开关。</p>
              </div>
              <el-button size="small" type="primary" plain :loading="testingBark" @click="testBark">
                发送测试通知
              </el-button>
            </div>
            <p class="foot-note">
              系统只提醒候选计划、持仓复审和运行异常；普通观望不会推送。推送失败只写日志，不会改变交易决策。
            </p>
          </div>

          <!-- 全局日志面板 -->
          <div v-if="activeGroup === 'logs'" class="ds-health panel-inset">
            <div class="ds-health-head">
              <div>
                <div class="ds-health-title">全局日志</div>
                <p class="block-help">运行中过程见决策页；此处为系统级日志（密钥已脱敏）。</p>
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <el-select v-model="logLevel" size="small" clearable placeholder="级别" style="width:110px" @change="loadLogs">
                  <el-option label="全部" value="" />
                  <el-option label="INFO" value="INFO" />
                  <el-option label="WARNING" value="WARNING" />
                  <el-option label="ERROR" value="ERROR" />
                  <el-option label="DEBUG" value="DEBUG" />
                </el-select>
                <el-button size="small" :loading="logLoading" @click="loadLogs">刷新</el-button>
                <el-button size="small" plain type="danger" @click="doPurgeLogs">清理过期</el-button>
              </div>
            </div>
            <div v-if="logItems.length" class="log-box mono">
              <div v-for="(row, i) in logItems" :key="row.id || i" class="log-line">
                <span class="dim">{{ row.created_at }}</span>
                <span :class="logLevelClass(row.level)">{{ row.level }}</span>
                <span class="dim">{{ row.logger_name }}</span>
                <span v-if="row.run_id" class="tag">run#{{ row.run_id }}</span>
                <span>{{ row.message }}</span>
              </div>
            </div>
            <p v-else class="empty">暂无日志。跑一轮决策或探测后会出现。</p>
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
          <p v-else-if="activeGroup === 'debug'" class="foot-note">
            P0 骨架：决策详情页有「调试」开关可展开 Agent 输入摘要；原始 token/HTTP 级日志在后续版本。
          </p>
        </div>
      </section>
    </div>

  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api/index.js'
import { formatNumber, numberPrecision, numberStep } from '../utils/numberInput.js'

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
const probing = ref(false)
const testingBark = ref(false)
const probeResults = ref([])
const rssFeeds = ref([])
const logItems = ref([])
const logLoading = ref(false)
const logLevel = ref('')

const STATIC_MODELS_GROUP = {
  id: 'models',
  label: '模型与策略',
  description: '摘要只读 · 增删启停请到「模型与策略」页',
}

const itemMap = computed(() => Object.fromEntries(items.value.map((i) => [i.key, i])))

/** 客户端保底分组顺序：后端未热更时也能看到「数据源」等入口 */
const FALLBACK_GROUPS = [
  { id: 'secrets', label: '密钥', description: 'LLM / 数据源 API' },
  { id: 'datasources', label: '数据源', description: '扶摇/新浪/Tushare/RSS' },
  { id: 'notifications', label: '通知', description: 'Bark 人工介入提醒' },
  { id: 'account', label: '账户', description: '初始资金' },
  { id: 'trading', label: '撮合', description: '佣金印花税' },
  { id: 'selector', label: '选股', description: '' },
  { id: 'prompt', label: 'Prompt', description: '' },
  { id: 'risk', label: '风控', description: '' },
  { id: 'factor', label: '因子', description: '' },
  { id: 'models', label: '模型与策略', description: '' },
  { id: 'schedule', label: '调度', description: '' },
  { id: 'validation', label: '证据门槛', description: '' },
  { id: 'logs', label: '日志', description: '全局日志' },
  { id: 'debug', label: '调试', description: '' },
]

const navGroups = computed(() => {
  const byId = Object.fromEntries(
    (groups.value || []).map((g) => [g.id, g]),
  )
  // 按保底顺序合并：API 有则用 API 元数据，缺的用 FALLBACK 补上
  const merged = []
  const seen = new Set()
  for (const fb of FALLBACK_GROUPS) {
    const g = byId[fb.id] || fb
    merged.push(g.id === 'models' ? { ...g, ...STATIC_MODELS_GROUP } : { ...fb, ...g })
    seen.add(g.id)
  }
  for (const g of groups.value || []) {
    if (!seen.has(g.id)) {
      merged.push(g.id === 'models' ? { ...g, ...STATIC_MODELS_GROUP } : g)
    }
  }
  return merged
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

function formatPct(v, item) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const digits = Math.max(numberPrecision(item || { type: 'percent' }) - 2, 1)
  return `${(Number(v) * 100).toFixed(digits)}%`
}

function formatPctNum(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function formatDefault(item) {
  if (item.type === 'percent') return formatPct(item.default, item)
  if (item.type === 'bool') return item.default ? '开' : '关'
  if (item.type === 'float' || item.type === 'int') {
    return formatNumber(item.default, numberPrecision(item))
  }
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
  if (row.type === 'rule') return '历史'
  if (row.type === 'ensemble') return '正式策略'
  return '顾问'
}

function selectGroup(id) {
  activeGroup.value = id
  router.replace({ query: { ...route.query, tab: id } })
  if (id === 'models') loadModels()
  if (id === 'datasources') loadDatasourceStatus()
  if (id === 'logs') loadLogs()
}

function logLevelClass(lv) {
  if (lv === 'ERROR' || lv === 'CRITICAL') return 'down'
  if (lv === 'WARNING') return 'warn-text'
  return ''
}

async function loadLogs() {
  logLoading.value = true
  try {
    const data = await api.getLogs({
      limit: 200,
      level: logLevel.value || undefined,
    })
    logItems.value = data.items || []
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    logLoading.value = false
  }
}

async function doPurgeLogs() {
  try {
    const res = await api.purgeLogs()
    ElMessage.success(`已清理 ${res.removed ?? 0} 条过期日志`)
    await loadLogs()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function loadDatasourceStatus() {
  try {
    const data = await api.getDatasources()
    rssFeeds.value = data.rss_feeds || []
    probeResults.value = (data.sources || []).map((s) => ({
      ...s,
      ok: s.enabled !== false && (s.key_configured !== false || !s.needs_key),
      detail: s.needs_key
        ? (s.key_configured ? 'Key 已配置（未探测）' : 'Key 未配置')
        : (s.enabled === false ? '已禁用' : '已启用（未探测）'),
    }))
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function probeAll() {
  probing.value = true
  try {
    const data = await api.probeDatasources()
    probeResults.value = data.results || []
    ElMessage.success('探测完成')
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    probing.value = false
  }
}

async function testBark() {
  if (dirtyCount.value) {
    ElMessage.warning('请先保存通知配置，再发送测试')
    return
  }
  testingBark.value = true
  try {
    const data = await api.testBark()
    ElMessage.success(data.detail || 'Bark 测试通知已发送')
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    testingBark.value = false
  }
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
  const dirtyItems = []
  for (const it of items.value) {
    if (!isDirty(it.key) || !it.editable) continue
    dirtyItems.push(it)
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
  const needConfirm = dirtyItems.filter((i) => i.danger === 'confirm' || i.danger === 'frozen')
  if (needConfirm.length) {
    const names = needConfirm.map((i) => i.label).join('、')
    try {
      await ElMessageBox.confirm(
        `以下配置会影响前瞻证据可比性或仅对新实验生效：\n${names}\n\n确认保存？`,
        '危险配置确认',
        { type: 'warning', confirmButtonText: '仍要保存', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
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

function loadGroupSideData(tab) {
  if (tab === 'models') loadModels()
  if (tab === 'datasources') loadDatasourceStatus()
  if (tab === 'logs') loadLogs()
}

watch(
  () => route.query.tab,
  (tab) => {
    if (typeof tab === 'string' && tab && tab !== activeGroup.value) {
      activeGroup.value = tab
      loadGroupSideData(tab)
    }
  },
)

onMounted(async () => {
  const tab = typeof route.query.tab === 'string' ? route.query.tab : 'secrets'
  activeGroup.value = tab
  await loadAll()
  loadGroupSideData(tab)
  window.addEventListener('beforeunload', onBeforeUnload)
})

onUnmounted(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<style scoped src="../styles/settings.css"></style>

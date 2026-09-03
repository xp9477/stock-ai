<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <h1 class="page-title">策略</h1>
        <p class="page-sub">
          历史规则账户只读档案 · 不再创建资金账户或执行调仓
          <router-link class="link" to="/research">研究 / 回测 →</router-link>
        </p>
      </div>
      <div class="head-actions">
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="flow-hint panel">
      <div class="flow-hint-text">
        资本化规则赛马已退役。旧账户、持仓和订单仅作为历史证据保留；新的机械基准使用零资金 shadow，不产生订单。
      </div>
    </div>

    <div class="stat-grid">
      <div class="stat">
        <div class="stat-label">资本执行</div>
        <div class="stat-value" style="font-size:15px">已退役</div>
        <div class="stat-hint">无自动任务 · 无手动调仓</div>
      </div>
      <div class="stat">
        <div class="stat-label">S2 持仓 N</div>
        <div class="stat-value mono">{{ board.top_n || 10 }}</div>
        <div class="stat-hint">综合分前 N 等权</div>
      </div>
      <div class="stat">
        <div class="stat-label">样本门槛</div>
        <div class="stat-value mono" style="font-size:15px">
          {{ board.race?.min_trade_days || 60 }}日 · {{ board.race?.min_closed_trades || 100 }}笔
        </div>
        <div class="stat-hint">未达标灰显，不授冠</div>
      </div>
      <div class="stat">
        <div class="stat-label">证据角色</div>
        <div class="stat-value" style="font-size:15px">历史只读</div>
        <div class="stat-hint">不参与官方账户收益排行</div>
      </div>
    </div>

    <!-- 对照表 -->
    <section class="panel">
      <div class="panel-h">
        <div class="panel-title">对照排行</div>
        <span class="dim mono" style="font-size:11px">排序：夏普 ↓ · 锚置顶</span>
      </div>

      <div v-if="isMobile" class="m-list">
        <article
          v-for="(arm, i) in arms"
          :key="arm.model_id"
          class="m-card arm-card"
          :class="{ crown: arm.crown, dimmed: arm.exists && !arm.sample_ok, anchor: arm.role === 'anchor' }"
        >
          <div class="arm-head">
            <span class="rank mono">{{ arm.role === 'anchor' ? '锚' : i }}</span>
            <div class="arm-titles">
              <div class="arm-name">
                {{ arm.name || arm.model_id }}
                <span v-if="arm.crown" class="crown-badge">旧</span>
              </div>
              <div class="arm-tags">
                <span class="lane-pill" :class="roleClass(arm)">{{ roleLabel(arm) }}</span>
                <span class="lane-pill src">{{ sourceLabel(arm) }}</span>
                <el-tag v-if="arm.enabled === false" size="small" type="info">停用</el-tag>
                <el-tag v-else-if="arm.exists && !arm.sample_ok" size="small" type="warning">样本不足</el-tag>
                <el-tag v-else-if="arm.exists" size="small" type="info">历史</el-tag>
              </div>
            </div>
          </div>
          <p class="arm-desc">{{ arm.desc }}</p>
          <div class="m-grid metrics" v-if="arm.exists">
            <div><div class="m-label">夏普</div><div class="m-value mono">{{ fmtSharpe(arm.sharpe) }}</div></div>
            <div><div class="m-label">收益</div><div class="m-value mono" :class="(arm.pnl_pct||0)>=0?'up':'down'">{{ fmtPct(arm.pnl_pct) }}</div></div>
            <div><div class="m-label">回撤</div><div class="m-value mono down">{{ arm.max_drawdown_pct ?? '—' }}%</div></div>
            <div><div class="m-label">超额</div><div class="m-value mono" :class="excessClass(arm)">{{ fmtExcess(arm) }}</div></div>
          </div>
          <div v-if="arm.exists" class="arm-foot mono dim">
            {{ arm.trade_days || 0 }}日 · {{ arm.closed_trades || 0 }}笔平仓
            · 最近调仓 {{ arm.last_rebalance_at || '—' }}
          </div>
        </article>
      </div>

      <el-table
        v-else
        :data="arms"
        stripe
        v-loading="loading"
        :row-class-name="rowClass"
        empty-text="没有历史规则账户"
      >
        <el-table-column label="" width="48">
          <template #default="{ row }">
            <span v-if="row.crown" class="crown-badge">旧</span>
            <span v-else-if="row.role === 'anchor'" class="mono dim">锚</span>
          </template>
        </el-table-column>
        <el-table-column label="策略" min-width="160">
          <template #default="{ row }">
            <div class="name-cell">
              <b>{{ row.name || row.model_id }}</b>
              <div class="arm-tags">
                <span class="lane-pill" :class="roleClass(row)">{{ roleLabel(row) }}</span>
                <span class="lane-pill src">{{ sourceLabel(row) }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="夏普" width="90" sortable :sort-method="(a,b)=> (a.sharpe||0)-(b.sharpe||0)">
          <template #default="{ row }">
            <span class="mono" :class="{ dim: row.exists && !row.sample_ok }">{{ row.exists ? fmtSharpe(row.sharpe) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="收益" width="100">
          <template #default="{ row }">
            <span v-if="row.exists" class="mono" :class="(row.pnl_pct||0)>=0?'up':'down'">{{ fmtPct(row.pnl_pct) }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="最大回撤" width="100">
          <template #default="{ row }">
            <span v-if="row.exists" class="mono down">{{ row.max_drawdown_pct }}%</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="相对锚" width="100">
          <template #default="{ row }">
            <span class="mono" :class="excessClass(row)">{{ fmtExcess(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="样本" width="120">
          <template #default="{ row }">
            <template v-if="row.exists">
              <el-tag size="small" :type="row.sample_ok ? 'success' : 'info'">
                {{ row.sample_ok ? '达标' : '不足' }}
              </el-tag>
              <div class="mono dim" style="font-size:11px;margin-top:2px">
                {{ row.trade_days || 0 }}d · {{ row.closed_trades || 0 }}笔
              </div>
            </template>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="持仓/现金" width="140">
          <template #default="{ row }">
            <span v-if="row.exists" class="mono">
              {{ row.position_count }} · {{ fmtMoney(row.cash) }}
            </span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="最近调仓" width="140">
          <template #default="{ row }">
            <span class="mono dim">{{ row.last_rebalance_at || '—' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 因子截面：按需加载，避免进页就卡在扶摇批量拉数 -->
    <section class="panel">
      <div class="panel-h">
        <div class="panel-title">S2 因子截面</div>
        <el-button size="small" type="primary" plain :loading="factorLoading" @click="loadFactors">
          {{ factors.items?.length ? '刷新截面' : '加载截面' }}
        </el-button>
      </div>
      <p class="hint">
        需拉取股池日 K / 估值，可能需数十秒。仅运维查看时点加载，不影响对照表。
      </p>
      <p v-if="factors.message" class="hint">{{ factors.message }}</p>
      <p v-if="factors.top_n?.length" class="hint">
        当前前 {{ factors.top_n_size }}：
        <span class="mono accent">{{ factors.top_n.join(' · ') }}</span>
      </p>
      <el-table v-if="factors.items?.length" :data="factors.items" stripe size="small" max-height="360">
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column label="综合分" width="100">
          <template #default="{ row }"><span class="mono">{{ fmtNum(row.score) }}</span></template>
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
      </el-table>
      <el-empty
        v-else-if="!factorLoading && factorsLoaded"
        description="股池为空或因子未算出"
        :image-size="48"
      />
      <el-empty
        v-else-if="!factorLoading"
        description="点击「加载截面」查看当前因子排名"
        :image-size="48"
      />
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'

const { isMobile } = useIsMobile()
const board = ref({ arms: [], race: {} })
const factors = ref({ items: [], top_n: [] })
const loading = ref(false)
const factorLoading = ref(false)
const factorsLoaded = ref(false)

const arms = computed(() => board.value.arms || [])

function roleLabel(arm) {
  if (arm.role === 'anchor') return '锚'
  return '历史'
}
function roleClass(arm) {
  return arm.role === 'anchor' ? 'anchor' : 'rule'
}
function sourceLabel(arm) {
  if (arm.source === 'research') return '研究晋升'
  return '内置'
}
function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}
function fmtSharpe(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(2)
}
function fmtExcess(arm) {
  if (arm.role === 'anchor') return '基准'
  if (arm.excess_vs_anchor_pct == null) return '—'
  return fmtPct(arm.excess_vs_anchor_pct)
}
function excessClass(arm) {
  if (arm.role === 'anchor' || arm.excess_vs_anchor_pct == null) return 'dim'
  return arm.excess_vs_anchor_pct >= 0 ? 'up' : 'down'
}
function fmtMoney(v) {
  if (v == null) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
function fmtNum(v, d = 4) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(d)
}
function rowClass({ row }) {
  const cls = []
  if (row.crown) cls.push('row-crown')
  if (row.role === 'anchor') cls.push('row-anchor')
  if (row.exists && !row.sample_ok) cls.push('row-dim')
  return cls.join(' ')
}

async function load() {
  loading.value = true
  try {
    board.value = await api.strategiesBoard()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

async function loadFactors() {
  factorLoading.value = true
  try {
    factors.value = await api.factorsSnapshot()
    factorsLoaded.value = true
  } catch (err) {
    ElMessage.error(err.message || '因子截面加载失败（数据源超时可稍后重试）')
  } finally {
    factorLoading.value = false
  }
}

onMounted(() => {
  load()
  // 因子截面按需加载，避免进入策略页即被扶摇批量请求拖死
})
</script>

<style scoped>
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.link { margin-left: 10px; color: var(--accent); font-size: 13px; }
.hint { font-size: 12px; color: var(--text-muted); margin: 0 0 10px; }
.accent { color: var(--accent); }
.flow-hint {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 12px; padding: 12px 14px; margin-bottom: 4px;
  border-color: rgba(251, 191, 36, 0.3);
  background: rgba(251, 191, 36, 0.05);
}
.flow-hint-text { font-size: 13px; color: var(--text-muted); line-height: 1.5; flex: 1; }

.lane-pill {
  font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
  padding: 2px 7px; border-radius: 999px; text-transform: uppercase;
}
.lane-pill.rule { background: var(--lane-rule-dim, rgba(45,212,168,0.15)); color: var(--lane-rule, #2dd4a8); }
.lane-pill.anchor { background: rgba(148,163,184,0.2); color: #94a3b8; }
.lane-pill.src { background: var(--panel-2, #141e2e); color: var(--text-muted); border: 1px solid var(--border); }

.crown-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 999px;
  background: rgba(232,184,74,0.25); color: var(--accent, #e8b84a);
  font-size: 11px; font-weight: 800;
}
.name-cell { display: flex; flex-direction: column; gap: 4px; }
.arm-tags { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }

.arm-card { display: flex; flex-direction: column; gap: 8px; }
.arm-card.crown { border-color: rgba(232,184,74,0.45); }
.arm-card.anchor { border-style: dashed; }
.arm-card.dimmed { opacity: 0.72; }
.arm-head { display: flex; gap: 10px; align-items: flex-start; }
.rank { width: 28px; color: var(--text-dim); }
.arm-name { font-weight: 700; display: flex; align-items: center; gap: 6px; }
.arm-desc { margin: 0; font-size: 12px; color: var(--text-muted); line-height: 1.45; }
.arm-foot { font-size: 11px; }

:deep(.row-crown) { background: rgba(232,184,74,0.06) !important; }
:deep(.row-dim) { opacity: 0.7; }
:deep(.row-anchor td:first-child) { border-left: 3px solid #64748b; }
</style>

<template>
  <div class="page stack">
    <div class="page-head">
      <div>
        <button type="button" class="back-link" @click="$router.push('/runs')">← 决策列表</button>
        <h1 class="page-title">决策 #{{ $route.params.id }}</h1>
        <p class="page-sub">各 Agent 报告与最终动作</p>
      </div>
    </div>

    <el-alert v-if="detail.error" type="error" :title="detail.error" class="mb" show-icon />

    <el-tabs v-model="activeModel" class="detail-tabs" v-if="(detail.models || []).length">
      <el-tab-pane v-for="slot in detail.models" :key="slot.model_pk"
        :label="slot.model" :name="String(slot.model_pk)">
        <el-collapse v-if="slot.market_report || slot.reflection || slot.selector_report" class="mb report-collapse">
          <el-collapse-item v-if="slot.selector_report" name="selector">
            <template #title><b>AI 选股报告</b></template>
            <div class="markdown" v-html="render(slot.selector_report)" />
          </el-collapse-item>
          <el-collapse-item v-if="slot.market_report" name="market">
            <template #title><b>大盘环境</b></template>
            <div class="markdown" v-html="render(slot.market_report)" />
          </el-collapse-item>
          <el-collapse-item v-if="slot.reflection" name="reflect">
            <template #title><b>本轮反思</b></template>
            <div class="markdown" v-html="render(slot.reflection)" />
          </el-collapse-item>
        </el-collapse>

        <div v-for="stock in slot.stocks" :key="stock.code" class="panel mb stock-panel">
          <div class="stock-header">
            <span class="stock-title">{{ stock.name || '' }} <span class="mono dim">({{ stock.code }})</span></span>
            <template v-if="stock.decision">
              <el-tag :type="actionType(stock.decision.action)" effect="dark">
                {{ actionText(stock.decision.action) }}
                <template v-if="stock.decision.action !== 'hold'">
                  → {{ (stock.decision.target_position_pct * 100).toFixed(1) }}%
                </template>
              </el-tag>
              <span class="confidence mono">信心 {{ (stock.decision.confidence * 100).toFixed(0) }}%</span>
            </template>
          </div>

          <el-alert v-if="stock.decision?.error" type="error"
            :title="'分析失败: ' + stock.decision.error" :closable="false" class="mb" show-icon />
          <el-alert v-else-if="stock.decision" type="info" :title="stock.decision.reason"
            :closable="false" class="mb" show-icon />

          <el-collapse class="agent-collapse">
            <el-collapse-item v-for="agent in stock.agents" :key="agent.agent" :name="agent.agent">
              <template #title>
                <b>{{ agentName(agent.agent) }}</b>
                <span class="time mono">{{ agent.created_at }}</span>
              </template>
              <div class="markdown" v-html="render(agent.output)" />
              <div v-if="agent.input_summary" class="input-block">
                <div class="input-label">输入摘要</div>
                <pre class="input-summary">{{ agent.input_summary }}</pre>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-empty v-if="!loading && !(detail.models || []).length" description="该轮无分析数据" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { marked } from 'marked'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

const route = useRoute()
const detail = ref({})
const loading = ref(true)
const activeModel = ref('')

const AGENT_NAMES = {
  technical: '技术分析', fundamental: '基本面', news: '新闻情绪',
  bull_1: '多头 · 第1轮', bear_1: '空头 · 第1轮',
  bull_2: '多头 · 第2轮', bear_2: '空头 · 第2轮',
  trader: '交易员', risk: '风控',
}
const agentName = (key) => AGENT_NAMES[key] || key
const actionType = (a) => ({ buy: 'danger', sell: 'success', hold: 'info' }[a] || 'info')
const actionText = (a) => ({ buy: '买入', sell: '卖出', hold: '持有' }[a] || a)
const render = (text) => marked.parse(text || '')

onMounted(async () => {
  try {
    detail.value = await api.getRunDetail(route.params.id)
    if ((detail.value.models || []).length) {
      activeModel.value = String(detail.value.models[0].model_pk)
    }
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.back-link {
  background: none; border: none; color: var(--text-muted);
  font-size: 12px; padding: 0; margin-bottom: 6px; cursor: pointer;
}
.back-link:hover { color: var(--accent); }
.mb { margin-bottom: 14px; }
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
:deep(.el-tabs__item) { color: var(--text-muted); }
:deep(.el-tabs__item.is-active) { color: var(--accent); }
:deep(.el-tabs__active-bar) { background: var(--accent); }
:deep(.el-tabs__nav-wrap::after) { background: var(--border); }
</style>

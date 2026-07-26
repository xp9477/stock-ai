<template>
  <div>
    <el-page-header @back="$router.push('/runs')" :content="`决策详情 #${$route.params.id}`" class="mb" />
    <el-alert v-if="detail.error" type="error" :title="detail.error" class="mb" />

    <el-card v-for="stock in detail.stocks || []" :key="stock.code" class="mb">
      <template #header>
        <div class="stock-header">
          <span class="stock-title">{{ stock.name || '' }} ({{ stock.code }})</span>
          <template v-if="stock.decision">
            <el-tag :type="actionType(stock.decision.action)" effect="dark">
              {{ actionText(stock.decision.action) }}
              <template v-if="stock.decision.action !== 'hold'">
                → 目标仓位 {{ (stock.decision.target_position_pct * 100).toFixed(1) }}%
              </template>
            </el-tag>
            <span class="confidence">信心 {{ (stock.decision.confidence * 100).toFixed(0) }}%</span>
          </template>
        </div>
      </template>

      <el-alert v-if="stock.decision?.error" type="error" :title="'分析失败: ' + stock.decision.error"
        :closable="false" class="mb" />
      <el-alert v-else-if="stock.decision" type="info" :title="stock.decision.reason" :closable="false" class="mb" />

      <el-collapse>
        <el-collapse-item v-for="agent in stock.agents" :key="agent.agent" :name="agent.agent">
          <template #title>
            <b>{{ agentName(agent.agent) }}</b>
            <span class="time">{{ agent.created_at }}</span>
          </template>
          <div class="markdown" v-html="render(agent.output)" />
          <el-divider v-if="agent.input_summary" content-position="left">输入摘要</el-divider>
          <pre v-if="agent.input_summary" class="input-summary">{{ agent.input_summary }}</pre>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <el-empty v-if="!loading && !(detail.stocks || []).length" description="该轮无分析数据" />
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

const AGENT_NAMES = {
  technical: '🔍 技术分析师', fundamental: '📊 基本面分析师', news: '📰 新闻情绪分析师',
  bull_1: '🐂 多头研究员 (第1轮)', bear_1: '🐻 空头研究员 (第1轮)',
  bull_2: '🐂 多头研究员 (第2轮)', bear_2: '🐻 空头研究员 (第2轮)',
  trader: '💼 交易员', risk: '🛡️ 风控经理',
}
const agentName = (key) => AGENT_NAMES[key] || key
const actionType = (a) => ({ buy: 'danger', sell: 'success', hold: 'info' }[a] || 'info')
const actionText = (a) => ({ buy: '买入', sell: '卖出', hold: '持有' }[a] || a)
const render = (text) => marked.parse(text || '')

onMounted(async () => {
  try {
    detail.value = await api.getRunDetail(route.params.id)
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.mb { margin-bottom: 16px; }
.stock-header { display: flex; align-items: center; gap: 12px; }
.stock-title { font-size: 16px; font-weight: 700; }
.confidence { color: #909399; font-size: 13px; }
.time { margin-left: 12px; color: #c0c4cc; font-size: 12px; }
.markdown { line-height: 1.7; }
.input-summary { background: #f5f7fa; padding: 12px; border-radius: 4px; font-size: 12px;
  white-space: pre-wrap; max-height: 300px; overflow-y: auto; color: #606266; }
</style>

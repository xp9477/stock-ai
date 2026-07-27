<template>
  <el-card>
    <template #header>
      <div class="card-header" :class="{ 'card-header-mobile': isMobile }">
        <span>自选股池</span>
        <div class="add-form" :class="{ 'add-form-mobile': isMobile }">
          <el-input v-model="newCode" placeholder="6 位股票代码,如 600519"
            :style="isMobile ? 'flex: 1' : 'width: 220px'" maxlength="6" @keyup.enter="add" />
          <el-button type="primary" :loading="adding" @click="add">添加</el-button>
          <el-button type="warning" :loading="selecting" @click="autoSelect">AI 选股</el-button>
        </div>
      </div>
    </template>

    <template v-if="isMobile">
      <div v-for="row in items" :key="row.code" class="m-card">
        <div class="m-row">
          <div>
            <div class="m-card-title">
              {{ row.name }}
              <el-tag size="small" :type="row.source === 'auto' ? 'warning' : 'info'">
                {{ row.source === 'auto' ? 'AI 选入' : '手动' }}
              </el-tag>
            </div>
            <div class="code">{{ row.code }}</div>
          </div>
          <div class="quote">
            <div class="price">{{ row.price ?? '-' }}</div>
            <div v-if="row.pct_change != null" :class="row.pct_change >= 0 ? 'up' : 'down'" class="pct">
              {{ row.pct_change.toFixed(2) }}%
            </div>
            <div v-else class="pct">-</div>
          </div>
          <el-button size="small" type="danger" plain @click="remove(row.code)">移除</el-button>
        </div>
        <div v-if="row.source === 'auto' && row.miss_count >= 2" class="warn">
          ⚠️ 已连续 {{ row.miss_count }} 次未被 AI 看好,即将淘汰
        </div>
        <el-collapse v-if="row.select_reason" class="reason-collapse">
          <el-collapse-item title="AI 选入理由">
            <div class="reason-text">{{ row.select_reason }}</div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </template>

    <el-table v-else :data="items" stripe v-loading="loading">
      <el-table-column prop="code" label="代码" width="100" />
      <el-table-column prop="name" label="名称" width="130" />
      <el-table-column label="来源" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.source === 'auto' ? 'warning' : 'info'">
            {{ row.source === 'auto' ? 'AI 选入' : '手动' }}
          </el-tag>
          <el-tooltip v-if="row.source === 'auto' && row.miss_count >= 2"
            :content="`已连续 ${row.miss_count} 次未被 AI 看好,即将淘汰`">
            <span class="miss-warn">⚠️</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="现价" width="100">
        <template #default="{ row }">{{ row.price ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="涨跌幅" width="100">
        <template #default="{ row }">
          <span v-if="row.pct_change != null" :class="row.pct_change >= 0 ? 'up' : 'down'">
            {{ row.pct_change.toFixed(2) }}%
          </span>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="select_reason" label="AI 选入理由" show-overflow-tooltip />
      <el-table-column label="操作" width="90">
        <template #default="{ row }">
          <el-button size="small" type="danger" plain @click="remove(row.code)">移除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !items.length"
      description="股池为空,可手动添加或点击「AI 选股」自动选入" />
  </el-card>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'

const { isMobile } = useIsMobile()
const items = ref([])
const newCode = ref('')
const loading = ref(false)
const adding = ref(false)
const selecting = ref(false)
let timer = null

async function load() {
  try {
    items.value = await api.getWatchlist()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function add() {
  if (!/^\d{6}$/.test(newCode.value)) {
    ElMessage.warning('请输入 6 位数字代码')
    return
  }
  adding.value = true
  try {
    await api.addWatchlist(newCode.value)
    ElMessage.success('已添加')
    newCode.value = ''
    load()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    adding.value = false
  }
}

async function autoSelect() {
  selecting.value = true
  try {
    await api.autoSelect()
    ElMessage.success('AI 选股已启动,约 1-2 分钟后自动更新股池')
    timer = setInterval(load, 15000)
    setTimeout(() => { clearInterval(timer); timer = null }, 180000)
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    selecting.value = false
  }
}

async function remove(code) {
  try {
    await api.removeWatchlist(code)
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

onMounted(() => {
  loading.value = true
  load().finally(() => (loading.value = false))
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; }
.card-header-mobile { flex-direction: column; align-items: stretch; gap: 8px; }
.add-form { display: flex; gap: 8px; }
.add-form-mobile { width: 100%; }
.add-form-mobile .el-button + .el-button { margin-left: 0; }
.code { color: #909399; font-size: 12px; }
.quote { margin-left: auto; text-align: right; margin-right: 8px; }
.price { font-size: 15px; font-weight: 600; }
.pct { font-size: 12px; }
.warn { color: #e6a23c; font-size: 12px; margin-top: 6px; }
.miss-warn { margin-left: 4px; cursor: help; }
.reason-collapse { margin-top: 8px; }
.reason-collapse :deep(.el-collapse-item__header) { height: 32px; font-size: 12px; color: #909399; }
.reason-text { font-size: 13px; color: #606266; line-height: 1.6; }
</style>

<template>
  <el-card>
    <template #header>决策记录</template>

    <template v-if="isMobile">
      <div v-for="row in runs" :key="row.id" class="m-card clickable-card" @click="goDetail(row)">
        <div class="m-card-head">
          <span class="m-card-title">#{{ row.id }}</span>
          <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
          <el-tag size="small" :type="triggerTagType(row.trigger)">
            {{ triggerText(row.trigger) }}
          </el-tag>
        </div>
        <div class="time-line">{{ row.started_at.slice(5, 16) }} → {{ row.finished_at ? row.finished_at.slice(11, 16) : '-' }}</div>
        <div v-if="(row.summary || []).length" class="summary-line">
          <el-tag v-for="(item, i) in row.summary" :key="i" size="small" class="mr"
            :type="actionType(item.action)">
            {{ item.model }}·{{ item.name }} {{ actionText(item.action) }}
          </el-tag>
        </div>
      </div>
      <el-empty v-if="!loading && !runs.length" description="暂无决策记录,点击右上角「运行」" />
    </template>

    <template v-else>
      <el-table :data="runs" stripe v-loading="loading" @row-click="goDetail" class="clickable">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="started_at" label="开始时间" width="170" />
        <el-table-column prop="finished_at" label="结束时间" width="170" />
        <el-table-column label="触发" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="triggerTagType(row.trigger)">
              {{ triggerText(row.trigger) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="决策摘要">
          <template #default="{ row }">
            <el-tag v-for="(item, i) in row.summary" :key="i" size="small" class="mr"
              :type="actionType(item.action)">
              {{ item.model }}·{{ item.name }} {{ actionText(item.action) }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !runs.length" description="暂无决策记录,点击右上角「立即运行一轮」" />
    </template>
  </el-card>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import { useIsMobile } from '../composables/useIsMobile.js'

const { isMobile } = useIsMobile()
const router = useRouter()
const runs = ref([])
const loading = ref(false)
let timer = null

async function load() {
  try {
    runs.value = await api.getRuns()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function goDetail(row) {
  router.push(`/runs/${row.id}`)
}

const triggerTagType = (t) => ({ manual: 'primary', selector: 'warning' }[t] || 'info')
const triggerText = (t) => ({ manual: '手动', schedule: '定时', selector: 'AI 选股' }[t] || t)
const statusType = (s) => ({ running: 'warning', done: 'success', failed: 'danger' }[s] || 'info')
const statusText = (s) => ({ running: '运行中', done: '完成', failed: '失败' }[s] || s)
const actionType = (a) => ({ buy: 'danger', sell: 'success', hold: 'info' }[a] || 'info')
const actionText = (a) => ({ buy: '买入', sell: '卖出', hold: '持有' }[a] || a)

onMounted(() => {
  loading.value = true
  load().finally(() => (loading.value = false))
  timer = setInterval(load, 15000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.clickable :deep(.el-table__row) { cursor: pointer; }
.clickable-card { cursor: pointer; }
.mr { margin-right: 4px; margin-bottom: 4px; }
.time-line { font-size: 11px; color: #909399; margin-bottom: 6px; }
.summary-line { display: flex; flex-wrap: wrap; gap: 0; }
.clickable-card .m-card-title { font-size: 15px; }
</style>

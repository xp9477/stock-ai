<template>
  <el-card>
    <template #header>决策记录</template>
    <el-table :data="runs" stripe v-loading="loading" @row-click="goDetail" class="clickable">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="started_at" label="开始时间" width="170" />
      <el-table-column prop="finished_at" label="结束时间" width="170" />
      <el-table-column label="触发" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.trigger === 'manual' ? 'primary' : 'info'">
            {{ row.trigger === 'manual' ? '手动' : '定时' }}
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
          <el-tag v-for="item in row.summary" :key="item.code" size="small" class="mr"
            :type="actionType(item.action)">
            {{ item.name }} {{ actionText(item.action) }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !runs.length" description="暂无决策记录,点击右上角「立即运行一轮」" />
  </el-card>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

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
.mr { margin-right: 6px; }
</style>

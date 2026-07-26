<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>自选股池</span>
        <div class="add-form">
          <el-input v-model="newCode" placeholder="6 位股票代码,如 600519" style="width: 220px"
            maxlength="6" @keyup.enter="add" />
          <el-button type="primary" :loading="adding" @click="add">添加</el-button>
        </div>
      </div>
    </template>
    <el-table :data="items" stripe v-loading="loading">
      <el-table-column prop="code" label="代码" width="120" />
      <el-table-column prop="name" label="名称" width="160" />
      <el-table-column label="现价" width="120">
        <template #default="{ row }">{{ row.price ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="涨跌幅" width="120">
        <template #default="{ row }">
          <span v-if="row.pct_change != null" :class="row.pct_change >= 0 ? 'up' : 'down'">
            {{ row.pct_change.toFixed(2) }}%
          </span>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="{ row }">
          <el-button size="small" type="danger" plain @click="remove(row.code)">移除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !items.length" description="股池为空,添加自选股后 AI 才会开始分析" />
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

const items = ref([])
const newCode = ref('')
const loading = ref(false)
const adding = ref(false)

async function load() {
  loading.value = true
  try {
    items.value = await api.getWatchlist()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
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

async function remove(code) {
  try {
    await api.removeWatchlist(code)
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

onMounted(load)
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; }
.add-form { display: flex; gap: 8px; }
</style>

<template>
  <el-card>
    <template #header>交易记录</template>
    <el-table :data="orders" stripe v-loading="loading">
      <el-table-column prop="created_at" label="时间" width="170" />
      <el-table-column prop="code" label="代码" width="90" />
      <el-table-column prop="name" label="名称" width="120" />
      <el-table-column label="方向" width="80">
        <template #default="{ row }">
          <el-tag :type="row.side === 'buy' ? 'danger' : 'success'" size="small">
            {{ row.side === 'buy' ? '买入' : '卖出' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="price" label="价格" width="100" />
      <el-table-column prop="qty" label="数量" width="100" />
      <el-table-column prop="amount" label="金额" width="120" />
      <el-table-column prop="fee" label="费用" width="90" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'filled' ? 'success' : 'info'" size="small">
            {{ row.status === 'filled' ? '成交' : '拒绝' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="reject_reason" label="备注" />
    </el-table>
    <el-empty v-if="!loading && !orders.length" description="暂无交易记录" />
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

const orders = ref([])
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  try {
    orders.value = await api.getOrders()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
})
</script>

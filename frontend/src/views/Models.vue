<template>
  <div>
    <el-card>
      <template #header>
        <div class="card-header">
          <span>参赛模型</span>
          <div class="btns">
            <el-button size="small" type="primary" @click="openLlmDialog">添加 LLM 模型</el-button>
            <el-button size="small" type="warning" @click="openEnsembleDialog">新建合议组合</el-button>
          </div>
        </div>
      </template>
      <el-table :data="models" stripe>
        <el-table-column prop="name" label="名称" width="150" />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag :type="row.type === 'ensemble' ? 'warning' : 'primary'" size="small">
              {{ row.type === 'ensemble' ? '合议' : 'LLM' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model_id" label="API 模型名" width="200" />
        <el-table-column label="成员" min-width="180">
          <template #default="{ row }">
            <template v-if="row.type === 'ensemble'">
              <el-tag v-for="pk in row.members" :key="pk" size="small" class="mr">
                {{ nameOf(pk) }}
              </el-tag>
            </template>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="收益率" width="100">
          <template #default="{ row }">
            <span :class="row.pnl_pct >= 0 ? 'up' : 'down'">{{ row.pnl_pct.toFixed(2) }}%</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v) => toggle(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-popconfirm title="删除该模型及其全部账户数据?" @confirm="remove(row.id)">
              <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="llmDialog" title="添加 LLM 模型" width="420px">
      <el-form label-width="90px">
        <el-form-item label="展示名"><el-input v-model="llmForm.name" placeholder="如 GPT 5.6" /></el-form-item>
        <el-form-item label="API 模型名"><el-input v-model="llmForm.model_id" placeholder="如 gpt-5.6-luna" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="llmDialog = false">取消</el-button>
        <el-button type="primary" @click="createLlm">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="ensembleDialog" title="新建合议组合" width="420px">
      <el-form label-width="90px">
        <el-form-item label="组合名"><el-input v-model="ensembleForm.name" placeholder="如 A+B 合议" /></el-form-item>
        <el-form-item label="成员模型">
          <el-checkbox-group v-model="ensembleForm.members">
            <el-checkbox v-for="m in llmModels" :key="m.id" :value="m.id">{{ m.name }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <div class="hint">组合决策由成员每日最终决策多数票合成(过半才动手,仓位取均值),不额外消耗 LLM 调用。</div>
      <template #footer>
        <el-button @click="ensembleDialog = false">取消</el-button>
        <el-button type="warning" @click="createEnsemble">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'

const models = ref([])
const llmDialog = ref(false)
const ensembleDialog = ref(false)
const llmForm = ref({ name: '', model_id: '' })
const ensembleForm = ref({ name: '', members: [] })

const llmModels = computed(() => models.value.filter((m) => m.type === 'llm'))
const nameOf = (pk) => models.value.find((m) => m.id === pk)?.name || pk

async function load() {
  try {
    models.value = await api.getModels()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function openLlmDialog() {
  llmForm.value = { name: '', model_id: '' }
  llmDialog.value = true
}

function openEnsembleDialog() {
  ensembleForm.value = { name: '', members: [] }
  ensembleDialog.value = true
}

async function createLlm() {
  try {
    await api.createModel({ ...llmForm.value, type: 'llm' })
    ElMessage.success('已添加')
    llmDialog.value = false
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function createEnsemble() {
  try {
    await api.createModel({ name: ensembleForm.value.name, type: 'ensemble', members: ensembleForm.value.members })
    ElMessage.success('已创建')
    ensembleDialog.value = false
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function toggle(row, enabled) {
  try {
    await api.updateModel(row.id, { enabled })
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function remove(id) {
  try {
    await api.deleteModel(id)
    ElMessage.success('已删除')
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

onMounted(load)
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; }
.btns { display: flex; gap: 8px; }
.mr { margin-right: 6px; }
.hint { color: #909399; font-size: 12px; margin-top: 4px; }
</style>

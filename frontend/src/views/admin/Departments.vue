<template>
  <div class="page">
    <div class="page-head">
      <h2 class="page-title">部门管理</h2>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建部门</el-button>
    </div>

    <div v-loading="loading" class="tk-card">
      <el-table :data="items" style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="部门名称" min-width="160" />
        <el-table-column prop="user_count" label="用户数" width="90" align="center" />
        <el-table-column prop="doc_count" label="文档数" width="90" align="center" />
        <el-table-column prop="crawl_task_count" label="爬虫任务数" width="110" align="center" />
        <el-table-column label="操作" width="160" align="center">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openRename(row)">重命名</el-button>
            <el-button size="small" link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !items.length" description="暂无部门" />
    </div>

    <!-- 新建 / 重命名弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '重命名部门' : '新建部门'" width="400px">
      <el-form @submit.prevent="handleSubmit">
        <el-form-item label="部门名称" required>
          <el-input v-model="form.name" placeholder="请输入部门名称" maxlength="64" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { deptAdminApi } from '@/api/modules'

const loading = ref(false)
const submitting = ref(false)
const items = ref([])
const dialogVisible = ref(false)
const editingId = ref(null)
const form = reactive({ name: '' })

async function fetchData() {
  loading.value = true
  try {
    const res = await deptAdminApi.list()
    items.value = res.items || []
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  form.name = ''
  dialogVisible.value = true
}

function openRename(row) {
  editingId.value = row.id
  form.name = row.name
  dialogVisible.value = true
}

async function handleSubmit() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('请输入部门名称')
    return
  }
  submitting.value = true
  try {
    if (editingId.value) {
      await deptAdminApi.rename(editingId.value, name)
      ElMessage.success('已重命名')
    } else {
      await deptAdminApi.create(name)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await fetchData()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`删除部门「${row.name}」？存在用户/文档/爬虫任务引用的部门无法删除。`, '删除确认', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await deptAdminApi.remove(row.id)
    ElMessage.success('已删除')
    await fetchData()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

onMounted(fetchData)
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--sp-4);
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--ink-900);
  margin: 0;
}
</style>

<template>
  <div class="page-card tk-card">
    <div class="toolbar">
      <h3 class="page-title">用户管理</h3>
      <div class="toolbar-actions">
        <el-button type="primary" :icon="Plus" @click="openCreate">新建账号</el-button>
        <el-button :icon="Refresh" @click="fetchList">刷新</el-button>
      </div>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!loading && !items.length" description="暂无用户" />

      <template v-else>
        <el-table :data="items">
          <el-table-column label="用户名" min-width="140">
            <template #default="{ row }">
              <span class="username">{{ row.username }}</span>
              <el-tag v-if="row.id === myId" size="small" effect="plain" style="margin-left: 6px">当前账号</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="角色" width="120">
            <template #default="{ row }">
              <el-tag :type="ROLE_TAG[row.role] || 'info'" size="small">{{ ROLE_LABEL[row.role] || row.role }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="部门" width="140">
            <template #default="{ row }">{{ row.department_name || '无部门' }}</template>
          </el-table-column>
          <el-table-column label="创建时间" width="150">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="160" align="center">
            <template #default="{ row }">
              <el-button size="small" :icon="Edit" @click="openEdit(row)">编辑</el-button>
              <el-button
                size="small"
                type="danger"
                plain
                :icon="Delete"
                :disabled="row.id === myId"
                @click="handleDelete(row)"
              >删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="total > pageSize" class="pagination-wrap">
          <el-pagination
            background
            layout="prev, pager, next, total"
            :total="total"
            :page-size="pageSize"
            :current-page="page"
            @current-change="onPageChange"
          />
        </div>
      </template>
    </div>

    <!-- 新建 / 编辑弹窗 -->
    <el-dialog v-model="formVisible" :title="editing ? '编辑用户' : '新建账号'" width="480px" destroy-on-close>
      <el-form :model="form" label-width="90px">
        <el-form-item label="用户名" required>
          <el-input v-model="form.username" placeholder="登录账号" :disabled="!!editing" maxlength="64" />
        </el-form-item>
        <el-form-item v-if="!editing" label="初始密码" required>
          <el-input v-model="form.password" type="password" show-password placeholder="至少 6 位" maxlength="128" />
        </el-form-item>
        <el-form-item v-else label="重置密码">
          <el-input v-model="form.password" type="password" show-password placeholder="留空则不修改密码" maxlength="128" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option v-for="(label, value) in ROLE_LABEL" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="部门">
          <el-select v-model="form.department_id" placeholder="留空 = 无部门" clearable style="width: 100%">
            <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="!form.username.trim() || (!editing && form.password.length < 6)"
          @click="submitForm"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Edit, Delete } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { adminApi, authApi } from '@/api/modules'
import { formatTime } from '@/utils/format'

const ROLE_LABEL = { admin: '管理员', dept_admin: '部门管理员', user: '普通用户' }
const ROLE_TAG = { admin: 'danger', dept_admin: 'warning', user: 'info' }

const userStore = useUserStore()
const myId = userStore.user?.id

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const departments = ref([])

const formVisible = ref(false)
const editing = ref(null)
const saving = ref(false)
const form = ref({ username: '', password: '', role: 'user', department_id: null })

async function fetchList() {
  loading.value = true
  try {
    const res = await adminApi.users({ page: page.value, page_size: pageSize.value })
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function onPageChange(p) {
  page.value = p
  fetchList()
}

function openCreate() {
  editing.value = null
  form.value = { username: '', password: '', role: 'user', department_id: null }
  formVisible.value = true
}

function openEdit(row) {
  editing.value = row
  form.value = {
    username: row.username,
    password: '',
    role: row.role,
    department_id: row.department_id ?? null,
  }
  formVisible.value = true
}

async function submitForm() {
  saving.value = true
  try {
    if (editing.value) {
      const payload = { role: form.value.role, department_id: form.value.department_id ?? null }
      if (form.value.password.trim()) payload.password = form.value.password
      await adminApi.patchUser(editing.value.id, payload)
      ElMessage.success('用户已更新')
    } else {
      await adminApi.createUser({
        username: form.value.username.trim(),
        password: form.value.password,
        role: form.value.role,
        department_id: form.value.department_id ?? null,
      })
      ElMessage.success('账号已创建')
    }
    formVisible.value = false
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除用户「${row.username}」吗？其上传文档、收藏夹等将一并处理。`,
      '删除确认',
      { type: 'error', confirmButtonText: '删除' }
    )
  } catch (e) {
    return
  }
  try {
    await adminApi.deleteUser(row.id)
    ElMessage.success('用户已删除')
    fetchList()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

async function fetchDepartments() {
  try {
    departments.value = (await authApi.departments()) || []
  } catch (e) {
    /* 拦截器已提示 */
  }
}

onMounted(() => {
  fetchList()
  fetchDepartments()
})
</script>

<style scoped>
.page-card {
  padding: 18px 20px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.page-title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
}

.username {
  font-weight: 600;
}

.pagination-wrap {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
</style>

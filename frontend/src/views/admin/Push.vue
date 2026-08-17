<template>
  <div class="page-card tk-card push-wrap">
    <h3 class="page-title">部门推送</h3>
    <p class="sub">创建推送通知：目标部门留空 = 全员可见；可关联一篇文档供跳转。</p>

    <el-form :model="form" label-width="90px" class="push-form">
      <el-form-item label="标题" required>
        <el-input v-model="form.title" placeholder="推送标题" maxlength="128" show-word-limit />
      </el-form-item>
      <el-form-item label="内容">
        <el-input v-model="form.content" type="textarea" :rows="5" placeholder="推送正文（可选）" />
      </el-form-item>
      <el-form-item label="关联文档">
        <el-select
          v-model="form.document_id"
          placeholder="可选：关联一篇文档"
          clearable
          filterable
          style="width: 100%"
        >
          <el-option v-for="d in docOptions" :key="d.id" :label="d.title" :value="d.id">
            <span>{{ d.title }}</span>
            <span class="opt-meta muted">[{{ FILE_TYPE_LABEL[d.file_type] || d.file_type }}]</span>
          </el-option>
        </el-select>
      </el-form-item>
      <el-form-item label="目标部门">
        <el-select
          v-model="form.department_ids"
          placeholder="留空 = 全员；可多选目标部门"
          multiple
          clearable
          collapse-tags
          collapse-tags-tooltip
          style="width: 100%"
        >
          <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
        </el-select>
        <div v-if="isDeptAdmin" class="form-tip">部门管理员仅可向全员或包含本部门的组合推送（后端 403 兜底）</div>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="submitting" :disabled="!form.title.trim()" @click="handleSubmit">
          发布推送
        </el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { adminApi, authApi } from '@/api/modules'
import { FILE_TYPE_LABEL } from '@/utils/format'

const userStore = useUserStore()
const isDeptAdmin = computed(() => userStore.role === 'dept_admin')

const form = ref({ title: '', content: '', document_id: null, department_ids: [] })
const departments = ref([])
const docOptions = ref([])
const submitting = ref(false)

async function handleSubmit() {
  submitting.value = true
  try {
    await adminApi.push({
      title: form.value.title.trim(),
      content: form.value.content.trim(),
      document_id: form.value.document_id ?? null,
      department_ids: form.value.department_ids?.length
        ? [...form.value.department_ids]
        : [],
    })
    ElMessage.success('推送已发布')
    form.value = { title: '', content: '', document_id: null, department_ids: [] }
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    submitting.value = false
  }
}

/** 拉取可选文档（已通过的前 50 条）与部门列表 */
async function fetchOptions() {
  try {
    const [docs, depts] = await Promise.all([
      adminApi.documents({ page: 1, page_size: 50, status: 'approved' }),
      authApi.departments(),
    ])
    docOptions.value = docs.items || []
    departments.value = depts || []
    if (isDeptAdmin.value && userStore.user?.department_id != null) {
      form.value.department_ids = [userStore.user.department_id]
    }
  } catch (e) {
    /* 拦截器已提示 */
  }
}

onMounted(fetchOptions)
</script>

<style scoped>
.page-card {
  padding: 18px 20px;
  max-width: 720px;
}

.page-title {
  margin: 0 0 4px;
  font-size: 17px;
  font-weight: 600;
}

.sub {
  margin: 0 0 18px;
  font-size: 13px;
  color: var(--ink-600);
}

.push-form {
  max-width: 620px;
}

.opt-meta {
  margin-left: 8px;
  font-size: 12px;
}

.muted {
  color: var(--ink-400);
}

.form-tip {
  font-size: 12px;
  color: var(--ink-400);
  line-height: 1.6;
  width: 100%;
}
</style>
